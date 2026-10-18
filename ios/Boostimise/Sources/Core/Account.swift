import AuthenticationServices
import Foundation
import Observation
import UIKit

struct Quota {
    var kind: String
    var limit: Int?
    var used: Int
    var remaining: Int?
    var resetsAt: String
}

func toQuota(_ q: JSON?) -> Quota? {
    guard let q, !q.str("kind").isEmpty else { return nil }
    return Quota(kind: q.str("kind"), limit: q.intOrNil("limit"), used: q.int("used"), remaining: q.intOrNil("remaining"),
                 resetsAt: q.str("resets_at"))
}

struct Profile {
    var fullName: String?
    var avatar: String?
    var phone: String?
    var phoneCountry: String?
    var blocked: Bool
    var onboarded: Bool
}

struct Plan {
    var id: String
    var name: String
    var expiresAt: String?
}

struct Status {
    var quota: Quota?
    var limits: Supabase.Limits?
    var plan: Plan?
    var profile: Profile?
    var country: String?
    var raw: JSON
}

func toStatus(_ data: JSON?) -> Status {
    let d = data ?? [:]
    let profile = d.obj("profile").map {
        Profile(fullName: $0.strOrNil("full_name"), avatar: $0.strOrNil("avatar_url"), phone: $0.strOrNil("phone"),
                phoneCountry: $0.strOrNil("phone_country"), blocked: $0.bool("blocked"), onboarded: $0.bool("onboarded"))
    }
    let plan = d.obj("plan").map { Plan(id: $0.str("id"), name: $0.str("name"), expiresAt: $0.strOrNil("expires_at")) }
    let limits = d.obj("limits").map {
        Supabase.Limits(guest: $0.intOrNil("guest"), user: $0.intOrNil("user"), timezone: $0.strOrNil("timezone") ?? "UTC")
    }
    return Status(quota: toQuota(d.obj("quota")), limits: limits, plan: plan, profile: profile, country: d.strOrNil("country"), raw: d)
}

struct User {
    var id: String
    var email: String?
    var name: String?
    var avatar: String?
}

func userFrom(_ u: JSON?) -> User? {
    guard let u, u.has("id") else { return nil }
    let meta = u.obj("user_metadata") ?? [:]
    return User(id: u.str("id"), email: u.strOrNil("email"), name: meta.strOrNil("full_name") ?? meta.strOrNil("name"),
                avatar: meta.strOrNil("avatar_url") ?? meta.strOrNil("picture"))
}

func parseISODate(_ iso: String?) -> Date? {
    guard let s = iso, !s.isEmpty else { return nil }
    let f = ISO8601DateFormatter()
    f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    if let d = f.date(from: s) { return d }
    f.formatOptions = [.withInternetDateTime]
    if let d = f.date(from: s) { return d }
    // Postgres "2026-10-17 12:00:00+00" / "...+00:00"
    let g = DateFormatter()
    g.locale = Locale(identifier: "en_US_POSIX")
    for fmt in ["yyyy-MM-dd HH:mm:ss.SSSSSSXXXXX", "yyyy-MM-dd HH:mm:ssXXXXX", "yyyy-MM-dd'T'HH:mm:ss.SSSSSSXXXXX", "yyyy-MM-dd HH:mm:ssX"] {
        g.dateFormat = fmt
        if let d = g.date(from: s) { return d }
    }
    return nil
}

func formatResetIn(_ iso: String) -> String {
    guard let d = parseISODate(iso) else { return "soon" }
    let ms = Int(d.timeIntervalSinceNow * 1000)
    if ms <= 0 { return "soon" }
    let h = ms / 3_600_000
    let m = ms % 3_600_000 / 60_000
    return h > 0 ? "\(h)h \(m)m" : "\(m)m"
}

/// The signed-in account (core/account.py): Google sign-in through Supabase Auth, the session, the
/// account status (quota, plan, profile) and live updates. When Supabase can't be reached the last
/// known account is used, so a network hiccup never locks the tools; a session Supabase rejects
/// means signing in again.
@MainActor @Observable
final class Account {
    static let shared = Account()

