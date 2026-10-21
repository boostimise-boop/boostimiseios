package com.boostimise.app.tools.fetch

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import com.boostimise.app.core.Account
import com.boostimise.app.core.HttpError
import com.boostimise.app.core.Storage
import com.boostimise.app.tools.enhancer.EnhanceSettings
import com.boostimise.app.tools.enhancer.EnhancerApi
import com.boostimise.app.tools.rename.RenameTool
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.sync.withPermit
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.File
import kotlin.coroutines.coroutineContext

private const val MIN_FACE_CONFIDENCE = 0.8
// The biggest face must be at least this part of the photo's short side. People photos sent for a
// cover measured 0.15 and up; a contact picture or wallpaper in a phone screenshot is smaller.
private const val MIN_FACE_SHARE = 0.07
private const val PARALLEL = 2      // photos checked / enhanced at once: the server's GPU works on one while the next uploads
private const val POLL_MS = 1000L

val ENHANCE_SCALES = listOf(4 to "4× (recommended)", 2 to "2×", 0 to "Don't enhance")

/** Faces big and sure enough to be a photo of people, from the server's analysis. */
fun peopleIn(data: JSONObject?): Int {
    val a = data?.optJSONObject("analysis") ?: JSONObject()
    val short = minOf(a.optInt("width"), a.optInt("height"))
    val faces = data?.optJSONArray("faces") ?: return 0
    return (0 until faces.length()).mapNotNull { faces.optJSONObject(it) }
        .count { it.optDouble("confidence", 0.0) >= MIN_FACE_CONFIDENCE && it.optDouble("size_px", 0.0) >= MIN_FACE_SHARE * short }
}

/**
 * Fetch & Enhance (tools/fetch_enhance): Fetch by Code's search, then for each code only the photos with
 * people in them are kept and enhanced (4×) on the enhancement server. Phones, screenshots and other photos
 * without a person are never saved. They get the names Rename by Match gives: one photo
 * "2-226 Realme P3 Ultra.jpg", two or more a folder "2-221 Oppo a6 pro 5g" with img1.jpg, img2.jpg …
 * Each enhancement is taken from the account's daily quota exactly like in the Photo Enhancer.
 */
class FetchEnhanceTool(scope: CoroutineScope) : FetchTool("fetch_enhance", "Fetch & Enhance", scope) {
    var scale by mutableStateOf(prefs.getInt("scale", 4))   // 4 | 2 | 0 = don't enhance
    var peopleOnly by mutableStateOf(prefs.getBoolean("people_only", true))
    var addNote by mutableStateOf(prefs.getBoolean("add_note", false))
    /** Why the quota said no (limit | blocked | onboarding | update_required): shows the limit dialog. */
    var denied by mutableStateOf("")

    override fun saveSettings() {
        super.saveSettings()
        prefs.edit().putInt("scale", scale).putBoolean("people_only", peopleOnly).putBoolean("add_note", addNote).apply()
    }

    override suspend fun prepare() {
        if (!peopleOnly && scale == 0) return
        try {
            EnhancerApi.health()
        } catch (e: Exception) {
            throw StopRun("cannot reach the enhancement server (${e.message}). Check the internet and try again.")
        }
        if (scale > 0 && Account.quota?.remaining == 0) {
            denied = "limit"
            throw StopRun("no enhancements left today.")
        }
    }

    /** The photo ("base.jpg", "base (note).jpg") or folder ("base (note)/img1.jpg") saved for a code before,
     *  or Fetch by Code's "base 1.jpg". */
    override suspend fun existingLookup(): (String) -> String? {
        val names = Storage.namesUnder(relPath)
        val top = names.filter { '/' !in it }.toSet()
        return { base ->
            names.firstOrNull { n ->
                val dir = n.substringBefore('/', "")
                if (dir.isNotEmpty()) RenameTool.isName(dir, base)
                else "." + n.substringAfterLast('.', "").lowercase() in RenameTool.IMAGE_EXTS &&
                    RenameTool.isName(n.substringBeforeLast('.'), base)
            }?.let { n -> n.substringBefore('/') + if ('/' in n) "/" else "" }
                ?: com.boostimise.app.core.Codes.existingOutput(top, base)
        }
    }

