package com.covercanvasbd.boostimise.tools.model

import com.covercanvasbd.boostimise.core.ProductCode
import com.covercanvasbd.boostimise.core.Storage
import com.covercanvasbd.boostimise.core.re

/** Product codes in chat text and the codes list / result file (tools/model_detect/codes.py). */
object ProductCodes {
    // 5a-403, 5A-403, 5a - 403, 5a_403, 5a:403 ... and the codes typed in a chat: core/ProductCode.kt
    val CODE_RE = ProductCode.CODE_RE
    val NOT_DONE = setOf("", "not found", "no image", "unknown")

    fun norm(prefix: String, number: String) = ProductCode.norm(prefix, number)
    fun split(code: String): Pair<String, Int> = ProductCode.split(code)

    class M(val start: Int, val end: Int)

    /** The codes written in a chat message ("redmi 12 8+128" and "at 10:30" are not codes). */
    fun codeMatches(text: String?): List<M> = ProductCode.codeMatches(text).map { M(it.range.first, it.range.last + 1) }

    fun codesInText(text: String?): List<String> = ProductCode.codesInText(text)

    /** The text with the codes in it replaced by spaces. */
    fun stripCodes(text: String?): String {
        var t = text ?: ""
        for (m in codeMatches(t).reversed()) t = t.substring(0, m.start) + " " + t.substring(m.end)
        return t
    }

    /** A model counts as finished unless it is empty, a status word, or only a guess ("redmi ?"). */
    fun isDone(model: String?): Boolean {
        val m = (model ?: "").trim().lowercase()
        return m !in NOT_DONE && !m.endsWith("?")
    }

    /** The codes list, kept line by line so the result has the same layout. */
    class CodeList(text: String) {
        val lines = mutableListOf<Pair<String, String?>>()        // raw line, code
        val blocks = linkedMapOf<String, MutableList<String>>()   // prefix -> codes in order
        val label = mutableMapOf<String, String>()                // code -> spelling in the list

        init {
            for (raw in text.lines()) {
                val s = raw.trim()
                val m = if (s.isNotEmpty()) CODE_RE.find(s) else null
                if (m == null || m.range.first != 0) {
                    lines += raw.trimEnd() to null
                    continue
                }
                val code = norm(m.groupValues[1], m.groupValues[2])
                lines += raw.trimEnd() to code
                if (code !in label) {
                    label[code] = s.substring(0, m.range.last + 1)
                    blocks.getOrPut(m.groupValues[1].lowercase()) { mutableListOf() } += code
                }
            }
        }

        val codes get() = blocks.values.flatten()
        fun prefixRange(prefix: String): Pair<Int, Int> = blocks[prefix]!!.map { split(it).second }.let { it.min() to it.max() }

        /** The models already written in the list ("5a-403 samsung a70"). */
        fun existing(): Map<String, String> {
            val out = mutableMapOf<String, String>()
            for ((raw, code) in lines) {
                if (code == null) continue
                val s = raw.trim()
                val m = CODE_RE.find(s) ?: continue
                out[code] = s.substring(m.range.last + 1).trim()
            }
            return out
        }

        /** "<code> <model>" for every code, keeping the list's order and blank lines. */
        fun write(models: Map<String, String>): String = lines.joinToString("\n") { (raw, code) ->
            if (code == null) raw else "${label[code]} ${models[code].orEmpty()}".trimEnd()
        } + "\n"
    }
}

/**
 * Model numbers -> the name the shop uses (tools/model_detect/model_codes.py): "SM-A065F" ->
 * "samsung a06", "V2111" -> "vivo y21". Samsung numbers follow a pattern; other brands use the
 * phone list assets/phone_models.tsv.
 */
object ModelNumbers {
    private val SAMSUNG = mapOf(
        "A013" to "a01 core", "A022" to "a02", "A025" to "a02s", "A032" to "a03 core", "A042" to "a04e",
        "A102" to "a10e", "A127" to "a12", "A137" to "a13", "A202" to "a20e", "A260" to "a2 core",
        "A320" to "a3 2017", "A510" to "a5 2016", "A520" to "a5 2017", "A530" to "a8 2018", "A600" to "a6",
        "A605" to "a6 plus", "A710" to "a7 2016", "A720" to "a7 2017", "A730" to "a8 plus 2018", "A750" to "a7 2018",
        "A908" to "a90 5g", "A910" to "a9 pro", "A920" to "a9 2018",
        "M013" to "m01 core", "M017" to "m01s", "M022" to "m02", "M025" to "m02s", "M127" to "m12", "F127" to "f12",
        "J100" to "j1", "J105" to "j1 mini", "J106" to "j1 mini prime", "J110" to "j1 ace", "J120" to "j1 2016",
        "J200" to "j2", "J210" to "j2 2016", "J250" to "j2 pro", "J260" to "j2 core", "J320" to "j3 2016",
        "J330" to "j3 pro", "J337" to "j3 2018", "J400" to "j4", "J410" to "j4 core", "J415" to "j4 plus",
        "J500" to "j5", "J510" to "j5 2016", "J530" to "j5 pro", "J600" to "j6", "J610" to "j6 plus", "J700" to "j7",
        "J701" to "j7 core", "J710" to "j7 2016", "J720" to "j7 duo", "J730" to "j7 pro", "J737" to "j7 2018",
        "J810" to "j8",
        "G355" to "core 2", "G360" to "core prime", "G361" to "core prime", "G530" to "grand prime",
        "G531" to "grand prime", "G532" to "j2 prime", "G550" to "on5", "G570" to "j5 prime", "G600" to "on7",
        "G610" to "j7 prime", "G611" to "j7 prime 2", "G615" to "j7 max", "G720" to "grand max",
        "G770" to "s10 lite", "G780" to "s20 fe", "G781" to "s20 fe 5g", "G900" to "s5", "G920" to "s6",
        "G925" to "s6 edge", "G928" to "s6 edge plus", "G930" to "s7", "G935" to "s7 edge", "G950" to "s8",
        "G955" to "s8 plus", "G960" to "s9", "G965" to "s9 plus", "G970" to "s10e", "G973" to "s10",
        "G975" to "s10 plus", "G977" to "s10 5g", "G980" to "s20", "G981" to "s20 5g", "G985" to "s20 plus",
        "G986" to "s20 plus 5g", "G988" to "s20 ultra", "G990" to "s21 fe", "G991" to "s21", "G996" to "s21 plus",
        "G998" to "s21 ultra",
        "N900" to "note 3", "N910" to "note 4", "N920" to "note 5", "N930" to "note 7", "N935" to "note fe",
        "N950" to "note 8", "N960" to "note 9", "N970" to "note 10", "N971" to "note 10 5g", "N975" to "note 10 plus",
        "N976" to "note 10 plus 5g", "N770" to "note 10 lite", "N980" to "note 20", "N981" to "note 20 5g",
        "N985" to "note 20 ultra", "N986" to "note 20 ultra 5g",
        "F700" to "z flip", "F711" to "z flip 3", "F721" to "z flip 4", "F731" to "z flip 5", "F741" to "z flip 6",
        "F916" to "z fold 2", "F926" to "z fold 3", "F936" to "z fold 4", "F946" to "z fold 5", "F956" to "z fold 6",
    )
    private val SAMSUNG_RE = re("(?<![A-Z0-9])SM[-_ ]?([AMFJGNS])([0-9O]{3})[0-9]?[A-Z]{0,3}(?![A-Z0-9])")
    private val SAMSUNG_KIND = mapOf('5' to "", '6' to " 5g", '7' to "s", '8' to "s 5g")
    private val S_VARIANT = mapOf('1' to "", '6' to " plus", '7' to " edge", '8' to " ultra")

