package com.covercanvasbd.boostimise.tools.enhancer

import com.covercanvasbd.boostimise.core.Config
import com.covercanvasbd.boostimise.core.Http
import com.covercanvasbd.boostimise.core.Http.await
import com.covercanvasbd.boostimise.core.HttpError
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.FormBody
import okhttp3.MultipartBody
import okhttp3.Request
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.net.URLEncoder

data class EnhanceSettings(
    val mode: String = "auto",          // auto | face | restore | upscale
    val scale: Int = 2,                 // 2 | 4
    val strength: Double = 0.8,
    val faceEnhance: Boolean = true,
    val faceModel: String = "auto",     // auto | codeformer | gfpgan | compare
    val fidelity: Double? = null,       // null = adaptive
    val denoise: Double? = null,        // null = automatic
    val sharpen: Double? = null,        // null = automatic
    val colorEnhance: Boolean = true,
    val quality: Int = 95,
)

/** Settings each mode starts from (the user can still change everything). */
fun presetFor(mode: String, s: EnhanceSettings): EnhanceSettings = when (mode) {
    "upscale" -> s.copy(mode = mode, faceEnhance = false, colorEnhance = false, strength = 1.0, sharpen = null, denoise = null)
    "face" -> s.copy(mode = mode, faceEnhance = true, colorEnhance = false, strength = 0.8)
    "restore" -> s.copy(mode = mode, faceEnhance = true, colorEnhance = true, strength = 0.9)
    else -> s.copy(mode = mode, faceEnhance = true, colorEnhance = true, strength = 0.8)
}

/** Client for the AI enhancement server (bmapi.boostimise.com) - tools/photo_enhancer/api.py. */
object EnhancerApi {
    private val base = Config.API_BASE
    val ACCEPTED = listOf("jpg", "jpeg", "png", "webp")

    fun contentType(name: String) = when (name.substringAfterLast('.', "").lowercase()) {
        "png" -> "image/png"
        "webp" -> "image/webp"
        else -> "image/jpeg"
    }

    suspend fun health(): JSONObject? = Http.getJson("$base/api/health") as? JSONObject

    suspend fun analyze(file: File, name: String, scale: Int): JSONObject? {
        val body = MultipartBody.Builder().setType(MultipartBody.FORM)
            .addFormDataPart("scale", scale.toString())
            .addFormDataPart("image", name, Http.ProgressFileBody(file, contentType(name), null))
            .build()
        val req = Request.Builder().url("$base/api/analyze").post(body).build()
        return Http.parseJson(Http.send(req).second) as? JSONObject
    }

    suspend fun jobs(ids: List<String>): JSONObject? {
        val q = URLEncoder.encode(ids.joinToString(","), "UTF-8")
        return Http.getJson("$base/api/jobs?ids=$q") as? JSONObject
    }

    suspend fun cancel(jobId: String) {
        runCatching { Http.send(Request.Builder().url("$base/api/job/$jobId").delete().build()) }
    }

    fun resultUrl(job: JSONObject, kind: String) = base + (job.optJSONObject("urls")?.optString(kind).orEmpty())

    private fun js(b: Boolean) = if (b) "true" else "false"

    /** Uploads the photo with its settings; returns the job id. */
    suspend fun submit(file: File, name: String, s: EnhanceSettings, onUpload: (Float) -> Unit): String {
        val b = MultipartBody.Builder().setType(MultipartBody.FORM)
            .addFormDataPart("mode", s.mode).addFormDataPart("scale", s.scale.toString())
            .addFormDataPart("strength", "%.2f".format(java.util.Locale.US, s.strength))
            .addFormDataPart("face_enhance", js(s.faceEnhance)).addFormDataPart("face_model", s.faceModel)
            .addFormDataPart("color_enhance", js(s.colorEnhance)).addFormDataPart("quality", s.quality.toString())
        // omitted fields = "automatic" on the server
        s.fidelity?.let { b.addFormDataPart("fidelity", "%.2f".format(java.util.Locale.US, it)) }
        s.denoise?.let { b.addFormDataPart("denoise", "%.2f".format(java.util.Locale.US, it)) }
        s.sharpen?.let { b.addFormDataPart("sharpen", "%.2f".format(java.util.Locale.US, it)) }
        b.addFormDataPart("image", name, Http.ProgressFileBody(file, contentType(name), onUpload))
        val (status, text) = Http.send(Request.Builder().url("$base/api/enhance").post(b.build()).build())
        val data = Http.parseJson(text) as? JSONObject ?: JSONObject()
        if (status != 202 || data.optString("job_id").isEmpty()) throw HttpError(status, "http_error", "Upload failed ($status)", data)
        return data.getString("job_id")
    }

    /** Streams a GET (or POST form) into [target]; returns the file name the server gives. */
    suspend fun download(url: String, target: File, fallback: String, form: Map<String, String>? = null,
                         onProgress: (Float) -> Unit = {}): String {
        val rb = Request.Builder().url(url).header("User-Agent", Config.USER_AGENT)
        if (form != null) rb.post(FormBody.Builder().apply { form.forEach { (k, v) -> add(k, v) } }.build())
        val resp = try {
            Http.client.newCall(rb.build()).await()
        } catch (e: IOException) {
            throw HttpError(0, "network", e.message ?: "network")
        }
        return resp.use {
            if (!it.isSuccessful) throw Http.errorFrom(it.code, withContext(Dispatchers.IO) { it.body?.string() })
            val cd = it.header("Content-Disposition").orEmpty()
            val name = Regex("filename\\*\\s*=\\s*[^']*''([^;]+)", RegexOption.IGNORE_CASE).find(cd)?.groupValues?.get(1)
                ?.let { n -> java.net.URLDecoder.decode(n, "UTF-8") }
                ?: Regex("filename\\s*=\\s*\"?([^\";]+)\"?", RegexOption.IGNORE_CASE).find(cd)?.groupValues?.get(1)
                ?: fallback
            val total = it.body!!.contentLength()
            withContext(Dispatchers.IO) {
                target.outputStream().use { os ->
                    val src = it.body!!.byteStream()
                    val buf = ByteArray(64 * 1024)
                    var done = 0L
                    while (true) {
                        val n = src.read(buf)
                        if (n < 0) break
                        os.write(buf, 0, n)
                        done += n
                        if (total > 0) onProgress(done.toFloat() / total)
                    }
                }
            }
            name
        }
    }
}
