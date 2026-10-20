package com.boostimise.app.tools.rename

import android.content.Intent
import android.net.Uri
import android.util.Base64
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import com.boostimise.app.core.Codes
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
import com.boostimise.app.wa.Jobs
import com.boostimise.app.wa.WaEngine
import com.boostimise.app.wa.WaError
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.time.ZoneId
import java.util.Date
import java.util.Locale
import kotlin.coroutines.coroutineContext

/** One line of the product list: "6-852 Nubia a56" -> code 6-852, list 6-. */
data class Product(val code: String, val label: String, val list: String)

/**
 * Rename by Match (tools/match_renamer): finds each code of the product list in WhatsApp, compares that
 * message's photos with the photos in a folder on the phone and renames the matching one "6-852 Nubia a56.jpg".
 */
class RenameTool(private val scope: CoroutineScope) {
    val key = "rename_by_match"
    val title = "Rename by Match"

    class Row(val product: Product) {
        var kind by mutableStateOf("")       // "" | working | ok | none | error | skipped
        var text by mutableStateOf("")
    }

    /** [home]: where its new name goes - its folder, or the one above when that is already a code's
     *  folder ("6-840 …/img1.jpg"). [doneCode]: the code its name already starts with. */
    private class LocalPhoto(var doc: Doc, var parent: Doc, val home: Doc, val rel: String, val sig: Signature,
                             val doneCode: String) {
        val usedFor = mutableListOf<String>()
    }

    private val prefs = Storage.prefs(key)
    var codeText by mutableStateOf("")
    val rows = mutableStateListOf<Row>()
    var skippedCodes by mutableStateOf(listOf<String>())
        private set
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
    var tree by mutableStateOf(prefs.getString("tree", "")!!.takeIf { it.isNotEmpty() }?.let { Uri.parse(it) })
        private set
    var chat by mutableStateOf(ChatFilter(prefs.getString("chat_id", "")!!, prefs.getString("chat_title", "")!!,
        prefs.getBoolean("groups_only", false)))
    var perList by mutableStateOf(prefs.getBoolean("per_list", true))
    val listChats = mutableStateMapOf<String, ChatFilter>().apply { putAll(listChatsFromJson(prefs.getString("list_chats", "{}"))) }
    var neighbors by mutableStateOf(prefs.getString("neighbors", "after")!!)
    var gapMinutes by mutableStateOf(prefs.getInt("gap_minutes", 5))
    var lookbackDays by mutableStateOf(prefs.getInt("lookback_days", 10))
    var threshold by mutableStateOf(prefs.getInt("threshold", 10))
    var addNote by mutableStateOf(prefs.getBoolean("add_note", false))
    var subFolders by mutableStateOf(prefs.getBoolean("recursive", false))
    var skipDone by mutableStateOf(prefs.getBoolean("skip_done", true))
    var dryRun by mutableStateOf(prefs.getBoolean("dry_run", false))
    /** When the last rename that can be undone was made ("" = nothing to undo). */
    var undoWhen by mutableStateOf(undoLog()?.optString("when") ?: "")
        private set

    val folderLabel get() = tree?.let { PhotoFolder.label(it) } ?: ""

    fun saveSettings() {
        prefs.edit().putString("tree", tree?.toString() ?: "").putString("chat_id", chat.id).putString("chat_title", chat.title)
            .putBoolean("groups_only", chat.groupsOnly).putBoolean("per_list", perList)
            .putString("list_chats", listChatsToJson(listChats)).putString("neighbors", neighbors)
            .putInt("gap_minutes", gapMinutes).putInt("lookback_days", lookbackDays).putInt("threshold", threshold)
            .putBoolean("add_note", addNote).putBoolean("recursive", subFolders)
            .putBoolean("skip_done", skipDone).putBoolean("dry_run", dryRun).apply()
    }

    /** The folder picked with Android's folder picker; the app keeps the right to rename in it. */
    fun setFolder(uri: Uri) {
        runCatching {
            Storage.app.contentResolver.takePersistableUriPermission(uri,
                Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION)
        }
        tree = uri
        saveSettings()
    }

    private fun hasAccess(uri: Uri) = Storage.app.contentResolver.persistedUriPermissions.any { it.uri == uri && it.isWritePermission }

    // ---- the product list --------------------------------------------------------------------

