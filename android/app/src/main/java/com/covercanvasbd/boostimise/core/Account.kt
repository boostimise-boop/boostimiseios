package com.covercanvasbd.boostimise.core

import android.content.Context
import android.content.SharedPreferences
import android.util.Log
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import org.json.JSONObject
import java.time.Instant
import java.util.UUID

data class Quota(val kind: String, val limit: Int?, val used: Int, val remaining: Int?, val resetsAt: String)

fun toQuota(q: JSONObject?): Quota? {
    if (q == null || q.optStr("kind").isNullOrEmpty()) return null
    return Quota(q.optString("kind"), q.optIntOrNull("limit"), q.optInt("used"), q.optIntOrNull("remaining"),
        q.optStr("resets_at").orEmpty())
}

data class Profile(val fullName: String?, val avatar: String?, val phone: String?, val phoneCountry: String?,
                   val blocked: Boolean, val onboarded: Boolean)

data class Plan(val id: String, val name: String, val expiresAt: String?)

data class Status(val quota: Quota?, val limits: Supabase.Limits?, val plan: Plan?, val profile: Profile?,
                  val country: String?, val raw: JSONObject)

fun toStatus(data: JSONObject?): Status {
    val d = data ?: JSONObject()
    val p = d.optJSONObject("profile")
    val profile = p?.let {
        Profile(it.optStr("full_name"), it.optStr("avatar_url"), it.optStr("phone"), it.optStr("phone_country"),
            it.optBoolean("blocked"), it.optBoolean("onboarded"))
    }
    val plan = d.optJSONObject("plan")?.let { Plan(it.optString("id"), it.optString("name"), it.optStr("expires_at")) }
    val limits = d.optJSONObject("limits")?.let {
        Supabase.Limits(it.optIntOrNull("guest"), it.optIntOrNull("user"), it.optStr("timezone") ?: "UTC")
    }
    return Status(toQuota(d.optJSONObject("quota")), limits, plan, profile, d.optStr("country"), d)
}

data class User(val id: String, val email: String?, val name: String?, val avatar: String?)

fun userFrom(u: JSONObject?): User? {
    if (u == null || !u.has("id")) return null
    val meta = u.optJSONObject("user_metadata") ?: JSONObject()
    return User(u.optString("id"), u.optStr("email"), meta.optStr("full_name") ?: meta.optStr("name"),
        meta.optStr("avatar_url") ?: meta.optStr("picture"))
}

fun formatResetIn(iso: String): String {
    val ms = try {
        Instant.parse(iso).toEpochMilli() - System.currentTimeMillis()
    } catch (e: Exception) {
        try {
            java.time.OffsetDateTime.parse(iso).toInstant().toEpochMilli() - System.currentTimeMillis()
        } catch (e2: Exception) {
            return "soon"
        }
    }
    if (ms <= 0) return "soon"
    val h = ms / 3_600_000
    val m = ms % 3_600_000 / 60_000
    return if (h > 0) "${h}h ${m}m" else "${m}m"
}

/**
 * The signed-in account (core/account.py): Google sign-in through Supabase Auth, the session, the
 * account status (quota, plan, profile) and live updates. Boostimise is used signed in; when
 * Supabase can't be reached the last known account is used, so a network hiccup never locks the
 * tools; a session Supabase rejects means signing in again.
 */
object Account {
    enum class State { LOADING, SIGNED_OUT, ONBOARDING, BLOCKED, READY }

    var state by mutableStateOf(State.LOADING)
        private set
    var user by mutableStateOf<User?>(null)
        private set
    var status by mutableStateOf<Status?>(null)
        private set
    var offline by mutableStateOf(false)
        private set
    var configured by mutableStateOf(true)
        private set
    var publicLimits by mutableStateOf(Supabase.Limits(5, null, "UTC"))
        private set
    var liveTick by mutableIntStateOf(0)
        private set
    var realtimeState by mutableStateOf("closed")
        private set
    var signInState by mutableStateOf("idle")   // idle | waiting | working | error
        private set
    var signInMessage by mutableStateOf("")
        private set
    var signedOutReason by mutableStateOf("")
        private set

    private lateinit var prefs: SharedPreferences
    private lateinit var scope: CoroutineScope
    private var session: JSONObject? = null
    private val lock = Mutex()
    private var server: AuthCallbackServer? = null
    private var verifier: String? = null
    private var channel: RealtimeChannel? = null
    private var refreshJob: Job? = null
    private var retryJob: Job? = null
    private var reloadJob: Job? = null
    private var signInTimeout: Job? = null
    lateinit var guestId: String
        private set

