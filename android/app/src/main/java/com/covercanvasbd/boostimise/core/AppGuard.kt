package com.covercanvasbd.boostimise.core

import android.app.ActivityManager
import android.content.Context
import android.content.SharedPreferences
import android.os.Build
import android.provider.Settings
import android.util.Log
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import com.covercanvasbd.boostimise.BuildConfig
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.json.JSONObject
import java.security.MessageDigest
import java.util.Locale
import java.util.TimeZone
import java.util.UUID

/**
 * May this copy of Boostimise run? (core/guard.py of the Windows app.) Asked of the server on start,
 * when the app comes back to the front and every few minutes while it is open.
 *
 * The admin panel's "Releases & access" page (Android tab) controls the answer
 * (boostimise/supabase/schema.sql, mobile_check_in / mobile_verdict): a minimum version, single
 * blocked versions, one switch that turns every copy off, and single blocked phones. While the
 * answer is "no" the app shows the update screen (ui/AccountScreens.kt UpdateScreen) instead of
 * the tools; a newer release that isn't required yet shows a banner.
 *
 * Each check also records this phone (mobile_installs): a hash of the Android ID (never the id
 * itself), the model, Android version, RAM, screen, the app version and the signed-in user, so the
 * admin panel can list who uses the app on which phones.
 *
 * Offline: a "no" stays a "no". A "yes" lasts ``offline_grace_hours`` (72 by default) after the
 * last successful check; after that the app needs the internet again.
 */
object AppGuard {
    enum class State { CHECKING, OK, LOCKED }

    var state by mutableStateOf(State.CHECKING)
        private set
    var verdict by mutableStateOf(JSONObject())
        private set
    var checking by mutableStateOf(false)
        private set
    /** The "update available" banner was closed for this version. */
    var dismissedUpdate by mutableStateOf<String?>(null)

    val reason: String get() = verdict.optStr("reason") ?: "update_required"
    val latestVersion: String? get() = verdict.optStr("latest_version")
    val downloadUrl: String? get() = verdict.optStr("download_url")
    val updateAvailable: Boolean get() = state == State.OK && verdict.optBoolean("update_available") && latestVersion != null

    private const val PLATFORM = "android"
    private const val DEFAULT_INTERVAL_MIN = 30
    private const val DEFAULT_GRACE_HOURS = 72.0
    private const val CLOCK_SLACK_MS = 5 * 60_000L

    private lateinit var app: Context
    private lateinit var scope: CoroutineScope
    private lateinit var prefs: SharedPreferences
    private var launchSent = false
    private var foreground = true
    private var lastCheck = 0L
    private var timer: Job? = null
    private var soonJob: Job? = null

    private val offlineVerdict get() = jsonOf("allowed" to false, "reason" to "offline",
        "message" to "Boostimise needs to check for updates. Connect this phone to the internet and try again.")

    fun init(context: Context, scope: CoroutineScope) {
        app = context.applicationContext
        this.scope = scope
        prefs = context.getSharedPreferences("guard", Context.MODE_PRIVATE)
    }

    fun start() {
        cached()?.let { (v, checkedAt) ->
            if (!v.optBoolean("allowed")) apply(State.LOCKED, v)      // a "no" holds until the server says otherwise
            else if (fresh(v, checkedAt)) apply(State.OK, v)           // start at once, confirm in the background
        }
        check()
    }

    /** Asks the server now (no-op while a check is running). */
    fun check() {
        if (checking) return
        checking = true
        val launch = !launchSent
        scope.launch {
            try {
                onAnswer(ask(launch), launch)
            } catch (e: Exception) {
                onFailed(e)
            }
        }
    }

    /** Checks again in a few seconds (after a live change, or once signed in). */
    fun soon() {
        soonJob?.cancel()
        soonJob = scope.launch {
            delay(3000)
            check()
        }
    }

    fun onForeground() {
        foreground = true
        if (System.currentTimeMillis() - lastCheck > 60_000) check()
    }

    fun onBackground() {
        foreground = false
    }

    /** get_my_status answers 'mobile' when this version may not run: lock at once. */
    fun lockFromStatus(lock: JSONObject) {
        if (state == State.LOCKED || lock.optStr("reason") == null) return
        val merged = JSONObject(verdict.toString())
        lock.keys().forEach { merged.put(it, lock.get(it)) }
        merged.put("allowed", false)
        apply(State.LOCKED, merged)
        soon()  // and fetch the full answer (download link, notes)
    }

    // ---- the server ------------------------------------------------------------------------------
    private suspend fun ask(launch: Boolean): JSONObject {
        val params = jsonOf("p_platform" to PLATFORM, "p_device_id" to deviceId(), "p_version" to Config.APP_VERSION,
            "p_info" to deviceInfo(), "p_launch" to launch)
        val token = if (Account.user != null) runCatching { Account.token() }.getOrNull() else null
        return try {
            Supabase.rpc("mobile_check_in", params, token) as? JSONObject ?: JSONObject()
        } catch (e: HttpError) {
            if (token != null && e.status in setOf(401, 403)) Supabase.rpc("mobile_check_in", params, null) as? JSONObject ?: JSONObject()
            else throw e
        }
    }

