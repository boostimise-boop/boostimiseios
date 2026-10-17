import Foundation
import Network

/// The browser's way back after Google sign-in (core/account.py's _CallbackServer): a tiny web
/// server on 127.0.0.1 that only runs while signing in. The sign-in sheet opens
/// http://127.0.0.1:53214/auth/callback?code=..., the app takes the code and the page says how it
/// ended, then goes to boostimise://signed-in, which closes the sheet.
final class AuthCallbackServer: @unchecked Sendable {
    private let listener: NWListener
    private let onResult: ([String: String]) -> Void
    private let lock = NSLock()
    private var delivered = false
    private let done = DispatchSemaphore(value: 0)
    private var outcome: (Bool, String) = (false, "Sign-in was cancelled.")
    private let queue = DispatchQueue(label: "auth-callback", attributes: .concurrent)

    init(onResult: @escaping ([String: String]) -> Void) throws {
        self.onResult = onResult
        let params = NWParameters.tcp
        params.requiredInterfaceType = .loopback
        params.allowLocalEndpointReuse = true
        listener = try NWListener(using: params, on: NWEndpoint.Port(rawValue: Config.authPort)!)
    }

    /// Starts listening; throws when the port cannot be opened.
    func start() throws {
        let ready = DispatchSemaphore(value: 0)
        let failure = ErrorBox()
        listener.stateUpdateHandler = { state in
            switch state {
            case .ready: ready.signal()
            case .failed(let e): failure.set(e); ready.signal()
            default: break
            }
        }
        listener.newConnectionHandler = { [weak self] conn in self?.accept(conn) }
        listener.start(queue: queue)
        if ready.wait(timeout: .now() + 3) == .timedOut { throw HttpError(status: 0, code: "port", message: "timeout") }
        if let e = failure.get() { throw e }
    }

    func stop() {
        listener.cancel()
    }

    func finish(_ ok: Bool, _ message: String = "") {
        lock.lock()
        outcome = (ok, message)
        lock.unlock()
        done.signal()
    }

    private func accept(_ conn: NWConnection) {
        conn.start(queue: queue)
        receive(conn, Data())
    }

    private func receive(_ conn: NWConnection, _ buffer: Data) {
        conn.receive(minimumIncompleteLength: 1, maximumLength: 16 * 1024) { [weak self] data, _, isComplete, error in
            guard let self else { return }
            var buf = buffer
            if let d = data { buf.append(d) }
            if buf.range(of: Data("\r\n\r\n".utf8)) != nil || isComplete || error != nil || buf.count > 64 * 1024 {
                // answer off the network queue: a successful sign-in waits here for the app to finish
                DispatchQueue.global().async { self.handle(conn, String(decoding: buf, as: UTF8.self)) }
            } else {
                self.receive(conn, buf)
            }
        }
    }

    private func handle(_ conn: NWConnection, _ request: String) {
        let first = request.components(separatedBy: "\r\n").first ?? ""
        let parts = first.split(separator: " ")
        let path = parts.count > 1 ? String(parts[1]) : "/"
        let comps = URLComponents(string: "http://127.0.0.1" + path)
        if comps?.path != "/auth/callback" {
            respond(conn, 404, "Not found")
            return
        }
        var qs: [String: String] = [:]
        for item in comps?.queryItems ?? [] { qs[item.name] = item.value ?? "" }
        let error = qs["error_description"] ?? qs["error"]
        if (qs["code"] ?? "").isEmpty && (qs["refresh_token"] ?? "").isEmpty && error == nil {
            page(conn, "Signing you in…", "One moment…", "", Self.hashScript)
        } else if let error {
            _ = deliver(qs)
            page(conn, "Sign-in failed", "\(esc(error))<br><br>Go back to Boostimise and try again.", Self.errIcon, Self.back)
        } else {
            let (ok, message) = deliver(qs, wait: true)
            if ok {
                page(conn, "You're signed in", "Going back to Boostimise…", Self.okIcon,
                     Self.back + "<script>setTimeout(function(){location.href='boostimise://signed-in'},600)</script>")
            } else {
                page(conn, "Sign-in failed", "\(esc(message))<br><br>Go back to Boostimise and try again.", Self.errIcon, Self.back)
            }
        }
    }

