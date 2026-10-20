package com.boostimise.app.core

import org.json.JSONArray
import org.json.JSONObject

/**
 * Product codes as people type them (core/codes.py). A shop writes its codes in one of three styles, its
 * shop setting "code_style" (see [cleanStyle]):
 *  - prefix (the default), as in 2-558. All of these mean 2-152:
 *      2-152  2:152  2_152  2+152  2;152  2&152  2:152+  2:152-  2+152-
 *    and several codes of one list may be written together:
 *      2-151+152  2-151-152  2-151+2-152  2:152+2-153  2+152-2+153  2-151 & 2-152
 *    A code joined by + & : or ; must start its line ("redmi 12 8+128" is memory, "at 10:30" a time).
 *  - letters: the shop's letters and a number, as in CC1023 or AB-12 (also cc 1023, CC-1023, CC1023+1024).
 *  - keyword: # or a word and a number, as in #1023 or Order 1023 (also Order: #1023, #1023+1024). These
 *    codes have no prefix: their key is the number alone.
 * [reader] reads codes in the shop's style; [setStyle] changes it (Shop.kt calls it). The unit test
 * (ProductCodeTest) checks this file against core/code_samples.json, like the other apps' readers.
 */
object ProductCode {
    // ---- the prefix style (2-558) ----------------------------------------------------------------
    const val PREFIX = """\d{1,3}[A-Za-z]{0,2}"""
    const val DASH = """[-‐-―_]"""   // joins the two halves of a code anywhere
    const val LOOSE = """[+&:;]"""             // joins them only in a code that starts a line
    const val SEP = "(?:$DASH|$LOOSE)"
    private const val END = """(?![0-9A-Za-z])"""
    private const val START = """(?<![0-9A-Za-z])"""
    // A number after a code that adds one more code of the same list. A lone digit followed by a joiner
    // starts a code of another list instead ("3-163+4-165" is 3-163 and 4-165).
    private const val MORE_NUM = """(?!\d[ \t]*""" + SEP + """[ \t]*\d)\d{1,6}""" + END

    /** What comes between two codes of a list whose prefix matches the regex [prefix]. */
    private fun join(prefix: String): String {
        val head = prefix + """\s*""" + SEP + """\s*"""
        return """(?:[ \t]*[+&][ \t]*(?:""" + head + """)?|[ \t]*""" + DASH + """[ \t]*""" + head +
            "|" + DASH + "(?:" + head + ")?)"
    }

    // The prefix style's patterns. Tools read codes with reader(), which has these for the shop's style.
    /** One code alone, as in a code list or a result file: group 1 prefix, group 2 number. */
    val CODE = START + "(" + PREFIX + """)\s*""" + SEP + """\s*(\d{1,6})""" + END
    val CODE_RE = re(CODE)
    /** A code in a chat with the codes that follow it: 1 prefix, 2 joiner, 3 number, 4 the rest of the list. */
    val CHAT_CODE = START + "(" + PREFIX + """)\s*(""" + SEP + """)\s*(\d{1,6})""" + END +
        "((?:" + join("""\1""") + MORE_NUM + ")*)"
    val CHAT_CODE_RE = re(CHAT_CODE, true)

    // ---- the letters (CC1023) and keyword (#1023) styles -----------------------------------------------
    private const val LETTERS_GAP = """[ \t]*""" + DASH + """?[ \t]*"""          // CC1023, CC 1023, CC-1023
    private const val KEYWORD_GAP = """[ \t]*(?:[-‐-―_:.#][ \t]*){0,2}"""   // #1023, Order: #1023
    private const val NUMBER = """\d{1,6}""" + END

    /** What comes between two codes of a list in these styles: "+1024", " & CC1024", "+#1024". */
    private fun listJoin(head: String) = """[ \t]*[+&][ \t]*(?:""" + head + ")?"

