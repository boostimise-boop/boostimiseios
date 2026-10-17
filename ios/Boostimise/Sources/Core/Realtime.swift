import Foundation

/// Supabase Realtime (core/realtime.py): live "a row changed" events over one WebSocket (Phoenix
/// channels, JSON v1), the same subscriptions the website makes. Reconnects by itself.
@MainActor
final class RealtimeChannel {
    private let topic: String
    private let changes: [JSON]
    private let onChange: (String) -> Void
    private let onState: (String) -> Void
    private var ws: URLSessionWebSocketTask?
    private var token: String?
    private var stopped = true
    private var ref = 0
    private var joinRef: String?
    private var pendingHeartbeat: String?
    private var attempt = 0
    private var generation = 0
    private var heartbeatTask: Task<Void, Never>?
    private var reconnectTask: Task<Void, Never>?
    private(set) var subscribed = false

    private static let heartbeatSeconds: UInt64 = 25
    private static let backoff: [UInt64] = [1, 2, 5, 10, 30]

    init(name: String, changes: [JSON], onChange: @escaping (String) -> Void, onState: @escaping (String) -> Void) {
        topic = "realtime:\(name)"
        self.changes = changes
        self.onChange = onChange
        self.onState = onState
    }

    func start(token: String?) {
        self.token = token
        stopped = false
        attempt = 0
        open()
    }

    func stop() {
        stopped = true
        reconnectTask?.cancel()
        heartbeatTask?.cancel()
        if subscribed { _ = push(topic, "phx_leave", [:]) }
        subscribed = false
        generation += 1
        ws?.cancel(with: .normalClosure, reason: nil)
        ws = nil
        onState("closed")
    }

    func setToken(_ token: String?) {
        self.token = token
        if subscribed, let t = token { _ = push(topic, "access_token", jsonOf(("access_token", t))) }
    }

    private func open() {
        if stopped { return }
        onState("connecting")
        generation += 1
        let g = generation
        ws?.cancel()
        pendingHeartbeat = nil
        let base = Config.supabaseURL.replacingOccurrences(of: "https://", with: "wss://")
        var req = URLRequest(url: URL(string: "\(base)/realtime/v1/websocket?apikey=\(Config.supabaseKey)&vsn=1.0.0")!)
        req.setValue(Config.userAgent, forHTTPHeaderField: "User-Agent")
        let task = Http.session.webSocketTask(with: req)
        ws = task
        task.resume()
        onConnected()
        Task { [weak self] in
            while true {
                do {
                    let msg = try await task.receive()
                    guard let self, self.generation == g else { return }
                    switch msg {
                    case .string(let s): self.onMessage(s)
                    case .data(let d): self.onMessage(String(decoding: d, as: UTF8.self))
                    @unknown default: break
                    }
                } catch {
                    guard let self, self.generation == g else { return }
                    self.onDisconnected()
                    return
                }
            }
        }
    }

    private func nextRef() -> String {
        ref += 1
        return String(ref)
    }

    @discardableResult
    private func push(_ topic: String, _ event: String, _ payload: JSON, join: Bool = false) -> String {
        let r = nextRef()
        var msg = jsonOf(("topic", topic), ("event", event), ("payload", payload), ("ref", r))
        if join { joinRef = r }
        if let j = joinRef, topic == self.topic { msg["join_ref"] = j }
        ws?.send(.string(JSONTools.string(msg))) { _ in }
        return r
    }

    private func onConnected() {
        reconnectTask?.cancel()
        heartbeatTask?.cancel()
        heartbeatTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: Self.heartbeatSeconds * 1_000_000_000)
                guard let self, !Task.isCancelled, !self.stopped else { return }
                if self.pendingHeartbeat != nil {
                    // no answer to the last one: the connection is dead
                    self.pendingHeartbeat = nil
                    self.ws?.cancel()
                    return
                }
                self.pendingHeartbeat = self.push("phoenix", "heartbeat", [:])
            }
        }
        let config = jsonOf(
            ("broadcast", jsonOf(("ack", false), ("self", false))),
            ("presence", jsonOf(("key", ""), ("enabled", false))),
            ("postgres_changes", changes),
            ("private", false)
        )
        var payload = jsonOf(("config", config))
        if let t = token { payload["access_token"] = t }
        push(topic, "phx_join", payload, join: true)
    }

    private func onDisconnected() {
        subscribed = false
        heartbeatTask?.cancel()
        if stopped { return }
        onState("error")
        if reconnectTask == nil || reconnectTask!.isCancelled {
            let delay = Self.backoff[min(attempt, Self.backoff.count - 1)]
            attempt += 1
            reconnectTask = Task { [weak self] in
                try? await Task.sleep(nanoseconds: delay * 1_000_000_000)
                guard let self, !Task.isCancelled else { return }
                self.reconnectTask = nil
                self.open()
            }
        }
    }

    private func onMessage(_ text: String) {
        guard let msg = JSONTools.parse(text) as? JSON else { return }
        let t = msg.str("topic")
        let event = msg.str("event")
        let payload = msg.obj("payload") ?? [:]
        if t == "phoenix" && event == "phx_reply" {
            if msg.str("ref") == pendingHeartbeat { pendingHeartbeat = nil }
            return
        }
        if t != topic { return }
        if event == "phx_reply" && msg.str("ref") == joinRef {
            if payload.str("status") == "ok" {
                subscribed = true
                attempt = 0
                onState("subscribed")
            } else {
                onState("error")
                ws?.cancel()
            }
        } else if event == "postgres_changes" {
            onChange(payload.obj("data")?.str("table") ?? "")
        } else if event == "phx_error" || event == "phx_close" {
            subscribed = false
            if !stopped { ws?.cancel() }
        }
    }
}