    val quota get() = status?.quota
    val limits get() = status?.limits ?: publicLimits
    val profile get() = status?.profile
    val plan get() = status?.plan

    fun displayName(): String? = profile?.fullName ?: user?.name
    fun displayAvatar(): String? = profile?.avatar ?: user?.avatar
    fun firstName(): String = (displayName() ?: "").split(" ").first()

    fun init(context: Context, scope: CoroutineScope) {
        prefs = context.getSharedPreferences("account", Context.MODE_PRIVATE)
        this.scope = scope
        guestId = prefs.getString("guest_id", null)?.takeIf { runCatching { UUID.fromString(it) }.isSuccess }
            ?: UUID.randomUUID().toString().also { prefs.edit().putString("guest_id", it).apply() }
    }

    // ---- startup -------------------------------------------------------------------------------
    fun start() {
        scope.launch { runCatching { publicLimits = Supabase.publicLimits() } }
        val saved = loadSession()
        if (saved == null) {
            state = State.SIGNED_OUT
            return
        }
        session = saved
        user = userFrom(saved.optJSONObject("user"))
        state = State.LOADING
        scope.launch {
            try {
                refreshSession(force = true)
                val st = fetchStatus()
                offline = false
                applyStatus(st)
                afterSignIn()
            } catch (e: AuthRejected) {
                endSession("Your session has ended. Please sign in again.")
            } catch (e: Exception) {
                Log.w(TAG, "Account check failed, working offline: $e")
                offline = true
                val cached = session?.optJSONObject("status")
                applyStatus(cached?.let { toStatus(it) })
                afterSignIn(startRealtime = false)
                scheduleRetry()
            }
        }
    }

    private fun loadSession(): JSONObject? {
        val raw = prefs.getString("session", null) ?: return null
        return (Http.parseJson(raw) as? JSONObject)?.takeIf { it.optStr("refresh_token") != null }
    }

    private fun saveSession() {
        prefs.edit().apply { session?.let { putString("session", it.toString()) } ?: remove("session") }.apply()
    }

    // ---- sign in -------------------------------------------------------------------------------
    /** Starts the callback server; returns the Google sign-in URL for the browser (null on error). */
    fun beginSignIn(): String? {
        cancelSignIn(null)
        val (v, challenge) = Supabase.pkcePair()
        val srv = try {
            AuthCallbackServer { qs -> scope.launch { onCallback(qs) } }
        } catch (e: Exception) {
            setSignIn("error", "Another app is using port ${Config.AUTH_PORT}, which sign-in needs. Restart the phone and try again.")
            return null
        }
        srv.start()
        server = srv
        verifier = v
        signInTimeout = scope.launch {
            delay(10 * 60 * 1000L)
            cancelSignIn("Sign-in timed out. Please try again.")
        }
        setSignIn("waiting", "")
        return Supabase.googleAuthorizeUrl(Config.AUTH_REDIRECT, challenge)
    }

    fun cancelSignIn(message: String?) {
        signInTimeout?.cancel()
        server?.stop()
        server = null
        verifier = null
        if (message != null) setSignIn(if (message.isEmpty()) "idle" else "error", message)
    }

    private fun setSignIn(s: String, m: String) {
        signInState = s
        signInMessage = m
    }

    private suspend fun onCallback(qs: Map<String, String>) {
        val v = verifier
        val srv = server
        server = null
        signInTimeout?.cancel()
        verifier = null
        val error = qs["error_description"] ?: qs["error"]
        if (error != null) {
            srv?.stop()
            setSignIn("error", error)
            return
        }
        setSignIn("working", "")
        try {
            val s = if (!qs["refresh_token"].isNullOrEmpty()) {
                val tok = qs["access_token"].orEmpty()
                jsonOf("access_token" to tok, "refresh_token" to qs["refresh_token"],
                    "expires_in" to (qs["expires_in"]?.toIntOrNull() ?: 3600), "user" to Supabase.user(tok))
            } else Supabase.exchangeCode(qs["code"].orEmpty(), v.orEmpty())
            lock.withLock { storeSession(s) }
            val st = fetchStatus()
            srv?.finish(true)
            offline = false
            setSignIn("idle", "")
            applyStatus(st)
            afterSignIn()
        } catch (e: Exception) {
            Log.w(TAG, "Sign-in failed: $e")
            val msg = if (e is HttpError && e.status != 0) e.message
            else if (e is HttpError) "Could not reach Boostimise. Check your internet connection and try again."
            else "Could not sign you in (${e.message}). Please try again."
            session = null
            saveSession()
            srv?.finish(false, msg)
            setSignIn("error", msg)
        } finally {
            scope.launch {
                delay(3000)
                srv?.stop()
            }
        }
    }

