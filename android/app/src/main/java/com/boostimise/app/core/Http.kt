package com.boostimise.app.core

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import okhttp3.Call
import okhttp3.Callback
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okio.BufferedSink
import okio.source
import org.json.JSONArray
import org.json.JSONObject
import org.json.JSONTokener
import java.io.File
import java.io.IOException
import java.util.concurrent.TimeUnit
import kotlin.coroutines.resumeWithException

/** A request that failed: status 0 = no answer (offline, timeout), else the HTTP status.
 *  [code] / [message] come from the JSON error body when there is one (core/net.py's HttpError). */
class HttpError(val status: Int, val code: String, override val message: String, val body: Any? = null) :
    IOException(message)

object Http {
    val client: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(20, TimeUnit.SECONDS)
        .readTimeout(120, TimeUnit.SECONDS)
        .writeTimeout(120, TimeUnit.SECONDS)
        .pingInterval(0, TimeUnit.SECONDS)
        .build()

    private val JSON_TYPE = "application/json".toMediaType()

    fun jsonBody(value: Any?): RequestBody = (when (value) {
        null -> "null"
        is JSONObject, is JSONArray -> value.toString()
        is Map<*, *> -> JSONObject(value).toString()
        else -> value.toString()
    }).toRequestBody(JSON_TYPE)

    /** Reads the error out of the body: {"error": {"code", "message"}} (enhancement server),
     *  {"code", "message"} (Supabase REST), {"error", "error_description"} / {"msg"} (Supabase Auth). */
    fun errorFrom(status: Int, text: String?): HttpError {
        var code = "http_error"
        var message = "Request failed ($status)"
        val body = parseJson(text)
        if (body is JSONObject) {
            val err = body.opt("error")
            if (err is JSONObject) {
                code = err.optString("code").ifEmpty { code }
                message = err.optString("message").ifEmpty { message }
            } else {
                val errStr = (err as? String).orEmpty()
                code = body.optString("error_code").ifEmpty { body.optString("code") }.ifEmpty { errStr }.ifEmpty { code }
                message = body.optString("message").ifEmpty { body.optString("msg") }
                    .ifEmpty { body.optString("error_description") }.ifEmpty { errStr }.ifEmpty { message }
            }
        }
        return HttpError(status, code, message, body)
    }

    fun parseJson(text: String?): Any? {
        if (text.isNullOrBlank()) return null
        return try {
            JSONTokener(text).nextValue()
        } catch (e: Exception) {
            null
        }
    }

    suspend fun Call.await(): Response = suspendCancellableCoroutine { cont ->
        enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                if (!cont.isCancelled) cont.resumeWithException(e)
            }

            override fun onResponse(call: Call, response: Response) {
                cont.resume(response) { _, _, _ -> response.close() }
            }
        })
        cont.invokeOnCancellation { runCatching { cancel() } }
    }

    /** One request; returns the body text, throws HttpError for a non-2xx answer or no answer. */
    suspend fun send(request: Request, client: OkHttpClient = this.client): Pair<Int, String> {
        val req = request.newBuilder().apply {
            if (request.header("User-Agent") == null) header("User-Agent", Config.USER_AGENT)
            if (request.header("Accept") == null) header("Accept", "application/json, */*")
        }.build()
        val resp = try {
            client.newCall(req).await()
        } catch (e: HttpError) {
            throw e
        } catch (e: IOException) {
            throw HttpError(0, "network", e.message ?: e.javaClass.simpleName)
        }
        resp.use {
            val text = withContext(Dispatchers.IO) {
                try {
                    it.body?.string().orEmpty()
                } catch (e: IOException) {
                    throw HttpError(0, "network", e.message ?: "read failed")
                }
            }
            if (!it.isSuccessful) throw errorFrom(it.code, text)
            return it.code to text
        }
    }

    suspend fun getJson(url: String, headers: Map<String, String> = emptyMap()): Any? {
        val b = Request.Builder().url(url)
        headers.forEach { (k, v) -> b.header(k, v) }
        return parseJson(send(b.build()).second)
    }

    suspend fun bytes(url: String): ByteArray {
        val req = Request.Builder().url(url).header("User-Agent", Config.USER_AGENT).build()
        val resp = try {
            client.newCall(req).await()
        } catch (e: IOException) {
            throw HttpError(0, "network", e.message ?: "network")
        }
        resp.use {
            if (!it.isSuccessful) throw errorFrom(it.code, withContext(Dispatchers.IO) { it.body?.string() })
            return withContext(Dispatchers.IO) { it.body!!.bytes() }
        }
    }

    /** A file body that reports upload progress (0..1) and can be cancelled through the call. */
    class ProgressFileBody(
        private val file: File,
        private val type: String,
        private val onProgress: ((Float) -> Unit)?,
    ) : RequestBody() {
        override fun contentType() = type.toMediaType()
        override fun contentLength() = file.length()
        override fun writeTo(sink: BufferedSink) {
            val total = contentLength().coerceAtLeast(1)
            var done = 0L
            var last = 0L
            file.source().use { src ->
                val buf = okio.Buffer()
                while (true) {
                    val n = src.read(buf, 64 * 1024)
                    if (n == -1L) break
                    sink.write(buf, n)
                    done += n
                    val now = System.currentTimeMillis()
                    if (now - last > 80 || done >= total) {
                        last = now
                        onProgress?.invoke(done.toFloat() / total)
                    }
                }
            }
        }
    }
}

// ---- small JSON helpers -----------------------------------------------------------------------
fun JSONObject.optStr(key: String): String? = if (isNull(key)) null else optString(key).takeIf { has(key) }
fun JSONObject.optIntOrNull(key: String): Int? = if (!has(key) || isNull(key)) null else optInt(key)
fun JSONObject.optDoubleOrNull(key: String): Double? = if (!has(key) || isNull(key)) null else optDouble(key)
fun JSONArray?.objects(): List<JSONObject> =
    if (this == null) emptyList() else (0 until length()).mapNotNull { optJSONObject(it) }
fun JSONArray?.strings(): List<String> =
    if (this == null) emptyList() else (0 until length()).map { optString(it) }

fun jsonOf(vararg pairs: Pair<String, Any?>): JSONObject = JSONObject().apply {
    for ((k, v) in pairs) put(k, v ?: JSONObject.NULL)
}
