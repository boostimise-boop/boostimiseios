package com.covercanvasbd.boostimise.tools.pathao

import android.annotation.SuppressLint
import android.content.Context
import android.content.Intent
import android.content.MutableContextWrapper
import android.net.Uri
import android.view.ViewGroup
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import com.covercanvasbd.boostimise.core.Http
import com.covercanvasbd.boostimise.core.OrderParser
import com.covercanvasbd.boostimise.core.Storage
import com.covercanvasbd.boostimise.core.jsonOf
import com.covercanvasbd.boostimise.core.objects
import com.covercanvasbd.boostimise.tools.fetch.ToolLog
import com.covercanvasbd.boostimise.wa.Jobs
import com.covercanvasbd.boostimise.wa.WaEngine
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.time.ZoneId
import java.time.ZonedDateTime
import java.util.Date
import java.util.Locale
import kotlin.coroutines.resume

/** Something the user has to do first (log in to Pathao, open a page). */
class NeedUser(message: String) : Exception(message)

/**
 * Pathao Auto-fill (tools/pathao_autofill): reads the order message of each code in WhatsApp and
 * fills Pathao's New Delivery form on merchant.pathao.com, which runs inside the app. You check the
 * form and tap Save; the next code fills itself after the save.
 */
class PathaoTool(private val scope: CoroutineScope) {
    companion object {
        const val PENDING = "Pending"
        const val FETCHING = "Reading WhatsApp…"
        const val FILLED = "Filled - waiting for Save"
        const val SAVED = "Saved ✓"
        const val SAVED_EARLIER = "Saved earlier"
        const val SKIPPED = "Skipped"
        const val NOT_FOUND = "Not found"

        val DEFAULT_CHATS = linkedMapOf(
            "2" to "Free Cover BD(2-)",
            "3" to "Cover Print BD 0.1 (3-)",
            "4" to "Customise Cover BD (4-)",
            "4a" to "Customise Cover BD(4a-)",
            "5" to "Misty Cover House 3.0 (5-)",
            "5a" to "Misty Cover House (5a-)",
            "6" to "Customise Phone Case (6-)",
        )
        const val PATHAO_URL = "https://merchant.pathao.com/"

        // key -> (placeholder, which match, label shown above the box)
        val FIELDS = linkedMapOf(
            "order_id" to Triple("Type ID (Optional)", 0, "Merchant Order ID"),
            "amount" to Triple("Enter the amount including delivery charge", 0, "Amount to Collect"),
            "phone" to Triple("Enter phone number", 0, "Recipient's Phone"),
            "phone2" to Triple("Enter phone number", 1, "Recipient's Secondary Phone"),
            "name" to Triple("Type Name", 0, "Recipient's Name"),
            "address" to Triple("Enter full address", 0, "Recipient's Address"),
        )

        // Finds a Pathao input by placeholder, falling back to the label text above it.
        private const val JS_FIND = """
const __vis = (el) => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
const __find = (spec) => {
  if (!spec) return null;
  const inputs = [...document.querySelectorAll('input, textarea')].filter(__vis);
  const ph = (spec.ph || '').trim().toLowerCase();
  if (ph) {
    const hits = inputs.filter(e => (e.getAttribute('placeholder') || '').trim().toLowerCase() === ph);
    if (hits.length > spec.nth) return hits[spec.nth];
  }
  if (!spec.label) return null;
  const norm = (s) => (s || '').replace(/[‘’`]/g, "'").replace(/\s+/g, ' ').trim().toLowerCase();
  const want = norm(spec.label);
  for (const lab of document.querySelectorAll('label, span, div, p, legend, strong, b, h5, h6')) {
    if (norm(lab.textContent) !== want || !__vis(lab)) continue;
    const next = inputs.find(i => lab.compareDocumentPosition(i) & Node.DOCUMENT_POSITION_FOLLOWING);
    if (next) return next;
  }
  return null;
};
"""
        // Remembers when Save was clicked, and every POST/PUT answer (Playwright saw these on the PC).
        private const val JS_HOOKS = """
if (!window.__ccbdHooked) {
  window.__ccbdHooked = true;
  const mark = (on) => {
    try { on ? sessionStorage.setItem('__ccbdSave', String(Date.now())) : sessionStorage.removeItem('__ccbdSave'); } catch (e) {}
  };
  document.addEventListener('click', (e) => {
    let el = e.target;
    for (let i = 0; el && i < 5; i++, el = el.parentElement) {
      if (el.matches && el.matches('input, textarea, select') && !['submit', 'button'].includes(el.type)) return;
      const t = ((el.innerText || el.value || '') + '').trim().toLowerCase();
      if (t.length > 40) return;
      if (['save', 'save order', 'submit', 'confirm'].includes(t)) { mark(true); return; }
      if (['cancel', 'reset', 'clear'].includes(t)) { mark(false); return; }
    }
  }, true);
  document.addEventListener('submit', () => mark(true), true);
  const note = (start, status, method) => {
    try {
      const a = JSON.parse(sessionStorage.getItem('__ccbdResp') || '[]');
      a.push([start, status, method]);
      sessionStorage.setItem('__ccbdResp', JSON.stringify(a.slice(-40)));
    } catch (e) {}
  };
  const of = window.fetch;
  window.fetch = function (input, init) {
    const method = ((init && init.method) || (input && input.method) || 'GET').toUpperCase();
    const start = Date.now();
    const p = of.apply(this, arguments);
    if (method === 'POST' || method === 'PUT') p.then(r => note(start, r.status, method), () => note(start, 0, method));
    return p;
  };
  const oo = XMLHttpRequest.prototype.open, os = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (m) { this.__m = String(m || 'GET').toUpperCase(); return oo.apply(this, arguments); };
  XMLHttpRequest.prototype.send = function () {
    if (this.__m === 'POST' || this.__m === 'PUT') {
      const start = Date.now(), m = this.__m;
      this.addEventListener('loadend', () => note(start, this.status, m));
    }
    return os.apply(this, arguments);
  };
}
"""
    }

