package com.boostimise.app.tools.enhancer

import android.graphics.BitmapFactory
import android.net.Uri
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import com.boostimise.app.core.Account
import com.boostimise.app.core.HttpError
import com.boostimise.app.core.Storage
import com.boostimise.app.wa.Jobs
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import org.json.JSONObject
import java.io.File

private const val MAX_ACTIVE = 2       // jobs on the server at once (1 processing + 1 queued keeps the GPU busy)
private const val POLL_MS = 600L
private val ACTIVE = setOf("uploading", "queued", "processing")

fun isFinished(s: String) = s in setOf("done", "failed", "cancelled")
fun isStartable(s: String) = s == "idle" || isFinished(s)

/** One photo in the list (tools/photo_enhancer/queue.py's QueueItem). */
class QueueItem(val id: String, val file: File, val name: String, val size: Long, settings: EnhanceSettings) {
    var settings by mutableStateOf(settings)
    var dims by mutableStateOf<Pair<Int, Int>?>(null)
    var status by mutableStateOf("idle")   // idle waiting uploading queued processing done failed cancelled
    var stage by mutableStateOf("idle")
    var progress by mutableStateOf(0f)     // 0..100 (server)
    var upload by mutableStateOf(0f)       // 0..1
    var jobId by mutableStateOf<String?>(null)
    var job by mutableStateOf<JSONObject?>(null)
    var error by mutableStateOf<String?>(null)
    var recordId: String? = null
    var uploadJob: Job? = null
}

/**
 * The AI Photo Enhancer - the website's enhancer (lib/useEnhanceQueue.ts): photos wait until you press
 * Enhance; uploads are paced a few jobs ahead of the GPU; all active jobs are polled with one request.
 * Each enhancement is taken from the account's daily quota first and its outcome recorded.
 */
class EnhancerTool(private val scope: CoroutineScope) {
    val items = mutableStateListOf<QueueItem>()
    var selectedId by mutableStateOf<String?>(null)
    var settings by mutableStateOf(EnhanceSettings())
    var applyAll by mutableStateOf(false)
    var health by mutableStateOf<JSONObject?>(null)
        private set
    var offline by mutableStateOf(false)
        private set
    /** "<id>:<scale>" -> analysis (null = failed) */
    val analyses = mutableStateMapOf<String, JSONObject?>()
    var toast by mutableStateOf("")
    var denied by mutableStateOf("")       // limit | blocked | onboarding - shows the limit dialog
    private var polling = false
    private var failures = 0
    private var cooldownUntil = 0L
    private var batchAnnounced = true
    private var counter = 0
    private var healthJob: Job? = null

    val selected get() = items.firstOrNull { it.id == selectedId } ?: items.firstOrNull()
    fun maxUploadMb(): Double = health?.optJSONObject("limits")?.optDouble("max_upload_mb", 50.0) ?: 50.0

    fun startHealth() {
        if (healthJob?.isActive == true) return
        healthJob = scope.launch {
            while (isActive) {
                try {
                    health = EnhancerApi.health()
                    offline = false
                } catch (e: Exception) {
                    offline = true
                }
                delay(10_000)
            }
        }
    }

    fun stopHealth() {
        healthJob?.cancel()
    }

    private fun updateBusy() {
        val active = items.count { it.status in ACTIVE || it.status == "waiting" }
        if (active > 0) Jobs.begin("enhancer", "Enhancing $active photo(s)…") else Jobs.end("enhancer")
    }