    fun parse() {
        val (products, skipped) = parseProducts(codeText)
        val old = rows.associateBy { it.product }
        rows.clear()
        rows.addAll(products.reversed().map { old[it] ?: Row(it) })   // shown as pasted, worked on bottom first
        skippedCodes = skipped
    }

    /** The lists in the product list, bottom list first, with how many codes each has: [("6-", 7), ("5a-", 22)]. */
    fun lists(): List<Pair<String, Int>> = rows.reversed().groupingBy { it.product.list }.eachCount().toList()

    fun chatFor(list: String): ChatFilter = if (perList) listChats[list] ?: chat else chat

    fun autoFillListChats() = autoFillListChats(lists().map { it.first }, listChats)

    fun stop() {
        job?.cancel()
    }

    fun start() {
        if (running) return
        parse()
        val root = tree
        when {
            rows.isEmpty() -> { summary = "Paste the product list first (lines like \"6-852 Nubia a56\")."; return }
            root == null || !hasAccess(root) -> { summary = "Choose the photo folder first."; return }
        }
        saveSettings()
        rows.forEach { it.kind = ""; it.text = "Queued" }
        running = true
        summary = ""
        Jobs.begin(key, "$title: starting…")
        job = scope.launch {
            try {
                runJob(root!!)
            } catch (e: CancellationException) {
                log.add("Stopped by user.")
                summary = "Stopped."
            } catch (e: Exception) {
                log.add("Error: ${e.message}")
                summary = "Error: ${e.message}"
            } finally {
                for (r in rows) if (r.kind == "working" || r.text == "Queued") {
                    r.kind = ""
                    r.text = "Stopped"
                }
                running = false
                activity = ""
                progress = 0f
                Jobs.end(key)
            }
        }
    }

    private fun status(s: String) {
        activity = s
        Jobs.progress(key, "$title: $s")
    }

    // ---- the run -----------------------------------------------------------------------------

    private class Stats {
        var photos = 0
        var codes = 0
        var already = 0
        var notFound = 0
        var noMatch = 0
        var errors = 0
    }

    private lateinit var ops: Ops
    private val waSigs = HashMap<String, Signature?>()      // WhatsApp photo id -> signature (null = could not load)
    private val waPreview = HashSet<String>()                // ids compared with WhatsApp's small preview only
    private val localSigs = HashMap<String, Signature>()     // local photo "id|size" -> signature

    private suspend fun runJob(tree: Uri) {
        val started = System.currentTimeMillis()
        val root = withContext(Dispatchers.IO) { PhotoFolder.root(tree) }
        val byList = rows.reversed().groupBy { it.product.list }   // bottom list first, each list bottom code first
        ops = Ops(tree, dryRun)
        waSigs.clear()
        waPreview.clear()
        val stats = Stats()
        val allPhotos = mutableListOf<LocalPhoto>()
        log.add("")
        log.add("${rows.size} product codes in ${byList.size} list(s): ${byList.keys.joinToString(", ") { it.ifEmpty { "Orders" } }} (bottom list first)" +
            if (dryRun) " - PREVIEW ONLY, nothing is renamed" else "")
        if (skippedCodes.isNotEmpty()) log.add("Skipped (no product name): ${skippedCodes.joinToString(", ")}")
        var done = 0
        for ((list, listRows) in byList) {
            coroutineContext.ensureActive()
            val ps = listRows.map { it.product }
            log.add("")
            log.add("── $list list: ${ps.size} codes, from ${ps.first().code} up to ${ps.last().code} ──")
            val folder = withContext(Dispatchers.IO) { listFolder(root, list) }
            if (folder == null) {
                log.add("No folder \"$list\" in ${root.name}, so this list is skipped.")
                listRows.forEach { it.kind = "skipped"; it.text = "No \"$list\" folder" }
                done += ps.size
                continue
            }
            val photos = scanFolder(folder)
            allPhotos += photos
            if (photos.isEmpty()) {
                log.add("No photos in ${folder.name}, so this list is skipped.")
                listRows.forEach { it.kind = "skipped"; it.text = "No photos in ${folder.name}" }
                done += ps.size
                continue
            }
            // codes that already have a photo with their exact name need no WhatsApp search
            val todo = listRows.filter { r ->
                val plain = windowsSafeName(r.product.label)
                val mine = if (skipDone) photos.filter { it.doneCode == r.product.code && namedFor(it.doc, it.parent, plain) } else emptyList()
                if (mine.isNotEmpty()) {
                    mine.forEach { it.usedFor += r.product.label }
                    r.kind = "skipped"
                    r.text = "Already named (" + mine.joinToString(", ") { it.rel } + ")"
                    stats.already++
                }
                mine.isEmpty()
            }
            if (todo.isEmpty()) {
                done += ps.size
                continue
            }
            WaEngine.awaitReady { status(it) }
            val hits = WaEngine.withTurn(title, { status(it) }) {
                val hits = scanWhatsApp(list, todo)
                loadPhotos(hits)
                hits
            }
            for ((i, r) in todo.withIndex()) {
                coroutineContext.ensureActive()
                status("Matching ${i + 1}/${todo.size}: ${r.product.label}")
                match(r, hits[i].orEmpty(), photos, stats)
                progress = (done + i + 1).toFloat() / rows.size
            }
            done += ps.size
        }
        summarise(allPhotos, stats, (System.currentTimeMillis() - started) / 1000)
    }