    class Item(val code: String) {
        var status by mutableStateOf(PENDING)
        var order by mutableStateOf<OrderParser.Order?>(null)
        var raw by mutableStateOf("")
        var formPhone = ""
    }

    private val prefs = Storage.prefs("pathao_autofill")
    val log = ToolLog()
    val items = mutableStateListOf<Item>()
    var listName by mutableStateOf("")
        private set
    private var listUri: Uri? = null
    var listText by mutableStateOf("")
        private set
    var cur by mutableStateOf<Item?>(null)
        private set
    var running by mutableStateOf(false)
        private set
    var waiting by mutableStateOf(false)
        private set
    var state by mutableStateOf("Open your code list, log in to Pathao below, then press Start.")
        private set
    var attention by mutableStateOf("")
    var lookbackDays by mutableStateOf(prefs.getInt("lookback_days", 30))
    var writePhone by mutableStateOf(prefs.getBoolean("write_phone_to_list", true))
    var startFromBottom by mutableStateOf(prefs.getBoolean("start_from_bottom", true))
    val chatNames = mutableStateListOf<Pair<String, String>>().apply {
        val saved = Http.parseJson(prefs.getString("chat_names", null)) as? JSONObject
        val m = LinkedHashMap(DEFAULT_CHATS)
        saved?.keys()?.forEach { m[it] = saved.optString(it) }
        addAll(m.toList())
    }
    private var createUrl: String
        get() = prefs.getString("pathao_create_url", "")!!
        set(v) = prefs.edit().putString("pathao_create_url", v).apply()

    // order messages read from WhatsApp: code -> (message id, text, order)
    private val cache = mutableMapOf<String, Triple<String, String, OrderParser.Order>>()
    private val duplicates = mutableMapOf<String, MutableSet<String>>()
    private val readPrefixes = mutableSetOf<String>()
    private var job: Job? = null
    private var watchJob: Job? = null

    // the save watch
    private var baselineToasts = setOf<String>()
    private var fillUrl = ""
    private var armedMs = 0L
    private var successHits = 0
    private var resetHits = 0
    private var resetWarned = false

    fun saveSettings() {
        val m = JSONObject()
        chatNames.forEach { (p, n) -> m.put(p, n) }
        prefs.edit().putInt("lookback_days", lookbackDays).putBoolean("write_phone_to_list", writePhone)
            .putBoolean("start_from_bottom", startFromBottom).putString("chat_names", m.toString()).apply()
        cache.clear()
        readPrefixes.clear()
    }

    private fun chatFor(prefix: String) = (chatNames.firstOrNull { it.first == prefix }?.second ?: "$prefix-").trim()

    private fun savedCodes(): Set<String> = prefs.getStringSet("saved_codes", emptySet())!!

