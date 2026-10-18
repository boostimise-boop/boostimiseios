import CryptoKit
import Foundation
import UIKit

/// May this copy of Boostimise run? (core/AppGuard.kt of the Android app, core/guard.py of the
/// Windows app.) Asked of the server on start, when the app comes back to the front and every few
/// minutes while it is open.
///
/// The admin panel's "Releases & access" page (iPhone tab) controls the answer
/// (boostimise/supabase/schema.sql, mobile_check_in / mobile_verdict): a minimum version, single
/// blocked versions, one switch that turns every copy off, and single blocked iPhones. While the
/// answer is "no" the app shows UpdateScreen instead of the tools; a newer release that isn't
/// required yet shows a banner.
///
/// Each check also records this iPhone (mobile_installs): a hash of an id kept in the Keychain
/// (the same after reinstalling the app), the model, iOS version, RAM, screen, the app version and
/// the signed-in user, so the admin panel can list who uses the app on which phones.
///
/// Offline: a "no" stays a "no". A "yes" lasts `offline_grace_hours` (72 by default) after the last
/// successful check; after that the app needs the internet again.
@MainActor @Observable
final class AppGuard {
    static let shared = AppGuard()

    enum State { case checking, ok, locked }

    private(set) var state: State = .checking
    private(set) var verdict: JSON = [:]
    private(set) var checking = false
    /// The "update available" banner was closed for this version.
    var dismissedUpdate: String?

    var reason: String { verdict.strOrNil("reason") ?? "update_required" }
    var latestVersion: String? { verdict.strOrNil("latest_version") }
    var downloadURL: URL? { verdict.strOrNil("download_url").flatMap { URL(string: $0) } }
    var updateAvailable: Bool { state == .ok && verdict.bool("update_available") && latestVersion != nil }

    @ObservationIgnored private let prefs = Storage.prefs("guard")
    @ObservationIgnored private var launchSent = false
    @ObservationIgnored private var foreground = true
    @ObservationIgnored private var lastCheck = Date.distantPast
    @ObservationIgnored private var timer: Task<Void, Never>?
    @ObservationIgnored private var soonJob: Task<Void, Never>?

    private static let platform = "ios"
    private static let defaultIntervalMin = 30
    private static let defaultGraceHours = 72.0
    private static let clockSlack: TimeInterval = 300

    private let offlineVerdict: JSON = jsonOf(
        ("allowed", false), ("reason", "offline"),
        ("message", "Boostimise needs to check for updates. Connect this iPhone to the internet and try again."))

    private init() {}

    func start() {
        if case let (v, checkedAt)? = cached() {
            if !v.bool("allowed") {
                apply(.locked, v)           // a "no" holds until the server says otherwise
            } else if fresh(v, checkedAt) {
                apply(.ok, v)               // start at once, confirm in the background
            }
        }
        check()
    }

    /// Asks the server now (no-op while a check is running).
    func check() {
        if checking { return }
        checking = true
        let launch = !launchSent
        Task {
            do {
                onAnswer(try await ask(launch), launch: launch)
            } catch {
                onFailed(error)
            }
        }
    }

    /// Checks again in a few seconds (after a live change, or once signed in).
    func soon() {
        soonJob?.cancel()
        soonJob = Task {
            try? await Task.sleep(nanoseconds: 3_000_000_000)
            if !Task.isCancelled { check() }
        }
    }

    func onForeground() {
        foreground = true
        if Date().timeIntervalSince(lastCheck) > 60 { check() }
    }

    func onBackground() {
        foreground = false
    }

    /// get_my_status answers 'mobile' when this version may not run: lock at once.
    func lockFromStatus(_ lock: JSON) {
        if state == .locked || lock.strOrNil("reason") == nil { return }
        var merged = verdict
        for (k, v) in lock { merged[k] = v }
        merged["allowed"] = false
        apply(.locked, merged)
        soon()  // and fetch the full answer (download link, notes)
    }

    // MARK: - the server

    private func ask(_ launch: Bool) async throws -> JSON {
        let params = jsonOf(("p_platform", Self.platform), ("p_device_id", Self.deviceId()), ("p_version", Config.appVersion),
                            ("p_info", deviceInfo()), ("p_launch", launch))
        var token: String?
        if Account.shared.user != nil { token = try? await Account.shared.token() }
        do {
            return try await Supabase.rpc("mobile_check_in", params, token: token) as? JSON ?? [:]
        } catch let e as HttpError where token != nil && [401, 403].contains(e.status) {
            return try await Supabase.rpc("mobile_check_in", params, token: nil) as? JSON ?? [:]
        }
    }