    /** The folder with one list's photos: in a day folder "24.09.26" the 6- list uses its sub folder "6-".
     *  Null if the folder has list folders but not this one; a plain folder is used for every list. */
    private fun listFolder(root: Doc, list: String): Doc? {
        if (list.isEmpty()) return root   // the codes without a prefix (#1023) are one list: no list folders
        val names = if (list[0].isDigit()) setOf(list) else setOf(list, list.dropLast(1))   // "cc-" is in "CC-" or "CC"
        val same = { name: String -> name.replace(Regex("\\s+"), "").lowercase() in names }
        if (same(root.name)) return root
        val dirRe = groupDirRe()
        val subs = PhotoFolder.children(root).filter { it.isDir && dirRe.matches(it.name) }
        subs.firstOrNull { same(it.name) }?.let { return it }
        if (subs.isNotEmpty() || dirRe.matches(root.name)) return null
        return root
    }

    /** The photos of one list's folder (plus the folders earlier runs made, like "6-840 …"). */
    private suspend fun scanFolder(folder: Doc): List<LocalPhoto> {
        status("Reading the folder…")
        class Entry(val doc: Doc, val parent: Doc, val home: Doc, val rel: String, val done: String)
        val doneRe = doneRe()
        val entries = withContext(Dispatchers.IO) {
            val out = mutableListOf<Entry>()
            fun walk(dir: Doc, up: Doc?, path: List<String>, deep: Boolean) {
                val home = if (up != null && doneRe.containsMatchIn(dir.name)) up else dir
                for (d in PhotoFolder.children(dir)) {
                    if (d.isDir) {
                        if (deep || (path.isEmpty() && doneRe.containsMatchIn(d.name))) walk(d, dir, path + d.name, deep)
                        continue
                    }
                    if ("." + d.name.substringAfterLast('.', "").lowercase() !in IMAGE_EXTS) continue
                    val done = (listOf(d.name.substringBeforeLast('.')) + path.reversed()).firstNotNullOfOrNull { doneCode(it, doneRe) } ?: ""
                    out += Entry(d, dir, home, (path + d.name).joinToString("/"), done)
                }
            }
            walk(folder, null, emptyList(), subFolders)
            out.sortedBy { it.rel.lowercase() }
        }
        val done = entries.count { it.done.isNotEmpty() }
        log.add("Folder ${folder.name}: ${entries.size} photos" +
            if (done > 0) " ($done already named with a code: renamed again if their name is not the one in the list)" else "")
        val photos = mutableListOf<LocalPhoto>()
        for ((i, e) in entries.withIndex()) {
            coroutineContext.ensureActive()
            val doc = e.doc
            status("Reading photos in the folder… ${i + 1}/${entries.size}")
            val sig = localSigs["${doc.id}|${doc.size}"] ?: withContext(Dispatchers.Default) {
                runCatching { Signature.of(withContext(Dispatchers.IO) { PhotoFolder.read(doc) }) }.getOrNull()
            }
            if (sig == null) {
                log.add("  skipped ${e.rel} (cannot read it)")
                continue
            }
            localSigs["${doc.id}|${doc.size}"] = sig
            photos += LocalPhoto(doc, e.parent, e.home, e.rel, sig, e.done)
        }
        return photos
    }