    enum State { case loading, signedOut, onboarding, blocked, ready }

    private(set) var state: State = .loading
    private(set) var user: User?
    private(set) var status: Status?
    private(set) var offline = false
    private(set) var configured = true
    private(set) var publicLimits = Supabase.Limits(guest: 5, user: nil, timezone: "UTC")
    private(set) var liveTick = 0
    private(set) var realtimeState = "closed"
    private(set) var signInState = "idle"   // idle | waiting | working | error
    private(set) var signInMessage = ""
    private(set) var signedOutReason = ""

    @ObservationIgnored private let prefs = Storage.prefs("account")
    @ObservationIgnored private var session: JSON?
    @ObservationIgnored private var refreshing: Task<Void, Error>?
    @ObservationIgnored private var server: AuthCallbackServer?
    @ObservationIgnored private var verifier: String?
    @ObservationIgnored private var channel: RealtimeChannel?
    @ObservationIgnored private var refreshJob: Task<Void, Never>?
    @ObservationIgnored private var retryJob: Task<Void, Never>?
    @ObservationIgnored private var reloadJob: Task<Void, Never>?
    @ObservationIgnored private var signInTimeout: Task<Void, Never>?
    @ObservationIgnored private var authSession: ASWebAuthenticationSession?
    let guestId: String
    @ObservationIgnored private let presenter = AuthPresenter()

    var quota: Quota? { status?.quota }
    var limits: Supabase.Limits { status?.limits ?? publicLimits }
    var profile: Profile? { status?.profile }
    var plan: Plan? { status?.plan }

    func displayName() -> String? { profile?.fullName ?? user?.name }
    func displayAvatar() -> String? { profile?.avatar ?? user?.avatar }
    func firstName() -> String { (displayName() ?? "").split(separator: " ").first.map(String.init) ?? "" }

    private init() {
        let saved = UserDefaults.standard.string(forKey: "account.guest_id")
        if let s = saved, UUID(uuidString: s) != nil {
            guestId = s
        } else {
            guestId = UUID().uuidString.lowercased()
            UserDefaults.standard.set(guestId, forKey: "account.guest_id")
        }
    }

    // MARK: - startup

    func start() {
        Task { if let l = try? await Supabase.publicLimits() { publicLimits = l } }
        guard let saved = loadSession() else {
            state = .signedOut
            return
        }
        session = saved
        user = userFrom(saved.obj("user"))
        state = .loading
        Task {
            do {
                try await refreshSession(force: true)
                let st = try await fetchStatus()
                offline = false
                applyStatus(st)
                afterSignIn()
            } catch is AuthRejected {
                endSession("Your session has ended. Please sign in again.")
            } catch {
                print("Account check failed, working offline: \(error)")
                offline = true
                applyStatus(session?.obj("status").map { toStatus($0) })
                afterSignIn(startRealtime: false)
                scheduleRetry()
            }
        }
    }

    private func loadSession() -> JSON? {
        guard let raw = Keychain.get("session"), let s = JSONTools.parse(raw) as? JSON, s.strOrNil("refresh_token") != nil else { return nil }
        return s
    }

    private func saveSession() {
        Keychain.set("session", session.map { JSONTools.string($0) })
    }

    // MARK: - sign in

