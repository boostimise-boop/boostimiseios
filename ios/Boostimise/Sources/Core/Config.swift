import Foundation

/// The online services Boostimise talks to - the same ones as the website, the Windows app and the
/// Android app. The Supabase key is the *publishable* key: public by design; Row Level Security and
/// the database functions in boostimise/supabase/schema.sql do the checks.
enum Config {
    static let appName = "Boostimise"
    static let appVersion = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "1.0"

    static let supabaseURL = "https://pfdpgttxobqcirfzgrjo.supabase.co"
    static let supabaseKey = "sb_publishable_2kuJo7V-RnyDYp6mNTDvdA_UH-R0bJT"
    static let defaultApiBase = "https://bmapi.boostimise.com"

    // The enhancement server comes from Supabase (app_settings.api_base, set with `npm run set-api` in
    // the admin folder), so a new server needs no new app. defaultApiBase until it has been read.
    private static let apiLock = NSLock()
    private static var _apiBase = defaultApiBase
    private static var apiCheckedAt: Date?
    static var apiBase: String { apiLock.lock(); defer { apiLock.unlock() }; return _apiBase }

    /// Reads app_settings.api_base when the last read is older than `maxAge` seconds; keeps the
    /// current address when Supabase can't be reached.
    @discardableResult
    static func refreshApiBase(maxAge: TimeInterval = 60) async -> String {
        apiLock.lock()
        let fresh = apiCheckedAt.map { Date().timeIntervalSince($0) < maxAge } ?? false
        apiLock.unlock()
        if fresh { return apiBase }
        guard let rows = try? await Supabase.select("app_settings", "select=api_base&id=eq.1") else { return apiBase }
        let value = rows.first?.strOrNil("api_base")?.trimmingCharacters(in: .whitespaces) ?? ""
        var base = value.isEmpty ? defaultApiBase : value
        while base.hasSuffix("/") { base.removeLast() }
        apiLock.lock(); _apiBase = base; apiCheckedAt = Date(); apiLock.unlock()
        return base
    }

    // Google sign-in comes back to a tiny web server in the app on this address, the same one the
    // Windows and Android apps use, so it is already in Supabase's Redirect URLs.
    static let authPort: UInt16 = 53214
    static let authRedirect = "http://127.0.0.1:\(authPort)/auth/callback"

    static let siteURL = "https://boostimise.com"
    static let termsURL = siteURL + "/terms"
    static let privacyURL = siteURL + "/privacy"

    // Sent with every Supabase request. The database reads the version from it and locks out a
    // version the admin panel blocked or set below the minimum (mobile_verdict in schema.sql).
    static let clientInfo = "boostimise-ios/\(appVersion)"
    static let userAgent = "Mozilla/5.0 (iPhone; CPU iPhone OS like Mac OS X) Boostimise-iOS/\(appVersion)"
}