    /** The WhatsApp photos of each code (index in [todo]), read through WhatsApp Web's own modules. */
    private suspend fun scanWhatsApp(list: String, todo: List<Row>): Map<Int, List<JSONObject>> {
        val since = Codes.scanSince(emptyList(), lookbackDays)
        val sinceTs = since.atZone(ZoneId.systemDefault()).toEpochSecond()
        val api = "window.__waf_basic"
        val filter = chatFor(list)
        val targets = (WaEngine.call("$api.scanTargets(${filter.toJs(sinceTs)})") as? JSONArray).objects()
        log.add("Searching ${todo.size} code(s) in ${filter.label} (${targets.size} chat(s) active in the last $lookbackDays days)…")
        todo.forEach { it.kind = "working"; it.text = "Reading chats…" }
        val codes = JSONArray()
        for ((i, r) in todo.withIndex()) codes.put(jsonOf("key" to i.toString(), "pattern" to Codes.codePattern(r.product.code)))
        val opts = ProductCode.scanPatterns(jsonOf("codes" to codes, "since" to sinceTs, "neighbors" to neighbors,
            "gapMinutes" to gapMinutes, "maxNeighbors" to 30, "maxLoads" to 300))
        val found = HashMap<Int, LinkedHashMap<String, JSONObject>>()
        for ((n, target) in targets.withIndex()) {
            coroutineContext.ensureActive()
            val t = target.optString("title").ifEmpty { target.optString("id") }
            status("Reading chat ${n + 1}/${targets.size}: $t")
            val res = try {
                WaEngine.call("$api.scanChat(${WaEngine.quote(target.optString("id"))}, $opts)", 30 * 60_000L) as? JSONObject
            } catch (e: WaError) {
                log.add("$t: ${e.message}")
                null
            } ?: continue
            if (res.has("error")) {
                log.add("$t: ${res.optString("error")}")
                continue
            }
            res.optJSONArray("warnings")?.let { w -> for (k in 0 until w.length()) log.add("$t: ${w.optString(k)}") }
            val hits = res.optJSONObject("hits") ?: continue
            for (k in hits.keys()) {
                val bucket = found.getOrPut(k.toInt()) { linkedMapOf() }
                for (img in hits.optJSONArray(k).objects()) bucket.putIfAbsent(img.optString("id"), img)
                todo[k.toInt()].text = "Reading chats… ${bucket.size} photo(s) found"
            }
        }
        return found.mapValues { (_, v) -> v.values.sortedBy { it.optLong("t") } }
    }

    /** Downloads every WhatsApp photo found (WhatsApp's small preview if the photo itself is gone) and hashes it. */
    private suspend fun loadPhotos(hits: Map<Int, List<JSONObject>>) {
        val ids = hits.values.flatten().map { it.optString("id") }.distinct().filter { it !in waSigs }
        for ((i, id) in ids.withIndex()) {
            coroutineContext.ensureActive()
            status("Loading WhatsApp photo ${i + 1}/${ids.size}…")
            var bytes = fetchBytes("window.__waf_basic.download(${WaEngine.quote(id)})", 180_000)
            if (bytes == null) {
                bytes = fetchBytes("window.__wab.preview(${WaEngine.quote(id)})", 20_000)
                if (bytes != null) waPreview += id
            }
            waSigs[id] = bytes?.let { b -> withContext(Dispatchers.Default) { runCatching { Signature.of(b) }.getOrNull() } }
        }
    }

    private suspend fun fetchBytes(expr: String, timeout: Long): ByteArray? {
        val res = try {
            WaEngine.call(expr, timeout) as? JSONObject
        } catch (e: WaError) {
            null
        } ?: return null
        val data = res.optString("data")
        if (res.has("error") || data.isEmpty()) return null
        return runCatching { Base64.decode(data, Base64.DEFAULT) }.getOrNull()
    }