    // ---- adding ------------------------------------------------------------------------------
    fun add(uris: List<Uri>) {
        scope.launch {
            val maxMb = maxUploadMb()
            val q = Account.quota
            var added = 0
            var skipped = 0
            for (uri in uris) {
                val file = try {
                    Storage.copyToCache(uri, "enhancer", "photo.jpg")
                } catch (e: Exception) {
                    skipped++
                    continue
                }
                val ext = file.name.substringAfterLast('.', "").lowercase()
                val err = when {
                    ext !in EnhancerApi.ACCEPTED -> "Unsupported file type. Please use JPG, JPEG, PNG or WEBP."
                    file.length() == 0L -> "The file is empty."
                    file.length() > maxMb * 1024 * 1024 -> "File is too large (max ${maxMb.toInt()} MB)."
                    else -> null
                }
                if (err != null) {
                    toast = "${file.name}: $err"
                    file.delete()
                    skipped++
                    continue
                }
                val it = QueueItem("${System.currentTimeMillis().toString(16)}-${counter++}", file, file.name, file.length(), settings)
                val o = BitmapFactory.Options().apply { inJustDecodeBounds = true }
                BitmapFactory.decodeFile(file.path, o)
                if (o.outWidth > 0) it.dims = o.outWidth to o.outHeight
                items += it
                added++
                if (selectedId == null) selectedId = it.id
            }
            if (q?.remaining != null && added > q.remaining) {
                toast = "You have ${q.remaining} enhancement(s) left today; the rest wait for tomorrow or a plan."
            }
            if (skipped > 0 && toast.isEmpty()) toast = "$skipped file(s) could not be added."
            selected?.let { ensureAnalysis(it) }
        }
    }

    fun ensureAnalysis(it: QueueItem) {
        val key = "${it.id}:${settings.scale}"
        if (key in analyses || offline) return
        analyses[key] = null
        scope.launch {
            analyses[key] = try {
                EnhancerApi.analyze(it.file, it.name, settings.scale)
            } catch (e: Exception) {
                null
            }
        }
    }

    fun analysisOf(it: QueueItem): JSONObject? = analyses["${it.id}:${settings.scale}"]

    // ---- running -----------------------------------------------------------------------------
    fun enhance(onlySelected: Boolean) {
        val ids = if (onlySelected && !applyAll) listOfNotNull(selected?.id) else null
        val targets = items.filter { isStartable(it.status) && (ids == null || it.id in ids) }
        if (targets.isEmpty()) return
        for (it in targets) it.jobId?.let { j -> scope.launch { EnhancerApi.cancel(j) } }
        if (targets.size > 1) batchAnnounced = false
        for (it in targets) {
            it.settings = settings
            it.status = "waiting"
            it.stage = "waiting"
            it.progress = 0f
            it.upload = 0f
            it.jobId = null
            it.job = null
            it.error = null
            it.recordId = null
        }
        updateBusy()
        pump()
    }

    private fun pump() {
        val wait = cooldownUntil - System.currentTimeMillis()
        if (wait > 0) {
            scope.launch {
                delay(wait + 50)
                pump()
            }
            return
        }
        while (true) {
            val active = items.count { it.status in ACTIVE }
            val next = items.firstOrNull { it.status == "waiting" }
            if (next == null || active >= MAX_ACTIVE) break
            next.status = "uploading"
            next.stage = "uploading"
            next.upload = 0f
            start(next)
        }
        val sent = items.filter { it.status != "idle" }
        if (sent.size > 1 && !batchAnnounced && sent.all { isFinished(it.status) }) {
            batchAnnounced = true
            val ok = sent.count { it.status == "done" }
            if (ok > 0) toast = "$ok of ${sent.size} photos enhanced"
        }
        updateBusy()
    }

