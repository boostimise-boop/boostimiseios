package com.boostimise.app.tools.model

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import com.boostimise.app.core.re
import com.google.mlkit.vision.common.InputImage
import com.google.mlkit.vision.text.TextRecognition
import com.google.mlkit.vision.text.latin.TextRecognizerOptions
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.tasks.await
import java.io.ByteArrayOutputStream

data class Identification(val model: String, val confidence: String, val reason: String)

fun cleanModelName(name0: String?): String {
    var name = (name0 ?: "").trim().lowercase().replace("/", " & ")
    name = Regex("\\s+").replace(name, " ").trim { it in " .,:;-\"'" }
    name = Regex("^apple\\s+(?=iphone)").replace(name, "")
    name = Regex("^samsung\\s+galaxy\\s+").replace(name, "samsung ")
    name = Regex("^galaxy\\s+").replace(name, "samsung ")
    name = re("^xiaomi\\s+(?=(redmi|poco)\\b)").replace(name, "")
    name = Regex("(?<=[a-z0-9])\\+(?=\\s|$)").replace(name, " plus")
    return name.ifEmpty { "unknown" }
}

/** Photo bytes -> JPEG at most 1600 px (WhatsApp's own size), or null when unusable. */
fun prepareImage(raw: ByteArray, maxSide: Int = 1600): ByteArray? = try {
    val bmp = BitmapFactory.decodeByteArray(raw, 0, raw.size)
    if (bmp == null || minOf(bmp.width, bmp.height) < 48) null
    else {
        val k = minOf(1.0, maxSide.toDouble() / maxOf(bmp.width, bmp.height))
        val out = if (k < 1) Bitmap.createScaledBitmap(bmp, (bmp.width * k).toInt(), (bmp.height * k).toInt(), true) else bmp
        ByteArrayOutputStream().use { s ->
            out.compress(Bitmap.CompressFormat.JPEG, 88, s)
            s.toByteArray()
        }
    }
} catch (e: Throwable) {
    null
}

/**
 * Free model detection (tools/model_detect/text_identifier.py): the phone model name read from the
 * chat text posted with the photos and from text printed inside the photos (on-device OCR, ML Kit):
 * names ("Galaxy A06"), model numbers ("SM-A065F", "V2111") and brand logos.
 */
class TextIdentifier(private val log: (String) -> Unit) {
    private val ocrLock = Mutex()
    private var useOcr = true
    private val recognizer by lazy { TextRecognition.getClient(TextRecognizerOptions.DEFAULT_OPTIONS) }