    /// Starts the callback server and opens Google sign-in in an in-app browser sheet.
    func signIn() {
        cancelSignIn(nil)
        let pair = Supabase.pkcePair()
        let srv: AuthCallbackServer
        do {
            srv = try AuthCallbackServer { [weak self] qs in
                Task { @MainActor in await self?.onCallback(qs) }
            }
            try srv.start()
        } catch {
            setSignIn("error", "Sign-in could not start (port \(Config.authPort) is busy). Close other apps and try again.")
            return
        }
        server = srv
        verifier = pair.verifier
        signInTimeout = Task {
            try? await Task.sleep(nanoseconds: 10 * 60 * 1_000_000_000)
            if !Task.isCancelled { cancelSignIn("Sign-in timed out. Please try again.") }
        }
        setSignIn("waiting", "")
        let url = Supabase.googleAuthorizeURL(redirectTo: Config.authRedirect, challenge: pair.challenge)
        // The sheet loads Google, then http://127.0.0.1:53214/auth/callback (the server above), whose
        // page goes on to boostimise://signed-in, which closes the sheet.
        let s = ASWebAuthenticationSession(url: url, callbackURLScheme: "boostimise") { [weak self] _, error in
            Task { @MainActor in
                guard let self else { return }
                self.authSession = nil
                if let e = error as? ASWebAuthenticationSessionError, e.code == .canceledLogin, self.signInState == "waiting" {
                    self.cancelSignIn("")
                }
            }
        }
        s.presentationContextProvider = presenter
        s.prefersEphemeralWebBrowserSession = false
        authSession = s
        if !s.start() {
            cancelSignIn("Could not open the sign-in page. Please try again.")
        }
    }

    func cancelSignIn(_ message: String?) {
        signInTimeout?.cancel()
        server?.stop()
        server = nil
        verifier = nil
        if let m = message {
            authSession?.cancel()
            authSession = nil
            setSignIn(m.isEmpty ? "idle" : "error", m)
        }
    }

    private func setSignIn(_ s: String, _ m: String) {
        signInState = s
        signInMessage = m
    }

    private func onCallback(_ qs: [String: String]) async {
        let v = verifier
        let srv = server
        server = nil
        signInTimeout?.cancel()
        verifier = nil
        if let error = qs["error_description"] ?? qs["error"] {
            srv?.stop()
            setSignIn("error", error)
            return
        }
        setSignIn("working", "")
        defer {
            Task {
                try? await Task.sleep(nanoseconds: 3_000_000_000)
                srv?.stop()
            }
        }
        do {
            let s: JSON
            if let rt = qs["refresh_token"], !rt.isEmpty {
                let tok = qs["access_token"] ?? ""
                let u = try await Supabase.user(tok)
                s = jsonOf(("access_token", tok), ("refresh_token", rt), ("expires_in", Int(qs["expires_in"] ?? "") ?? 3600),
                           ("user", u))
            } else {
                s = try await Supabase.exchangeCode(qs["code"] ?? "", verifier: v ?? "")
            }
            storeSession(s)
            let st = try await fetchStatus()
            srv?.finish(true)
            offline = false
            setSignIn("idle", "")
            applyStatus(st)
            afterSignIn()
        } catch {
            print("Sign-in failed: \(error)")
            let msg: String
            if let h = error as? HttpError {
                msg = h.status != 0 ? h.message : "Could not reach Boostimise. Check your internet connection and try again."
            } else {
                msg = "Could not sign you in (\(error.localizedDescription)). Please try again."
            }
            session = nil
            saveSession()
            srv?.finish(false, msg)
            setSignIn("error", msg)
        }
    }

    private func afterSignIn(startRealtime: Bool = true) {
        AppGuard.shared.soon()  // records the signed-in user on this iPhone
        scheduleRefresh()
        if startRealtime { self.startRealtime() }
    }

    // MARK: - sign out

    func signOut() {
        if let token = session?.strOrNil("access_token") {
            Task { try? await Supabase.logout(token) }
        }
        endSession("")
    }

    private func endSession(_ reason: String) {
        stopRealtime()
        refreshJob?.cancel()
        retryJob?.cancel()
        session = nil
        saveSession()
        user = nil
        status = nil
        offline = false
        signedOutReason = reason
        state = .signedOut
        Task { if let l = try? await Supabase.publicLimits() { publicLimits = l } }
    }

    // MARK: - session

    private func storeSession(_ s: JSON) {
        let expiresIn = s.int("expires_in", 3600)
        let u = s.obj("user") ?? session?.obj("user")
        session = jsonOf(("access_token", s.str("access_token")), ("refresh_token", s.str("refresh_token")),
                         ("expires_at", Date().timeIntervalSince1970 + Double(expiresIn)), ("user", u),
                         ("status", session?.obj("status")))
        user = userFrom(u)
        saveSession()
    }