    private fun start(it: QueueItem) {
        it.uploadJob = scope.launch {
            val w = it.dims
            val res = Account.reserveEnhancement(it.name, it.size, it.settings.mode, it.settings.scale, w?.first, w?.second)
            Account.setQuota(res.quota)
            if (it !in items || it.status != "uploading") {
                res.id?.let { id -> Account.finishEnhancement(id, status = "cancelled") }
                pump()
                return@launch
            }
            if (!res.ok) {
                // nothing more can start today: park this photo and everything still waiting
                for (i in items) if (i === it || i.status == "waiting") {
                    i.status = "idle"
                    i.stage = "idle"
                    i.upload = 0f
                }
                if (res.code == "onboarding") Account.refresh() else denied = res.code
                updateBusy()
                return@launch
            }
            it.recordId = res.id
            try {
                val jobId = EnhancerApi.submit(it.file, it.name, it.settings) { f -> it.upload = f }
                if (it !in items) {
                    EnhancerApi.cancel(jobId)
                    settle(res.id, "cancelled")
                } else {
                    res.id?.let { id -> scope.launch { Account.finishEnhancement(id, jobId = jobId) } }
                    it.status = "queued"
                    it.stage = "queued"
                    it.jobId = jobId
                    poll()
                }
            } catch (e: kotlinx.coroutines.CancellationException) {
                settle(res.id, "cancelled")
            } catch (e: HttpError) {
                if (e.status == 503) {   // the server's queue is full (other users): try again shortly
                    settle(res.id, "cancelled")
                    it.status = "waiting"
                    it.stage = "waiting"
                    it.recordId = null
                    cooldownUntil = System.currentTimeMillis() + 3000
                } else {
                    val msg = if (e.status == 0) "Cannot reach the enhancement server." else e.message
                    settle(res.id, "failed", error = msg)
                    it.status = "failed"
                    it.stage = "failed"
                    it.error = msg
                    toast = "${it.name}: $msg"
                }
            } catch (e: Exception) {
                settle(res.id, "failed", error = e.message)
                it.status = "failed"
                it.error = e.message
            }
            pump()
        }
    }

    private fun settle(recordId: String?, status: String, error: String? = null, jobId: String? = null,
                       w: Int? = null, h: Int? = null, secs: Double? = null) {
        if (recordId == null) return
        scope.launch {
            Account.finishEnhancement(recordId, status, jobId, w, h, secs, error)
            if (status != "completed") {
                delay(600)
                Account.refresh()   // failed / cancelled ones are refunded
            }
        }
    }

    private fun tracked() = items.filter {
        it.jobId != null && (it.status == "queued" || it.status == "processing" ||
            (it.status == "done" && it.job?.has("png_ready") == true && it.job?.optBoolean("png_ready") == false))
    }

    private fun poll() {
        if (polling) return
        polling = true
        scope.launch {
            while (true) {
                val tracked = tracked()
                if (tracked.isEmpty()) break
                try {
                    val res = EnhancerApi.jobs(tracked.mapNotNull { it.jobId }) ?: JSONObject()
                    failures = 0
                    onJobs(tracked, res)
                } catch (e: Exception) {
                    failures++
                    if (failures >= 20) {
                        for (it in tracked) if (it.status != "done") {
                            settle(it.recordId, "failed", error = "Lost contact with the enhancement server")
                            it.status = "failed"
                            it.error = "Lost contact with the enhancement server"
                        }
                        toast = "Lost contact with the enhancement server"
                        failures = 0
                        break
                    }
                }
                delay(POLL_MS)
            }
            polling = false
            updateBusy()
        }
    }