    // The words an order message may put before its code: "Order: 2-558", "কোড 2-558".
    private const val ORDER_START = """^\W*(?:(?:order|code|id|অর্ডার|কোড)\W*)?"""
    private val ALNUM = Regex("[0-9A-Za-z]")

    private fun jsEscape(s: String) = Regex("""([.*+?^${'$'}{}()|\[\]\\/])""").replace(s) { "\\" + it.value }

    private fun cleanLine(line: String): String {
        var l = bnToAscii(line).replace(' ', ' ')
        l = Regex("[*~`]|(?<!\\d)_|_(?!\\d)").replace(l, "")   // WhatsApp *bold* / _italic_ markers, not "2_152"
        return Regex("\\s+").replace(l, " ").trim()
    }

    // ---- the style setting ---------------------------------------------------------------------------
    /** One code style: kind "prefix", "letters" (with [letters] and [sep]) or "keyword" (with [keywords]). */
    data class Style(val kind: String, val letters: List<String> = emptyList(), val sep: String = "",
                     val keywords: List<String> = emptyList()) {
        fun toJson(): JSONObject = when (kind) {
            "letters" -> JSONObject().put("kind", kind).put("letters", JSONArray(letters)).put("sep", sep)
            "keyword" -> JSONObject().put("kind", kind).put("keywords", JSONArray(keywords))
            else -> JSONObject().put("kind", "prefix")
        }
    }

    val DEFAULT_STYLE = Style("prefix")
    private const val MAX_LETTERS = 12
    private const val MAX_KEYWORDS = 6
    private val LETTERS_RE = Regex("[A-Z]{1,4}")
    private val WORD_RE = Regex("[A-Za-z]{1,12}")

    /**
     * A valid style from the shop setting; the prefix style for anything else.
     * {"kind": "letters", "letters": ["CC", "AB"], "sep": ""}: sep is how a code is written ("", "-" or " ").
     * {"kind": "keyword", "keywords": ["#", "Order"]}: # or words of A-Z; the first one is how a code is written.
     */
    fun cleanStyle(o: JSONObject?): Style {
        if (o == null) return DEFAULT_STYLE
        when (o.optString("kind")) {
            "letters" -> {
                val letters = mutableListOf<String>()
                o.optJSONArray("letters")?.let { a ->
                    for (i in 0 until a.length()) {
                        val l = a.opt(i).toString().trim().uppercase()
                        if (LETTERS_RE.matches(l) && l !in letters) letters += l
                    }
                }
                if (letters.isNotEmpty()) {
                    val sep = (o.opt("sep") as? String)?.takeIf { it in listOf("", "-", " ") } ?: ""
                    return Style("letters", letters.take(MAX_LETTERS), sep)
                }
            }
            "keyword" -> {
                val words = mutableListOf<String>()
                o.optJSONArray("keywords")?.let { a ->
                    for (i in 0 until a.length()) {
                        val w = a.opt(i).toString().trim()
                        if ((w == "#" || WORD_RE.matches(w)) && words.none { it.equals(w, ignoreCase = true) }) words += w
                    }
                }
                if (words.isNotEmpty()) return Style("keyword", keywords = words.take(MAX_KEYWORDS))
            }
        }
        return DEFAULT_STYLE
    }

    /** How a code of the style looks: "2-558", "CC1023", "#1023". */
    fun example(style: Style): String {
        val r = Reader(style)
        return if (r.kind == "prefix") r.written("2", "558") else r.written(r.prefixes().firstOrNull() ?: "", "1023")
    }

    /** "Prefix and number, like 2-558", "Letters and number (CC, AB), like CC1023", "# and number, like #1023". */
    fun describe(style: Style): String = when (style.kind) {
        "letters" -> "Letters and number (${style.letters.joinToString(", ")}), like ${example(style)}"
        "keyword" -> "${style.keywords.joinToString(" or ")} and number, like ${example(style)}"
        else -> "Prefix and number, like ${example(style)}"
    }

