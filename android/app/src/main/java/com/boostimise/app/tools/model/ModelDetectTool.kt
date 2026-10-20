package com.boostimise.app.tools.model

import android.util.Base64
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import com.boostimise.app.core.Shop
import com.boostimise.app.core.Storage
import com.boostimise.app.core.jsonOf
import com.boostimise.app.core.objects
import com.boostimise.app.tools.fetch.ToolLog
import com.boostimise.app.wa.Jobs
import com.boostimise.app.wa.WaChat
import com.boostimise.app.wa.WaEngine
import com.boostimise.app.wa.WaError
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import org.json.JSONObject
import java.time.ZoneId
import java.time.ZonedDateTime

private const val MARKER_MAX_LEN = 120   // a message that doesn't START with the code must be this short to count
private const val GROUP_MAX_MSGS = 80    // never look further than this many messages away from a code
private const val NEAR_MSGS = 12         // a model name written in chat must be this close to the code
private const val RANGE_BELOW = 40       // codes this far below the list's smallest number still count as boundaries
private const val RANGE_ABOVE = 300      // ... and this far above its largest number

class Msg(val id: String, val text: String, val imgs: Int, val codes: List<String>)

class Group(val code: String, val markerId: String, val markerText: String) {
    val imageIds = mutableListOf<String>()
    val context = mutableListOf<String>()
    val near = mutableListOf<String>()
    val shared = mutableListOf<String>()
}

/** Codes (from the searched blocks) that label this message (wa_driver.py's marker_codes). */
fun markerCodes(text0: String, hasImages: Boolean, ranges: Map<String, Pair<Int, Int>>): List<String> {
    val text = text0.trim()
    if (text.isEmpty()) return emptyList()
    val first = ProductCodes.codeMatches(text).firstOrNull()
    val startsWithCode = first != null && first.start == 0
    if (!startsWithCode && text.length > MARKER_MAX_LEN && !hasImages) return emptyList()
    return ProductCodes.codesInText(text).filter { code ->
        val (p, n) = ProductCodes.split(code)
        val r = ranges[p]
        r != null && n in (r.first - RANGE_BELOW)..(r.second + RANGE_ABOVE)
    }
}

/**
 * For every found code, the messages (photos + text) that belong to it (wa_driver.py's build_groups).
 * mode "below": from the code's first message until another code starts; "above": back from the
 * code's last message to the previous code. A code in several places: the one with photos wins, then the oldest.
 */
fun buildGroups(msgs: List<Msg>, found: Set<String>, mode: String, maxImages: Int): Map<String, Group> {
    data class Seg(val first: Int, var last: Int, val codes: MutableSet<String>)
    val segments = mutableListOf<Seg>()
    for ((i, m) in msgs.withIndex()) {
        if (m.codes.isEmpty()) continue
        val last = segments.lastOrNull()
        if (last != null && m.codes.any { it in last.codes }) {
            last.last = i
            last.codes += m.codes
        } else segments += Seg(i, i, m.codes.toMutableSet())
    }
    val groups = mutableMapOf<String, Pair<Pair<Int, Int>, Group>>()
    for ((k, seg) in segments.withIndex()) {
        val span: List<Int>
        val other: Int?
        if (mode == "above") {
            val start = if (k > 0) segments[k - 1].last + 1 else 0
            span = (seg.last downTo maxOf(start, seg.last - GROUP_MAX_MSGS)).toList()
            other = if (k > 0) start - 1 else null
        } else {
            val end = if (k + 1 < segments.size) segments[k + 1].first else msgs.size
            span = (seg.first until minOf(end, seg.first + GROUP_MAX_MSGS)).toList()
            other = if (k + 1 < segments.size) end else null
        }
        for (code in seg.codes) {
            if (code !in found) continue
            val g = Group(code, msgs[seg.first].id, msgs[seg.first].text)
            var count = 0
            var hasPhotos = false
            for ((pos, j) in span.withIndex()) {
                val m = msgs[j]
                if (m.imgs > 0 && count < maxImages) {
                    g.imageIds += m.id
                    count += m.imgs
                    hasPhotos = true
                }
                if (m.text.isNotEmpty() && m.codes.isEmpty()) g.context += m.text
                if (m.text.isNotEmpty() && pos <= NEAR_MSGS) {
                    val mine = if (j > seg.last) j - seg.last else if (j < seg.first) seg.first - j else 0
                    val closerToOther = other != null && Math.abs(j - other) < mine
                    (if (closerToOther) g.shared else g.near) += m.text
                }
            }
            val score = (if (hasPhotos) 1 else 0) to -seg.first
            val prev = groups[code]
            if (prev == null || compareValuesBy(score, prev.first, { it.first }, { it.second }) > 0) groups[code] = score to g
        }
    }
    return groups.mapValues { it.value.second }
}

/**
 * Model Detect (tools/model_detect): finds each code's photos in WhatsApp and writes the phone model
 * next to the code. Reads written names (chat text, text in the photos) for free; can ask Claude.
 */
