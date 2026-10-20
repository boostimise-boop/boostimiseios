package com.boostimise.app.tools.fetch

import android.util.Base64
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import com.boostimise.app.core.CodeItem
import com.boostimise.app.core.Codes
import com.boostimise.app.core.ProductCode
import com.boostimise.app.core.Storage
import com.boostimise.app.core.jsonOf
import com.boostimise.app.core.objects
import com.boostimise.app.wa.Jobs
import com.boostimise.app.wa.WaEngine
import com.boostimise.app.wa.WaError
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject
import java.security.MessageDigest
import java.time.ZoneId
import java.time.format.DateTimeFormatter

/** The log of a tool, newest line last (kept short). */
class ToolLog {
    val lines = mutableStateListOf<String>()
    fun add(s: String) {
        for (line in s.lines()) lines += line
        while (lines.size > 400) lines.removeAt(0)
    }
    fun clear() = lines.clear()
}

/** Chat filter: all chats, all groups, one chat (by id) or chats whose name contains a text. */
data class ChatFilter(val id: String = "", val title: String = "", val groupsOnly: Boolean = false) {
    fun toJs(since: Long) = jsonOf("since" to since, "chatId" to id, "chatTitle" to title, "groupsOnly" to groupsOnly)
    val label get() = when {
        groupsOnly -> "All groups"
        id.isNotEmpty() || title.isNotEmpty() -> title
        else -> "All chats"
    }
}

val NEIGHBOUR_MODES = listOf(
    "after" to "Captioned with the code + uncaptioned ones sent right after",
    "none" to "Only photos whose caption contains the code",
    "before" to "Captioned with the code + uncaptioned ones sent right before",
    "both" to "Captioned with the code + uncaptioned ones before and after",
)

/** The list a code belongs to: "6-" for 6-377, "5a-" for 5A-200, "cc-" for CC1023 ("" if it has no list
 *  prefix, like #1023), in the shop's code style. */
fun listOfCode(code: String): String =
    ProductCode.reader().codeParts(code)?.first?.takeIf { it.isNotEmpty() }?.let { "$it-" } ?: ""

/** True if a chat name is for this list, e.g. "My Shop (6-)" for "6-" (match_renamer's group_in_name). */
fun listInName(list: String, name: String): Boolean =
    list.length > 1 && Regex("(?<![0-9A-Za-z])" + Regex.escape(list.dropLast(1)) + "\\s*-(?!\\s*\\d)", RegexOption.IGNORE_CASE)
        .containsMatchIn(name)

/** The chat of each list, as saved in a tool's settings ("6-" -> chat). */
fun listChatsFromJson(json: String?): Map<String, ChatFilter> {
    val saved = runCatching { JSONObject(json ?: "{}") }.getOrElse { JSONObject() }
    return saved.keys().asSequence().mapNotNull { k ->
        saved.optJSONObject(k)?.let { k to ChatFilter(it.optString("id"), it.optString("title")) }
    }.toMap()
}

fun listChatsToJson(chats: Map<String, ChatFilter>): String {
    val out = JSONObject()
    for ((k, f) in chats) out.put(k, jsonOf("id" to f.id, "title" to f.title))
    return out.toString()
}

/** Gives each list without a (still existing) chat the chat named for it ("… (6-)"), the most recent one if several. */
fun autoFillListChats(lists: Collection<String>, into: MutableMap<String, ChatFilter>) {
    val chats = WaEngine.chats
    if (chats.isEmpty()) return
    for (list in lists) {
        val now = into[list]
        if (now != null && chats.any { it.id == now.id }) continue
        val match = chats.filter { listInName(list, it.title) }.maxByOrNull { it.t } ?: continue
        into[list] = ChatFilter(match.id, match.title)
    }
}

/**
 * Fetch by Code (tools/whatsapp_fetch): finds the photos of each code in the
 * WhatsApp chats and saves them as "2-558 Realme C85.jpg" in Pictures/Boostimise/<tool>.
 */
class FetchTool(val key: String, val title: String, private val scope: CoroutineScope) {
    class Row(val item: CodeItem) {
        var kind by mutableStateOf("")       // "" | working | ok | none | error | skipped
        var text by mutableStateOf("")
        var saved by mutableStateOf("")
    }