    private val GUESS_PREFIX = re("(" + PREFIX + """)\s*""" + SEP + """\s*(\d{1,6})""")
    private val GUESS = re("""(#|[A-Za-z]{1,12})(""" + KEYWORD_GAP + """)(\d{1,6})""")
    private val KIND_NAMES = mapOf("prefix" to "prefix and number", "letters" to "letters and number",
        "keyword" to "# or a word and number")

    /** The style of a few codes typed one per line or with commas: (style, null), or (null, what is wrong).
     *  "2-558, 3-12" is the prefix style, "CC1023, AB-12" letters, "#1023" or "Order 1023" keyword. */
    fun guessStyle(text: String?): Pair<Style?, String?> {
        val examples = bnToAscii(text ?: "").split('\n', ',').map { it.trim() }.filter { it.isNotEmpty() }
        if (examples.isEmpty()) return null to "Type two or three of your codes."
        val kinds = linkedMapOf<String, String>()
        val letters = mutableListOf<String>()
        val seps = mutableListOf<String>()
        val keywords = mutableListOf<String>()
        for (ex in examples) {
            if (GUESS_PREFIX.matches(ex)) {
                kinds.putIfAbsent("prefix", ex)
                continue
            }
            val m = GUESS.matchEntire(ex)
                ?: return null to "“$ex” is not a code this app can read: a code is letters, a prefix, # or a word, then a number."
            val word = m.groupValues[1]
            val gap = m.groupValues[2]
            val mark = gap.replace(" ", "").replace("\t", "")
            val upper = word.any { it.isLetter() } && word == word.uppercase()
            if (word != "#" && word.length <= 4 && (mark.isEmpty() || Regex(DASH).matches(mark)) && (' ' !in gap || upper)) {
                kinds.putIfAbsent("letters", ex)
                if (word.uppercase() !in letters) letters += word.uppercase()
                seps += if (mark.isNotEmpty()) "-" else if (gap.isNotEmpty()) " " else ""
            } else {
                kinds.putIfAbsent("keyword", ex)
                if (keywords.none { it.equals(word, ignoreCase = true) }) keywords += word
            }
        }
        if (kinds.size > 1) {
            val shown = kinds.entries.joinToString(", ") { (k, ex) -> "$ex is ${KIND_NAMES[k]}" }
            return null to "These codes are written in different styles ($shown). Type codes of one style."
        }
        return when (kinds.keys.first()) {
            "letters" -> {
                val sep = listOf("", "-", " ").maxByOrNull { s -> seps.count { it == s } } ?: ""
                cleanStyle(Style("letters", letters, sep).toJson()) to null
            }
            "keyword" -> cleanStyle(Style("keyword", keywords = keywords).toJson()) to null
            else -> DEFAULT_STYLE to null
        }
    }

    // ---- the reader ------------------------------------------------------------------------------------
    /**
     * Reads the codes of one style (core/codes.py's Reader). [code]: one code alone, as in a code list
     * (group 1 prefix, 2 number). [chatCode]: a code in a chat with the codes after it (1 prefix, 2 joiner,
     * 3 number, 4 the rest). [jsAnyCode] / [jsCodeParts]: what the WhatsApp script tells lists from single
     * codes with, and counts per prefix ([scanPatterns]). A keyword-style code has no prefix: its group 1 is
     * always empty.
     */
    class Reader(val style: Style = DEFAULT_STYLE) {
        val kind = style.kind
        val code: String
        val chatCode: String
        val jsAnyCode: String
        val jsCodeParts: String
        private val head: String
        val codeRe: Regex
        val chatCodeRe: Regex
        private val listRe: Regex
        private val lineRe: Regex
        private val orderRe: Regex
        private val more = HashMap<String, Regex>()