class ModelDetectTool(private val scope: CoroutineScope) {
    class Result(val code: String) {
        var model by mutableStateOf("")
        var confidence by mutableStateOf("")
        var note by mutableStateOf("")
        var images by mutableStateOf(0)
        var chat by mutableStateOf("")
        var done by mutableStateOf(false)
        var photoNames by mutableStateOf<List<String>>(emptyList())
    }

    private val prefs = Storage.prefs("model_detect")
    var codeText by mutableStateOf("")
    var mode by mutableStateOf(prefs.getString("mode", "below")!!)
    var maxImages by mutableStateOf(prefs.getInt("max_images", 4))
    var lookbackDays by mutableStateOf(prefs.getInt("lookback_days", 30))
    var engine by mutableStateOf(prefs.getString("engine", "text")!!)   // text | claude | none
    var apiKey by mutableStateOf(prefs.getString("api_key", "")!!)
    var model by mutableStateOf(prefs.getString("model", ClaudeIdentifier.DEFAULT_MODEL)!!)
    var effort by mutableStateOf(prefs.getString("effort", "high")!!)
    var savePhotos by mutableStateOf(prefs.getBoolean("save_photos", true))
    var skipDone by mutableStateOf(prefs.getBoolean("skip_done", true))
    /** code group (prefix) -> WhatsApp chat */
    val chatFor = mutableStateMapOf<String, WaChat>()
    val results = mutableStateListOf<Result>()
    val log = ToolLog()
    var running by mutableStateOf(false)
        private set
    var activity by mutableStateOf("")
        private set
    var progress by mutableStateOf(0f)
        private set
    private var job: Job? = null
    private var list: ProductCodes.CodeList? = null
    private var ai: ClaudeIdentifier? = null

    fun save() {
        prefs.edit().putString("mode", mode).putInt("max_images", maxImages).putInt("lookback_days", lookbackDays)
            .putString("engine", engine).putString("api_key", apiKey).putString("model", model).putString("effort", effort)
            .putBoolean("save_photos", savePhotos).putBoolean("skip_done", skipDone).apply()
        for ((p, c) in chatFor) prefs.edit().putString("chat_$p", c.title).apply()
    }

    fun groups(): Map<String, List<String>> = ProductCodes.CodeList(codeText).blocks

    /** The group's chat in the shop settings is matched by itself (the orders chat for codes without a
     *  prefix, like #1023), else the one chosen last time, else a chat whose name contains the group code
     *  ("(5a-)", "5a-"). */
    fun autoMatch(chats: List<WaChat>) {
        for (p in groups().keys) {
            if (p in chatFor) continue
            val given = (if (p.isEmpty()) Shop.get("orders_chat") as? String else Shop.chats()[p]).orEmpty().trim()
            val remembered = prefs.getString("chat_$p", null)
            val hit = chats.firstOrNull { given.isNotEmpty() && it.title.trim().equals(given, ignoreCase = true) }
                ?: chats.firstOrNull { it.title == remembered }
                ?: chats.firstOrNull { p.isNotEmpty() && it.title.lowercase().contains("($p-)") }
                ?: chats.firstOrNull { p.isNotEmpty() && Regex("(?<![0-9a-z])${Regex.escape(p)}-").containsMatchIn(it.title.lowercase()) }
            if (hit != null) chatFor[p] = hit
        }
    }

    fun resultText(): String {
        val l = list ?: ProductCodes.CodeList(codeText)
        val models = l.existing().toMutableMap()
        for (r in results) if (r.model.isNotEmpty()) models[r.code] = r.model
        return l.write(models)
    }

    fun stop() {
        job?.cancel()
    }

    fun start() {
        if (running) return
        save()
        val l = ProductCodes.CodeList(codeText)
        list = l
        if (l.codes.isEmpty()) {
            log.add("Paste your code list first.")
            return
        }
        results.clear()
        running = true
        Jobs.begin("model_detect", "Model Detect: starting…")
        job = scope.launch {
            try {
                WaEngine.awaitReady { activity = it }
                WaEngine.withTurn("Model Detect", { activity = it }) { run(l) }
                log.add("All done.")
            } catch (e: CancellationException) {
                log.add("Stopped by user.")
            } catch (e: Exception) {
                log.add("Error: ${e.message}")
            } finally {
                running = false
                activity = ""
                Jobs.end("model_detect")
            }
        }
    }

