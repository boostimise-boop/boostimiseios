package com.covercanvasbd.boostimise.core

import com.covercanvasbd.boostimise.BuildConfig

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

    // Google sign-in comes back to a tiny web server in the app on this address, the same one the
    // Windows app uses, so it is already in Supabase's Redirect URLs (no new setting is needed).
    const val AUTH_PORT = 53214
    const val AUTH_REDIRECT = "http://127.0.0.1:$AUTH_PORT/auth/callback"

    const val SITE_URL = "https://boostimise.com"
    const val TERMS_URL = "$SITE_URL/terms"
    const val PRIVACY_URL = "$SITE_URL/privacy"

    // Sent with every Supabase request. Not "boostimise-desktop/...": the database's version lock
    // (desktop_verdict) is for the Windows app only, so the phone app is treated like the website.
    val CLIENT_INFO = "boostimise-android/$APP_VERSION"
    val USER_AGENT = "Mozilla/5.0 (Linux; Android) Boostimise-Android/$APP_VERSION"
}