    private func onAnswer(_ v: JSON, launch: Bool) {
        checking = false
        lastCheck = Date()
        if launch { launchSent = true }
        guard v["allowed"] != nil else {
            onFailed(HttpError(status: 0, code: "", message: "unexpected answer"))
            return
        }
        prefs.set("cache", JSONTools.string(jsonOf(("verdict", v), ("checked_at", Date().timeIntervalSince1970),
                                                   ("version", Config.appVersion))))
        apply(v.bool("allowed") ? .ok : .locked, v)
        schedule(v.intOrNil("check_interval_minutes") ?? Self.defaultIntervalMin)
    }

    private func onFailed(_ error: Error) {
        checking = false
        if let e = error as? HttpError, e.status == 404, e.code == "PGRST202" || e.message.contains("mobile_check_in") {
            // the database doesn't have the phone tables yet (old schema.sql): don't lock anyone out
            print("mobile_check_in missing - install the latest supabase/schema.sql")
            apply(.ok, jsonOf(("allowed", true), ("reason", "unconfigured")))
            schedule(Self.defaultIntervalMin)
            return
        }
        print("version check failed: \(error)")
        if case let (v, checkedAt)? = cached() {
            if !v.bool("allowed") {
                apply(.locked, v)
            } else if fresh(v, checkedAt) {
                apply(.ok, v)
            } else {
                apply(.locked, offlineVerdict)
            }
        } else {
            apply(.locked, offlineVerdict)
        }
        timer?.cancel()
        timer = Task {
            try? await Task.sleep(nanoseconds: 60_000_000_000)  // offline: try again in a minute
            if !Task.isCancelled { check() }
        }
    }

    private func schedule(_ minutes: Int) {
        timer?.cancel()
        timer = Task {
            try? await Task.sleep(nanoseconds: UInt64(max(5, minutes)) * 60_000_000_000)
            if !Task.isCancelled && foreground { check() }  // in the background the next onForeground checks
        }
    }

    private func apply(_ s: State, _ v: JSON) {
        state = s
        verdict = v
    }

    private func cached() -> (JSON, TimeInterval)? {
        guard let c = JSONTools.parse(prefs.stringOrNil("cache")) as? JSON, c.str("version") == Config.appVersion,
              let v = c.obj("verdict") else { return nil }
        return (v, c.double("checked_at"))
    }

    private func fresh(_ v: JSON, _ checkedAt: TimeInterval) -> Bool {
        let grace = v.doubleOrNil("offline_grace_hours") ?? Self.defaultGraceHours
        let age = Date().timeIntervalSince1970 - checkedAt
        return age >= -Self.clockSlack && age <= grace * 3600
    }

    // MARK: - this iPhone

    /// sha256 of a random id kept in the Keychain, which survives deleting and reinstalling the app.
    private static func deviceId() -> String {
        var raw = Keychain.get("device_id")
        if raw == nil || raw!.isEmpty {
            raw = UUID().uuidString.lowercased()
            Keychain.set("device_id", raw)
        }
        let digest = SHA256.hash(data: Data("boostimise-ios:\(raw!.lowercased())".utf8))
        return digest.map { String(format: "%02x", $0) }.joined()
    }

    private static func sysctlString(_ name: String) -> String? {
        var size = 0
        guard sysctlbyname(name, nil, &size, nil, 0) == 0, size > 0 else { return nil }
        var buf = [CChar](repeating: 0, count: size)
        guard sysctlbyname(name, &buf, &size, nil, 0) == 0 else { return nil }
        return String(cString: buf)
    }

    private func deviceInfo() -> JSON {
        let device = UIDevice.current
        let model = Self.sysctlString("hw.machine") ?? device.model           // "iPhone14,5"
        let name = device.name                                                 // "iPhone" unless the app may read the name
        let screen = UIApplication.shared.connectedScenes.compactMap { ($0 as? UIWindowScene)?.screen }.first
        let size = screen.map { s in "\(Int(min(s.nativeBounds.width, s.nativeBounds.height)))x\(Int(max(s.nativeBounds.width, s.nativeBounds.height))) @\(Int(s.nativeScale))x" }
        let receipt = Bundle.main.appStoreReceiptURL
        let source: String
        if receipt?.lastPathComponent == "sandboxReceipt" {
            source = "testflight"
        } else if let r = receipt, FileManager.default.fileExists(atPath: r.path) {
            source = "app_store"
        } else {
            source = "sideload"
        }
        let build = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String
        return jsonOf(
            ("app_build", build),
            ("install_source", source),
            ("manufacturer", "Apple"),
            ("model", model),
            ("device_name", name == device.model ? nil : name),
            ("os_version", "\(device.systemName) \(device.systemVersion)"),
            ("os_build", Self.sysctlString("kern.osversion")),
            ("arch", "arm64"),
            ("cpu", nil),
            ("cpu_cores", ProcessInfo.processInfo.processorCount),
            ("ram_mb", Int(ProcessInfo.processInfo.physicalMemory / (1024 * 1024))),
            ("screen", size),
            ("locale", Locale.current.identifier),
            ("timezone", TimeZone.current.identifier)
        )
    }
}