    private func deliver(_ qs: [String: String], wait: Bool = false) -> (Bool, String) {
        lock.lock()
        if delivered {
            lock.unlock()
            return (false, "This sign-in link was already used.")
        }
        delivered = true
        lock.unlock()
        onResult(qs)
        if wait && done.wait(timeout: .now() + 30) == .timedOut { return (true, "") }
        lock.lock()
        defer { lock.unlock() }
        return outcome
    }

    private func esc(_ s: String) -> String {
        s.replacingOccurrences(of: "&", with: "&amp;").replacingOccurrences(of: "<", with: "&lt;")
            .replacingOccurrences(of: ">", with: "&gt;").replacingOccurrences(of: "\"", with: "&quot;")
    }

    private func page(_ conn: NWConnection, _ title: String, _ text: String, _ icon: String, _ extra: String = "") {
        let body = Self.pageHTML.replacingOccurrences(of: "{title}", with: title).replacingOccurrences(of: "{text}", with: text)
            .replacingOccurrences(of: "{icon}", with: icon).replacingOccurrences(of: "{script}", with: extra)
        respond(conn, 200, body, "text/html; charset=utf-8")
    }

    private func respond(_ conn: NWConnection, _ status: Int, _ body: String, _ type: String = "text/plain; charset=utf-8") {
        let bytes = Data(body.utf8)
        let head = "HTTP/1.1 \(status) \(status == 200 ? "OK" : "Not Found")\r\nContent-Type: \(type)\r\n" +
            "Content-Length: \(bytes.count)\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n"
        var out = Data(head.utf8)
        out.append(bytes)
        conn.send(content: out, completion: .contentProcessed { _ in conn.cancel() })
    }

    private static let pageHTML = """
<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{title} - Boostimise</title>
<meta name="viewport" content="width=device-width, initial-scale=1"><style>
body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;font-family:system-ui,sans-serif;
background:#fff;color:#0b1730;background-image:radial-gradient(60% 50% at 50% 0%,rgba(33,123,244,.16),transparent 70%)}
main{text-align:center;padding:24px;max-width:360px}h1{font-size:20px;font-weight:600;margin:16px 0 8px}
p{font-size:14px;color:#62748e;line-height:1.5;margin:0}.b{font-weight:700}.b span{color:#0062e9}
a.btn{display:inline-block;margin-top:24px;background:#0a6cf0;color:#fff;text-decoration:none;padding:12px 22px;border-radius:10px;font-weight:600}</style></head>
<body><main>{icon}<h1>{title}</h1><p>{text}</p>{script}<p style="margin-top:24px" class="b">Boost<span>imise</span></p></main></body></html>
"""
    private static let okIcon = ##"<svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="#00bc7d" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="m9 12 2 2 4-4"/></svg>"##
    private static let errIcon = ##"<svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="#e7000b" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 8v4"/><path d="M12 16h.01"/></svg>"##
    private static let back = #"<a class="btn" href="boostimise://signed-in">Back to Boostimise</a>"#
    // Supabase normally answers with ?code= (PKCE); tokens in the #fragment are handed over as a query.
    private static let hashScript = "<script>if(location.hash.length>1&&!location.search){location.replace(location.pathname+'?'+location.hash.slice(1))}</script>"
}

/// An error handed over from another thread.
final class ErrorBox: @unchecked Sendable {
    private let lock = NSLock()
    private var error: Error?

    func set(_ e: Error) {
        lock.lock()
        error = e
        lock.unlock()
    }

    func get() -> Error? {
        lock.lock()
        defer { lock.unlock() }
        return error
    }
}