    private fun onJobs(tracked: List<QueueItem>, res: JSONObject) {
        val byId = mutableMapOf<String, JSONObject>()
        res.optJSONArray("jobs")?.let { a -> for (i in 0 until a.length()) a.optJSONObject(i)?.let { byId[it.optString("job_id")] = it } }
        val missing = res.optJSONArray("missing")?.let { a -> (0 until a.length()).map { a.optString(it) }.toSet() } ?: emptySet()
        var finished = false
        for (it in tracked) {
            val jid = it.jobId ?: continue
            val j = byId[jid]
            if (j == null) {
                if (jid in missing && it in items) {
                    if (it.status != "done") settle(it.recordId, "failed", error = "Job expired or the server restarted")
                    it.status = "failed"
                    it.error = "Job expired or the server restarted"
                    finished = true
                }
                continue
            }
            if (it !in items) continue
            when (val status = j.optString("status")) {
                "completed" -> {
                    if (it.status != "done") {
                        finished = true
                        val result = j.optJSONObject("result") ?: JSONObject()
                        val bench = result.optJSONObject("benchmark") ?: JSONObject()
                        val inp = j.optJSONObject("input") ?: JSONObject()
                        settle(it.recordId, "completed", jobId = jid,
                            w = result.optInt("width").takeIf { v -> v > 0 } ?: inp.optInt("output_width").takeIf { v -> v > 0 },
                            h = result.optInt("height").takeIf { v -> v > 0 } ?: inp.optInt("output_height").takeIf { v -> v > 0 },
                            secs = bench.optDouble("total_seconds").takeIf { v -> !v.isNaN() })
                        if (batchAnnounced && result.has("width")) {
                            toast = "Enhanced to ${result.optInt("width")}×${result.optInt("height")} in " +
                                "%.1fs".format(bench.optDouble("total_seconds", 0.0))
                        }
                    }
                    it.status = "done"
                    it.stage = "done"
                    it.progress = 100f
                    it.job = j
                }
                "failed", "cancelled" -> {
                    val err = j.optJSONObject("error")?.optString("message")
                    settle(it.recordId, status, error = err)
                    it.status = status
                    it.stage = status
                    it.job = j
                    it.error = err
                    if (status == "failed") toast = "${it.name}: ${err ?: "processing failed"}"
                    finished = true
                }
                else -> {
                    val queued = status == "queued"
                    it.status = if (queued) "queued" else "processing"
                    it.stage = if (queued) "queued" else j.optString("stage").ifEmpty { "processing" }
                    it.progress = j.optDouble("progress", 0.0).toFloat()
                    it.job = j
                }
            }
        }
        if (finished) pump()
    }

    // ---- editing the list --------------------------------------------------------------------
    private fun stop(it: QueueItem) {
        it.uploadJob?.cancel()
        it.jobId?.let { j -> scope.launch { EnhancerApi.cancel(j) } }   // cancels if running, deletes results if finished
        if (it.status == "queued" || it.status == "processing") settle(it.recordId, "cancelled")
    }

    fun remove(it: QueueItem) {
        stop(it)
        items.remove(it)
        it.file.delete()
        if (selectedId == it.id) selectedId = items.firstOrNull()?.id
        pump()
    }

    fun clear() {
        items.toList().forEach { stop(it); it.file.delete() }
        items.clear()
        selectedId = null
        updateBusy()
    }

    // ---- downloads ---------------------------------------------------------------------------
    var downloading by mutableStateOf("")
        private set

    fun download(it: QueueItem, kind: String) {
        val job = it.job ?: return
        scope.launch {
            downloading = kind
            try {
                val tmp = File(Storage.app.cacheDir, "dl-${System.nanoTime()}")
                val name = EnhancerApi.download(EnhancerApi.resultUrl(job, kind), tmp,
                    "${job.optString("original_name").ifEmpty { "photo" }}_enhanced.$kind")
                Storage.saveStream(Storage.picturesPath("Enhanced"), name) { os -> tmp.inputStream().use { s -> s.copyTo(os) } }
                tmp.delete()
                toast = "Saved to Pictures/Boostimise/Enhanced/$name"
            } catch (e: Exception) {
                toast = "Download failed: ${e.message}"
            } finally {
                downloading = ""
            }
        }
    }

    /** One ZIP with all finished results, in Download/Boostimise. */
    fun downloadZip(fmt: String) {
        val ids = items.filter { it.status == "done" }.mapNotNull { it.jobId }
        if (ids.isEmpty()) return
        scope.launch {
            downloading = "zip"
            try {
                val tmp = File(Storage.app.cacheDir, "zip-${System.nanoTime()}")
                val name = EnhancerApi.download("${com.boostimise.app.core.Config.API_BASE}/api/download-zip", tmp,
                    "enhanced_photos_${ids.size}_$fmt.zip", form = mapOf("ids" to ids.joinToString(","), "format" to fmt))
                Storage.saveStream(Storage.downloadsPath(), name) { os -> tmp.inputStream().use { s -> s.copyTo(os) } }
                tmp.delete()
                toast = "Saved to Download/Boostimise/$name"
            } catch (e: Exception) {
                toast = "Download failed: ${e.message}"
            } finally {
                downloading = ""
            }
        }
    }
}
