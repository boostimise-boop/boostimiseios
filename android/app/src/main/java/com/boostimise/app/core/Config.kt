package com.boostimise.app.core

import com.boostimise.app.BuildConfig

/**
 * The online services Boostimise talks to - the same ones as the website and the Windows app
 * (core/config.py there). The Supabase key is the *publishable* key: public by design; Row Level
 * Security and the database functions in boostimise/supabase/schema.sql do the checks.
 */
object Config {
    const val APP_NAME = "Boostimise"
    val APP_VERSION: String = BuildConfig.VERSION_NAME

    const val SUPABASE_URL = "https://pfdpgttxobqcirfzgrjo.supabase.co"
    const val SUPABASE_KEY = "sb_publishable_2kuJo7V-RnyDYp6mNTDvdA_UH-R0bJT"
    const val API_BASE = "https://bmapi.boostimise.com"

    // The enhancement server comes from Supabase (app_settings.api_base, set with `npm run set-api` in
    // the admin folder), so a new server needs no new app. API_BASE until it has been read.
    @Volatile var apiBase: String = API_BASE
        private set
    @Volatile private var apiCheckedAt = 0L

    /** Reads app_settings.api_base when the last read is older than [maxAgeMs]; keeps the current
     *  address when Supabase can't be reached. */
    suspend fun refreshApiBase(maxAgeMs: Long = 60_000): String {
        if (apiCheckedAt != 0L && System.nanoTime() / 1_000_000 - apiCheckedAt < maxAgeMs) return apiBase
        val row = runCatching { Supabase.select("app_settings", "select=api_base&id=eq.1").firstOrNull() }
            .getOrElse { return apiBase }
        apiCheckedAt = System.nanoTime() / 1_000_000
        apiBase = (row?.optStr("api_base")?.takeIf { it.isNotBlank() } ?: API_BASE).trim().trimEnd('/')
        return apiBase
    }

    // Google sign-in comes back to a tiny web server in the app on this address, the same one the
    // Windows app uses, so it is already in Supabase's Redirect URLs (no new setting is needed).
    const val AUTH_PORT = 53214
    const val AUTH_REDIRECT = "http://127.0.0.1:$AUTH_PORT/auth/callback"

    const val SITE_URL = "https://boostimise.com"
    const val TERMS_URL = "$SITE_URL/terms"
    const val PRIVACY_URL = "$SITE_URL/privacy"

    // Sent with every Supabase request. The database reads the version from it and locks out a
    // version the admin panel blocked or set below the minimum (mobile_verdict in schema.sql).
    val CLIENT_INFO = "boostimise-android/$APP_VERSION"
    val USER_AGENT = "Mozilla/5.0 (Linux; Android) Boostimise-Android/$APP_VERSION"
}