    // ---- one code ----------------------------------------------------------------------------
    override suspend fun saveCode(row: Row, base: String, files: List<Pair<ByteArray, String>>, failed: Int,
                                  images: List<JSONObject>): Pair<String, Int> {
        val code = row.item.code
        val work = File(Storage.app.cacheDir, "fetch_enhance/${System.nanoTime()}")
        try {
            val paths = withContext(Dispatchers.IO) {
                work.mkdirs()
                files.mapIndexed { i, (data, ext) ->
                    val (d, e) = uploadable(data, ext)
                    File(work, "${code.replace('/', '-')} ${i + 1}$e").apply { writeBytes(d) }
                }
            }
            val kept = if (peopleOnly) people(row, paths) else paths
            if (kept.isEmpty()) {
                val text = "No photos of people (${paths.size} skipped: phones, screenshots …)"
                row.kind = "none"
                row.text = text
                log.add("[$code] $text")
                return "none" to 0
            }
            val (photos, plain) = if (scale > 0) enhanceAll(row, kept)
            else withContext(Dispatchers.IO) { kept.map { it.readBytes() to "." + it.extension.lowercase() } } to 0

            val captions = images.map { it.optString("caption") }.filter { it.isNotEmpty() }
            val label = RenameTool.windowsSafeName(base + if (addNote) RenameTool.captionNote(captions, base) else "")
            val saved = saveNamed(label, photos)

            var text = "${photos.size} photo${if (photos.size > 1) "s" else ""} " + if (scale > 0) "enhanced $scale×" else "saved"
            if (kept.size < paths.size) text += ", ${paths.size - kept.size} without people skipped"
            if (plain > 0) text += " ($plain not enhanced, saved as received)"
            if (failed > 0) text += " ($failed download(s) failed)"
            row.kind = if (plain > 0) "error" else "ok"
            row.text = text
            row.saved = saved
            log.add("[$code] $text → $relPath$saved")
            return "ok" to photos.size
        } catch (e: StopRun) {
            row.kind = "error"
            row.text = "Not saved: ${e.message}"
            log.add("[$code] not saved: ${e.message}")
            throw e
        } finally {
            withContext(Dispatchers.IO) { work.deleteRecursively() }
        }
    }

    /** One photo: "<label>.jpg". Several: the folder "<label>" with img1.jpg, img2.jpg … Returns what was saved. */
    private suspend fun saveNamed(label: String, photos: List<Pair<ByteArray, String>>): String {
        if (photos.size == 1) {
            val (data, ext) = photos[0]
            val name = "$label$ext"
            Storage.delete(relPath, name)
            Storage.save(relPath, name, data)
            return name
        }
        val dir = Storage.picturesPath("${folder.ifBlank { title }}/$label")
        photos.forEachIndexed { i, (data, ext) ->
            val name = "img${i + 1}$ext"
            Storage.delete(dir, name)
            Storage.save(dir, name, data)
        }
        return "$label/"
    }

    /** The server takes JPG, PNG and WEBP; anything else (rare on WhatsApp: GIF, HEIC) becomes a JPG. */
    private fun uploadable(data: ByteArray, ext: String): Pair<ByteArray, String> {
        if (ext.removePrefix(".") in EnhancerApi.ACCEPTED) return data to ext
        val bmp = BitmapFactory.decodeByteArray(data, 0, data.size) ?: return data to ext
        val out = ByteArrayOutputStream()
        bmp.compress(Bitmap.CompressFormat.JPEG, 95, out)
        bmp.recycle()
        return out.toByteArray() to ".jpg"
    }

    /** Runs [f] on every item, [PARALLEL] at a time; a failure is that item's result, except a stop. */
    private suspend fun <T, R> parallel(items: List<T>, f: suspend (T) -> R): List<Result<R>> = coroutineScope {
        val gate = Semaphore(PARALLEL)
        items.map { item ->
            async {
                gate.withPermit {
                    try {
                        Result.success(f(item))
                    } catch (e: CancellationException) {
                        throw e
                    } catch (e: StopRun) {
                        throw e
                    } catch (e: Exception) {
                        Result.failure(e)
                    }
                }
            }
        }.awaitAll()
    }

    /** The photos with people in them (a photo that can't be checked is kept). */
    private suspend fun people(row: Row, paths: List<File>): List<File> {
        val code = row.item.code
        row.kind = "working"
        row.text = "Looking for people in ${paths.size} photo(s)…"
        activity = "[$code] looking for people in ${paths.size} photo(s)"
        val results = parallel(paths) { peopleIn(EnhancerApi.analyze(it, it.name, 4)) }
        val kept = paths.zip(results).filter { (path, r) ->
            r.exceptionOrNull()?.let { e ->
                log.add("[$code] photo ${path.nameWithoutExtension.substringAfterLast(' ')} could not be checked for " +
                    "people (${e.message}), so it is kept")
                return@filter true
            }
            r.getOrDefault(0) > 0
        }.map { it.first }
        log.add("[$code] ${kept.size} of ${paths.size} photo(s) with people")
        return kept
    }