    private suspend fun run(l: ProductCodes.CodeList) {
        val reader = if (engine != "none") TextIdentifier { log.add(it) } else null
        ai = null
        if (engine == "claude") {
            if (apiKey.isNotBlank()) {
                ai = ClaudeIdentifier(apiKey.trim(), model, effort)
                log.add("Photos with no written name go to $model (effort $effort).")
            } else log.add("No Claude API key - only written model names will be used.")
        }
        val existing = l.existing()
        val since = ZonedDateTime.now(ZoneId.systemDefault()).minusDays(lookbackDays.toLong()).toEpochSecond()
        val total = l.codes.size.coerceAtLeast(1)
        var doneCount = 0
        for ((prefix, codes0) in l.blocks) {
            val codes = if (skipDone) codes0.filter { !ProductCodes.isDone(existing[it]) } else codes0
            if (codes.isEmpty()) {
                doneCount += codes0.size
                continue
            }
            val chat = chatFor[prefix]
            val rows = codes.map { Result(it).also { r -> results += r } }
            if (chat == null) {
                rows.forEach { it.note = "no chat chosen for group $prefix"; it.done = true }
                log.add("Group $prefix: choose its WhatsApp chat first.")
                continue
            }
            log.add("=== ${chat.title}: ${codes.size} codes ===")
            activity = "Reading ${chat.title}…"
            Jobs.progress("model_detect", "Model Detect: reading ${chat.title}")
            val res = WaEngine.call("window.__wab.messages(${WaEngine.quote(chat.id)}, ${jsonOf("since" to since, "maxLoads" to 400)})",
                30 * 60_000L) as? JSONObject ?: JSONObject()
            if (res.has("error")) {
                log.add("${chat.title}: ${res.optString("error")}")
                rows.forEach { it.note = res.optString("error"); it.done = true }
                continue
            }
            val ranges = mapOf(prefix to l.prefixRange(prefix))
            val msgs = res.optJSONArray("messages").objects().map {
                val text = it.optString("text")
                val img = it.optBoolean("image")
                Msg(it.optString("id"), text, if (img) 1 else 0, markerCodes(text, img, ranges))
            }
            val found = msgs.flatMap { it.codes }.toSet().intersect(codes.toSet())
            log.add("  ${msgs.size} messages read, ${found.size}/${codes.size} codes found.")
            val groups = buildGroups(msgs, found, mode, maxImages)
            for (r in rows.reversed()) {   // bottom of the list first
                doneCount++
                progress = doneCount.toFloat() / total
                r.chat = chat.title
                val g = groups[r.code]
                if (g == null) {
                    r.model = "NOT FOUND"
                    r.note = if (res.optBoolean("reached")) "code message not found in this chat"
                    else "older than what WhatsApp Web loaded - raise 'Look back' and run again"
                    r.done = true
                    continue
                }
                r.note = "collecting photos…"
                activity = "${r.code}: collecting photos"
                val images = mutableListOf<ByteArray>()
                for (id in g.imageIds) {
                    if (images.size >= maxImages) break
                    val d = try {
                        WaEngine.call("window.__waf_basic.download(${WaEngine.quote(id)})", 180_000) as? JSONObject
                    } catch (e: WaError) {
                        null
                    } ?: continue
                    val data = d.optString("data")
                    if (data.isEmpty()) continue
                    prepareImage(Base64.decode(data, Base64.DEFAULT))?.let { images += it }
                }
                if (images.isEmpty()) {
                    r.model = "NO IMAGE"
                    r.note = "code found ('${g.markerText.take(40)}') but no photos next to it"
                    r.done = true
                    continue
                }
                r.images = images.size
                if (savePhotos) {
                    val rel = Storage.picturesPath("Model Detect/${r.code}")
                    val names = mutableListOf<String>()
                    images.forEachIndexed { i, img ->
                        val name = "photo${i + 1}.jpg"
                        Storage.delete(rel, name)
                        Storage.save(rel, name, img)
                        names += name
                    }
                    r.photoNames = names
                }
                if (reader == null) {
                    r.note = "photos saved"
                    r.done = true
                    continue
                }
                r.note = "reading…"
                identify(r, reader, images, g)
            }
        }
    }

    private suspend fun identify(r: Result, reader: TextIdentifier, images: List<ByteArray>, g: Group) {
        val ai = ai
        try {
            val seen = mutableListOf<String>()
            var id = reader.identify(images, g.near, g.shared, seen)
            // nothing written, only the brand logo ("redmi ?") or an unsure reading: let the AI look at the phone
            if ((id == null || id.confidence == "low") && ai != null) {
                val context = Regex("\\d{6,}").replace((g.near + g.context).distinct().joinToString("\n"), "#").take(800)
                var hint = if (id != null) "\nRead so far (not sure): ${id.model.trimEnd(' ', '?')} - ${id.reason}" else ""
                if (seen.isNotEmpty()) hint += "\nText printed in the photos: " + seen.distinct().joinToString(" | ").take(400)
                r.note = "asking Claude…"
                val answer = ai.identify(r.code, images, context + hint)
                if (id == null || answer.model != "unknown") id = answer
            }
            if (id == null) {
                r.note = "no model name written - see the photos"
                log.add("  ${r.code}: no model name found")
            } else {
                r.model = id.model
                r.confidence = id.confidence
                r.note = id.reason
                log.add("  ${r.code}: ${id.model} (${id.confidence})")
            }
        } catch (e: ClaudeKeyError) {
            log.add("Claude API refused the key or model - identification with Claude is off. (${e.message})")
            this.ai = null
            r.note = "API key / model error"
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            r.note = "error: ${e.message}".take(200)
            log.add("  ${r.code}: ${e.message}")
        } finally {
            r.done = true
        }
    }
}