    private val prefs = Storage.prefs(key)
    var codeText by mutableStateOf("")
    val rows = mutableStateListOf<Row>()
    val log = ToolLog()
    var running by mutableStateOf(false)
        private set
    var activity by mutableStateOf("")
        private set
    var progress by mutableStateOf(0f)
        private set
    var summary by mutableStateOf("")
        private set
    private var job: Job? = null

    // settings (remembered)
    var folder by mutableStateOf(prefs.getString("folder", title)!!)
    var chat by mutableStateOf(ChatFilter(prefs.getString("chat_id", "")!!, prefs.getString("chat_title", "")!!,
        prefs.getBoolean("groups_only", false)))
    var neighbors by mutableStateOf(prefs.getString("neighbors", "after")!!)
    var gapMinutes by mutableStateOf(prefs.getInt("gap_minutes", 5))
    var lookbackDays by mutableStateOf(prefs.getInt("lookback_days", 10))
    var includeDate by mutableStateOf(prefs.getBoolean("include_date", false))
    var skipExisting by mutableStateOf(prefs.getBoolean("skip_existing", true))
    /** Each list in its own chat: the 6- codes in "My Shop (6-)", the 5a- codes in … */
    var perList by mutableStateOf(prefs.getBoolean("per_list", false))
    /** The chat of each list ("6-" -> chat); a list without one is searched in [chat]. */
    val listChats = mutableStateMapOf<String, ChatFilter>().apply { putAll(listChatsFromJson(prefs.getString("list_chats", "{}"))) }

    val relPath get() = Storage.picturesPath(folder.ifBlank { title })

    fun saveSettings() {
        prefs.edit().putString("folder", folder).putString("chat_id", chat.id).putString("chat_title", chat.title)
            .putBoolean("groups_only", chat.groupsOnly).putString("neighbors", neighbors).putInt("gap_minutes", gapMinutes)
            .putInt("lookback_days", lookbackDays).putBoolean("include_date", includeDate)
            .putBoolean("skip_existing", skipExisting).putBoolean("per_list", perList)
            .putString("list_chats", listChatsToJson(listChats)).apply()
    }

    /** The lists in the code list, in order, with how many codes each has: [("6-", 7), ("5a-", 22), …]. */
    fun lists(): List<Pair<String, Int>> =
        rows.groupingBy { listOfCode(it.item.code) }.eachCount().filterKeys { it.isNotEmpty() }.toList()

    /** Where a list's codes are searched. */
    fun chatFor(list: String): ChatFilter = if (perList) listChats[list] ?: chat else chat

    /** Gives each list without a chat the chat named for it ("… (6-)"), the most recent one if several. */
    fun autoFillListChats() = autoFillListChats(lists().map { it.first }, listChats)

    fun parse() {
        val items = Codes.parseCodeList(codeText)
        val old = rows.associateBy { it.item }
        rows.clear()
        rows.addAll(items.map { old[it] ?: Row(it) })
    }

    fun stop() {
        job?.cancel()
    }

    fun start(only: List<Row>? = null) {
        if (running) return
        parse()
        if (rows.isEmpty()) {
            summary = "Paste your code list first."
            return
        }
        saveSettings()
        val todoRows = only ?: rows.toList()
        todoRows.forEach { it.kind = ""; it.text = "Queued"; it.saved = "" }
        running = true
        summary = ""
        Jobs.begin(key, "$title: starting…")
        job = scope.launch {
            try {
                WaEngine.awaitReady { activity = it; Jobs.progress(key, "$title: $it") }
                WaEngine.withTurn(title, { activity = it }) { runJob(todoRows) }
            } catch (e: CancellationException) {
                log.add("Stopped by user.")
                summary = "Stopped."
            } catch (e: Exception) {
                log.add("Error: ${e.message}")
                summary = "Error: ${e.message}"
            } finally {
                for (r in todoRows) if (r.kind == "working" || r.text == "Queued") {
                    r.kind = ""
                    r.text = "Stopped"
                }
                running = false
                activity = ""
                Jobs.end(key)
            }
        }
    }

