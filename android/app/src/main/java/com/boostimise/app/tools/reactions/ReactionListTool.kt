package com.boostimise.app.tools.reactions

import android.util.Base64
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import com.boostimise.app.core.CodeItem
import com.boostimise.app.core.Codes
import com.boostimise.app.core.OrderParser
import com.boostimise.app.core.Shop
import com.boostimise.app.core.ShopReaction
import com.boostimise.app.core.ProductCode
import com.boostimise.app.core.Storage
import com.boostimise.app.core.bnToAscii
import com.boostimise.app.core.jsonOf
import com.boostimise.app.core.objects
import com.boostimise.app.core.re
import com.boostimise.app.tools.fetch.ChatFilter
import com.boostimise.app.tools.fetch.ToolLog
import com.boostimise.app.tools.fetch.autoFillListChats
import com.boostimise.app.tools.fetch.listChatsFromJson
import com.boostimise.app.tools.fetch.listChatsToJson
import com.boostimise.app.tools.fetch.listOfCode
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

/** The reaction buttons (tools/reaction_list/helpers.py): one per reaction of the shop settings,
 *  All and Custom. 😢 counts as 😥. Custom = the emojis typed for it. */
data class Choice(val key: String, val label: String, val emojis: List<String>, val meaning: String = "")

const val CUSTOM = "custom"
const val ALL = "all"
private val OLD_KEYS = mapOf("x" to "❌", "nophone" to "📵", "sad" to "😥")  // the saved choice up to 4.4
private val ALIASES = mapOf("😥" to listOf("😢"))

/** The emojis looked for: each one, and right after it the reactions that count as it (😥 → 😢). */
fun withAliases(emojis: List<String>): List<String> =
    emojis.flatMap { listOf(it) + (ALIASES[it] ?: emptyList()) }.distinct()

fun makeChoices(reactions: List<ShopReaction>): List<Choice> = buildList {
    for (r in reactions) add(Choice(r.emoji, r.emoji, withAliases(listOf(r.emoji)), r.label))
    if (reactions.size > 1) add(Choice(ALL, reactions.joinToString(" ") { it.emoji }, withAliases(reactions.map { it.emoji })))
    add(Choice(CUSTOM, "Custom", emptyList()))
}

/** {"❌": "Cancelled", …}, also for the reactions that count as one of them (😢 as 😥). */
fun reactionLabels(reactions: List<ShopReaction>): Map<String, String> = buildMap {
    for (r in reactions) if (r.label.isNotEmpty()) for (e in withAliases(listOf(r.emoji))) if (e !in this) put(e, r.label)
}

object ReactionEmojis {
    // As wa_bridge_reactions.js reads reactions: '❤️' and '❤', '👍🏻' and '👍' are the same reaction.
    private val MARKS = Regex("[\\uFE0E\\uFE0F\\x{1F3FB}-\\x{1F3FF}]")

    /** '👍 ❤️,🔥' -> [👍, ❤, 🔥]: one entry per emoji, without skin tones and variation marks, each once. */
    fun split(s: String): List<String> {
        val text = MARKS.replace(s, "")
        val out = linkedSetOf<String>()
        val it = java.text.BreakIterator.getCharacterInstance()
        it.setText(text)
        var start = it.first()
        var end = it.next()
        while (end != java.text.BreakIterator.DONE) {
            val e = text.substring(start, end)
            if (e.isNotBlank() && e !in setOf(",", ";", ".")) out += e
            start = end
            end = it.next()
        }
        return out.toList()
    }

    /** The emojis typed for Custom; 😥 also brings 😢, as the 😥 button does. */
    fun custom(s: String): List<String> {
        return withAliases(split(s))
    }
}

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
 * Reaction List (tools/reaction_list): the codes whose messages got ❌, 📵, 😥 or your own emojis, with the phone
 * number from the order message. Empty code box = every reacted code in the chats read.
 */