    companion object {
        private val BRANDS: List<Pair<List<String>, String>> = listOf(
            listOf("apple", "iphone") to "iphone", listOf("iphone") to "iphone", listOf("i", "phone") to "iphone",
            listOf("samsung", "galaxy") to "samsung", listOf("samsung") to "samsung", listOf("galaxy") to "samsung",
            listOf("xiaomi", "redmi") to "redmi", listOf("xiaomi", "poco") to "poco", listOf("xiaomi", "mi") to "mi",
            listOf("xiaomi") to "xiaomi", listOf("redmi") to "redmi", listOf("poco") to "poco", listOf("mi") to "mi",
            listOf("realme") to "realme", listOf("narzo") to "realme narzo", listOf("oppo") to "oppo", listOf("vivo") to "vivo",
            listOf("iqoo") to "iqoo", listOf("infinix") to "infinix", listOf("tecno") to "tecno", listOf("itel") to "itel",
            listOf("one", "plus") to "oneplus", listOf("oneplus") to "oneplus", listOf("nokia") to "nokia",
            listOf("motorola") to "motorola", listOf("moto") to "moto", listOf("huawei") to "huawei", listOf("honor") to "honor",
            listOf("google", "pixel") to "google pixel", listOf("pixel") to "google pixel", listOf("symphony") to "symphony",
            listOf("walton") to "walton", listOf("lava") to "lava", listOf("nothing", "phone") to "nothing phone",
            listOf("asus") to "asus", listOf("zte") to "zte", listOf("nord") to "oneplus nord",
            listOf("walton", "nexg") to "nexg", listOf("nexg") to "nexg", listOf("walton", "xanon") to "xanon", listOf("xanon") to "xanon",
            listOf("spark") to "tecno spark", listOf("camon") to "tecno camon", listOf("pova") to "tecno pova",
            listOf("reno") to "oppo reno", listOf("innova") to "symphony innova", listOf("helio") to "symphony helio",
        ).sortedBy { -it.first.size }

        private val WORDS = setOf(
            "pro", "max", "plus", "ultra", "lite", "mini", "prime", "neo", "power", "play", "note", "hot", "spark",
            "camon", "pova", "smart", "zero", "fold", "flip", "edge", "fe", "se", "xr", "xs", "x", "5g", "4g",
            "nord", "ce", "go", "core", "narzo", "reno", "find", "nova", "mate", "turbo", "star", "gt", "active",
            "air", "vip", "pop", "phantom", "innova", "helio", "xanon", "nexg",
            "e", "s", "t", "r", "i", "+", "pro+", "a", "c", "y", "v", "k", "f", "m", "p", "g", "z",
        )
        private val MODEL_TOKEN = Regex("^(?:[a-z]{0,6}\\d{1,4}[a-z]{0,4}\\+?|[a-z]\\d{0,4})$")
        private val NUM_FIRST = Regex("^\\d{1,4}[a-z]?\\+?$")
        private val LETTER_FIRST = Regex("^[a-z]{1,4}\\d{1,5}[a-z]{0,4}\\+?$")
        private val NET = setOf("5g", "4g")
        private val YEAR = Regex("^20[0-3]\\d$")
        private val SERIES_START = setOf("note", "hot", "spark", "camon", "pova", "narzo", "nord", "reno", "find", "nova",
            "mate", "galaxy", "redmi", "poco", "iphone", "pixel", "smart", "zero", "edge")
        private val NEEDS_SERIES = setOf("tecno", "infinix", "itel", "samsung", "vivo", "oppo", "poco", "symphony", "walton",
            "nexg", "motorola", "moto", "huawei", "lava", "zte", "asus")
        private val FILLER = setOf("cover", "covers", "case", "back", "er", "model", "mobile", "set", "phone", "for", "r",
            "no", "name", "number")
        private val OS_WORD = Regex("^(ui|os)\\d*$")
        private val PEEL = setOf(
            "samsung", "galaxy", "apple", "iphone", "xiaomi", "redmi", "poco", "realme", "narzo", "oppo", "vivo",
            "infinix", "tecno", "itel", "oneplus", "nokia", "motorola", "huawei", "honor", "google", "pixel",
            "note", "hot", "spark", "camon", "pova", "nord", "reno", "nova", "mate", "pro", "max", "plus", "ultra",
            "lite", "mini", "mi", "smart", "zero", "walton", "symphony", "lava",
            "gt", "neo", "go", "pop", "nexg", "xanon", "innova", "helio",
            "dual", "sim", "global", "version", "snapdragon", "smartphone", "mobile", "price",
        ).sortedByDescending { it.length }
        private val DIGIT_SUFFIX = Regex("(\\d)(pro|max|plus|ultra|lite|mini|dpi|prime|power|play|edge|(?:se|fe|neo|air|vip)(?![a-z]))")
        private val GLUED_5G = Regex("([a-z\\d])([45]g)(?![0-9b])")
        private val NOISE = re("\\bs\\s*/\\s*n\\b|\\bimei\\s*\\d?|\\b\\d{1,2}\\s*[/+]\\s*\\d{2,4}\\s*(gb|ge|g|r)?\\b" +
            "|\\b\\d{2,4}\\s*[/+]\\s*\\d{1,2}\\s*(gb|g|r)?\\b|\\b\\d+\\s*(gb|tb|mb|mp|mah|hz|mm|w)\\b")
        private val O_FOR_ZERO = Regex("^([a-z]{0,2})(\\d[\\do]*)([a-z]?)$")
        private val SPEC = Regex("^\\d+(gb|tb|mb|mp|mah|w|hz|mm)$")
        private val WATERMARK_WORDS = re("\\b(?:shot|captured)\\s*(?:on|by|with)\\b|camera", true)

        private fun splitGlued(tok0: String): List<String> {
            var tok = tok0
            val out = mutableListOf<String>()
            while (true) {
                val w = PEEL.firstOrNull { w ->
                    val rest = tok.substring(minOf(w.length, tok.length))
                    tok.startsWith(w) && rest.isNotEmpty() &&
                        (rest[0].isDigit() || PEEL.any { rest.startsWith(it) } || MODEL_TOKEN.matches(rest))
                }
                if (w == null) {
                    out += tok
                    return out
                }
                out += w
                tok = tok.substring(w.length)
            }
        }

        private fun fixOcrDigits(tok: String): String {
            val m = O_FOR_ZERO.matchEntire(tok) ?: return tok
            return m.groupValues[1] + m.groupValues[2].replace('o', '0') + m.groupValues[3]
        }

        fun tokens(text0: String): List<String> {
            var text = ProductCodes.stripCodes(text0).lowercase()   // never read the product code as a model
            text = DIGIT_SUFFIX.replace(text, "$1 $2")
            text = NOISE.replace(text, " ")
            text = GLUED_5G.replace(text, "$1 $2 ")
            return Regex("[^a-z0-9+]+").replace(text, " ").split(" ").filter { it.isNotEmpty() }
                .flatMap { splitGlued(it) }.map { fixOcrDigits(it) }
        }

        private fun isModelToken(t: String): Boolean {
            if (t in WORDS) return true
            return !SPEC.matches(t) && (NUM_FIRST.matches(t) || LETTER_FIRST.matches(t) || Regex("[a-z]").matches(t))
        }

        private fun hasNumber(tokens: List<String>) = tokens.any { t -> t.any { it.isDigit() } }
        private fun isAlpha(s: String) = s.isNotEmpty() && s.all { it.isLetter() }

        private fun brandAt(toks: List<String>, i: Int): Pair<String, Int>? {
            for ((words, name) in BRANDS) {
                if (i + words.size <= toks.size && toks.subList(i, i + words.size) == words) return name to words.size
            }
            return null
        }

        /** 'Samsung Galaxy A70 5G' -> ['samsung a70 5g'] ; 'I phone 17pro max cover' -> ['iphone 17 pro max'] */
        fun modelsWithBrand(text: String?): List<String> {
            val out = mutableListOf<String>()
            val lines = (text ?: "").lines().ifEmpty { listOf("") }
            for (line in lines) out += modelsInLine(tokens(line))
            return out
        }

        private fun modelsInLine(toks: List<String>): List<String> {
            val out = mutableListOf<String>()
            var i = 0
            while (i < toks.size) {
                val b = brandAt(toks, i)
                if (b == null) {
                    i++
                    continue
                }
                val (name, n) = b
                var j = i + n
                if (j < toks.size && OS_WORD.matches(toks[j])) {
                    i = j + 1
                    continue
                }
                while (j < toks.size && toks[j] in FILLER) j++
                var model = mutableListOf<String>()
                if (j + 1 < toks.size && !isModelToken(toks[j]) && isAlpha(toks[j]) && toks[j].length in 2..10 &&
                    toks[j] !in FILLER && brandAt(toks, j) == null && isModelToken(toks[j + 1]) && hasNumber(listOf(toks[j + 1]))) {
                    model += toks[j]
                    j++
                }
                var extraWord = true
                var hasNum = false
                while (j < toks.size && model.size < 5) {
                    val t = toks[j]
                    if (!isModelToken(t)) {
                        val nxt = toks.getOrElse(j + 1) { "" }
                        if (extraWord && model.isNotEmpty() && !hasNum && isAlpha(t) && t.length in 2..10 &&
                            t !in FILLER && brandAt(toks, j) == null && isModelToken(nxt) &&
                            Regex("[a-z]").containsMatchIn(nxt) && hasNumber(listOf(nxt))) {
                            extraWord = false
                            model += t
                            j++
                            continue
                        }
                        break
                    }
                    val nb = brandAt(toks, j)
                    if (nb != null && nb.first != name && nb.first != "samsung" && model.isNotEmpty()) break
                    if (hasNumber(listOf(t))) {
                        if (t in NET) {
                            if (model.isNotEmpty()) {
                                model += t
                                j++
                            }
                            break
                        }
                        if (hasNum && !YEAR.matches(t)) break
                        hasNum = true
                    }
                    model += t
                    j++
                }
                if (model.firstOrNull() == "galaxy") model = model.drop(1).toMutableList()
                while (model.isNotEmpty() && model.last().length == 1 && model.last()[0].isLetter() && model.last() !in setOf("x", "s", "e")) {
                    model.removeAt(model.size - 1)
                }
                val joined = mutableListOf<String>()
                for (t in model) {
                    if (joined.isNotEmpty() && name != "iphone" && joined.last().length == 1 && joined.last()[0].isLetter() && t.all { it.isDigit() }) {
                        joined[joined.size - 1] = joined.last() + t
                    } else if (joined.isEmpty() || joined.last() != t) joined += t
                }
                model = joined
                var ok = hasNumber(model) || (name == "iphone" && model.isNotEmpty() && model[0] in setOf("x", "xr", "xs", "se"))
                val bare = model.isNotEmpty() && model[0][0].isDigit() && model[0].length != 4
                if ((name in NEEDS_SERIES && bare) || (name == "symphony helio" && !bare)) ok = false
                if (ok) out += "$name ${model.joinToString(" ")}"
                i = maxOf(j, i + 1)
            }
            return out
        }

        /** A line that is only a model: 'A70', 'Note 7 pro', 'Y21 cover', 'c55 5g'. */
        fun modelWithoutBrand(text: String): String? {
            val stripped = ProductCodes.stripCodes(text).trim()
            if (stripped.isEmpty() || stripped.length > 30) return null
            val toks = tokens(stripped).filter { it !in FILLER }
            if (toks.isEmpty() || toks.size > 5 || !toks.all { isModelToken(it) } || !hasNumber(toks)) return null
            val first = toks[0]
            if (!(Regex("^[a-z]{1,4}\\d").containsMatchIn(first) || (first in SERIES_START && toks.size > 1))) return null
            return toks.joinToString(" ")
        }

        /** (shop name, known) for the model written in a text, or null. */
        fun nameIn(text: String): Pair<String, Boolean?>? {
            val found = modelsWithBrand(text)
            if (found.isNotEmpty()) return ModelNumbers.resolve(cleanModelName(found[0]), text)
            val numbers = ModelNumbers.namesFromCodes(text)
            return if (numbers.isNotEmpty()) cleanModelName(numbers[0]) to true else null
        }

        fun short(text: String) = "'" + text.split(Regex("\\s+")).filter { it.isNotEmpty() }.joinToString(" ").take(60) + "'"
    }