    private fun afterSignIn(startRealtime: Boolean = true) {
        AppGuard.soon()  // records the signed-in user on this phone
        scheduleRefresh()
        if (startRealtime) startRealtime()
    }

    // ---- sign out ------------------------------------------------------------------------------
    fun signOut() {
        val token = session?.optStr("access_token")
        if (token != null) scope.launch { runCatching { Supabase.logout(token) } }
        endSession("")
    }

    private fun endSession(reason: String) {
        stopRealtime()
        refreshJob?.cancel()
        retryJob?.cancel()
        session = null
        saveSession()
        user = null
        status = null
        offline = false
        signedOutReason = reason
        state = State.SIGNED_OUT
        scope.launch { runCatching { publicLimits = Supabase.publicLimits() } }
    }

    // ---- session -------------------------------------------------------------------------------
    private fun storeSession(s: JSONObject) {
        val expiresIn = s.optInt("expires_in", 3600)
        val u = s.optJSONObject("user") ?: session?.optJSONObject("user")
        session = jsonOf(
            "access_token" to s.optString("access_token"), "refresh_token" to s.optString("refresh_token"),
            "expires_at" to System.currentTimeMillis() / 1000.0 + expiresIn, "user" to u,
            "status" to session?.optJSONObject("status"),
        )
        user = userFrom(u)
        saveSession()
    }

    private suspend fun refreshSession(force: Boolean = false) {
        lock.withLock {
            val s = session ?: throw AuthRejected("Not signed in")
            if (!force && s.optDouble("expires_at", 0.0) - System.currentTimeMillis() / 1000.0 > 60) return
            storeSession(Supabase.refresh(s.optString("refresh_token")))
        }
        scheduleRefresh()
        channel?.setToken(session?.optStr("access_token"))
    }

    /** A valid access token (refreshed first when needed). */
    suspend fun token(): String {
        refreshSession()
        return session?.optStr("access_token") ?: throw AuthRejected("Not signed in")
    }

    private fun scheduleRefresh() {
        val s = session ?: return
        val delaySec = maxOf(30.0, s.optDouble("expires_at", 0.0) - System.currentTimeMillis() / 1000.0 - 300)
        refreshJob?.cancel()
        refreshJob = scope.launch {
            delay((minOf(delaySec, 3600.0) * 1000).toLong())
            try {
                refreshSession(force = true)
            } catch (e: AuthRejected) {
                endSession("Your session has ended. Please sign in again.")
            } catch (e: Exception) {
                delay(30_000)
                scheduleRefresh()
            }
        }
    }

    private fun scheduleRetry() {
        retryJob?.cancel()
        retryJob = scope.launch {
            delay(30_000)
            refresh()
        }
    }

    // ---- status --------------------------------------------------------------------------------
    private suspend fun fetchStatus(): Status {
        val params = jsonOf("p_guest_id" to guestId)
        val data = try {
            Supabase.rpc("get_my_status", params, token())
        } catch (e: HttpError) {
            if (e.status in setOf(401, 403) && e.message.contains("JWT")) {
                refreshSession(force = true)
                Supabase.rpc("get_my_status", params, token())
            } else if (e.status != 0 && e.status !in setOf(401, 403)) {
                Log.w(TAG, "account status unavailable: ${e.message}")
                return toStatus(null).copy(raw = jsonOf("_unconfigured" to true))
            } else throw e
        }
        return toStatus(data as? JSONObject)
    }

    /** Reloads the status (quota, plan, profile) - also after Realtime changes. */
    fun refresh() {
        if (session == null) return
        scope.launch {
            try {
                val st = fetchStatus()
                if (offline) {
                    offline = false
                    startRealtime()
                }
                applyStatus(st)
            } catch (e: AuthRejected) {
                endSession("Your session has ended. Please sign in again.")
            } catch (e: HttpError) {
                if (e.status == 0) {
                    offline = true
                    scheduleRetry()
                }
            } catch (e: Exception) {
                Log.w(TAG, "status refresh failed: $e")
            }
        }
    }