    private func refreshSession(force: Bool = false) async throws {
        if let running = refreshing {
            try await running.value
            return
        }
        guard let s = session else { throw AuthRejected("Not signed in") }
        if !force && s.double("expires_at") - Date().timeIntervalSince1970 > 60 { return }
        let t = Task { @MainActor in
            let fresh = try await Supabase.refresh(s.str("refresh_token"))
            self.storeSession(fresh)
        }
        refreshing = t
        defer { refreshing = nil }
        try await t.value
        scheduleRefresh()
        channel?.setToken(session?.strOrNil("access_token"))
    }

    /// A valid access token (refreshed first when needed).
    func token() async throws -> String {
        try await refreshSession()
        guard let t = session?.strOrNil("access_token") else { throw AuthRejected("Not signed in") }
        return t
    }

    private func scheduleRefresh() {
        guard let s = session else { return }
        let delaySec = max(30.0, s.double("expires_at") - Date().timeIntervalSince1970 - 300)
        refreshJob?.cancel()
        refreshJob = Task {
            try? await Task.sleep(nanoseconds: UInt64(min(delaySec, 3600) * 1_000_000_000))
            if Task.isCancelled { return }
            do {
                try await refreshSession(force: true)
            } catch is AuthRejected {
                endSession("Your session has ended. Please sign in again.")
            } catch {
                try? await Task.sleep(nanoseconds: 30_000_000_000)
                if !Task.isCancelled { scheduleRefresh() }
            }
        }
    }

    private func scheduleRetry() {
        retryJob?.cancel()
        retryJob = Task {
            try? await Task.sleep(nanoseconds: 30_000_000_000)
            if !Task.isCancelled { refresh() }
        }
    }

    // MARK: - status

    private func fetchStatus() async throws -> Status {
        let params = jsonOf(("p_guest_id", guestId))
        let data: Any?
        do {
            let t = try await token()
            data = try await Supabase.rpc("get_my_status", params, token: t)
        } catch let e as HttpError {
            if [401, 403].contains(e.status) && e.message.contains("JWT") {
                try await refreshSession(force: true)
                let t = try await token()
                data = try await Supabase.rpc("get_my_status", params, token: t)
            } else if e.status != 0 && ![401, 403].contains(e.status) {
                print("account status unavailable: \(e.message)")
                var st = toStatus(nil)
                st.raw = jsonOf(("_unconfigured", true))
                return st
            } else {
                throw e
            }
        }
        return toStatus(data as? JSON)
    }

    /// Reloads the status (quota, plan, profile) - also after Realtime changes.
    func refresh() {
        if session == nil { return }
        Task {
            do {
                let st = try await fetchStatus()
                if offline {
                    offline = false
                    startRealtime()
                }
                applyStatus(st)
            } catch is AuthRejected {
                endSession("Your session has ended. Please sign in again.")
            } catch let e as HttpError {
                if e.status == 0 {
                    offline = true
                    scheduleRetry()
                }
            } catch {
                print("status refresh failed: \(error)")
            }
        }
    }

    private func applyStatus(_ st: Status?) {
        if session == nil { return }
        configured = st != nil && !(st!.raw.bool("_unconfigured"))
        status = configured ? st : nil
        if configured, let st {
            session?["status"] = st.raw
            saveSession()
        }
        // a version that may not run comes as 'mobile' (and as blocked, for older copies): the
        // update screen shows it, not "Account suspended"
        let lock = status?.raw.obj("mobile")
        if let lock { AppGuard.shared.lockFromStatus(lock) }
        if let p = profile, p.blocked, lock == nil {
            state = .blocked
        } else if let p = profile, !p.onboarded {
            state = .onboarding
        } else {
            state = .ready
        }
    }

    func setQuota(_ q: Quota?) {
        if let q, status != nil { status?.quota = q }
    }

    // MARK: - realtime