    private data class Row(val text: String, val mid: Double, val h: Double)

    /** Text rows inside a photo, top to bottom; middle and height as fractions of the photo's height. */
    private suspend fun readPhoto(bmp: Bitmap?): List<Row> {
        if (!useOcr || bmp == null) return emptyList()
        val result = ocrLock.withLock {
            try {
                recognizer.process(InputImage.fromBitmap(bmp, 0)).await()
            } catch (e: Exception) {
                if (useOcr) log("Photo text reading (OCR) is not available: ${e.message}")
                useOcr = false
                return emptyList()
            }
        }
        val height = bmp.height.toDouble()
        data class Box(val mid: Double, val h: Double, val left: Int, val text: String)
        val boxes = mutableListOf<Box>()
        for (block in result.textBlocks) for (line in block.lines) {
            val r = line.boundingBox ?: continue
            if (line.confidence < 0.5f) continue
            boxes += Box((r.top + r.bottom) / 2.0, (r.bottom - r.top).toDouble(), r.left, line.text)
        }
        val rows = mutableListOf<Triple<Double, Double, MutableList<Pair<Int, String>>>>()
        for (b in boxes.sortedWith(compareBy({ it.mid }, { it.h }, { it.left }))) {
            val last = rows.lastOrNull()
            if (last != null && Math.abs(b.mid - last.first) < 0.5 * minOf(b.h, last.second)) last.third += b.left to b.text
            else rows += Triple(b.mid, b.h, mutableListOf(b.left to b.text))
        }
        return rows.map { (mid, h, parts) -> Row(parts.sortedBy { it.first }.joinToString(" ") { it.second }, mid / height, h / height) }
    }