        init {
            val core: String
            when (kind) {
                "letters" -> {
                    val letters = "(?:" + style.letters.sortedByDescending { it.length }.joinToString("|") + ")"
                    core = "($letters)" + LETTERS_GAP + """(\d{1,6})"""
                    head = ""
                    chatCode = START + "($letters)(" + LETTERS_GAP + """)(\d{1,6})""" + END +
                        "((?:" + listJoin("""\1""" + LETTERS_GAP) + NUMBER + ")*)"
                    jsAnyCode = START + core + END
                    jsCodeParts = jsAnyCode
                    code = START + core + END
                }
                "keyword" -> {
                    val words = "(?:" + style.keywords.sortedByDescending { it.length }.joinToString("|") { jsEscape(it) } + ")"
                    head = words + KEYWORD_GAP
                    core = "(?:$head)?()" + """(\d{1,6})"""   // in a code list the keyword may be left out
                    chatCode = START + words + "()(" + KEYWORD_GAP + """)(\d{1,6})""" + END +
                        "((?:" + listJoin(head) + NUMBER + ")*)"
                    jsAnyCode = START + words + "()" + KEYWORD_GAP + """(\d{1,6})""" + END
                    jsCodeParts = jsAnyCode
                    code = START + core + END
                }
                else -> {
                    core = "(" + PREFIX + """)\s*""" + SEP + """\s*(\d{1,6})"""
                    head = ""
                    code = CODE
                    chatCode = CHAT_CODE
                    jsAnyCode = START + """(\d{1,3}[A-Za-z]?)\s*-\s*(\d{2,4})""" + END
                    jsCodeParts = START + "(" + PREFIX + """)\s*""" + DASH + """\s*(\d{1,6})""" + END
                }
            }
            codeRe = re(code, true)
            chatCodeRe = re(chatCode, true)
            listRe = re("""\s*""" + core + """[\s+\-]*""", true)
            lineRe = re("""^\s*""" + core + """[+\-]*(?=\s|$)""", true)
            orderRe = re(ORDER_START + chatCode, true)
        }

        // ---- one code -----------------------------------------------------------------------------------
        /** The letters of the letters style; [] for the other styles. */
        fun prefixes(): List<String> = if (kind == "letters") style.letters else emptyList()

        /** How a code is written in lists and file names: "2-558", "CC1023", "AB-12", "#1023", "Order 1023". */
        fun written(prefix: String, number: String): String = when (kind) {
            "letters" -> (style.letters.firstOrNull { it.equals(prefix, ignoreCase = true) } ?: prefix.uppercase()) +
                style.sep + number
            "keyword" -> style.keywords[0].let { w -> w + (if (w == "#") "" else " ") + number }
            else -> "$prefix-$number"
        }

        /** A code typed in a code list, written the usual way: "2:152+" -> "2-152", "cc 1023" -> "CC1023". */
        fun listCode(token: String?): String {
            val m = listRe.matchEntire(bnToAscii(token ?: "")) ?: return (token ?: "").trim()
            return written(m.groupValues[1], m.groupValues[2])
        }

        /** The key of a code: "2 - 558", "2:558+" -> "2-558", "CC1023" -> "cc-1023", "#1023" -> "1023". */
        fun normalize(code: String?): String {
            val m = listRe.matchEntire(bnToAscii(code ?: "")) ?: return (code ?: "").trim().lowercase()
            return norm(m.groupValues[1], m.groupValues[2])
        }

        /** (prefix in lower case, number) of "4a-205", ("", 1023) of "#1023"; null for anything else. */
        fun codeParts(code: String?): Pair<String, Int>? {
            val m = listRe.matchEntire(bnToAscii(code ?: "")) ?: return null
            return m.groupValues[1].lowercase() to m.groupValues[2].toInt()
        }

        /** 2-558 < 2-560 < 3-12 < 5a-380; cc-12 < cc-1023; 12 < 1023. */
        val order: Comparator<String> = Comparator { a, b ->
            val (x, y) = sortKey(a) to sortKey(b)
            compareValuesBy(x, y, { it.first }, { it.second }, { it.third })
        }