    /** Matches one code's WhatsApp photos with the folder and renames (engine.py match_list). */
    private suspend fun match(row: Row, images: List<JSONObject>, photos: List<LocalPhoto>, stats: Stats) {
        val p = row.product
        if (images.isEmpty()) {
            row.kind = "none"
            row.text = "Not found in WhatsApp"
            log.add("✗ ${p.label}: not found in WhatsApp")
            stats.notFound++
            return
        }
        val wa = images.mapNotNull { img -> waSigs[img.optString("id")]?.let { img.optString("id") to it } }
        if (wa.isEmpty()) {
            row.kind = "none"
            row.text = "Found in WhatsApp, but its photo could not be loaded"
            log.add("✗ ${p.label}: found in WhatsApp, but its photo could not be loaded")
            stats.noMatch++
            return
        }
        // Among matching photos prefer: already named with this code, then a photo not used in this run,
        // and only then one used for another code in this run (it gets copied)
        val chosen = mutableListOf<Triple<LocalPhoto, Int, String>>()
        var closest: Int? = null
        for ((id, wsig) in wa) {
            var best: LocalPhoto? = null
            var bestRank: Pair<Int, Int>? = null
            for (ph in photos) {
                val d = wsig.difference(ph.sig)
                closest = minOf(closest ?: d, d)
                if (d > threshold) continue
                val kind = if (ph.doneCode == p.code) 0 else if (ph.usedFor.isEmpty()) 1 else 2
                val rank = kind to d
                if (bestRank == null || compareValuesBy(rank, bestRank, { it.first }, { it.second }) < 0) {
                    best = ph
                    bestRank = rank
                }
            }
            if (best != null && chosen.none { it.first === best }) chosen += Triple(best, bestRank!!.second, id)
        }
        if (chosen.isEmpty()) {
            val why = closest?.let { " (closest difference $it, limit $threshold)" } ?: ""
            row.kind = "none"
            row.text = "${wa.size} WhatsApp photo(s), none of them is in the folder$why"
            log.add("✗ ${p.label}: ${wa.size} WhatsApp photo(s), none of them is in the folder$why")
            stats.noMatch++
            return
        }
        // photos that already have this code's name stay as they are; any other name is renamed
        val plain = windowsSafeName(p.label)
        val total = chosen.size
        val already = chosen.filter { it.first.usedFor.isEmpty() && namedFor(it.first.doc, it.first.parent, plain) }
        chosen.removeAll(already)
        for ((ph, _, _) in already) {
            ph.usedFor += p.label
            log.add("✓ ${p.label}: already named (${ph.rel})")
        }
        if (chosen.isEmpty()) {
            row.kind = "skipped"
            row.text = "Already named (${already.joinToString(", ") { it.first.rel }})"
            stats.already++
            return
        }
        val note = if (addNote) captionNote(images.filter { it.optString("via") == "caption" }.map { it.optString("caption") }, p.label) else ""
        val base = windowsSafeName(p.label + note)
        val lines = mutableListOf<String>()
        try {
            val names = try {
                withContext(Dispatchers.IO) { applyNames(p, base, chosen, total, lines) }
            } finally {
                lines.forEach { log.add(it) }
            }
            stats.codes++
            stats.photos += chosen.size
            row.kind = "ok"
            row.text = (if (dryRun) "Would rename: " else "") + names.joinToString("\n")
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            row.kind = "error"
            row.text = "Could not rename: ${e.message}"
            log.add("✗ ${p.label}: could not rename (${e.message})")
            stats.errors++
        }
    }

    /**
     * One photo: "2-226 Realme P3 Ultra.jpg". Several ([total], with the ones already named): a folder
     * "2-221 Oppo a6 pro 5g" with img1.jpg, img2.jpg … (as the Windows app and the website name them).
     * Runs on the IO thread: its log lines go to [log] (the caller adds them to the tool's log).
     */
    private fun applyNames(p: Product, base: String, chosen: List<Triple<LocalPhoto, Int, String>>, total: Int,
                           log: MutableList<String>): List<String> {
        val ext = { ph: LocalPhoto -> "." + ph.doc.name.substringAfterLast('.', "jpg") }
        // (folder it goes to, new name)
        val targets: List<Pair<Doc, String>> = if (total == 1) {
            chosen[0].first.let { listOf(it.home to ops.freeName(it.home, base, ext(it))) }
        } else {
            val dir = ops.mkdir(chosen[0].first.home, base)
            chosen.map { (ph, _, _) -> dir to ops.nextNumbered(dir, "img", ext(ph).lowercase()) }
        }
        val out = mutableListOf<String>()
        for ((c, target) in chosen.zip(targets)) {
            val (ph, d, id) = c
            val (dir, name) = target
            var how = if (d == 0) "perfect match" else "difference $d"
            if (id in waPreview) how += ", compared with WhatsApp's small preview"
            val shown = if (dir.id == ph.parent.id) name else "${dir.name}/$name"
            if (ph.usedFor.isNotEmpty()) {
                ops.copy(ph.doc, dir, name)
                val other = ph.usedFor.firstOrNull() ?: ph.rel
                log += "✓ ${p.label}: same photo as $other, ${if (dryRun) "would copy" else "copied"} to $shown"
                out += "copy of ${ph.rel} → $shown"
            } else {
                val from = ph.doc.name
                ops.place(ph.doc, ph.parent, dir, name)?.let {
                    ph.doc = it
                    ph.parent = dir
                    localSigs["${it.id}|${it.size}"] = ph.sig
                }
                log += "✓ ${p.label}: $from  →  $shown  ($how)"
                out += "$from → $shown"
            }
            ph.usedFor += p.label
        }
        return out
    }