    private fun onAnswer(v: JSONObject, launch: Boolean) {
        checking = false
        lastCheck = System.currentTimeMillis()
        if (launch) launchSent = true
        if (!v.has("allowed")) {
            onFailed(HttpError(0, "", "unexpected answer"))
            return
        }
        prefs.edit().putString("verdict", v.toString()).putLong("checked_at", System.currentTimeMillis())
            .putString("version", Config.APP_VERSION).apply()
        apply(if (v.optBoolean("allowed")) State.OK else State.LOCKED, v)
        schedule(v.optInt("check_interval_minutes", DEFAULT_INTERVAL_MIN))
    }

    private fun onFailed(e: Exception) {
        checking = false
        if (e is HttpError && e.status == 404 && (e.code == "PGRST202" || e.message.contains("mobile_check_in"))) {
            // the database doesn't have the phone tables yet (old schema.sql): don't lock anyone out
            Log.w(TAG, "mobile_check_in missing - install the latest supabase/schema.sql")
            apply(State.OK, jsonOf("allowed" to true, "reason" to "unconfigured"))
            schedule(DEFAULT_INTERVAL_MIN)
            return
        }
        Log.w(TAG, "version check failed: $e")
        val c = cached()
        when {
            c == null -> apply(State.LOCKED, offlineVerdict)
            !c.first.optBoolean("allowed") -> apply(State.LOCKED, c.first)
            fresh(c.first, c.second) -> apply(State.OK, c.first)
            else -> apply(State.LOCKED, offlineVerdict)
        }
        timer?.cancel()
        timer = scope.launch {
            delay(60_000)  // offline: try again in a minute
            check()
        }
    }

    private fun schedule(minutes: Int) {
        timer?.cancel()
        timer = scope.launch {
            delay(maxOf(5, minutes) * 60_000L)
            if (foreground) check()  // in the background the next onForeground checks
        }
    }

    private fun apply(s: State, v: JSONObject) {
        if (s != state || v.toString() != verdict.toString()) Log.i(TAG, "guard: $s (${v.optStr("reason")})")
        state = s
        verdict = v
    }

    private fun cached(): Pair<JSONObject, Long>? {
        if (prefs.getString("version", null) != Config.APP_VERSION) return null
        val v = Http.parseJson(prefs.getString("verdict", null)) as? JSONObject ?: return null
        return v to prefs.getLong("checked_at", 0)
    }

    private fun fresh(v: JSONObject, checkedAt: Long): Boolean {
        val grace = v.optDoubleOrNull("offline_grace_hours") ?: DEFAULT_GRACE_HOURS
        val age = System.currentTimeMillis() - checkedAt
        return age >= -CLOCK_SLACK_MS && age <= grace * 3_600_000
    }

    // ---- this phone ------------------------------------------------------------------------------
    /** sha256 of the Android ID (the same after reinstalling the app); a random id when there is none. */
    private fun deviceId(): String {
        val androidId = Settings.Secure.getString(app.contentResolver, Settings.Secure.ANDROID_ID)
            ?.takeIf { it.isNotBlank() && it != "9774d56d682e549c" }
            ?: prefs.getString("device_id", null)
            ?: UUID.randomUUID().toString().also { prefs.edit().putString("device_id", it).apply() }
        val digest = MessageDigest.getInstance("SHA-256").digest("boostimise-android:${androidId.lowercase()}".toByteArray())
        return digest.joinToString("") { "%02x".format(it) }
    }

    private fun deviceInfo(): JSONObject {
        val ram = runCatching {
            ActivityManager.MemoryInfo().also { app.getSystemService(ActivityManager::class.java).getMemoryInfo(it) }.totalMem / (1024 * 1024)
        }.getOrNull()
        val dm = app.resources.displayMetrics
        val name = runCatching { Settings.Global.getString(app.contentResolver, Settings.Global.DEVICE_NAME) }.getOrNull()
        val installer = runCatching {
            if (Build.VERSION.SDK_INT >= 30) app.packageManager.getInstallSourceInfo(app.packageName).installingPackageName
            else @Suppress("DEPRECATION") app.packageManager.getInstallerPackageName(app.packageName)
        }.getOrNull()
        val source = when {
            installer == "com.android.vending" -> "play_store"
            installer == null || installer.contains("packageinstaller") || installer == "com.android.shell" -> "sideload"
            else -> installer
        }
        val cpu = if (Build.VERSION.SDK_INT >= 31) listOf(Build.SOC_MANUFACTURER, Build.SOC_MODEL)
            .filter { it.isNotBlank() && it != Build.UNKNOWN }.joinToString(" ").ifBlank { Build.HARDWARE } else Build.HARDWARE
        return jsonOf(
            "app_build" to BuildConfig.VERSION_CODE.toString(),
            "install_source" to source,
            "manufacturer" to Build.MANUFACTURER.replaceFirstChar { it.titlecase(Locale.ROOT) },
            "model" to Build.MODEL,
            "device_name" to name?.takeIf { it.isNotBlank() && it != Build.MODEL },
            "os_version" to "Android ${Build.VERSION.RELEASE}",
            "os_build" to "${Build.DISPLAY} (API ${Build.VERSION.SDK_INT})",
            "arch" to Build.SUPPORTED_ABIS.firstOrNull(),
            "cpu" to cpu,
            "cpu_cores" to Runtime.getRuntime().availableProcessors(),
            "ram_mb" to ram,
            "screen" to "${minOf(dm.widthPixels, dm.heightPixels)}x${maxOf(dm.widthPixels, dm.heightPixels)} @${dm.densityDpi}dpi",
            "locale" to Locale.getDefault().toLanguageTag(),
            "timezone" to TimeZone.getDefault().id,
        )
    }

    private const val TAG = "AppGuard"
}