        fun sortKey(code: String?): Triple<Int, String, Int> {
            val m = listRe.matchEntire(bnToAscii(code ?: "")) ?: return Triple(1_000_000, code ?: "", 0)
            val p = m.groupValues[1]
            val digits = p.takeWhile { it.isDigit() }
            return Triple(digits.toIntOrNull() ?: 0, p.substring(digits.length).lowercase(), m.groupValues[2].toInt())
        }

        /** A code-list line -> (code, the rest): "2-558<TAB>Realme C85" -> ("2-558", "Realme C85"). A line that
         *  does not start with a code gives its first word as the code, as written. */
        fun splitListLine(line0: String?): Pair<String, String> {
            val line = (line0 ?: "").trim()
            lineRe.find(bnToAscii(line))?.let { m ->
                return written(m.groupValues[1], m.groupValues[2]) to line.substring(m.range.last + 1).trim()
            }
            val first = line.replaceFirst("\t", " ")
            val cut = first.indexOf(' ')
            return listCode(if (cut < 0) first else first.substring(0, cut)) to (if (cut < 0) "" else first.substring(cut + 1).trim())
        }

        // ---- codes in a message -------------------------------------------------------------------------
        /** The numbers in group 4 of chatCode: "+2-153-154" -> [153, 154]. */
        fun moreNumbers(prefix: String, more0: String): List<String> {
            if (more0.isEmpty()) return emptyList()
            val r = synchronized(more) {
                more.getOrPut(prefix.lowercase()) {
                    re(when (kind) {
                        "prefix" -> join(Regex.escape(prefix)) + "(" + MORE_NUM + ")"
                        "letters" -> listJoin(jsEscape(prefix) + LETTERS_GAP) + "(" + NUMBER + ")"
                        else -> listJoin(head) + "(" + NUMBER + ")"
                    }, true)
                }
            }
            return r.findAll(more0).map { it.groupValues[1] }.toList()
        }

        /** The codes of one chatCode match: "2-151-152" -> [2-151, 2-152]. */
        fun matchCodes(m: MatchResult): List<String> {
            val p = m.groupValues[1]
            return (listOf(m.groupValues[3]) + moreNumbers(p, m.groupValues[4])).map { norm(p, it) }.distinct()
        }

        /** The chatCode matches in a message (a code joined by + & : or ; must start its line). */
        fun codeMatches(text: String?): List<MatchResult> {
            val t = text ?: ""
            if (kind != "prefix") return chatCodeRe.findAll(t).toList()
            val out = mutableListOf<MatchResult>()
            var pos = 0
            while (pos <= t.length) {
                val m = chatCodeRe.find(t, pos) ?: break
                val start = m.range.first
                val line = t.substring(t.lastIndexOf('\n', start - 1) + 1, start)
                if (m.groupValues[2] in setOf("+", "&", ":", ";") && ALNUM.containsMatchIn(line)) {
                    pos = m.groups[1]!!.range.last + 1   // "redmi 12 8+128", "at 10:30": not a code
                    continue
                }
                out += m
                pos = m.range.last + 1
            }
            return out
        }

        /** The keys of the codes written in a message: "2-151+152 and 3-12" -> [2-151, 2-152, 3-12]. */
        fun codesInText(text: String?): List<String> = codeMatches(bnToAscii(text ?: "")).flatMap { matchCodes(it) }.distinct()

        /** The codes an order message starts with (one of its first three lines): ["6-872"], ["3-163", "3-164"]. */
        fun orderCodes(text: String?): List<String> {
            for (line in (text ?: "").lines().map { cleanLine(it) }.filter { it.isNotEmpty() }.take(3)) {
                val m = orderLine(line) ?: continue
                return matchCodes(m)
            }
            return emptyList()
        }

        /** The chatCode match a cleaned order-message line starts with ("Order: 3-163+164"), or null. */
        fun orderLine(line: String): MatchResult? = orderRe.find(line)