    private fun summarise(photos: List<LocalPhoto>, s: Stats, secs: Long) {
        val unused = photos.filter { it.usedFor.isEmpty() && it.doneCode.isEmpty() }
        log.add("")
        log.add("Summary")
        log.add("  ${if (dryRun) "Would rename" else "Renamed"}: ${s.photos} photo(s) for ${s.codes} code(s)")
        if (s.already > 0) log.add("  Already named right: ${s.already} code(s)")
        log.add("  Not found in WhatsApp: ${s.notFound}")
        log.add("  Found in WhatsApp but no matching photo in the folder: ${s.noMatch}")
        if (s.errors > 0) log.add("  Errors: ${s.errors}")
        log.add("  Photos in the folder that matched no code: ${unused.size}")
        for (ph in unused.take(40)) log.add("     ${ph.rel}")
        if (unused.size > 40) log.add("     … and ${unused.size - 40} more")
        summary = "Done in ${secs}s: ${if (dryRun) "would rename" else "renamed"} ${s.photos} photo(s) for ${s.codes} code(s), " +
            "${s.notFound} not found, ${s.noMatch} no match" + (if (s.already > 0) ", ${s.already} already named" else "") +
            (if (s.errors > 0) ", ${s.errors} error(s)" else "") + "."
        if (dryRun) {
            log.add("Preview only - nothing was changed. Untick \"Preview only\" and press Start to rename.")
            summary += " Preview only: nothing was changed."
        } else if (ops.count > 0) {
            log.add("Done. If something is wrong, press \"Undo last rename\".")
            undoWhen = undoLog()?.optString("when") ?: ""
        }
    }

    // ---- renaming, with a log for "Undo last rename" -----------------------------------------

