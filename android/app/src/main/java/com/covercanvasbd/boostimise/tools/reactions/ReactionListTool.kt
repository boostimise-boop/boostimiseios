package com.covercanvasbd.boostimise.tools.reactions

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import com.covercanvasbd.boostimise.core.Codes
import com.covercanvasbd.boostimise.core.OrderParser
import com.covercanvasbd.boostimise.core.ProductCode
import com.covercanvasbd.boostimise.core.Storage
import com.covercanvasbd.boostimise.core.bnToAscii
import com.covercanvasbd.boostimise.core.jsonOf
import com.covercanvasbd.boostimise.core.objects
import com.covercanvasbd.boostimise.core.re
import com.covercanvasbd.boostimise.tools.fetch.ChatFilter
import com.covercanvasbd.boostimise.tools.fetch.ToolLog
import com.covercanvasbd.boostimise.wa.Jobs
import com.covercanvasbd.boostimise.wa.WaEngine
import com.covercanvasbd.boostimise.wa.WaError
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject
import java.time.ZoneId
import java.time.format.DateTimeFormatter

/** The reaction choices (tools/reaction_list/helpers.py). 😢 counts as 😥. */
data class Choice(val key: String, val label: String, val emojis: List<String>)

val CHOICES = listOf(
    Choice("x", "❌", listOf("❌")),
    Choice("nophone", "📵", listOf("📵")),
    Choice("sad", "😥", listOf("😥", "😢")),
    Choice("all", "❌ 📵 😥", listOf("❌", "📵", "😥", "😢")),
)

object ReactionCodes {
    // A date such as 16-10-2026 or 2026-10-16 is not a code.
    private val DATE = re("(?<![0-9A-Za-z])(?:(?:[12]?\\d|3[01])[-/.](?:0?[1-9]|1[0-2])[-/.](?:\\d{4}|\\d{2})" +
        "|\\d{4}[-/.]\\d{1,2}[-/.]\\d{1,2})(?![0-9A-Za-z])")
    // One code of a list: "2-558", "2:558+", "5A_380" ...
    private val CODE = re("\\s*(" + ProductCode.PREFIX + ")\\s*" + ProductCode.SEP + "\\s*(\\d{1,6})[\\s+\\-]*", true)
    private const val MAX_CODES = 3

    fun normalize(code: String): String {
        val m = CODE.matchEntire(code) ?: return code.trim().lowercase()
        return OrderParser.normalizeCode(m.groupValues[1], m.groupValues[2])
    }

    /** The codes a reacted message is about, if it names no more than MAX_CODES. */
    fun messageCodes(text: String): List<String> {
        val written = ProductCode.codesInText(DATE.replace(bnToAscii(text), " "))
        val codes = (OrderParser.findCodes(text) + written).distinct()
        return if (codes.size <= MAX_CODES) codes else emptyList()
    }

    /** 2-558 < 2-560 < 3-12 < 5a-380. */
    val comparator = Comparator<String> { a, b -> key(a).compareTo(key(b)) }

    private data class K(val n: Int, val s: String, val m: Int) : Comparable<K> {
        override fun compareTo(other: K) = compareValuesBy(this, other, { it.n }, { it.s }, { it.m })
    }

    private fun key(code: String): K {
        val m = CODE.matchEntire(code) ?: return K(1_000_000, code, 0)
        val p = Regex("(\\d+)(.*)").matchEntire(m.groupValues[1])!!
        return K(p.groupValues[1].toInt(), p.groupValues[2].lowercase(), m.groupValues[2].toInt())
    }
}

/**
 * Reaction List (tools/reaction_list): the codes whose messages got ❌, 📵 or 😥, with the phone
 * number from the order message. Empty code box = every reacted code in the chats read.
 */
class ReactionListTool(private val scope: CoroutineScope) {
    class Found(val code: String, val name: String, val order: Int) {
        val emojis = mutableSetOf<String>()
        val phones = mutableListOf<String>()
        val chats = mutableListOf<String>()
        var t = 0L
        fun react(wanted: List<String>) = wanted.filter { it in emojis }.joinToString("")
    }

    private val prefs = Storage.prefs("reaction_list")
    var codeText by mutableStateOf("")
    var choice by mutableStateOf(CHOICES.firstOrNull { it.key == prefs.getString("choice", "all") } ?: CHOICES[3])
    var lookbackDays by mutableStateOf(prefs.getInt("lookback_days", 10))
    var chat by mutableStateOf(ChatFilter(prefs.getString("chat_id", "")!!, prefs.getString("chat_title", "")!!,
        prefs.getBoolean("groups_only", false)))
    val results = mutableStateListOf<Found>()
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

    private fun save() {
        prefs.edit().putString("choice", choice.key).putInt("lookback_days", lookbackDays).putString("chat_id", chat.id)
            .putString("chat_title", chat.title).putBoolean("groups_only", chat.groupsOnly).apply()
    }

    fun stop() {
        job?.cancel()
    }

    /** '1. 2-558 Realme C85 ❌ 01734484040', one code per line. */
    fun formatList(): String = results.mapIndexed { n, f ->
        "${n + 1}. " + listOf(f.code, f.name, f.react(choice.emojis), f.phones.joinToString(", ")).filter { it.isNotEmpty() }.joinToString(" ")
    }.joinToString("\n") + if (results.isNotEmpty()) "\n" else ""

