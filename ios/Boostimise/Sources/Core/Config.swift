import Foundation

/// The online services Boostimise talks to - the same ones as the website, the Windows app and the
/// Android app. The Supabase key is the *publishable* key: public by design; Row Level Security and
/// the database functions in boostimise/supabase/schema.sql do the checks.
enum Config {
    static let appName = "Boostimise"
    static let appVersion = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "1.0"

    static let supabaseURL = "https://pfdpgttxobqcirfzgrjo.supabase.co"
    static let supabaseKey = "sb_publishable_2kuJo7V-RnyDYp6mNTDvdA_UH-R0bJT"
    static let apiBase = "https://bmapi.boostimise.com"

    // Google sign-in comes back to a tiny web server in the app on this address, the same one the
    // Windows and Android apps use, so it is already in Supabase's Redirect URLs.
    static let authPort: UInt16 = 53214
    static let authRedirect = "http://127.0.0.1:\(authPort)/auth/callback"

    static let siteURL = "https://boostimise.com"
    static let termsURL = siteURL + "/terms"
    static let privacyURL = siteURL + "/privacy"

    // Sent with every Supabase request. Not "boostimise-desktop/...": the database's version lock
    // is for the Windows app only, so the phone apps are treated like the website.
    static let clientInfo = "boostimise-ios/\(appVersion)"
    static let userAgent = "Mozilla/5.0 (iPhone; CPU iPhone OS like Mac OS X) Boostimise-iOS/\(appVersion)"
}
