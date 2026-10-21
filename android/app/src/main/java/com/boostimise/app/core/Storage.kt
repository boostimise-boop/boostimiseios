package com.boostimise.app.core

import android.content.ContentValues
import android.content.Context
import android.content.SharedPreferences
import android.net.Uri
import android.os.Environment
import android.provider.MediaStore
import android.provider.OpenableColumns
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File
import java.io.InputStream

/**
 * Where the tools put their files. Photos go to the phone's gallery under Pictures/Boostimise/<tool>,
 * text and ZIP files to Download/Boostimise. Android 10+ lets an app write there without asking.
 */
object Storage {
    lateinit var app: Context
        private set

    fun init(context: Context) {
        app = context.applicationContext
    }

    fun prefs(name: String): SharedPreferences = app.getSharedPreferences(name, Context.MODE_PRIVATE)

    fun picturesPath(sub: String) = "${Environment.DIRECTORY_PICTURES}/Boostimise/${sub.trim('/')}/"
    fun downloadsPath(sub: String = "") =
        "${Environment.DIRECTORY_DOWNLOADS}/Boostimise/" + (if (sub.isBlank()) "" else "${sub.trim('/')}/")

    private fun mimeFor(name: String): String = when (name.substringAfterLast('.', "").lowercase()) {
        "jpg", "jpeg" -> "image/jpeg"
        "png" -> "image/png"
        "webp" -> "image/webp"
        "gif" -> "image/gif"
        "heic" -> "image/heic"
        "bmp" -> "image/bmp"
        "zip" -> "application/zip"
        "txt" -> "text/plain"
        "csv" -> "text/csv"
        else -> "application/octet-stream"
    }

    private fun collection(relPath: String): Uri =
        if (relPath.startsWith(Environment.DIRECTORY_PICTURES)) MediaStore.Images.Media.EXTERNAL_CONTENT_URI
        else MediaStore.Downloads.EXTERNAL_CONTENT_URI

    /** File names this app saved in that folder (MediaStore only shows an app its own files). */
    suspend fun namesIn(relPath: String): Set<String> = withContext(Dispatchers.IO) {
        val out = mutableSetOf<String>()
        val uri = collection(relPath)
        app.contentResolver.query(uri, arrayOf(MediaStore.MediaColumns.DISPLAY_NAME),
            "${MediaStore.MediaColumns.RELATIVE_PATH}=?", arrayOf(relPath), null)?.use { c ->
            while (c.moveToNext()) out += c.getString(0)
        }
        out
    }

    /** Like [namesIn], also in its sub folders: "2-226 Realme P3 Ultra.jpg", "2-221 Oppo a6 pro 5g/img1.jpg". */
    suspend fun namesUnder(relPath: String): Set<String> = withContext(Dispatchers.IO) {
        val out = mutableSetOf<String>()
        app.contentResolver.query(collection(relPath),
            arrayOf(MediaStore.MediaColumns.RELATIVE_PATH, MediaStore.MediaColumns.DISPLAY_NAME),
            "${MediaStore.MediaColumns.RELATIVE_PATH} LIKE ?", arrayOf("$relPath%"), null)?.use { c ->
            while (c.moveToNext()) out += (c.getString(0) ?: "").removePrefix(relPath) + c.getString(1)
        }
        out
    }

    /** Deletes this app's own file of that name in the folder (to replace it). */
    suspend fun delete(relPath: String, name: String) = withContext(Dispatchers.IO) {
        runCatching {
            app.contentResolver.delete(collection(relPath),
                "${MediaStore.MediaColumns.RELATIVE_PATH}=? AND ${MediaStore.MediaColumns.DISPLAY_NAME}=?",
                arrayOf(relPath, name))
        }
    }

    /** Saves bytes as relPath/name; returns the new file's content Uri. */
    suspend fun save(relPath: String, name: String, data: ByteArray): Uri = saveStream(relPath, name) { it.write(data) }

    suspend fun saveStream(relPath: String, name: String, write: (java.io.OutputStream) -> Unit): Uri =
        withContext(Dispatchers.IO) {
            val values = ContentValues().apply {
                put(MediaStore.MediaColumns.DISPLAY_NAME, name)
                put(MediaStore.MediaColumns.MIME_TYPE, mimeFor(name))
                put(MediaStore.MediaColumns.RELATIVE_PATH, relPath)
                put(MediaStore.MediaColumns.IS_PENDING, 1)
            }
            val resolver = app.contentResolver
            val uri = resolver.insert(collection(relPath), values) ?: throw IllegalStateException("Could not create $name")
            try {
                resolver.openOutputStream(uri)!!.use(write)
                resolver.update(uri, ContentValues().apply { put(MediaStore.MediaColumns.IS_PENDING, 0) }, null, null)
            } catch (e: Exception) {
                resolver.delete(uri, null, null)
                throw e
            }
            uri
        }

    fun displayName(uri: Uri): String? = runCatching {
        app.contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { c ->
            if (c.moveToFirst()) c.getString(0) else null
        }
    }.getOrNull()

    fun open(uri: Uri): InputStream? = app.contentResolver.openInputStream(uri)

    /** Copies a picked / shared file into the app's cache (uploads need a real file with a size). */
    suspend fun copyToCache(uri: Uri, dir: String, fallbackName: String): File = withContext(Dispatchers.IO) {
        val name = safeFileName(displayName(uri) ?: fallbackName).ifEmpty { fallbackName }
        val folder = File(app.cacheDir, dir).apply { mkdirs() }
        var file = File(folder, name)
        var n = 2
        while (file.exists()) {
            file = File(folder, "${name.substringBeforeLast('.')} ($n).${name.substringAfterLast('.', "jpg")}")
            n++
        }
        open(uri)!!.use { input -> file.outputStream().use { input.copyTo(it) } }
        file
    }

    /** A text file as UTF-8, or UTF-16 when it starts with that BOM (Notepad's "Unicode", as the Windows app reads it). */
    suspend fun readText(uri: Uri): String = withContext(Dispatchers.IO) {
        val raw = open(uri)!!.use { it.readBytes() }
        val utf16 = raw.size >= 2 && ((raw[0] == 0xFF.toByte() && raw[1] == 0xFE.toByte()) || (raw[0] == 0xFE.toByte() && raw[1] == 0xFF.toByte()))
        (if (utf16) raw.toString(Charsets.UTF_16) else raw.toString(Charsets.UTF_8)).removePrefix("﻿")
    }

    suspend fun writeText(uri: Uri, text: String) = withContext(Dispatchers.IO) {
        app.contentResolver.openOutputStream(uri, "wt")!!.use { it.write(text.toByteArray()) }
    }
}