class ReactionListTool(private val scope: CoroutineScope) {
    class Found(val code: String, val name: String, val order: Int) {
        val emojis = mutableSetOf<String>()
        val phones = mutableListOf<String>()
        val chats = mutableListOf<String>()
        var t = 0L
        /** With "List + photos": what was saved ("3 photos", "No photos" …). */
        var photos by mutableStateOf("")
        val chatIds = mutableListOf<String>()
        /** '❌📵', or with what they mean in the shop settings '❌ Cancelled, 📵 Phone off'. */
        fun react(wanted: List<String>, labels: Map<String, String> = emptyMap()): String {
            val found = wanted.filter { it in emojis }
            if (found.none { !labels[it].isNullOrEmpty() }) return found.joinToString("")
            return found.joinToString(", ") { e -> labels[e]?.takeIf { it.isNotEmpty() }?.let { "$e $it" } ?: e }
        }
    }

    private val prefs = Storage.prefs("reaction_list")
    var codeText by mutableStateOf("")
    /** The buttons, from the shop settings (they change with them). */
    val choices get() = makeChoices(Shop.reactions())
    var choiceKey by mutableStateOf(prefs.getString("choice", ALL)!!.let { OLD_KEYS[it] ?: it })
    val choice get() = choices.let { c -> c.firstOrNull { it.key == choiceKey } ?: c.firstOrNull { it.key == ALL } ?: c.first() }
    val labels get() = reactionLabels(Shop.reactions())
    /** The emojis typed for the Custom choice ("👍 ❤️ 🔥"). */
    var customText by mutableStateOf(prefs.getString("custom_reactions", "")!!)
    /** The reactions of the last run, in the order they are shown. */
    var shown by mutableStateOf(emptyList<String>())
        private set
    var lookbackDays by mutableStateOf(prefs.getInt("lookback_days", 10))
    var chat by mutableStateOf(ChatFilter(prefs.getString("chat_id", "")!!, prefs.getString("chat_title", "")!!,
        prefs.getBoolean("groups_only", false)))
    /** Each list of the code box in its own chat (the 6- codes in "My Shop (6-)" …), not all chats. */
    var perList by mutableStateOf(prefs.getBoolean("per_list", false))
    /** The chat of each list ("6-" -> chat); a list without one is searched in [chat]. */
    val listChats = mutableStateMapOf<String, ChatFilter>().apply { putAll(listChatsFromJson(prefs.getString("list_chats", "{}"))) }
    /** false = list only; true = also save each listed code's reacted photos in Pictures/Boostimise/[folder]. */
    var withPhotos by mutableStateOf(prefs.getBoolean("with_photos", false))
    var folder by mutableStateOf(prefs.getString("folder", "Reaction List")!!)
    val relPath get() = Storage.picturesPath(folder.ifBlank { "Reaction List" })
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
        prefs.edit().putString("choice", choiceKey).putString("custom_reactions", customText.trim())
            .putInt("lookback_days", lookbackDays).putBoolean("with_photos", withPhotos).putString("folder", folder)
            .putString("chat_id", chat.id).putBoolean("per_list", perList).putString("list_chats", listChatsToJson(listChats))
            .putString("chat_title", chat.title).putBoolean("groups_only", chat.groupsOnly).apply()
    }

    /** The lists in the code box with how many codes each has: [("6-", 7), ("5a-", 22), …]. */
    fun lists(): List<Pair<String, Int>> =
        Codes.parseCodeList(codeText).groupingBy { listOfCode(it.code) }.eachCount().filterKeys { it.isNotEmpty() }.toList()

    /** Where a list's codes are read. */
    private fun chatFor(list: String): ChatFilter = if (perList) listChats[list] ?: chat else chat

    fun autoFillListChats() = autoFillListChats(lists().map { it.first }, listChats)

    /** The emojis looked for: the chosen button's, or for Custom the ones typed. */
    fun wanted(): List<String> = if (choice.key == CUSTOM) ReactionEmojis.custom(customText) else choice.emojis

    fun stop() {
        job?.cancel()
    }

    /** '1. 2-558 Realme C85 ❌ 01734484040' (or '❌ Cancelled' with a label), one code per line. */
    fun formatList(): String = results.mapIndexed { n, f ->
        "${n + 1}. " + listOf(f.code, f.name, f.react(shown, labels), f.phones.joinToString(", ")).filter { it.isNotEmpty() }.joinToString(" ")
    }.joinToString("\n") + if (results.isNotEmpty()) "\n" else ""

    fun start() {
        if (running) return
        save()
        val emojis = wanted()
        if (emojis.isEmpty()) {
            summary = "Type the reactions to look for in the Custom box, e.g. 👍 ❤️ 🔥"
            return
        }
        shown = emojis
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
        val reactions = shown.joinToString("")
        val since = Codes.scanSince(items, lookbackDays)
        val sinceTs = since.atZone(ZoneId.systemDefault()).toEpochSecond()
        val dd = DateTimeFormatter.ofPattern("dd/MM/yyyy")
        // One pass per chat filter: all codes in [chat], or with [perList] each list's codes only in its own chat
        // (the codes of a pass are the only ones kept from its chats). With an empty code box there are no lists.
        val groups = if (perList && items.isNotEmpty()) items.groupBy { chatFor(listOfCode(it.code)) } else mapOf(chat to items)
        val passes = groups.map { (filter, group) ->
            val targets = (WaEngine.call("window.__waf_reactions.scanTargets(${filter.toJs(sinceTs)})") as? JSONArray).objects()
            val codes = if (perList && items.isNotEmpty()) group.map { ReactionCodes.normalize(it.code) }.toSet() else wanted
            if (perList && items.isNotEmpty()) {
                val names = group.map { listOfCode(it.code).ifEmpty { "other" } }.distinct()
                log.add("${names.joinToString(", ")} (${group.size} code(s)) → ${filter.label}" +
                    if (targets.isEmpty()) ": not active since ${since.format(dd)}" else "")
            }
            Triple(codes, targets, filter)
        }
        val reads = passes.flatMap { (codes, targets) -> targets.map { it to codes } }
        val what = if (items.isNotEmpty()) "${items.size} code(s)" else "every code"
        log.add("Reading ${reads.size} chat(s) active since ${since.format(dd)}: $what reacted with $reactions…")
        val opts = jsonOf("since" to sinceTs, "reactions" to reactions, "gapMinutes" to 5, "maxLoads" to 300)
        val byCode = linkedMapOf<String, Found>()
        var noCode = 0
        var orphans = 0
        for ((n, read) in reads.withIndex()) {
            val (target, passCodes) = read
            val title = target.optString("title").ifEmpty { target.optString("id") }
            activity = "Reading chat ${n + 1}/${reads.size}: $title"
            Jobs.progress("reaction_list", "Reaction List: $activity")
            val res = try {
                WaEngine.call("window.__waf_reactions.reactedMessages(${WaEngine.quote(target.optString("id"))}, $opts)",
                    30 * 60_000L) as? JSONObject
            } catch (e: WaError) {
                log.add("$title: ${e.message}")
                null
            } ?: JSONObject()
            progress = (n + 1).toFloat() / reads.size.coerceAtLeast(1)
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
                    if (passCodes.isNotEmpty() && code !in passCodes) continue
                    val (order, item) = listed[code] ?: (0 to null)
                    val f = byCode.getOrPut(code) { Found(item?.code ?: code, item?.name ?: "", order) }
                    hit.optString("react").let { r -> f.emojis += ReactionEmojis.split(r) }
                    phones.forEach { if (it !in f.phones) f.phones += it }
                    val chatName = hit.optString("chat").ifEmpty { title }
                    if (chatName !in f.chats) f.chats += chatName
                    f.t = maxOf(f.t, hit.optLong("t"))
                    if (target.optString("id") !in f.chatIds) f.chatIds += target.optString("id")
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
        val photos = if (withPhotos && byCode.isNotEmpty()) savePhotos(byCode, sinceTs, reactions) else null
        val secs = (System.currentTimeMillis() - started) / 1000
        summary = if (items.isNotEmpty()) "Done in ${secs}s: ${byCode.size} of ${wanted.size} code(s) reacted with $reactions"
        else "Done in ${secs}s: ${byCode.size} code(s) reacted with $reactions"
        summary += if (photos != null) ", photos saved for ${photos.first} (${photos.second} images)." else "."
        log.add(summary)
    }

    /**
     * Saves the reacted photos of each listed code as "2-558 Realme C85.jpg" (as Fetch by Code names them):
     * the photos with the reaction, or all photos of a code whose message has it. Returns (codes, images) saved.
     */
    private suspend fun savePhotos(byCode: Map<String, Found>, sinceTs: Long, reactions: String): Pair<Int, Int> {
        val codes = byCode.keys.sortedWith(ReactionCodes.comparator)
        val key = codes.withIndex().associate { (i, c) -> c to i.toString() }
        val existing = Storage.namesIn(relPath)
        val base = { c: String -> CodeItem(byCode.getValue(c).code, byCode.getValue(c).name).baseName() }
        val todo = codes.filter { c ->
            val prev = Codes.existingOutput(existing, base(c))
            if (prev != null) byCode.getValue(c).photos = "Saved earlier"
            prev == null
        }
        val byChat = linkedMapOf<String, MutableList<String>>()
        for (c in todo) for (id in byCode.getValue(c).chatIds) byChat.getOrPut(id) { mutableListOf() } += c
        log.add("Finding the photos of ${todo.size} code(s) in ${byChat.size} chat(s)…")
        val total = (byChat.size + todo.size).coerceAtLeast(1)
        val found = mutableMapOf<String, MutableMap<String, JSONObject>>()
        for ((n, entry) in byChat.entries.withIndex()) {
            val (chatId, chatCodes) = entry
            activity = "Finding photos: chat ${n + 1}/${byChat.size}"
            Jobs.progress("reaction_list", "Reaction List: $activity")
            val list = JSONArray()
            for (c in chatCodes) list.put(jsonOf("key" to key.getValue(c), "pattern" to Codes.codePattern(c)))
            val opts = jsonOf("codes" to list, "since" to sinceTs, "neighbors" to "after", "gapMinutes" to 5,
                "maxNeighbors" to 30, "maxLoads" to 300, "reactions" to reactions)
            val res = try {
                WaEngine.call("window.__waf_reactions.scanChat(${WaEngine.quote(chatId)}, $opts)", 30 * 60_000L) as? JSONObject
            } catch (e: WaError) {
                log.add("Photos: ${e.message}")
                null
            } ?: JSONObject()
            if (res.has("error")) log.add("Photos: ${res.optString("error")}")
            val hits = res.optJSONObject("hits") ?: JSONObject()
            for (k in hits.keys()) {
                val bucket = found.getOrPut(k) { linkedMapOf() }
                for (img in hits.optJSONArray(k).objects()) bucket.putIfAbsent(img.optString("id"), img)
            }
            progress = (n + 1).toFloat() / total
        }
        var saved = 0
        var images = 0
        for ((i, c) in todo.withIndex()) {
            val f = byCode.getValue(c)
            progress = (byChat.size + i + 1).toFloat() / total
            val imgs = found[key.getValue(c)].orEmpty().values.sortedBy { it.optLong("t") }
            if (imgs.isEmpty()) {
                f.photos = "No photos"
                log.add("[${f.code}] no reacted photos.")
                continue
            }
            val files = mutableListOf<Pair<ByteArray, String>>()
            val hashes = mutableSetOf<String>()
            for ((j, img) in imgs.withIndex()) {
                f.photos = "Downloading ${j + 1}/${imgs.size}…"
                activity = "[${f.code}] downloading ${j + 1}/${imgs.size}"
                val res = try {
                    WaEngine.call("window.__waf_reactions.download(${WaEngine.quote(img.optString("id"))})", 180_000) as? JSONObject
                } catch (e: WaError) {
                    jsonOf("error" to e.message)
                } ?: JSONObject()
                val data = res.optString("data")
                if (res.has("error") || data.isEmpty()) {
                    log.add("[${f.code}] photo ${j + 1} failed: ${res.optString("error").ifEmpty { "no data" }}")
                    continue
                }
                val bytes = Base64.decode(data, Base64.DEFAULT)
                val digest = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }
                if (!hashes.add(digest)) continue // same picture posted / forwarded twice
                files += bytes to Codes.extFor(res.optString("mimetype"), bytes, res.optString("filename"))
            }
            if (files.isEmpty()) {
                f.photos = "Found ${imgs.size}, download failed"
                continue
            }
            // one photo -> "<base>.jpg"; several -> "<base> 1.jpg", "<base> 2.jpg", ...
            files.forEachIndexed { n, (bytes, ext) ->
                val name = if (files.size == 1) "${base(c)}$ext" else "${base(c)} ${n + 1}$ext"
                Storage.delete(relPath, name)
                Storage.save(relPath, name, bytes)
            }
            saved++
            images += files.size
            f.photos = "${files.size} photo" + if (files.size > 1) "s" else ""
            log.add("[${f.code}] saved ${f.photos} → $relPath")
        }
        return saved to images
    }
}
