package com.covercanvasbd.boostimise.core

import android.net.Uri
import android.util.Base64
import okhttp3.Request
import org.json.JSONArray
import org.json.JSONObject
import java.security.MessageDigest
import java.security.SecureRandom

/** Supabase refused the saved sign-in (signed out elsewhere, user deleted, token reused). */
class AuthRejected(message: String) : Exception(message)

/**
 * A small Supabase client - core/supabase.py of the Windows app: the REST API (tables and the
 * database functions) and Auth (Google sign-in with PKCE, refreshing and ending sessions). Every
 * write goes through a Postgres function in boostimise/supabase/schema.sql that checks the caller.
 */
object Supabase {
    fun headers(token: String? = null): Map<String, String> = buildMap {
        put("apikey", Config.SUPABASE_KEY)
        put("X-Client-Info", Config.CLIENT_INFO)
        if (token != null) put("Authorization", "Bearer $token")
    }

    private fun Request.Builder.with(h: Map<String, String>) = apply { h.forEach { (k, v) -> header(k, v) } }

    /** Calls a database function (POST /rest/v1/rpc/<fn>) and returns its result. */
    suspend fun rpc(fn: String, params: JSONObject = JSONObject(), token: String? = null): Any? {
        val req = Request.Builder().url("${Config.SUPABASE_URL}/rest/v1/rpc/$fn")
            .with(headers(token)).post(Http.jsonBody(params)).build()
        return Http.parseJson(Http.send(req).second)
    }

    /** GET /rest/v1/<table>?<query> (PostgREST syntax). */
    suspend fun select(table: String, query: String, token: String? = null): List<JSONObject> {
        val req = Request.Builder().url("${Config.SUPABASE_URL}/rest/v1/$table?$query").with(headers(token)).get().build()
        return (Http.parseJson(Http.send(req).second) as? JSONArray).objects()
    }

    /** Readable message from a database function error (raise exception '...'), like the website. */
    fun dbMessage(e: Throwable, fallback: String): String {
        if (e is HttpError && e.status == 0) return "Network error. Please try again."
        val m = (e as? HttpError)?.message ?: ""
        if (m.isNotBlank() && !Regex("permission denied|function .* does not exist|schema cache|JWT|Request failed",
                RegexOption.IGNORE_CASE).containsMatchIn(m)) return m
        return fallback
    }

    data class Limits(val guest: Int?, val user: Int?, val timezone: String)

    /** The daily limits shown before signing in (app_settings is readable without an account). */
    suspend fun publicLimits(): Limits {
        val row = select("app_settings", "select=guest_daily_limit,user_daily_limit,quota_timezone&id=eq.1").firstOrNull()
        return Limits(row?.optIntOrNull("guest_daily_limit") ?: 5, row?.optIntOrNull("user_daily_limit"),
            row?.optStr("quota_timezone") ?: "UTC")
    }

    // ---- Auth ------------------------------------------------------------------------------------
    fun pkcePair(): Pair<String, String> {
        val raw = ByteArray(64).also { SecureRandom().nextBytes(it) }
        val verifier = Base64.encodeToString(raw, Base64.URL_SAFE or Base64.NO_PADDING or Base64.NO_WRAP).take(86)
        val digest = MessageDigest.getInstance("SHA-256").digest(verifier.toByteArray())
        val challenge = Base64.encodeToString(digest, Base64.URL_SAFE or Base64.NO_PADDING or Base64.NO_WRAP)
        return verifier to challenge
    }

    /** Where the browser goes to sign in with Google; Supabase sends it back to [redirectTo] with ?code=. */
    fun googleAuthorizeUrl(redirectTo: String, challenge: String): String =
        Uri.parse("${Config.SUPABASE_URL}/auth/v1/authorize").buildUpon()
            .appendQueryParameter("provider", "google")
            .appendQueryParameter("redirect_to", redirectTo)
            .appendQueryParameter("code_challenge", challenge)
            .appendQueryParameter("code_challenge_method", "s256")
            .appendQueryParameter("prompt", "select_account")
            .build().toString()

    private suspend fun token(grant: String, body: JSONObject): JSONObject {
        val req = Request.Builder().url("${Config.SUPABASE_URL}/auth/v1/token?grant_type=$grant")
            .with(headers()).post(Http.jsonBody(body)).build()
        try {
            return Http.parseJson(Http.send(req).second) as JSONObject
        } catch (e: HttpError) {
            if (e.status in setOf(400, 401, 403, 404, 422)) throw AuthRejected(e.message)
            throw e
        }
    }

    suspend fun exchangeCode(code: String, verifier: String) =
        token("pkce", jsonOf("auth_code" to code, "code_verifier" to verifier))

    suspend fun refresh(refreshToken: String) = token("refresh_token", jsonOf("refresh_token" to refreshToken))

    suspend fun user(accessToken: String): JSONObject {
        val req = Request.Builder().url("${Config.SUPABASE_URL}/auth/v1/user").with(headers(accessToken)).get().build()
        return Http.parseJson(Http.send(req).second) as JSONObject
    }

    /** Ends this session only (the website and the PC stay signed in). */
    suspend fun logout(accessToken: String) {
        val req = Request.Builder().url("${Config.SUPABASE_URL}/auth/v1/logout?scope=local")
            .with(headers(accessToken)).post(Http.jsonBody(JSONObject())).build()
        Http.send(req)
    }
}