    fun samsungName(letter: String, digits0: String): String? {
        val digits = digits0.replace('O', '0')
        SAMSUNG[letter + digits]?.let { return "samsung $it" }
        if (letter in "AMF" && digits[2] in SAMSUNG_KIND) return "samsung ${letter.lowercase()}${digits.take(2)}${SAMSUNG_KIND[digits[2]]}"
        if (letter == "S" && digits[0] == '9' && digits[2] in S_VARIANT) return "samsung s${22 + digits[1].digitToInt()}${S_VARIANT[digits[2]]}"
        if (letter == "S" && digits[0] == '7' && digits[2] == '1') return "samsung s${22 + digits[1].digitToInt()} fe"
        return null
    }

    private val table: Map<String, Pair<String, String>> by lazy {
        val t = HashMap<String, Pair<String, String>>(12000)
        runCatching {
            Storage.app.assets.open("phone_models.tsv").bufferedReader().useLines { lines ->
                for (line in lines) {
                    val p = line.split('\t')
                    if (p.size == 3) t[p[0]] = p[1] to p[2]
                }
            }
        }
        t
    }

    private fun key(text: String) = text.uppercase().replace(Regex("[^A-Z0-9]"), "")

    private fun variants(k: String) = buildList {
        add(k)
        if ('O' in k) add(k.replace('O', '0'))
        if ('I' in k) add(k.replace('I', '1'))
    }

    /** The shop name for one model number, or null. Short numbers only count with their brand in [text]. */
    fun lookup(token: String, text: String = ""): String? {
        val k = key(token)
        if (k.length < 3 || table.isEmpty()) return null
        val low = text.lowercase()
        for (v in variants(k)) {
            val hit = table[v] ?: continue
            if (v.length >= 5 || hit.first in low) return hit.second
        }
        return null
    }

    private val TOKEN = Regex("[A-Za-z0-9][A-Za-z0-9_-]{2,16}")

    /** Shop names of the model numbers written in a text, best first. */
    fun namesFromCodes(text0: String): List<String> {
        val text = ProductCodes.stripCodes(text0)
        val up = text.uppercase()
        val out = mutableListOf<String>()
        for (m in SAMSUNG_RE.findAll(up)) {
            (lookup(m.value, text) ?: samsungName(m.groupValues[1], m.groupValues[2]))?.let { out += it }
        }
        for (m in TOKEN.findAll(text)) {
            val tok = m.value
            if (tok.any { it.isDigit() } && (tok.any { it.isLetter() && it.code < 128 } || key(tok).length == 4)) {
                lookup(tok, text)?.let { out += it }
            }
        }
        return out.distinct()
    }

    private val CODE_SHAPE = mapOf(
        "vivo" to "v\\d{4}[a-z]?|\\d{4}", "oppo" to "cph\\d{4}", "realme" to "rmx\\d{4}", "infinix" to "x\\d{3,4}[a-z]?",
        "tecno" to "[a-z]{2}\\d[a-z]?", "itel" to "[a-z]\\d{3,4}[a-z]?|it\\d{4}", "xiaomi" to "\\d{4,}[a-z0-9]*|m\\d{4}\\w+",
        "redmi" to "\\d{4,}[a-z0-9]*|m\\d{4}\\w+", "poco" to "\\d{4,}[a-z0-9]*|m\\d{4}\\w+",
    )

    /** (name, known): true = a model number in the phone list, false = one that is not, null = a plain name. */
    fun resolve(name: String, text: String = ""): Pair<String, Boolean?> {
        val words = name.split(" ")
        if (words.size != 2) return name to null
        lookup(words[1], "$text ${words[0]}")?.let { return it to true }
        val shape = CODE_SHAPE[words[0]]
        if (shape != null && Regex(shape).matches(words[1])) return name to false
        return name to null
    }
}
