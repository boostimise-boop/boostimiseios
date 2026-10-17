package com.covercanvasbd.boostimise.wa

import android.annotation.SuppressLint
import android.content.Context
import android.content.MutableContextWrapper
import android.graphics.Bitmap
import android.util.Log
import android.view.ViewGroup
import android.webkit.ConsoleMessage
import android.webkit.JavascriptInterface
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.webkit.UserAgentMetadata
import androidx.webkit.WebSettingsCompat
import androidx.webkit.WebViewCompat
import androidx.webkit.WebViewFeature
import com.covercanvasbd.boostimise.core.Http
import com.covercanvasbd.boostimise.core.objects
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.withTimeout
import org.json.JSONArray
import org.json.JSONObject
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap
import kotlin.coroutines.resume

class WaError(message: String) : Exception(message)

data class WaChat(val id: String, val title: String, val t: Long) {
    val isGroup get() = id.endsWith("@g.us")
}

/**
 * The one WhatsApp Web that every tool uses (core/whatsapp.py of the Windows app). It runs in a
 * WebView inside the app that says it is desktop Chrome, linked to the phone's WhatsApp once with
 * "Link with phone number". The Windows app's scripts (wa_bridge.js, wa_bridge_reactions.js) are
 * injected unchanged and read chats, reactions and photos through WhatsApp Web's own modules.
 *
 * The WebView lives for the whole app; MainActivity keeps it on screen behind the tools (so
 * Android never pauses it) and in front on the WhatsApp page.
 */
object WaEngine {
    const val URL = "https://web.whatsapp.com/"
    private const val TAG = "WaEngine"

    enum class Phase { OFF, LOADING, QR, READY, ERROR }

    var phase by mutableStateOf(Phase.OFF)
        private set
    var message by mutableStateOf("Not opened yet")
        private set
    var chats by mutableStateOf<List<WaChat>>(emptyList())
        private set
    var version by mutableStateOf("")
        private set
    /** The tool that holds WhatsApp right now (only one reads chats at a time). */
    var holder by mutableStateOf("")
        private set

    var webView: WebView? = null
        private set
    private var wrapper: MutableContextWrapper? = null
    private lateinit var scope: CoroutineScope
    private lateinit var appContext: Context
    private val pending = ConcurrentHashMap<String, CompletableDeferred<String?>>()
    private val sources = mutableMapOf<String, String>()   // window name -> script
    private var poller: Job? = null
    private var readySince = 0L
    private var loadingSince = 0L
    private var reloadedOnce = false
    private val turn = Mutex()

    fun init(context: Context, scope: CoroutineScope) {
        appContext = context.applicationContext
        this.scope = scope
        fun asset(name: String) = appContext.assets.open(name).bufferedReader().use { it.readText() }
        // As in the Windows app, each bridge gets its own name in the page.
        sources["__waf_basic"] = asset("wa_bridge.js").replace("window.__waf", "window.__waf_basic")
        sources["__waf_reactions"] = asset("wa_bridge_reactions.js").replace("window.__waf", "window.__waf_reactions")
        sources["__wab"] = asset("boost_bridge.js")
    }

    val isReady get() = phase == Phase.READY

