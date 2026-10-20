package com.boostimise.app.tools.rename

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Matrix
import android.media.ExifInterface
import java.io.ByteArrayInputStream
import java.security.MessageDigest
import kotlin.math.cos
import kotlin.math.max
import kotlin.math.min

/**
 * Exact hash + two perceptual hashes of a photo (tools/match_renamer/engine.py image_signature),
 * so the same photo still matches after WhatsApp has resized / recompressed it.
 */
class Signature(val sha1: String, val dhash: Long, val phash: Long) {
    /** 0 = identical file, small = same photo, large = different photo. */
    fun difference(o: Signature): Int =
        if (sha1 == o.sha1) 0 else max(java.lang.Long.bitCount(dhash xor o.dhash), java.lang.Long.bitCount(phash xor o.phash))

    companion object {
        // Cosine table for the 8 lowest frequencies of a 32-point DCT (used by phash)
        private val DCT = Array(8) { u -> DoubleArray(32) { x -> cos(Math.PI * (2 * x + 1) * u / 64) } }

        /** Null if the bytes are not a picture Android can open. */
        fun of(data: ByteArray): Signature? {
            val gray = grayscale(data) ?: return null
            val (px, w, h) = gray

            val d = resize(px, w, h, 9, 8)
            var dhash = 0L
            for (r in 0 until 8) for (c in 0 until 8) dhash = (dhash shl 1) or (if (d[r * 9 + c] > d[r * 9 + c + 1]) 1L else 0L)

            val p = resize(px, w, h, 32, 32)
            val tmp = Array(32) { y -> DoubleArray(8) { u -> (0 until 32).sumOf { x -> p[y * 32 + x] * DCT[u][x] } } }
            val coef = DoubleArray(64)
            for (v in 0 until 8) for (u in 0 until 8) coef[v * 8 + u] = (0 until 32).sumOf { y -> tmp[y][u] * DCT[v][y] }
            val sorted = coef.sorted()
            val median = (sorted[31] + sorted[32]) / 2
            var phash = 0L
            for (c in coef) phash = (phash shl 1) or (if (c > median) 1L else 0L)

            val sha1 = MessageDigest.getInstance("SHA-1").digest(data).joinToString("") { "%02x".format(it) }
            return Signature(sha1, dhash, phash)
        }

        /** The photo turned upright (EXIF) in gray, decoded small (at least 160 px a side when it is that big). */
        private fun grayscale(data: ByteArray): Triple<FloatArray, Int, Int>? {
            val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
            BitmapFactory.decodeByteArray(data, 0, data.size, bounds)
            if (bounds.outWidth <= 0 || bounds.outHeight <= 0) return null
            var sample = 1
            while (min(bounds.outWidth, bounds.outHeight) / (sample * 2) >= 160) sample *= 2
            var bmp = BitmapFactory.decodeByteArray(data, 0, data.size,
                BitmapFactory.Options().apply { inSampleSize = sample; inPreferredConfig = Bitmap.Config.ARGB_8888 }) ?: return null
            val turn = runCatching { exifMatrix(ExifInterface(ByteArrayInputStream(data))) }.getOrNull()
            if (turn != null) {
                val turned = Bitmap.createBitmap(bmp, 0, 0, bmp.width, bmp.height, turn, true)
                if (turned !== bmp) bmp.recycle()
                bmp = turned
            }
            val w = bmp.width
            val h = bmp.height
            val argb = IntArray(w * h)
            bmp.getPixels(argb, 0, w, 0, 0, w, h)
            bmp.recycle()
            // as Pillow's convert("L")
            val out = FloatArray(w * h) { i ->
                val c = argb[i]
                (((c shr 16) and 0xff) * 299 + ((c shr 8) and 0xff) * 587 + (c and 0xff) * 114) / 1000f
            }
            return Triple(out, w, h)
        }

        private fun exifMatrix(exif: ExifInterface): Matrix? {
            val m = Matrix()
            when (exif.getAttributeInt(ExifInterface.TAG_ORIENTATION, ExifInterface.ORIENTATION_NORMAL)) {
                ExifInterface.ORIENTATION_FLIP_HORIZONTAL -> m.setScale(-1f, 1f)
                ExifInterface.ORIENTATION_ROTATE_180 -> m.setRotate(180f)
                ExifInterface.ORIENTATION_FLIP_VERTICAL -> m.setScale(1f, -1f)
                ExifInterface.ORIENTATION_TRANSPOSE -> { m.setRotate(90f); m.postScale(-1f, 1f) }
                ExifInterface.ORIENTATION_ROTATE_90 -> m.setRotate(90f)
                ExifInterface.ORIENTATION_TRANSVERSE -> { m.setRotate(-90f); m.postScale(-1f, 1f) }
                ExifInterface.ORIENTATION_ROTATE_270 -> m.setRotate(-90f)
                else -> return null
            }
            return m
        }

        /** Area-average downscale (every source pixel counts, so small details don't alias). */
        private fun resize(src: FloatArray, w: Int, h: Int, ow: Int, oh: Int): FloatArray {
            val rows = FloatArray(ow * h)
            for (y in 0 until h) axis(w, ow) { o, i, f -> rows[y * ow + o] += src[y * w + i] * f }
            val out = FloatArray(ow * oh)
            for (x in 0 until ow) axis(h, oh) { o, i, f -> out[o * ow + x] += rows[i * ow + x] * f }
            return out
        }

        /** Calls add(out, in, weight) for every input pixel overlapping each output pixel (weights sum to 1). */
        private inline fun axis(n: Int, on: Int, add: (Int, Int, Float) -> Unit) {
            val scale = n.toDouble() / on
            for (o in 0 until on) {
                val a = o * scale
                val b = (o + 1) * scale
                var i = a.toInt()
                while (i < b && i < n) {
                    val cover = min(b, i + 1.0) - max(a, i.toDouble())
                    if (cover > 0) add(o, i, (cover / scale).toFloat())
                    i++
                }
            }
        }
    }
}