    // ---- the list ----------------------------------------------------------------------------
    suspend fun openList(uri: Uri) {
        runCatching {
            Storage.app.contentResolver.takePersistableUriPermission(uri,
                Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION)
        }
        listUri = uri
        listName = Storage.displayName(uri) ?: "list.txt"
        load(Storage.readText(uri))
    }

    fun pasteList(text: String) {
        listUri = null
        listName = "pasted list"
        load(text)
    }

    private fun load(text: String) {
        listText = text
        var codes = OrderParser.readCodeList(text)
        if (startFromBottom) codes = codes.reversed()
        val saved = savedCodes()
        items.clear()
        items.addAll(codes.map { c -> Item(c).also { if (c in saved) it.status = SAVED_EARLIER } })
        cur = null
        running = false
        waiting = false
        state = "${codes.size} codes loaded (${items.count { it.status == SAVED_EARLIER }} saved earlier). Press Start."
    }

    // ---- the Pathao page ---------------------------------------------------------------------
    var web: WebView? = null
        private set
    private var wrapper: MutableContextWrapper? = null

    @SuppressLint("SetJavaScriptEnabled")
    fun view(activity: Context): WebView {
        web?.let { v ->
            wrapper?.baseContext = activity
            (v.parent as? ViewGroup)?.removeView(v)
            return v
        }
        val w = MutableContextWrapper(activity)
        wrapper = w
        val v = WebView(w)
        // not WRAP_CONTENT (AndroidView's default): that gives the page a 0 px tall viewport (see WaEngine.view)
        v.layoutParams = ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT)
        v.settings.javaScriptEnabled = true
        v.settings.domStorageEnabled = true
        v.settings.databaseEnabled = true
        v.webViewClient = object : WebViewClient() {
            override fun onPageFinished(view: WebView, url: String?) {
                view.evaluateJavascript("(function(){$JS_HOOKS})();void 0;", null)
            }
        }
        v.loadUrl(createUrl.ifEmpty { PATHAO_URL })
        web = v
        return v
    }

    private suspend fun eval(js: String): Any? = suspendCancellableCoroutine { cont ->
        val v = web
        if (v == null) cont.resume(null)
        else v.post { v.evaluateJavascript(js) { cont.resume(Http.parseJson(it)) } }
    }

    private fun spec(key: String): JSONObject {
        val (ph, nth, label) = FIELDS[key]!!
        return jsonOf("ph" to ph, "nth" to nth, "label" to label)
    }

    private suspend fun pageState(): JSONObject? {
        val specs = JSONObject().apply { listOf("order_id", "phone", "address").forEach { put(it, spec(it)) } }
        val js = "(function(specs){$JS_FIND$JS_HOOKS" + """
  const val = (k) => { const el = __find(specs[k]); return el ? (el.value || '') : null; };
  const oid = val('order_id');
  const toasts = [];
  document.querySelectorAll('[role="alert"], [role="status"], [role="dialog"], [class*="toast" i], [class*="notif" i], [class*="snack" i], [class*="swal" i], [class*="alert" i], [class*="message" i]').forEach(el => {
    const t = (el.textContent || '').replace(/\s+/g, ' ').trim();
    if (t && t.length <= 300 && /success|created|placed|সফল/i.test(t) && __vis(el)) toasts.push(t);
  });
  let click = 0, resp = [];
  try { click = parseInt(sessionStorage.getItem('__ccbdSave') || '0', 10) || 0; } catch (e) {}
  try { resp = JSON.parse(sessionStorage.getItem('__ccbdResp') || '[]'); } catch (e) {}
  return {url: location.href, docStart: performance.timeOrigin, click, form: oid !== null,
          orderId: oid, phone: val('phone'), address: val('address'), toasts, resp};
})(""" + specs + ")"
        return eval(js) as? JSONObject
    }

    /** Sets a field the way typing does, so Pathao's React form takes the value. */
    private suspend fun setField(key: String, value: String): Boolean {
        val js = "(function(spec, value){$JS_FIND" + """
  const el = __find(spec);
  if (!el) return false;
  el.focus();
  const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, value);
  el.dispatchEvent(new Event('input', {bubbles: true}));
  el.dispatchEvent(new Event('change', {bubbles: true}));
  el.dispatchEvent(new KeyboardEvent('keyup', {bubbles: true, key: 'End'}));
  return true;
})(""" + spec(key) + "," + JSONObject.quote(value) + ")"
        return eval(js) == true
    }

    private suspend fun fieldValue(key: String): String? =
        eval("(function(spec){$JS_FIND const el = __find(spec); return el ? (el.value || '') : null; })(${spec(key)})") as? String

    private suspend fun waitForm(timeoutMs: Long = 40_000) {
        val end = System.currentTimeMillis() + timeoutMs
        while (System.currentTimeMillis() < end) {
            val st = pageState()
            if (st != null && st.optBoolean("form")) {
                createUrl = st.optString("url")
                return
            }
            delay(500)
        }
        throw NeedUser("Pathao's 'New Delivery' form is not open. Log in to Pathao below, open New Delivery, then press Start.")
    }

    /** Every code starts on a fresh, empty New Delivery form. */
    private suspend fun prepareForm() {
        val st = pageState()
        if (st != null && st.optBoolean("form") && st.optString("orderId").isEmpty() && st.optString("phone").isEmpty() &&
            st.optString("address").isEmpty()) {
            createUrl = st.optString("url")
            return
        }
        val url = createUrl
        if (url.isEmpty()) {
            if (st != null && st.optBoolean("form")) return
            throw NeedUser("Open Pathao's 'New Delivery' page below, then press Start.")
        }
        state = "Opening a fresh Pathao New Delivery form…"
        web?.post { web?.loadUrl(url) }
        delay(1500)
        waitForm()
    }

    private suspend fun fill(item: Item, prepare: Boolean) {
        val o = item.order ?: return
        if (prepare) prepareForm() else waitForm(15_000)
        val values = mutableListOf("order_id" to item.code, "amount" to o.amount, "phone" to o.phone, "phone2" to o.phone2,
            "name" to o.name)
        if (o.address.isNotEmpty()) values += "address" to o.address   // no address: leave what Pathao suggests
        for ((k, v) in values) {
            if (!setField(k, v)) {
                if (k in setOf("phone2", "address")) {
                    if (v.isNotEmpty()) log.add("Could not find the '${FIELDS[k]!!.third}' box - fill $v by hand.")
                } else throw NeedUser("Could not find the '${FIELDS[k]!!.third}' box on the Pathao page. Open the New Delivery form and press Start.")
            }
            if (k == "phone2") delay(700)   // Pathao may look the phone up and fill things in
        }
        delay(600)
        for ((k, v) in values) {   // put our values back if Pathao's lookup overwrote any
            val now = fieldValue(k)
            if (now != null && now.trim() != v.trim()) setField(k, v)
        }
    }

    private suspend fun arm() {
        eval("(function(){try{sessionStorage.removeItem('__ccbdSave');}catch(e){}})()")
        val st = pageState() ?: JSONObject()
        baselineToasts = (0 until (st.optJSONArray("toasts")?.length() ?: 0)).map { st.getJSONArray("toasts").getString(it) }.toSet()
        fillUrl = st.optString("url")
        armedMs = System.currentTimeMillis()
        successHits = 0
        resetHits = 0
        resetWarned = false
        waiting = true
    }

    private fun urlKey(url: String) = Uri.parse(url).let { "${it.host}${it.path?.trimEnd('/')}" }

    private fun postedSince(st: JSONObject, ms: Long): Boolean {
        val a = st.optJSONArray("resp") ?: return false
        return (0 until a.length()).any { val r = a.optJSONArray(it); r != null && r.optLong(0) >= ms && r.optInt(1) in 200..299 }
    }

    /** Saved = Save was tapped, Pathao accepted a POST after it, and the page shows it. */
    private suspend fun checkSaved() {
        val item = cur ?: run { waiting = false; return }
        val st = pageState() ?: return
        if (st.optBoolean("form") && st.optString("orderId").trim().lowercase() == item.code && st.optString("phone").isNotEmpty()) {
            item.formPhone = st.optString("phone")
        }
        val click = st.optLong("click")
        if (click == 0L) {
            val cleared = st.optBoolean("form") && st.optString("orderId").isEmpty() && st.optString("phone").isEmpty()
            resetHits = if (cleared) resetHits + 1 else 0
            if (resetHits == 3) {
                if (!postedSince(st, armedMs)) {
                    log.add("${item.code}: the Pathao form was emptied without Save - filling it again.")
                    fill(item, prepare = false)
                    arm()
                } else if (!resetWarned) {
                    resetWarned = true
                    attention = "The Pathao form was emptied. If ${item.code} was saved tap 'Saved → Next', otherwise 'Fill again'."
                }
            }
            return
        }
        if (!postedSince(st, click - 500)) {
            successHits = 0
            return
        }
        val toasts = (0 until (st.optJSONArray("toasts")?.length() ?: 0)).map { st.getJSONArray("toasts").getString(it) }
        val newToast = toasts.any { it !in baselineToasts }
        val moved = urlKey(st.optString("url")) != urlKey(fillUrl)
        val cleared = (!st.optBoolean("form") || st.optString("orderId").isEmpty()) && st.optDouble("docStart") < click
        if (newToast || moved) onSaved()
        else if (cleared) {
            successHits++
            if (successHits >= 2) onSaved()
        } else successHits = 0
    }

    private suspend fun onSaved() {
        val item = cur ?: return
        recordSaved(item)
        cur = null
        waiting = false
        delay(1000)
    }

    private suspend fun recordSaved(item: Item) {
        prefs.edit().putStringSet("saved_codes", savedCodes() + item.code).apply()
        val o = item.order
        val row = listOf(item.code, SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US).format(Date()), o?.name, o?.phone, o?.phone2,
            o?.amount, o?.address).joinToString("\t") { (it ?: "").replace("\t", " ").replace("\n", " ") }
        prefs.edit().putString("saved_log", (prefs.getString("saved_log", "") + row + "\n").takeLast(200_000)).apply()
        item.status = SAVED
        log.add("✓ ${item.code} saved.")
        if (writePhone) writePhoneToList(item)
    }

    private suspend fun writePhoneToList(item: Item) {
        val phone = (OrderParser.findPhones(item.formPhone) + OrderParser.findPhones(item.order?.phone)).firstOrNull()
        if (phone == null) {
            log.add("${item.code}: ⚠ no phone number to write in the list - type it there yourself.")
            return
        }
        val changed = OrderParser.writePhoneToList(listText, item.code, phone)
        if (changed == null) {
            log.add("${item.code}: the list has no line with just ${item.code} any more - phone not written.")
            return
        }
        listText = changed
        val uri = listUri
        if (uri != null) {
            try {
                Storage.writeText(uri, changed)
                log.add("${item.code}: wrote $phone next to it in $listName.")
            } catch (e: Exception) {
                log.add("${item.code}: ⚠ could not write $phone in $listName (${e.message}) - type it there yourself.")
            }
        } else log.add("${item.code}: wrote $phone next to it in the list (copy the list to keep it).")
    }

    // ---- WhatsApp ----------------------------------------------------------------------------
    /** Reads the prefix's chat once and caches every order message in it (newest wins). */
    private suspend fun readChat(prefix: String) {
        val name = chatFor(prefix)
        WaEngine.awaitReady { state = it }
        if (WaEngine.chats.isEmpty()) WaEngine.refreshChats()
        val want = WaEngine.normName(name)
        val chat = WaEngine.chats.firstOrNull { WaEngine.normName(it.title) == want }
            ?: throw NeedUser("WhatsApp has no chat called '$name'. Fix the chat name for $prefix- in the settings.")
        state = "Reading WhatsApp chat '${chat.title}'…"
        val since = ZonedDateTime.now(ZoneId.systemDefault()).minusDays(lookbackDays.toLong()).toEpochSecond()
        val res = WaEngine.withTurn("Pathao Auto-fill", { state = it }) {
            WaEngine.call("window.__wab.messages(${WaEngine.quote(chat.id)}, ${jsonOf("since" to since, "maxLoads" to 400)})",
                30 * 60_000L) as? JSONObject
        } ?: JSONObject()
        if (res.has("error")) throw NeedUser("${chat.title}: ${res.optString("error")}")
        val msgs = res.optJSONArray("messages").objects()
        for (m in msgs.reversed()) {   // newest first, so the newest message of a code wins
            val text = m.optString("text")
            for (code in OrderParser.findCodes(text)) {
                if (OrderParser.splitCode(code).first != prefix) continue
                val order = OrderParser.parseOrder(text).copy(code = code)
                if (order.phone.isEmpty() && order.name.isEmpty() && order.address.isEmpty()) continue
                val prev = cache[code]
                if (prev != null && prev.first != m.optString("id") && order.phone.isNotEmpty() &&
                    prev.third.phone.isNotEmpty() && order.phone != prev.third.phone) {
                    duplicates.getOrPut(code) { mutableSetOf() }.addAll(listOf(order.phone, prev.third.phone))
                }
                if (prev == null || (order.phone.isNotEmpty() && prev.third.phone.isEmpty())) {
                    cache[code] = Triple(m.optString("id"), text, order)
                }
            }
        }
        readPrefixes += prefix
        log.add("Read ${msgs.size} messages of '${chat.title}' (last $lookbackDays days).")
    }

    // ---- the flow ----------------------------------------------------------------------------
    fun start() {
        if (items.isEmpty()) {
            state = "Open or paste your code list first."
            return
        }
        saveSettings()
        running = true
        attention = ""
        if (job?.isActive == true) return
        Jobs.begin("pathao", "Pathao Auto-fill is running")
        job = scope.launch {
            try {
                loop()
            } catch (e: CancellationException) {
                // paused
            } catch (e: NeedUser) {
                attention = e.message ?: ""
                state = "Waiting for you."
                running = false
            } catch (e: Exception) {
                attention = "Error: ${e.message}"
                log.add("Error: ${e.message}")
                running = false
            } finally {
                Jobs.end("pathao")
            }
        }
    }

    private suspend fun loop() {
        while (running) {
            if (waiting) {
                checkSaved()
                delay(700)
                continue
            }
            val next = cur?.takeIf { it.status == PENDING || it.status == FETCHING } ?: items.firstOrNull { it.status == PENDING }
            if (next == null) {
                running = false
                state = "All codes in the list are done."
                log.add("Reached the end of the list.")
                return
            }
            process(next, prepare = true, explicit = false)
        }
    }

    private suspend fun process(item: Item, prepare: Boolean, explicit: Boolean) {
        cur?.takeIf { it !== item && it.status == FILLED }?.status = PENDING
        cur = item
        waiting = false
        item.status = FETCHING
        val prefix = OrderParser.splitCode(item.code).first
        try {
            if (explicit || prefix !in readPrefixes) readChat(prefix)
        } catch (e: Exception) {
            item.status = PENDING
            cur = null
            throw e
        }
        val rec = cache[item.code]
        if (rec == null) {
            item.status = NOT_FOUND
            cur = null
            log.add("${item.code}: no message starting with ${item.code} in '${chatFor(prefix)}' (last $lookbackDays days). Skipped.")
            return
        }
        item.order = rec.third
        item.raw = rec.second
        rec.third.warnings.forEach { log.add("${item.code}: ⚠ $it - check it before saving.") }
        duplicates[item.code]?.let { log.add("${item.code}: ⚠ more than one order message with this code (phones ${it.sorted().joinToString(", ")}). The newest was used - check it.") }
        try {
            fill(item, prepare)
        } catch (e: Exception) {
            item.status = PENDING
            cur = null
            throw e
        }
        item.status = FILLED
        arm()
        state = "${item.code} is filled in Pathao. Check it, finish the form and tap Save; the next code fills itself after the save."
    }

    fun pause() {
        running = false
        waiting = false
        job?.cancel()
        state = "Paused. Press Start to continue."
    }

    fun skip() {
        val item = cur ?: items.firstOrNull { it.status == PENDING } ?: return
        item.status = SKIPPED
        log.add("${item.code}: skipped.")
        cur = null
        waiting = false
        if (!running) start() else Unit
    }

    fun markSaved() {
        val item = cur ?: run { log.add("No code is on the Pathao form right now."); return }
        scope.launch {
            recordSaved(item)
            cur = null
            waiting = false
            if (!running) start()
        }
    }

    /** Fill the form again with the (edited) values of the current code. */
    fun refill(order: OrderParser.Order) {
        val item = cur ?: return
        item.order = order
        scope.launch {
            try {
                fill(item, prepare = false)
                item.status = FILLED
                arm()
                state = "${item.code} is filled with your values. Tap Save there."
                if (!running) start()
            } catch (e: Exception) {
                attention = e.message ?: "error"
            }
        }
    }

    /** Read the code's order message again (the chat is read fresh). */
    fun reread(item: Item) {
        val old = job
        pause()
        scope.launch {
            old?.join()
            item.status = PENDING
            readPrefixes.remove(OrderParser.splitCode(item.code).first)
            cur = item
            start()
        }
    }
}