    /** The enhanced photos (a photo the server fails on is kept as received), and how many failed. */
    private suspend fun enhanceAll(row: Row, paths: List<File>): Pair<List<Pair<ByteArray, String>>, Int> {
        val code = row.item.code
        Account.quota?.remaining?.let { left ->
            if (left < paths.size) {
                denied = "limit"
                throw StopRun("$left enhancement(s) left today, $code needs ${paths.size}.")
            }
        }
        val settings = EnhanceSettings(scale = scale)
        val lock = Mutex()
        var done = 0
        row.kind = "working"
        row.text = "Enhancing $scale× 0/${paths.size}…"
        activity = "[$code] enhancing ${paths.size} photo(s) $scale×"
        val results = parallel(paths) { path ->
            val bytes = enhanceOne(path, settings)
            lock.withLock {
                done++
                row.text = "Enhancing $scale× $done/${paths.size}…"
            }
            bytes
        }
        var plain = 0
        val photos = paths.zip(results).map { (path, r) ->
            r.getOrNull()?.let { it to ".jpg" } ?: run {
                plain++
                log.add("[$code] photo ${path.nameWithoutExtension.substringAfterLast(' ')} not enhanced " +
                    "(${r.exceptionOrNull()?.message}), saved as received")
                withContext(Dispatchers.IO) { path.readBytes() } to "." + path.extension.lowercase()
            }
        }
        return photos to plain
    }

    // ---- one photo on the enhancement server (the Photo Enhancer's quota steps) ---------------
    /** Records how a reserved enhancement ended; failed / cancelled ones are refunded. */
    private fun settle(recordId: String?, status: String, error: String? = null, jobId: String? = null,
                       w: Int? = null, h: Int? = null, secs: Double? = null) {
        if (recordId == null) return
        scope.launch {
            Account.finishEnhancement(recordId, status, jobId, w, h, secs, error)
            if (status != "completed") {
                delay(600)
                Account.refresh()
            }
        }
    }

    private suspend fun enhanceOne(file: File, s: EnhanceSettings): ByteArray {
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeFile(file.path, bounds)
        val (w, h) = bounds.outWidth.takeIf { it > 0 } to bounds.outHeight.takeIf { it > 0 }
        var recordId: String? = null
        var jobId = ""
        while (true) {
            coroutineContext.ensureActive()
            val res = Account.reserveEnhancement(file.name, file.length(), s.mode, s.scale, w, h)
            Account.setQuota(res.quota)
            if (!res.ok) {   // nothing more can start today
                if (res.code == "onboarding") Account.refresh() else denied = res.code
                throw StopRun(res.message.ifEmpty { "Today's limit is reached." })
            }
            recordId = res.id
            try {
                jobId = EnhancerApi.submit(file, file.name, s) {}
                break
            } catch (e: CancellationException) {
                settle(recordId, "cancelled")
                throw e
            } catch (e: HttpError) {
                if (e.status != 503) {
                    settle(recordId, "failed", error = e.message)
                    throw e
                }
                settle(recordId, "cancelled")   // the server's queue is full (other users): try again
                delay(3000)
            } catch (e: Exception) {
                settle(recordId, "failed", error = e.message)
                throw e
            }
        }
        recordId?.let { id -> scope.launch { Account.finishEnhancement(id, jobId = jobId) } }
        val job = try {
            waitJob(jobId)
        } catch (e: CancellationException) {
            scope.launch { EnhancerApi.cancel(jobId) }
            settle(recordId, "cancelled")
            throw e
        } catch (e: Exception) {
            settle(recordId, "failed", error = e.message)
            throw e
        }
        val result = job.optJSONObject("result") ?: JSONObject()
        val inp = job.optJSONObject("input") ?: JSONObject()
        settle(recordId, "completed", jobId = jobId,
            w = result.optInt("width").takeIf { it > 0 } ?: inp.optInt("output_width").takeIf { it > 0 },
            h = result.optInt("height").takeIf { it > 0 } ?: inp.optInt("output_height").takeIf { it > 0 },
            secs = result.optJSONObject("benchmark")?.optDouble("total_seconds")?.takeIf { !it.isNaN() })
        val target = File(file.parentFile, file.nameWithoutExtension + " enhanced.jpg")
        try {
            EnhancerApi.download(EnhancerApi.resultUrl(job, "jpg"), target, target.name)
            return withContext(Dispatchers.IO) { target.readBytes() }
        } finally {
            scope.launch { EnhancerApi.cancel(jobId) }   // saved here: delete it from the server
        }
    }

    private suspend fun waitJob(jobId: String): JSONObject {
        var misses = 0
        while (true) {
            coroutineContext.ensureActive()
            val res = try {
                EnhancerApi.jobs(listOf(jobId)) ?: JSONObject()
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                if (++misses >= 20) throw IllegalStateException("Lost contact with the enhancement server")
                delay(POLL_MS)
                continue
            }
            misses = 0
            val jobs = res.optJSONArray("jobs")
            val job = (0 until (jobs?.length() ?: 0)).mapNotNull { jobs?.optJSONObject(it) }.firstOrNull { it.optString("job_id") == jobId }
            if (job == null) {
                val missing = res.optJSONArray("missing")
                if (missing != null && (0 until missing.length()).any { missing.optString(it) == jobId })
                    throw IllegalStateException("Job expired or the server restarted")
            } else when (job.optString("status")) {
                "completed" -> return job
                "failed", "cancelled" -> throw IllegalStateException(
                    job.optJSONObject("error")?.optString("message")?.ifEmpty { null } ?: "processing ${job.optString("status")}")
            }
            delay(POLL_MS)
        }
    }
}