    private fun watermark(rows: List<Row>, k: Int): Boolean {
        val near = rows.subList(maxOf(0, k - 1), minOf(rows.size, k + 2)).joinToString(" ") { it.text }
        if (WATERMARK_WORDS.containsMatchIn(near)) return true
        val r = rows[k]
        return r.mid > 0.85 && r.h < 0.05 && rows.size <= 6
    }

    private data class PhotoHit(val name: String, val kind: String, val text: String)

    private fun inPhoto(rows: List<Row>, smallPrint: Boolean = false): PhotoHit? {
        val texts = rows.map { it.text }
        val whole = texts.joinToString("\n")
        var weak: PhotoHit? = null
        for (k in texts.indices) {
            val options = if (k + 1 < texts.size) listOf(listOf(k), listOf(k, k + 1)) else listOf(listOf(k))
            for (lines in options) {
                val cand = lines.joinToString(" ") { texts[it] }
                val found = modelsWithBrand(cand)
                if (found.isEmpty()) continue
                val (name, known) = ModelNumbers.resolve(cleanModelName(found[0]), whole)
                if (known == false) weak = weak ?: PhotoHit(name, "number?", cand)
                else if (!smallPrint && lines.any { watermark(rows, it) }) weak = weak ?: PhotoHit(name, "watermark", cand)
                else return PhotoHit(name, "sure", cand)
                break
            }
        }
        val numbers = ModelNumbers.namesFromCodes(whole)
        if (numbers.isNotEmpty()) {
            return PhotoHit(cleanModelName(numbers[0]), "sure", texts.firstOrNull { ModelNumbers.namesFromCodes(it).isNotEmpty() } ?: whole)
        }
        return weak
    }