        /** Regex (valid in both Kotlin and JavaScript) matching a message that is about [code0]:
         *  '2-152' matches '2 - 152', '2:152+', '2_152', '2-151-152', '2+151-2+152', '2-151+152', not '12-152';
         *  'CC1023' matches 'cc 1023' and 'CC1022+1023'. */
        fun codePattern(code0: String?): String {
            if (kind == "prefix") return prefixCodePattern(code0 ?: "")
            val m = listRe.matchEntire(bnToAscii(code0 ?: ""))
                ?: return START + "(?:" + jsEscape((code0 ?: "").trim()) + ")" + END
            val h = if (kind == "letters") jsEscape(m.groupValues[1]) + LETTERS_GAP else head
            val last = "0*" + m.groupValues[2].toInt()
            val j = listJoin(h)
            val inList = h + """\d{1,6}""" + END + "(?:" + j + NUMBER + ")*?" + j + last
            return START + "(?:" + h + last + "|" + inList + ")" + END
        }
    }

    // ---- helpers that do not depend on the style ---------------------------------------------------------
    /** The key of a code: ("2", "0558") -> "2-558", ("5A", "380") -> "5a-380", ("", "1023") -> "1023". */
    fun norm(prefix: String, number: String) = (if (prefix.isEmpty()) "" else "${prefix.lowercase()}-") + number.toInt()

    /** A key back to its parts: "2-558" -> (2, 558), "cc-1023" -> (cc, 1023), "1023" -> ("", 1023). */
    fun split(code: String): Pair<String, Int> =
        (if ('-' in code) code.substringBeforeLast('-') else "") to (code.substringAfterLast('-').toIntOrNull() ?: 0)

    private val PREFIX_LIST_CODE = re("""\s*(""" + PREFIX + """)\s*""" + SEP + """\s*(\d{1,6})[\s+\-]*""")

    private fun prefixCodePattern(code0: String): String {
        val code = PREFIX_LIST_CODE.matchEntire(bnToAscii(code0))?.let { "${it.groupValues[1]}-${it.groupValues[2]}" } ?: code0.trim()
        val sep = """\s*""" + SEP + """\s*"""
        val parts = Regex(sep).split(code).filter { it.isNotEmpty() }.map { jsEscape(it) }
        if (parts.size != 2 || !parts[1].all { it.isDigit() }) return START + "(?:" + parts.joinToString(sep) + ")" + END
        val (prefix, number) = parts
        var last = "0*" + number.toInt()
        val one = prefix + sep + last
        if (number.toInt() < 10) last += """(?![ \t]*""" + SEP + """[ \t]*\d)"""   // "3-163+4-165" is not about 3-4
        val inList = prefix + sep + """\d{1,6}""" + END + "(?:" + join(prefix) + MORE_NUM + ")*?" + join(prefix) + last
        return START + "(?:" + one + "|" + inList + ")" + END
    }

    // ---- the shop's style ----------------------------------------------------------------------------------
    @Volatile private var current = Reader(DEFAULT_STYLE)

    /** The reader of the shop's code style. A job that runs for a while keeps the one it started with. */
    fun reader(): Reader = current

    /** Reads codes in this style from now on (the shop setting "code_style"; null is the prefix style). */
    fun setStyle(o: JSONObject?) {
        val s = cleanStyle(o)
        if (s != current.style) current = Reader(s)
    }

    /** What wa_bridge.js's scanChat reads codes with in the shop's style (opts.anyCode, opts.codeParts). */
    fun scanPatterns(opts: JSONObject): JSONObject =
        opts.put("anyCode", current.jsAnyCode).put("codeParts", current.jsCodeParts)

    // The shop's style, for the tools that read codes without holding a reader.
    fun codeMatches(text: String?) = current.codeMatches(text)
    fun codesInText(text: String?) = current.codesInText(text)
    fun matchCodes(m: MatchResult) = current.matchCodes(m)
    fun orderCodes(text: String?) = current.orderCodes(text)
    fun listCode(token: String) = current.listCode(token)
    fun normalize(code: String?) = current.normalize(code)
    fun codePattern(code: String) = current.codePattern(code)
}