    private fun applyStatus(st: Status?) {
        if (session == null) return
        configured = st != null && !st.raw.optBoolean("_unconfigured")
        status = if (configured) st else null
        if (configured && st != null) {
            session?.put("status", st.raw)
            saveSession()
        }
        // a version that may not run comes as 'mobile' (and as blocked, for older copies): the
        // update screen shows it, not "Account suspended"
        val lock = status?.raw?.optJSONObject("mobile")
        if (lock != null) AppGuard.lockFromStatus(lock)
        val p = profile
        state = when {
            p != null && p.blocked && lock == null -> State.BLOCKED
            p != null && !p.onboarded -> State.ONBOARDING
            else -> State.READY
        }
    }

    fun setQuota(q: Quota?) {
        val st = status
        if (q != null && st != null) status = st.copy(quota = q)
    }

    // ---- realtime ------------------------------------------------------------------------------
    private fun startRealtime() {
        stopRealtime()
        val uid = user?.id ?: return
        if (!configured) return
        val changes = listOf(
            jsonOf("event" to "*", "schema" to "public", "table" to "app_settings"),
            jsonOf("event" to "*", "schema" to "public", "table" to "plans"),
            jsonOf("event" to "*", "schema" to "public", "table" to "profiles", "filter" to "id=eq.$uid"),
            jsonOf("event" to "*", "schema" to "public", "table" to "subscriptions", "filter" to "user_id=eq.$uid"),
            jsonOf("event" to "*", "schema" to "public", "table" to "enhancements", "filter" to "user_id=eq.$uid"),
        )
        val ch = RealtimeChannel("account:$uid", changes, onChange = {
            liveTick++
            AppGuard.soon()  // releases / access changed in the admin panel ping app_settings
            reloadJob?.cancel()
            reloadJob = scope.launch {
                delay(400)
                refresh()
            }
        }, onState = { realtimeState = it })
        channel = ch
        ch.start(session?.optStr("access_token"))
    }

    private fun stopRealtime() {
        channel?.stop()
        channel = null
    }

    // ---- database functions --------------------------------------------------------------------
    suspend fun rpc(fn: String, params: JSONObject = JSONObject()): Any? = Supabase.rpc(fn, params, token())
    suspend fun select(table: String, query: String): List<JSONObject> = Supabase.select(table, query, token())

    data class Reservation(val ok: Boolean, val id: String?, val quota: Quota?, val code: String = "", val message: String = "")

    /** Take one enhancement from today's quota. Network / database problems never block the user;
     *  only an explicit "no" from the database does. */
    suspend fun reserveEnhancement(fileName: String, fileSize: Long, mode: String, scale: Int, width: Int?, height: Int?): Reservation {
        val data = try {
            rpc("reserve_my_enhancement", jsonOf(
                "p_guest_id" to guestId, "p_file_name" to fileName.take(255), "p_file_size" to fileSize,
                "p_mode" to mode, "p_scale" to scale, "p_input_width" to width, "p_input_height" to height,
                "p_device_type" to "mobile")) as? JSONObject ?: JSONObject()
        } catch (e: Exception) {
            Log.w(TAG, "quota check unavailable: $e")
            return Reservation(true, null, null)
        }
        if (data.optBoolean("allowed")) return Reservation(true, data.optStr("id"), toQuota(data))
        val reason = data.optStr("reason")
        val code = if (reason in setOf("blocked", "onboarding", "update_required")) reason!! else "limit"
        val message = when (code) {
            "update_required" -> data.optStr("message") ?: "Please update Boostimise."
            "blocked" -> "Your account has been suspended."
            "onboarding" -> "Add your mobile number to continue."
            else -> if (data.optString("kind") == "user") "You've reached today's limit for your plan."
            else "You've used all ${data.optStr("limit") ?: ""} free enhancements for today."
        }
        return Reservation(false, null, toQuota(data), code, message)
    }

    suspend fun finishEnhancement(id: String, status: String? = null, jobId: String? = null, outW: Int? = null,
                                  outH: Int? = null, seconds: Double? = null, error: String? = null) {
        try {
            rpc("finish_my_enhancement", jsonOf("p_id" to id, "p_guest_id" to guestId, "p_status" to status,
                "p_job_id" to jobId, "p_output_width" to outW, "p_output_height" to outH, "p_seconds" to seconds,
                "p_error" to error))
        } catch (e: Exception) {
            Log.w(TAG, "could not record enhancement result: $e")
        }
    }

    private const val TAG = "Account"
}