    private fun logo(rows: List<Row>): String? {
        for (r in rows) {
            val toks = tokens(r.text)
            val b = if (toks.isNotEmpty()) brandAt(toks, 0) else null
            if (b != null && b.second == toks.size) return b.first
        }
        return null
    }

    private fun decode(jpeg: ByteArray): Bitmap? = BitmapFactory.decodeByteArray(jpeg, 0, jpeg.size)

    private fun upscale(bmp: Bitmap, factor: Double = 2.0, limit: Int = 2600): Bitmap? {
        val f = minOf(factor, limit.toDouble() / maxOf(bmp.width, bmp.height))
        if (f <= 1.05) return null
        return Bitmap.createScaledBitmap(bmp, (bmp.width * f).toInt(), (bmp.height * f).toInt(), true)
    }

    /** The photo cut in n x n overlapping parts, each enlarged, so small print becomes readable. */
    private fun tiles(bmp: Bitmap, n: Int = 2, overlap: Double = 0.15, target: Int = 1800): List<Bitmap> {
        val tw = bmp.width.toDouble() / n
        val th = bmp.height.toDouble() / n
        val out = mutableListOf<Bitmap>()
        for (r in 0 until n) for (c in 0 until n) {
            val x0 = maxOf(0.0, (c - overlap) * tw).toInt()
            val y0 = maxOf(0.0, (r - overlap) * th).toInt()
            val x1 = minOf(bmp.width.toDouble(), (c + 1 + overlap) * tw).toInt()
            val y1 = minOf(bmp.height.toDouble(), (r + 1 + overlap) * th).toInt()
            var tile = Bitmap.createBitmap(bmp, x0, y0, x1 - x0, y1 - y0)
            val f = minOf(3.0, target.toDouble() / maxOf(tile.width, tile.height))
            if (f > 1.05) tile = Bitmap.createScaledBitmap(tile, (tile.width * f).toInt(), (tile.height * f).toInt(), true)
            out += tile
        }
        return out
    }