    // ---- the WebView -------------------------------------------------------------------------
    /** The WebView for the activity's layout (made on first use). */
    @SuppressLint("SetJavaScriptEnabled")
    fun view(activity: Context): WebView {
        val existing = webView
        if (existing != null) {
            wrapper?.baseContext = activity
            (existing.parent as? ViewGroup)?.removeView(existing)
            return existing
        }
        val w = MutableContextWrapper(activity)
        wrapper = w
        val v = WebView(w)
        // Compose's AndroidView adds a view with WRAP_CONTENT unless it has its own layout params, and a
        // WRAP_CONTENT WebView makes the page's viewport 0 px tall (100vh = 0): WhatsApp's html/body/#app
        // { height: 100% } then collapse and the login page shows only its grey background.
        v.layoutParams = ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT)
        v.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            databaseEnabled = true
            useWideViewPort = true
            loadWithOverviewMode = true
            setSupportZoom(true)
            builtInZoomControls = true
            displayZoomControls = false
            mediaPlaybackRequiresUserGesture = true
            cacheMode = WebSettings.LOAD_DEFAULT
            userAgentString = desktopUserAgent(activity)
        }
        // show the whole 1100 px wide page (WhatsApp changes its layout width after the page has loaded)
        v.setInitialScale((activity.resources.displayMetrics.widthPixels * 100 / (1100 * activity.resources.displayMetrics.density)).toInt())
        v.addOnLayoutChangeListener { view, l, _, r, _, ol, _, or_, _ -> if (r - l != or_ - ol) fitWidth(view as WebView) }
        if (WebViewFeature.isFeatureSupported(WebViewFeature.USER_AGENT_METADATA)) {
            // Client hints too (what Chrome's "Desktop site" sends), or WhatsApp sees "Android, mobile"
            // and sends the phone to the app store page.
            val brand = UserAgentMetadata.BrandVersion.Builder().setBrand("Google Chrome")
                .setMajorVersion(chromeMajor(activity)).setFullVersion(chromeVersion(activity)).build()
            WebSettingsCompat.setUserAgentMetadata(v.settings, UserAgentMetadata.Builder()
                .setBrandVersionList(listOf(brand)).setFullVersion(chromeVersion(activity))
                .setPlatform("Linux").setPlatformVersion("").setArchitecture("x86")
                .setMobile(false).setModel("").setBitness(64).build())
        }
        v.addJavascriptInterface(NativeBridge, "BoostNative")
        v.webViewClient = object : WebViewClient() {
            override fun onPageStarted(view: WebView, url: String?, favicon: Bitmap?) {
                failPending("WhatsApp Web reloaded")
            }

            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                // WhatsApp Web stays here; a link to another site (from a chat) opens in the phone's browser
                val url = request.url
                val host = url.host.orEmpty()
                if (!request.isForMainFrame || url.scheme !in setOf("http", "https")) return false
                if (host.endsWith("whatsapp.com") || host.endsWith("whatsapp.net")) return false
                runCatching {
                    view.context.startActivity(android.content.Intent(android.content.Intent.ACTION_VIEW, url)
                        .addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK))
                }
                return true
            }

            override fun onPageFinished(view: WebView, url: String?) {
                lastUrl = url.orEmpty()
                view.evaluateJavascript(DESKTOP_VIEWPORT, null)
                fitWidth(view)
            }
        }
        v.webChromeClient = object : WebChromeClient() {
            override fun onConsoleMessage(m: ConsoleMessage): Boolean {
                val level = m.messageLevel()
                if (level == ConsoleMessage.MessageLevel.ERROR || level == ConsoleMessage.MessageLevel.WARNING) {
                    Log.d(TAG, "console: ${m.message()}")
                    synchronized(console) {
                        console += "${level.name.take(1)} ${m.message().take(300)} " +
                            "(${m.sourceId().substringAfterLast('/').take(40)}:${m.lineNumber()})"
                        while (console.size > 40) console.removeAt(0)
                    }
                }
                return true
            }
        }
        if (WebViewFeature.isFeatureSupported(WebViewFeature.DOCUMENT_START_SCRIPT)) {
            // lay WhatsApp Web out at a PC's width before its own scripts look at the screen
            WebViewCompat.addDocumentStartJavaScript(v, DESKTOP_VIEWPORT, setOf("https://web.whatsapp.com"))
        }
        WebView.setWebContentsDebuggingEnabled(true)
        webView = v
        if (wanted) load()
        return v
    }

    // Runs before WhatsApp Web's own scripts (and again after each page load):
    // - Chrome's "Desktop site" lays pages out 980+ px wide; WhatsApp's login page needs that width,
    //   and WhatsApp puts its own viewport tag back, so every viewport tag is kept at a PC's width.
    // - WhatsApp asks for permanent ("persisted") storage. Chrome grants it, the in-app browser
    //   refuses, and WhatsApp then never connects (no QR code, no pairing code). The data is kept
    //   anyway (the app's storage is never cleared by the browser), so the request is answered yes.
    private const val DESKTOP_VIEWPORT = """(function(){
  if (window.__boostPatched) return; window.__boostPatched = true;
  var W = 'width=1100';
  var fix = function(){
    var ms = document.querySelectorAll('meta[name=viewport]');
    if (!ms.length && (document.head || document.documentElement)) {
      var m = document.createElement('meta'); m.name = 'viewport'; m.content = W;
      (document.head || document.documentElement).appendChild(m); return;
    }
    for (var i = 0; i < ms.length; i++) if (ms[i].content !== W) ms[i].content = W;
  };
  fix();
  try { new MutationObserver(fix).observe(document.documentElement || document, {subtree: true, childList: true, attributes: true, attributeFilter: ['content']}); } catch (e) {}
  document.addEventListener('DOMContentLoaded', fix);
  try {
    var st = navigator.storage;
    if (st) {
      st.persist = function(){ return Promise.resolve(true); };
      st.persisted = function(){ return Promise.resolve(true); };
    }
    var sb = navigator.storageBuckets;
    if (sb && sb.open) {
      var open = sb.open.bind(sb);
      sb.open = function(name, opts){
        var o = Object.assign({}, opts || {}); delete o.persisted;
        return open(name, o).then(function(b){
          try { b.persist = function(){ return Promise.resolve(true); }; b.persisted = function(){ return Promise.resolve(true); }; } catch (e) {}
          return b;
        });
      };
    }
  } catch (e) {}
})();void 0;"""

    private const val PAGE_PROBE = """(function(){var b=document.body;return {url:location.href,title:document.title,
  text:(b&&b.innerText||'').replace(/\s+/g,' ').slice(0,600),canvas:document.querySelectorAll('canvas').length,
  width:innerWidth,sw:!!navigator.serviceWorker,ctrl:!!(navigator.serviceWorker&&navigator.serviceWorker.controller),
  req:typeof window.require,mobile:navigator.userAgentData?navigator.userAgentData.mobile:null,
  platform:navigator.userAgentData?navigator.userAgentData.platform:navigator.platform};})()"""

    // The login page: a QR code, or the "log in with phone number" steps (no QR canvas there).
    private const val LOGIN_PROBE = """(function(){var t=(document.body&&document.body.innerText)||'';
  return !!document.querySelector('canvas') || /phone number|link with|log ?in|scan|enter code|steps to/i.test(t);})()"""

    /** Zooms out until the page's width fits the screen (the page can still be pinched bigger). */
    private fun fitWidth(view: WebView) {
        view.postDelayed({ while (view.zoomOut()) Unit }, 400)
        view.postDelayed({ while (view.zoomOut()) Unit }, 2500)
    }

    private val console = mutableListOf<String>()
    private var lastUrl = ""
    private var wanted = false

    private fun load() {
        val v = webView ?: return
        phase = Phase.LOADING
        message = "Loading WhatsApp Web…"
        readySince = 0
        loadingSince = System.currentTimeMillis()
        v.loadUrl(URL)
    }

    /** What the page is doing, for the Details button. */
    suspend fun diagnostics(): String {
        val v = webView ?: return "WhatsApp is not open"
        val page = evalRaw("JSON.stringify($PAGE_PROBE)")?.let { Http.parseJson(it) } ?: "?"
        val st = runCatching { status()?.toString() }.getOrNull()
        val logs = synchronized(console) { console.toList() }
        return buildString {
            appendLine("Boostimise ${com.covercanvasbd.boostimise.core.Config.APP_VERSION} · Android ${android.os.Build.VERSION.RELEASE} · ${android.os.Build.MODEL}")
            appendLine("WebView ${chromeVersion(v.context)} · metadata ${WebViewFeature.isFeatureSupported(WebViewFeature.USER_AGENT_METADATA)} · start-script ${WebViewFeature.isFeatureSupported(WebViewFeature.DOCUMENT_START_SCRIPT)}")
            appendLine("Phase $phase · $message")
            appendLine("UA: ${v.settings.userAgentString}")
            appendLine("Last page: $lastUrl")
            appendLine("Status: $st")
            appendLine("Page: $page")
            appendLine("Console (${logs.size}):")
            logs.takeLast(25).forEach { appendLine(it) }
        }
    }

    private fun chromeVersion(ctx: Context): String =
        Regex("Chrome/([\\d.]+)").find(WebSettings.getDefaultUserAgent(ctx))?.groupValues?.get(1) ?: "130.0.0.0"

    private fun chromeMajor(ctx: Context) = chromeVersion(ctx).substringBefore('.')

    private fun desktopUserAgent(ctx: Context) =
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) " +
            "Chrome/${chromeMajor(ctx)}.0.0.0 Safari/537.36"

    /** Opens WhatsApp Web (once) and starts watching its state. */
    fun open() {
        wanted = true
        if (webView != null && (phase == Phase.OFF || phase == Phase.ERROR)) load()
        if (poller?.isActive != true) poller = scope.launch { watch() }
    }

    fun reload(clearCache: Boolean = false) {
        wanted = true
        if (clearCache) webView?.clearCache(true)
        load()
        message = "Reloading WhatsApp Web…"
        if (poller?.isActive != true) poller = scope.launch { watch() }
    }

    /** Signs this phone's WhatsApp Web out (the phone's WhatsApp > Linked devices also does that). */
    fun forgetLogin() {
        val v = webView ?: return
        android.webkit.WebStorage.getInstance().deleteAllData()
        android.webkit.CookieManager.getInstance().removeAllCookies(null)
        v.clearCache(true)
        chats = emptyList()
        reload()
    }

    // ---- state ---------------------------------------------------------------------------------
    private suspend fun watch() {
        while (scope.isActive) {
            val st = try {
                status()
            } catch (e: Exception) {
                null
            }
            val ready = st != null && st.optBoolean("ready")
            val qr = (st != null && st.optBoolean("qr")) || (!ready && evalRaw(LOGIN_PROBE) == "true")
            val now = System.currentTimeMillis()
            if (ready) {
                if (readySince == 0L) {
                    readySince = now
                    phase = Phase.LOADING
                    message = "Logged in, loading chats…"
                }
                val waited = now - readySince
                if (phase != Phase.READY && ((st!!.optInt("chats") > 0 && waited > 3000) || waited > 20_000)) {
                    version = st.optString("version")
                    phase = Phase.READY
                    message = "Connected"
                    runCatching { refreshChats() }
                }
                loadingSince = 0
            } else {
                readySince = 0
                if (qr) {
                    phase = Phase.QR
                    message = "Not linked yet: tap \"Log in with phone number\" below"
                    loadingSince = 0
                } else {
                    if (phase == Phase.READY || phase == Phase.QR) {
                        phase = Phase.LOADING
                        message = "Loading WhatsApp Web…"
                    }
                    if (loadingSince == 0L) loadingSince = now
                    // stuck on the logo with neither a QR code nor chats: clear the cache and reload, once
                    if (!reloadedOnce && now - loadingSince > 45_000) {
                        reloadedOnce = true
                        webView?.clearCache(false)
                        reload()
                    }
                }
            }
            delay(if (phase == Phase.READY) 2500 else 1000)
        }
    }

    suspend fun refreshChats() {
        val arr = call("window.__waf_basic.listChats()") as? JSONArray
        chats = arr.objects().map { WaChat(it.optString("id"), it.optString("title"), it.optLong("t")) }
    }

    private suspend fun status(): JSONObject? = call("window.__waf_basic.status()", 8000) as? JSONObject

    /** Waits until WhatsApp Web is linked and loaded; [onWait] gets what it is waiting for. */
    suspend fun awaitReady(onWait: (String) -> Unit = {}) {
        open()
        var told = ""
        while (phase != Phase.READY) {
            val msg = if (phase == Phase.QR) "WhatsApp is not linked yet - open the WhatsApp page and link it"
            else "Waiting for WhatsApp Web to load…"
            if (msg != told) {
                told = msg
                onWait(msg)
            }
            delay(1000)
        }
    }

    /** Runs [block] holding WhatsApp; another tool waits meanwhile (core/whatsapp.py's turn()). */
    suspend fun <T> withTurn(tool: String, onWait: (String) -> Unit, block: suspend () -> T): T {
        if (!turn.tryLock()) {
            onWait("Waiting for $holder to finish with WhatsApp…")
            turn.lock()
        }
        holder = tool
        try {
            return block()
        } finally {
            holder = ""
            turn.unlock()
        }
    }

    // ---- calling the bridges -----------------------------------------------------------------
    private suspend fun evalRaw(js: String): String? = suspendCancellableCoroutine { cont ->
        val v = webView
        if (v == null) {
            cont.resume(null)
        } else {
            v.post { v.evaluateJavascript(js) { cont.resume(it) } }
        }
    }

    private suspend fun ensureBridges() {
        val names = sources.keys.toList()
        val probe = "[" + names.joinToString(",") { "typeof window.$it" } + "].join(',')"
        val res = evalRaw(probe)?.trim('"') ?: return
        val types = res.split(',')
        names.forEachIndexed { i, name ->
            if (types.getOrNull(i) != "object") evalRaw(sources[name]!! + "\n;void 0;")
        }
    }

    /** Evaluates a JavaScript expression (it may return a Promise) and returns its JSON value. */
    suspend fun call(expr: String, timeoutMs: Long = 120_000): Any? {
        if (webView == null) throw WaError("WhatsApp is not open")
        ensureBridges()
        val id = UUID.randomUUID().toString()
        val d = CompletableDeferred<String?>()
        pending[id] = d
        val js = "(async()=>{try{const __r=await ($expr);BoostNative.done('$id',JSON.stringify(__r===undefined?null:__r));}" +
            "catch(e){BoostNative.fail('$id',String((e&&e.message)||e));}})();void 0;"
        try {
            evalRaw(js)
            val text = withTimeout(timeoutMs) { d.await() }
            return Http.parseJson(text)
        } finally {
            pending.remove(id)
        }
    }

    fun quote(s: String): String = JSONObject.quote(s)

    private fun failPending(reason: String) {
        for ((_, d) in pending) d.completeExceptionally(WaError(reason))
        pending.clear()
    }

    private object NativeBridge {
        @JavascriptInterface
        fun done(id: String, json: String?) {
            pending[id]?.complete(json)
        }

        @JavascriptInterface
        fun fail(id: String, message: String?) {
            pending[id]?.completeExceptionally(WaError(message ?: "error"))
        }
    }

    /** Chats whose title matches a name the way WhatsApp's search does for the Pathao tool:
     *  case, spaces, brackets and emoji ignored. */
    fun normName(s: String) = s.lowercase().replace(Regex("[^\\p{L}\\p{M}\\p{N}]+"), " ").trim()
}
