import CryptoKit
import Foundation
import Security

/// Supabase refused the saved sign-in (signed out elsewhere, user deleted, token reused).
struct AuthRejected: LocalizedError {
    let message: String
    init(_ message: String) { self.message = message }
    var errorDescription: String? { message }
}

/// A small Supabase client (core/supabase.py of the Windows app): the REST API (tables and the
/// database functions) and Auth (Google sign-in with PKCE, refreshing and ending sessions). Every
/// write goes through a Postgres function in boostimise/supabase/schema.sql that checks the caller.
enum Supabase {
    static func headers(_ token: String? = nil) -> [String: String] {
        var h = ["apikey": Config.supabaseKey, "X-Client-Info": Config.clientInfo]
        if let t = token { h["Authorization"] = "Bearer \(t)" }
        return h
    }

    /// Calls a database function (POST /rest/v1/rpc/<fn>) and returns its result.
    static func rpc(_ fn: String, _ params: JSON = [:], token: String? = nil) async throws -> Any? {
        let req = Http.request("\(Config.supabaseURL)/rest/v1/rpc/\(fn)", method: "POST", headers: headers(token), json: params)
        return JSONTools.parse(try await Http.send(req).1)
    }

    /// GET /rest/v1/<table>?<query> (PostgREST syntax).
    static func select(_ table: String, _ query: String, token: String? = nil) async throws -> [JSON] {
        let req = Http.request("\(Config.supabaseURL)/rest/v1/\(table)?\(query)", headers: headers(token))
        return objects(JSONTools.parse(try await Http.send(req).1))
    }

    /// Readable message from a database function error (raise exception '...'), like the website.
    static func dbMessage(_ e: Error, _ fallback: String) -> String {
        if let h = e as? HttpError, h.status == 0 { return "Network error. Please try again." }
        let m = (e as? HttpError)?.message ?? ""
        let technical = Rx(#"permission denied|function .* does not exist|schema cache|JWT|Request failed"#, ignoreCase: true)
        if !m.trimmingCharacters(in: .whitespaces).isEmpty && !technical.contains(m) { return m }
        return fallback
    }

    struct Limits {
        var guest: Int?
        var user: Int?
        var timezone: String
    }

    /// The daily limits shown before signing in (app_settings is readable without an account).
    static func publicLimits() async throws -> Limits {
        let row = try await select("app_settings", "select=guest_daily_limit,user_daily_limit,quota_timezone&id=eq.1").first
        return Limits(guest: row?.intOrNil("guest_daily_limit") ?? 5, user: row?.intOrNil("user_daily_limit"),
                      timezone: row?.strOrNil("quota_timezone") ?? "UTC")
    }

    // MARK: - Auth

    static func base64url(_ data: Data) -> String {
        data.base64EncodedString().replacingOccurrences(of: "+", with: "-").replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }

    static func pkcePair() -> (verifier: String, challenge: String) {
        var raw = [UInt8](repeating: 0, count: 64)
        _ = SecRandomCopyBytes(kSecRandomDefault, raw.count, &raw)
        let verifier = String(base64url(Data(raw)).prefix(86))
        let digest = SHA256.hash(data: Data(verifier.utf8))
        return (verifier, base64url(Data(digest)))
    }

    /// Where the browser goes to sign in with Google; Supabase sends it back to `redirectTo` with ?code=.
    static func googleAuthorizeURL(redirectTo: String, challenge: String) -> URL {
        var c = URLComponents(string: "\(Config.supabaseURL)/auth/v1/authorize")!
        c.queryItems = [
            URLQueryItem(name: "provider", value: "google"),
            URLQueryItem(name: "redirect_to", value: redirectTo),
            URLQueryItem(name: "code_challenge", value: challenge),
            URLQueryItem(name: "code_challenge_method", value: "s256"),
            URLQueryItem(name: "prompt", value: "select_account"),
        ]
        // URLComponents leaves ':' and '/' as they are; encode the redirect fully like a browser would
        c.percentEncodedQuery = c.percentEncodedQuery?.replacingOccurrences(of: ":", with: "%3A")
            .replacingOccurrences(of: "/", with: "%2F")
        return c.url!
    }

    private static func token(_ grant: String, _ body: JSON) async throws -> JSON {
        let req = Http.request("\(Config.supabaseURL)/auth/v1/token?grant_type=\(grant)", method: "POST", headers: headers(), json: body)
        do {
            return (JSONTools.parse(try await Http.send(req).1) as? JSON) ?? [:]
        } catch let e as HttpError {
            if [400, 401, 403, 404, 422].contains(e.status) { throw AuthRejected(e.message) }
            throw e
        }
    }

    static func exchangeCode(_ code: String, verifier: String) async throws -> JSON {
        try await token("pkce", jsonOf(("auth_code", code), ("code_verifier", verifier)))
    }

    static func refresh(_ refreshToken: String) async throws -> JSON {
        try await token("refresh_token", jsonOf(("refresh_token", refreshToken)))
    }

    static func user(_ accessToken: String) async throws -> JSON {
        let req = Http.request("\(Config.supabaseURL)/auth/v1/user", headers: headers(accessToken))
        return (JSONTools.parse(try await Http.send(req).1) as? JSON) ?? [:]
    }

    /// Ends this session only (the website and the PC stay signed in).
    static func logout(_ accessToken: String) async throws {
        let req = Http.request("\(Config.supabaseURL)/auth/v1/logout?scope=local", method: "POST",
                               headers: headers(accessToken), json: JSON())
        try await Http.send(req)
    }
}

/// The saved sign-in lives in the Keychain (not in plain settings).
enum Keychain {
    private static let service = "com.covercanvasbd.boostimise"

    private static func query(_ key: String) -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service, kSecAttrAccount as String: key]
    }

    static func get(_ key: String) -> String? {
        var q = query(key)
        q[kSecReturnData as String] = true
        q[kSecMatchLimit as String] = kSecMatchLimitOne
        var out: CFTypeRef?
        guard SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess, let d = out as? Data else { return nil }
        return String(data: d, encoding: .utf8)
    }

    static func set(_ key: String, _ value: String?) {
        SecItemDelete(query(key) as CFDictionary)
        guard let v = value else { return }
        var q = query(key)
        q[kSecValueData as String] = Data(v.utf8)
        q[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlock
        SecItemAdd(q as CFDictionary, nil)
    }
}