    /**
     * texts: chat texts of the code, closest first. shared: texts that sit closer to another code -
     * used only when nothing else is found. seen gets the text read in the photos (the AI's hint).
     * Returns null when no model name is written anywhere.
     */
    suspend fun identify(images: List<ByteArray>, texts: List<String>, shared: List<String> = emptyList(),
                         seen: MutableList<String>? = null, brandOnly: Boolean = true): Identification? {
        val later = mutableListOf<Triple<Int, Int, Identification>>()
        fun keep(rank: Int, ident: Identification) {
            later += Triple(rank, later.size, ident)
        }
        for (t in texts) {
            val hit = nameIn(t)
            if (hit != null && hit.second != false) return Identification(hit.first, "high", "written in chat: ${short(t)}")
            if (hit != null) keep(3, Identification("${hit.first} ?", "low", "model number not in the phone list: ${short(t)}"))
        }

        fun photoHit(hit: PhotoHit, where: String): Identification? {
            when (hit.kind) {
                "sure" -> return Identification(hit.name, "high", "$where: ${short(hit.text)}")
                "number?" -> keep(3, Identification("${hit.name} ?", "low", "model number not in the phone list, $where: ${short(hit.text)}"))
                else -> keep(4, Identification(hit.name, "low", "camera watermark, $where: ${short(hit.text)} - the photo may be taken with another phone"))
            }
            return null
        }

        val bitmaps = images.map { decode(it) }
        var logo: Pair<String, Int>? = null
        for (enlarge in listOf(false, true)) {
            for ((k0, bmp) in bitmaps.withIndex()) {
                val k = k0 + 1
                if (bmp == null) continue
                val rows = readPhoto(if (enlarge) upscale(bmp) else bmp)
                if (seen != null && !enlarge) seen += rows.map { it.text }
                inPhoto(rows)?.let { hit -> photoHit(hit, "text in photo $k")?.let { return it } }
                if (logo == null) logo(rows)?.let { logo = it to k }
            }
        }

        for (t in texts) for (line in t.lines()) {
            val found = modelWithoutBrand(line) ?: continue
            val lg = logo
            val both = if (lg != null) modelsWithBrand("${lg.first} $found") else emptyList()
            if (both.isNotEmpty()) {
                return Identification(cleanModelName(both[0]), "medium", "written in chat: '${line.take(40)}', brand in photo ${lg!!.second}")
            }
            return Identification(cleanModelName(found), "medium", "written in chat (no brand): '${line.take(40)}'")
        }

        // a brand is printed on the phone: its model number may be in the small print next to it
        logo?.let { (_, k) ->
            bitmaps[k - 1]?.let { bmp ->
                for (tile in tiles(bmp)) {
                    inPhoto(readPhoto(tile), smallPrint = true)?.let { hit -> photoHit(hit, "small print in photo $k")?.let { return it } }
                }
            }
        }

        for (t in shared) {
            val hit = nameIn(t) ?: continue
            keep(2, Identification(hit.first + if (hit.second == false) " ?" else "", "low", "written next to another code: ${short(t)}"))
            break
        }
        if (later.isNotEmpty()) return later.minWith(compareBy({ it.first }, { it.second })).third
        logo?.let { (b, k) -> if (brandOnly) return Identification("$b ?", "low", "only the brand '$b' is printed (photo $k)") }
        return null
    }
}