    /** Every change in the folder, recorded as it happens so Undo works even after a crash. */
    private inner class Ops(val tree: Uri, val dry: Boolean) {
        private val steps = JSONArray()
        private val names = HashMap<String, MutableSet<String>>()   // folder id -> names in it (lowercase)
        var count = 0
            private set

        private fun namesIn(dir: Doc) = names.getOrPut(dir.id) { PhotoFolder.children(dir).map { it.name.lowercase() }.toMutableSet() }

        /** "img1.jpg", or the next number not used yet in the folder ("img4.jpg" after img1-3). */
        fun nextNumbered(dir: Doc, prefix: String, ext: String): String {
            val stems = namesIn(dir).map { it.substringBeforeLast('.') }.toSet()
            var k = 1
            while ("$prefix$k".lowercase() in stems) k++
            val name = "$prefix$k$ext"
            namesIn(dir) += name.lowercase()
            return name
        }

        /** "<base>.jpg", or "<base> (2).jpg" … if that name is taken. */
        fun freeName(dir: Doc, base: String, ext: String): String {
            val taken = namesIn(dir)
            var k = 1
            var name = "$base$ext"
            while (name.lowercase() in taken) {
                k++
                name = "$base ($k)$ext"
            }
            taken += name.lowercase()
            return name
        }

        fun mkdir(parent: Doc, name: String): Doc {
            PhotoFolder.children(parent).firstOrNull { it.isDir && it.name.equals(name, true) }?.let { return it }
            if (dry) return Doc(tree, parent.id + "/" + name, name, android.provider.DocumentsContract.Document.MIME_TYPE_DIR, 0)
                .also { names[it.id] = mutableSetOf() }
            val dir = PhotoFolder.mkdir(parent, name)
            namesIn(parent) += dir.name.lowercase()
            record(jsonOf("a" to "mkdir", "id" to dir.id))
            return dir
        }

        /** Renames [doc] to [name] in [to] (moving it there from [from] first). Null in a preview. */
        fun place(doc: Doc, from: Doc, to: Doc, name: String): Doc? {
            if (dry) return null
            var cur = doc
            if (to.id != from.id) {
                cur = PhotoFolder.move(cur, from, to)
                namesIn(from) -= doc.name.lowercase()
                record(jsonOf("a" to "move", "id" to cur.id, "from" to to.id, "to" to from.id, "name" to doc.name))
            }
            if (cur.name != name) {
                val renamed = PhotoFolder.rename(cur, name)
                if (to.id == from.id) namesIn(from) -= doc.name.lowercase()
                // a move was recorded with the file's id after it: point that step at the new id
                val last = steps.optJSONObject(steps.length() - 1)
                if (to.id != from.id && last?.optString("id") == cur.id) last.put("id", renamed.id)
                else record(jsonOf("a" to "rename", "id" to renamed.id, "name" to doc.name))
                cur = renamed
                save()
            }
            return cur
        }

        fun copy(doc: Doc, dir: Doc, name: String) {
            if (dry) return
            val made = PhotoFolder.copy(doc, dir, name)
            record(jsonOf("a" to "copy", "id" to made.id))
        }

        private fun record(step: JSONObject) {
            steps.put(step)
            count++
            save()
        }

        private fun save() {
            prefs.edit().putString("undo", jsonOf("when" to SimpleDateFormat("dd MMM, HH:mm", Locale.US).format(Date()),
                "tree" to tree.toString(), "steps" to steps).toString()).apply()
        }
    }

    private fun undoLog(): JSONObject? = runCatching { JSONObject(prefs.getString("undo", "")!!) }.getOrNull()
        ?.takeIf { (it.optJSONArray("steps")?.length() ?: 0) > 0 }

    fun undo() {
        if (running) return
        val undo = undoLog() ?: run { summary = "Nothing to undo."; return }
        val tree = Uri.parse(undo.optString("tree"))
        val steps = undo.optJSONArray("steps").objects()
        running = true
        scope.launch {
            var ok = 0
            var failed = 0
            log.add("")
            log.add("Undo the renaming of ${undo.optString("when")}…")
            withContext(Dispatchers.IO) {
                for (s in steps.reversed()) {
                    val id = s.optString("id")
                    val done = runCatching {
                        when (s.optString("a")) {
                            "rename" -> PhotoFolder.byId(tree, id)?.let { PhotoFolder.rename(it, s.optString("name")) } != null
                            "move" -> {
                                val doc = PhotoFolder.byId(tree, id)
                                val from = PhotoFolder.byId(tree, s.optString("from"))
                                val to = PhotoFolder.byId(tree, s.optString("to"))
                                if (doc == null || from == null || to == null) false
                                else PhotoFolder.move(doc, from, to).let { back ->
                                    if (back.name != s.optString("name")) PhotoFolder.rename(back, s.optString("name"))
                                    true
                                }
                            }
                            "copy" -> PhotoFolder.deleteOwn(tree, id, dir = false)
                            "mkdir" -> { PhotoFolder.deleteOwn(tree, id, dir = true); null }
                            else -> null
                        }
                    }.getOrElse { e -> withContext(Dispatchers.Main) { log.add("  ${e.message}") }; false }
                    when (done) {
                        true -> ok++
                        false -> {
                            failed++
                            withContext(Dispatchers.Main) { log.add("  could not undo: ${s.optString("name").ifEmpty { id.substringAfterLast('/') }}") }
                        }
                        null -> Unit
                    }
                }
            }
            prefs.edit().remove("undo").apply()
            localSigs.clear()
            undoWhen = ""
            summary = "Undo finished: $ok change(s) reverted" + if (failed > 0) ", $failed could not be reverted." else "."
            log.add(summary)
            running = false
        }
    }