    fun start() {
        if (running) return
        save()
        results.clear()
        summary = ""
        running = true
        Jobs.begin("reaction_list", "Reaction List: starting…")
        job = scope.launch {
            try {
                WaEngine.awaitReady { activity = it }
                WaEngine.withTurn("Reaction List", { activity = it }) { run() }
            } catch (e: CancellationException) {
                log.add("Stopped by user.")
                summary = "Stopped."
            } catch (e: Exception) {
                log.add("Error: ${e.message}")
                summary = "Error: ${e.message}"
            } finally {
                running = false
                activity = ""
                Jobs.end("reaction_list")
            }
        }
    }

    private suspend fun run() {
        val started = System.currentTimeMillis()
        val items = Codes.parseCodeList(codeText)
        val listed = items.withIndex().associate { (i, it) -> ReactionCodes.normalize(it.code) to (i + 1 to it) }
        val wanted = listed.keys
        val reactions = choice.emojis.joinToString("")
        val since = Codes.scanSince(items, lookbackDays)
        val sinceTs = since.atZone(ZoneId.systemDefault()).toEpochSecond()
        val targets = (WaEngine.call("window.__waf_reactions.scanTargets(${chat.toJs(sinceTs)})") as? JSONArray).objects()
        val what = if (items.isNotEmpty()) "${items.size} code(s)" else "every code"
        log.add("Reading ${targets.size} chat(s) active since ${since.format(DateTimeFormatter.ofPattern("dd/MM/yyyy"))}: $what reacted with $reactions…")
        val opts = jsonOf("since" to sinceTs, "reactions" to reactions, "gapMinutes" to 5, "maxLoads" to 300)
        val byCode = linkedMapOf<String, Found>()
        var noCode = 0
        var orphans = 0
        for ((n, target) in targets.withIndex()) {
            val title = target.optString("title").ifEmpty { target.optString("id") }
            activity = "Reading chat ${n + 1}/${targets.size}: $title"
            Jobs.progress("reaction_list", "Reaction List: $activity")
            val res = try {
                WaEngine.call("window.__waf_reactions.reactedMessages(${WaEngine.quote(target.optString("id"))}, $opts)",
                    30 * 60_000L) as? JSONObject
            } catch (e: WaError) {
                log.add("$title: ${e.message}")
                null
            } ?: JSONObject()
            progress = (n + 1).toFloat() / targets.size.coerceAtLeast(1)
            if (res.has("error")) {
                log.add("$title: ${res.optString("error")}")
                continue
            }
            res.optJSONArray("warnings")?.let { w -> for (k in 0 until w.length()) log.add("$title: ${w.optString(k)}") }
            orphans += res.optInt("orphans")
            val newCodes = sortedSetOf(ReactionCodes.comparator)
            for (hit in res.optJSONArray("hits").objects()) {
                val text = hit.optString("text")
                val inText = ReactionCodes.messageCodes(text)
                if (inText.isEmpty()) noCode++
                val phones = OrderParser.findPhones(text)
                for (code in inText) {
                    if (wanted.isNotEmpty() && code !in wanted) continue
                    val (order, item) = listed[code] ?: (0 to null)
                    val f = byCode.getOrPut(code) { Found(item?.code ?: code, item?.name ?: "", order) }
                    hit.optString("react").let { r -> splitEmoji(r).forEach { f.emojis += it } }
                    phones.forEach { if (it !in f.phones) f.phones += it }
                    val chatName = hit.optString("chat").ifEmpty { title }
                    if (chatName !in f.chats) f.chats += chatName
                    f.t = maxOf(f.t, hit.optLong("t"))
                    newCodes += code
                }
            }
            if (newCodes.isNotEmpty()) {
                log.add("$title: ${newCodes.joinToString(", ")}")
                val byList: Comparator<Found> = compareBy { it.order }
                val byCodeOrder = Comparator<Found> { a, b ->
                    ReactionCodes.comparator.compare(ReactionCodes.normalize(a.code), ReactionCodes.normalize(b.code))
                }
                val sorted = byCode.values.sortedWith(if (wanted.isNotEmpty()) byList else byCodeOrder)
                results.clear()
                results.addAll(sorted)
            }
        }
        if (noCode > 0) log.add("$noCode reacted message(s) name no code (left out).")
        if (orphans > 0) log.add("$orphans reacted photo(s) have no code written before them (left out).")
        val secs = (System.currentTimeMillis() - started) / 1000
        summary = if (items.isNotEmpty()) "Done in ${secs}s: ${byCode.size} of ${wanted.size} code(s) reacted with $reactions."
        else "Done in ${secs}s: ${byCode.size} code(s) reacted with $reactions."
        log.add(summary)
    }

    private fun splitEmoji(s: String): List<String> {
        val out = mutableListOf<String>()
        val it = java.text.BreakIterator.getCharacterInstance()
        it.setText(s)
        var start = it.first()
        var end = it.next()
        while (end != java.text.BreakIterator.DONE) {
            out += s.substring(start, end)
            start = end
            end = it.next()
        }
        return out
    }
}
