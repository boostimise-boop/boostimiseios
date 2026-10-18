package com.covercanvasbd.boostimise.core

/**
 * Product codes as people type them (core/codes.py). All of these mean 2-152:
 *   2-152  2:152  2_152  2+152  2;152  2&152  2:152+  2:152-  2+152-
 * and several codes of one list may be written together:
 *   2-151+152  2-151-152  2-151+2-152  2:152+2-153  2+152-2+153  2-151 & 2-152
 * A code joined by + & : or ; must start its line ("redmi 12 8+128" is memory, "at 10:30" a time).
 */
object ProductCode {
    const val PREFIX = """\d{1,3}[A-Za-z]{0,2}"""
    const val DASH = """[-‐-―_]"""   // joins the two halves of a code anywhere
    const val LOOSE = """[+&:;]"""             // joins them only in a code that starts a line
    const val SEP = "(?:$DASH|$LOOSE)"
    private const val END = """(?![0-9A-Za-z])"""
    // A number after a code that adds one more code of the same list. A lone digit followed by a joiner
    // starts a code of another list instead ("3-163+4-165" is 3-163 and 4-165).
    private const val MORE_NUM = """(?!\d[ \t]*""" + SEP + """[ \t]*\d)\d{1,6}""" + END

    /** What comes between two codes of a list whose prefix matches the regex [prefix]. */
    private fun join(prefix: String): String {
        val head = prefix + """\s*""" + SEP + """\s*"""
        return """(?:[ \t]*[+&][ \t]*(?:""" + head + """)?|[ \t]*""" + DASH + """[ \t]*""" + head +
            "|" + DASH + "(?:" + head + ")?)"
    }

    /** One code alone, as in a code list or a result file: group 1 prefix, group 2 number. */
    val CODE = """(?<![0-9A-Za-z])(""" + PREFIX + """)\s*""" + SEP + """\s*(\d{1,6})""" + END
    val CODE_RE = re(CODE)
    /** A code in a chat with the codes that follow it: 1 prefix, 2 joiner, 3 number, 4 the rest of the list. */
    val CHAT_CODE = """(?<![0-9A-Za-z])(""" + PREFIX + """)\s*(""" + SEP + """)\s*(\d{1,6})""" + END +
        "((?:" + join("""\1""") + MORE_NUM + ")*)"
    val CHAT_CODE_RE = re(CHAT_CODE, true)
    private val moreRes = HashMap<String, Regex>()
    private val LIST_CODE = re("""\s*(""" + PREFIX + """)\s*""" + SEP + """\s*(\d{1,6})[\s+\-]*""")
    private val ALNUM = Regex("[0-9A-Za-z]")

    fun norm(prefix: String, number: String) = "${prefix.lowercase()}-${number.toInt()}"
    fun split(code: String): Pair<String, Int> = code.substringBeforeLast('-') to code.substringAfterLast('-').toInt()

    /** The numbers in group 4 of CHAT_CODE: "+2-153-154" -> [153, 154]. */
    fun moreNumbers(prefix: String, more: String): List<String> {
        val r = synchronized(moreRes) {
            moreRes.getOrPut(prefix.lowercase()) { re(join(Regex.escape(prefix)) + "(" + MORE_NUM + ")", true) }
        }
        return r.findAll(more).map { it.groupValues[1] }.toList()
    }

    /** The codes of one CHAT_CODE match: "2-151-152" -> [2-151, 2-152]. */
    fun matchCodes(m: MatchResult): List<String> {
        val p = m.groupValues[1]
        return (listOf(m.groupValues[3]) + moreNumbers(p, m.groupValues[4])).map { norm(p, it) }.distinct()
    }

    /** The codes written in a chat message, as CHAT_CODE_RE matches. */
    fun codeMatches(text: String?): List<MatchResult> {
        val t = text ?: ""
        val out = mutableListOf<MatchResult>()
        var pos = 0
        while (pos <= t.length) {
            val m = CHAT_CODE_RE.find(t, pos) ?: break
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

    fun codesInText(text: String?): List<String> = codeMatches(bnToAscii(text ?: "")).flatMap { matchCodes(it) }.distinct()

    /** A code typed in a code list, written the usual way: "2:152+" -> "2-152", "5A_380" -> "5A-380". */
    fun listCode(token: String): String {
        val m = LIST_CODE.matchEntire(bnToAscii(token)) ?: return token.trim()
        return "${m.groupValues[1]}-${m.groupValues[2]}"
    }

    private fun jsEscape(s: String) = Regex("""([.*+?^${'$'}{}()|\[\]\\/])""").replace(s) { "\\" + it.value }

    /** Regex (valid in both Kotlin and JavaScript) matching a message that is about [code]:
     *  '2-152' matches '2 - 152', '2:152+', '2_152', '2-151-152', '2+151-2+152', '2-151+152', not '12-152'. */
    fun codePattern(code0: String): String {
        val code = listCode(code0)
        val sep = """\s*""" + SEP + """\s*"""
        val parts = Regex(sep).split(code).filter { it.isNotEmpty() }.map { jsEscape(it) }
        if (parts.size != 2 || !parts[1].all { it.isDigit() }) return """(?<![0-9A-Za-z])(?:""" + parts.joinToString(sep) + ")" + END
        val (prefix, number) = parts
        var last = "0*" + number.toInt()
        val one = prefix + sep + last
        if (number.toInt() < 10) last += """(?![ \t]*""" + SEP + """[ \t]*\d)"""   // "3-163+4-165" is not about 3-4
        val inList = prefix + sep + """\d{1,6}""" + END + "(?:" + join(prefix) + MORE_NUM + ")*?" + join(prefix) + last
        return """(?<![0-9A-Za-z])(?:""" + one + "|" + inList + ")" + END
    }
}