    companion object {
        val IMAGE_EXTS = setOf(".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".jfif", ".heic", ".heif")
        // Codes are read in the shop's code style (ProductCode.reader()): a list is the codes of one prefix,
        // "6-" / "5a-" / "cc-"; the codes without a prefix (#1023) are one list, "".
        /** File / folder names that already start with a code were renamed before -> never rename them again. */
        private val DONE_RE = Regex("^(\\d{1,2}[A-Za-z]?)\\s*-\\s*(\\d{1,5})(?!\\d)")
        /** Sub folders for one list, e.g. "6-" or "5a-". */
        private val GROUP_DIR_RE = Regex("^\\d{1,2}[A-Za-z]?\\s*-$")
        private val NEVER_RE = Regex("(?!)")

        /** Names starting with a code (group 1 prefix, 2 number): "6-852 Nubia a56", "CC1023 …", "#1023 …". */
        private fun doneRe(): Regex {
            val r = ProductCode.reader()
            return if (r.kind == "prefix") DONE_RE else re("^(?:" + r.jsAnyCode + ")", true)
        }

        /** Sub folders for one list: "6-" or "5a-"; "CC" or "CC-" in the letters style; none for #1023. */
        private fun groupDirRe(): Regex {
            val r = ProductCode.reader()
            return when (r.kind) {
                "letters" -> re("^(?:" + r.prefixes().joinToString("|") + ")\\s*-?$", true)
                "prefix" -> GROUP_DIR_RE
                else -> NEVER_RE
            }
        }

        private fun doneCode(name: String, doneRe: Regex): String? =
            doneRe.find(name)?.let { ProductCode.norm(it.groupValues[1], it.groupValues[2]) }

        /** "6-852 Nubia a56", with only (bracketed) text after it: a note or "(2)". */
        private fun isName(name: String, base: String): Boolean =
            name.startsWith(base, ignoreCase = true) && Regex("""(\s*\([^()]*\))*""").matches(name.substring(base.length))

        /** True if the photo already has the name its code gives it ("6-852 Nubia a56.jpg", "… (2).jpg", or img1.jpg
         *  in the folder "6-852 Nubia a56"): it is left as it is. Any other name, also one starting with a code
         *  ("6-852 Nubia a56 1.jpg", "6-840 …"), is renamed. */
        private fun namedFor(doc: Doc, parent: Doc, base: String): Boolean {
            val stem = doc.name.substringBeforeLast('.')
            if (Regex("""img\d+""", RegexOption.IGNORE_CASE).matches(stem) && isName(parent.name, base)) return true
            return isName(stem, base)
        }

        /** (products bottom line first, codes skipped because they have no product name) - engine.py parse_products. */
        fun parseProducts(text: String): Pair<List<Product>, List<String>> {
            val r = ProductCode.reader()
            val codeStart = re("^(?:" + r.code + ")", true)
            val products = mutableListOf<Product>()
            val skipped = mutableListOf<String>()
            for (raw in text.lines()) {
                val line = bnToAscii(raw).trim()
                val m = codeStart.find(line) ?: continue
                val name = line.substring(m.range.last + 1).trim().replace(Regex("\\s+"), " ")
                val codeText = r.written(m.groupValues[1], m.groupValues[2])
                if (name.none { it.isLetterOrDigit() }) {   // "4-337 ......" = no product name
                    skipped += codeText
                    continue
                }
                val prefix = m.groupValues[1].lowercase()
                products += Product(ProductCode.norm(m.groupValues[1], m.groupValues[2]), "$codeText $name",
                    if (prefix.isEmpty()) "" else "$prefix-")
            }
            return products.reversed().distinctBy { it.code } to skipped
        }

        /** Text in (brackets) from the WhatsApp caption that is not already in the name. */
        fun captionNote(captions: List<String>, label: String): String {
            val notes = mutableListOf<String>()
            for (cap in captions) for (m in Regex("\\(([^()\\n]*)\\)?").findAll(cap)) {
                val text = m.groupValues[1].trim()
                if (text.isNotEmpty() && !label.lowercase().contains(text.lowercase()) && text !in notes) notes += text
            }
            return if (notes.isEmpty()) "" else " (${notes.joinToString("; ")})"
        }

        /** engine.py safe_filename: the same names as the Windows app gives. */
        fun windowsSafeName(name: String): String {
            var n = Regex("[\\\\/:*?\"<>|\\r\\n\\t]+").replace(name, " ")
            n = Regex("\\s+").replace(n, " ").trim().trimEnd('.', ' ')
            return n.take(150).trimEnd('.', ' ').ifEmpty { "unnamed" }
        }
    }
}