    private func startRealtime() {
        stopRealtime()
        guard let uid = user?.id, configured else { return }
        let changes: [JSON] = [
            jsonOf(("event", "*"), ("schema", "public"), ("table", "app_settings")),
            jsonOf(("event", "*"), ("schema", "public"), ("table", "plans")),
            jsonOf(("event", "*"), ("schema", "public"), ("table", "profiles"), ("filter", "id=eq.\(uid)")),
            jsonOf(("event", "*"), ("schema", "public"), ("table", "subscriptions"), ("filter", "user_id=eq.\(uid)")),
            jsonOf(("event", "*"), ("schema", "public"), ("table", "enhancements"), ("filter", "user_id=eq.\(uid)")),
        ]
        let ch = RealtimeChannel(name: "account:\(uid)", changes: changes, onChange: { [weak self] _ in
            guard let self else { return }
            self.liveTick += 1
            AppGuard.shared.soon()  // releases / access changed in the admin panel ping app_settings
            self.reloadJob?.cancel()
            self.reloadJob = Task {
                try? await Task.sleep(nanoseconds: 400_000_000)
                if !Task.isCancelled { self.refresh() }
            }
        }, onState: { [weak self] s in self?.realtimeState = s })
        channel = ch
        ch.start(token: session?.strOrNil("access_token"))
    }

    private func stopRealtime() {
        channel?.stop()
        channel = nil
    }

    // MARK: - database functions

    func rpc(_ fn: String, _ params: JSON = [:]) async throws -> Any? {
        let t = try await token()
        return try await Supabase.rpc(fn, params, token: t)
    }

    func select(_ table: String, _ query: String) async throws -> [JSON] {
        let t = try await token()
        return try await Supabase.select(table, query, token: t)
    }

    struct Reservation {
        var ok: Bool
        var id: String?
        var quota: Quota?
        var code: String = ""
        var message: String = ""
    }

    /// Take one enhancement from today's quota. Network / database problems never block the user;
    /// only an explicit "no" from the database does.
    func reserveEnhancement(fileName: String, fileSize: Int, mode: String, scale: Int, width: Int?, height: Int?) async -> Reservation {
        let data: JSON
        do {
            data = (try await rpc("reserve_my_enhancement", jsonOf(
                ("p_guest_id", guestId), ("p_file_name", fileName.take(255)), ("p_file_size", fileSize),
                ("p_mode", mode), ("p_scale", scale), ("p_input_width", width), ("p_input_height", height),
                ("p_device_type", "mobile"))) as? JSON) ?? [:]
        } catch {
            print("quota check unavailable: \(error)")
            return Reservation(ok: true, id: nil, quota: nil)
        }
        if data.bool("allowed") { return Reservation(ok: true, id: data.strOrNil("id"), quota: toQuota(data)) }
        let reason = data.strOrNil("reason") ?? ""
        let code = ["blocked", "onboarding", "update_required"].contains(reason) ? reason : "limit"
        let message: String
        switch code {
        case "update_required": message = data.strOrNil("message") ?? "Please update Boostimise."
        case "blocked": message = "Your account has been suspended."
        case "onboarding": message = "Add your mobile number to continue."
        default:
            message = data.str("kind") == "user" ? "You've reached today's limit for your plan."
                : "You've used all \(data.str("limit")) free enhancements for today."
        }
        return Reservation(ok: false, id: nil, quota: toQuota(data), code: code, message: message)
    }

    func finishEnhancement(_ id: String, status: String? = nil, jobId: String? = nil, outW: Int? = nil, outH: Int? = nil,
                           seconds: Double? = nil, error: String? = nil) async {
        do {
            _ = try await rpc("finish_my_enhancement", jsonOf(("p_id", id), ("p_guest_id", guestId), ("p_status", status),
                ("p_job_id", jobId), ("p_output_width", outW), ("p_output_height", outH), ("p_seconds", seconds),
                ("p_error", error)))
        } catch {
            print("could not record enhancement result: \(error)")
        }
    }
}

/// The window the sign-in sheet shows over.
final class AuthPresenter: NSObject, ASWebAuthenticationPresentationContextProviding {
    func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        MainActor.assumeIsolated {
            let windows = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }.flatMap { $0.windows }
            return windows.first { $0.isKeyWindow } ?? windows.first ?? ASPresentationAnchor()
        }
    }
}