    private suspend fun runJob(todoRows: List<Row>) {
        val started = System.currentTimeMillis()
        var ok = 0
        var none = 0
        var skipped = 0
        var errors = 0
        var files = 0
        val existing = Storage.namesIn(relPath)
        val todo = mutableListOf<Pair<Row, String>>()
        for (r in todoRows) {
            val base = r.item.baseName(includeDate)
            val prev = if (skipExisting) Codes.existingOutput(existing, base) else null
            if (prev != null) {
                r.kind = "skipped"
                r.text = "Already saved"
                r.saved = prev
                skipped++
            } else todo += r to base
        }
        if (todo.isNotEmpty()) {
            val scan = scan(todo)
            for ((i, pair) in todo.withIndex()) {
                val (row, base) = pair
                val images = scan.found[i.toString()].orEmpty().values.sortedBy { it.optLong("t") }
                val (kind, n) = try {
                    downloadAndSave(row, base, images, scan.mentions[i.toString()] ?: 0, scan.since)
                } catch (e: CancellationException) {
                    throw e
                } catch (e: Exception) {
                    row.kind = "error"
                    row.text = "Error: ${e.message}"
                    log.add("[${row.item.code}] error: ${e.message}")
                    "error" to 0
                }
                when (kind) {
                    "ok" -> ok++
                    "none" -> none++
                    else -> errors++
                }
                files += n
                progress = (scan.chats + i + 1).toFloat() / (scan.chats + todo.size)
            }
        }
        val secs = (System.currentTimeMillis() - started) / 1000
        summary = "Done in ${secs}s: $ok code(s) saved ($files images), $none not found, $skipped skipped, $errors error(s)."
        log.add(summary)
    }

    private class Scan(val found: Map<String, Map<String, JSONObject>>, val mentions: Map<String, Int>,
                       val since: java.time.LocalDateTime, val chats: Int)

    private suspend fun scan(todo: List<Pair<Row, String>>): Scan {
        val since = Codes.scanSince(todo.map { it.first.item }, lookbackDays)
        val sinceTs = since.atZone(ZoneId.systemDefault()).toEpochSecond()
        val api = "window.__waf_basic"
        val dd = DateTimeFormatter.ofPattern("dd/MM/yyyy")
        // One pass per chat filter: all codes in [chat], or with [perList] each list's codes in its own chat.
        val passes = todo.indices.groupBy { chatFor(listOfCode(todo[it].first.item.code)) }.map { (filter, keys) ->
            val targets = (WaEngine.call("$api.scanTargets(${filter.toJs(sinceTs)})") as? JSONArray).objects()
            Triple(filter, keys, targets)
        }
        val chatCount = passes.sumOf { it.third.size }
        val total = chatCount + todo.size
        progress = 0f
        log.add("Reading $chatCount chat(s) active since ${since.format(dd)} for ${todo.size} code(s)…")
        todo.forEach { (r, _) -> r.kind = "working"; r.text = "Reading chats…" }

        val found = mutableMapOf<String, MutableMap<String, JSONObject>>()
        val mentions = mutableMapOf<String, Int>()
        var short = 0
        // (chat, the options with that pass's codes) for every chat to read
        val reads = passes.flatMap { (filter, keys, targets) ->
            if (perList) {
                val names = keys.map { listOfCode(todo[it].first.item.code).ifEmpty { "other" } }.distinct()
                log.add("${names.joinToString(", ")} (${keys.size} code(s)) → ${filter.label}" +
                    if (targets.isEmpty()) ": not active since ${since.format(dd)}" else "")
            }
            val codes = JSONArray()
            for (i in keys) codes.put(jsonOf("key" to i.toString(), "pattern" to Codes.codePattern(todo[i].first.item.code)))
            val opts = ProductCode.scanPatterns(jsonOf("codes" to codes, "since" to sinceTs, "neighbors" to neighbors,
                "gapMinutes" to gapMinutes, "maxNeighbors" to 30, "maxLoads" to 300))
            targets.map { it to opts }
        }
        for ((n, read) in reads.withIndex()) {
            val (target, opts) = read
            val t = target.optString("title").ifEmpty { target.optString("id") }
            activity = "Reading chat ${n + 1}/$chatCount: $t"
            Jobs.progress(key, "$title: $activity")
            val res = try {
                WaEngine.call("$api.scanChat(${WaEngine.quote(target.optString("id"))}, $opts)", 30 * 60_000L) as? JSONObject
            } catch (e: WaError) {
                log.add("$t: ${e.message}")
                null
            } ?: JSONObject()
            if (res.has("error")) {
                log.add("$t: ${res.optString("error")}")
                continue
            }
            res.optJSONArray("warnings")?.let { w -> for (k in 0 until w.length()) log.add("$t: ${w.optString(k)}") }
            val hits = res.optJSONObject("hits") ?: JSONObject()
            val hitKeys = hits.keys().asSequence().toList()
            for (k in hitKeys) {
                val bucket = found.getOrPut(k) { linkedMapOf() }
                for (img in hits.optJSONArray(k).objects()) bucket.putIfAbsent(img.optString("id"), img)
                val row = todo[k.toInt()].first
                row.text = "Reading chats… ${bucket.size} photo(s) found"
            }
            res.optJSONObject("mentions")?.let { m -> m.keys().forEach { mentions[it] = (mentions[it] ?: 0) + m.optInt(it) } }
            if (hitKeys.isNotEmpty()) log.add("$t: photos for ${hitKeys.joinToString(", ") { todo[it.toInt()].first.item.code }}")
            if (res.optInt("loaded") > 0 && !res.optBoolean("reached") && res.optBoolean("exhausted")) short++
            progress = (n + 1).toFloat() / total
        }
        if (short > 0) log.add("Note: $short chat(s) have no older messages on WhatsApp Web (before ${since.format(dd)}).")
        return Scan(found, mentions, since, chatCount)
    }

    private suspend fun downloadAndSave(row: Row, base: String, images: List<JSONObject>, mentions: Int,
                                        since: java.time.LocalDateTime): Pair<String, Int> {
        val code = row.item.code
        if (images.isEmpty()) {
            val detail = when {
                mentions > 0 -> "code is in $mentions message(s), none with a photo"
                else -> "not in any chat since ${since.format(DateTimeFormatter.ofPattern("dd/MM"))}"
            }
            row.kind = "none"
            row.text = "No photos ($detail)"
            log.add("[$code] no photos: $detail.")
            return "none" to 0
        }
        val chats = images.map { it.optString("chat").ifEmpty { "?" } }.toSortedSet()
        val marks = images.mapNotNull { it.optString("react").takeIf { r -> r.isNotEmpty() } }.toSortedSet().joinToString("")
        log.add("[$code] ${images.size} photo(s) in: ${chats.joinToString(", ")}" + if (marks.isNotEmpty()) "  (reacted $marks)" else "")
        val files = mutableListOf<Pair<ByteArray, String>>()
        val hashes = mutableSetOf<String>()
        var failed = 0
        for ((i, img) in images.withIndex()) {
            row.kind = "working"
            row.text = "Downloading ${i + 1}/${images.size}…"
            activity = "[$code] downloading ${i + 1}/${images.size}"
            val res = try {
                WaEngine.call("window.__waf_basic.download(${WaEngine.quote(img.optString("id"))})", 180_000) as? JSONObject
            } catch (e: WaError) {
                jsonOf("error" to e.message)
            } ?: JSONObject()
            val data = res.optString("data")
            if (res.has("error") || data.isEmpty()) {
                failed++
                log.add("[$code] photo ${i + 1} failed: ${res.optString("error").ifEmpty { "no data" }}")
                continue
            }
            val bytes = Base64.decode(data, Base64.DEFAULT)
            val digest = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }
            if (!hashes.add(digest)) continue // same picture posted / forwarded twice
            files += bytes to Codes.extFor(res.optString("mimetype"), bytes, res.optString("filename"))
        }
        if (files.isEmpty()) {
            row.kind = "error"
            row.text = "Found ${images.size}, download failed"
            return "error" to 0
        }
        // one photo -> "<base>.jpg"; several -> "<base> 1.jpg", "<base> 2.jpg", ...
        var first = ""
        files.forEachIndexed { i, (bytes, ext) ->
            val name = if (files.size == 1) "$base$ext" else "$base ${i + 1}$ext"
            Storage.delete(relPath, name)
            Storage.save(relPath, name, bytes)
            if (i == 0) first = name
        }
        var text = "Saved ${files.size} image" + if (files.size > 1) "s" else ""
        if (failed > 0) text += " ($failed failed)"
        row.kind = "ok"
        row.text = text
        row.saved = first
        log.add("[$code] $text → $relPath$first")
        return "ok" to files.size
    }
}
