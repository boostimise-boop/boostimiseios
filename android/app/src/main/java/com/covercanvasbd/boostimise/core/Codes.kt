package com.covercanvasbd.boostimise.core

import java.time.LocalDate
import java.time.LocalDateTime
import java.util.regex.Pattern

/** A regex with Python 3's Unicode semantics for \w \d \b (the patterns are ported from Python).
 *  Android's regex (ICU) is Unicode-aware for these already and throws "Unsupported flags: 256" for
 *  UNICODE_CHARACTER_CLASS, which crashed every tool that parses order messages. */
fun re(pattern: String, ignoreCase: Boolean = false): Regex {
    var flags = 0
    if (ignoreCase) flags = flags or Pattern.CASE_INSENSITIVE or Pattern.UNICODE_CASE
    return Pattern.compile(pattern, flags).toRegex()
}

const val BN_DIGITS = "০১২৩৪৫৬৭৮৯"
fun bnToAscii(s: String): String = buildString(s.length) {
    for (ch in s) {
        val i = BN_DIGITS.indexOf(ch)
        append(if (i >= 0) ('0' + i) else ch)
    }
}

private val BAD_CHARS = Regex("[<>:\"/\\\\|?*\\x00-\\x1f]")
private val RESERVED = setOf("CON", "PRN", "AUX", "NUL") + (1..9).map { "COM$it" } + (1..9).map { "LPT$it" }

fun safeFileName(name: String): String {
    var n = BAD_CHARS.replace(name, "-")
    n = Regex("\\s+").replace(n, " ").trim().trimEnd('.', ' ')
    if (n.substringBefore('.').uppercase() in RESERVED) n += "_"
    return n.take(150).trimEnd('.', ' ')
}

/** One line of a code list: '2-558<TAB>Realme C85<TAB>26/9' (tools/whatsapp_fetch/helpers.py). */
data class CodeItem(val code: String, val name: String = "", val date: String = "") {
    fun baseName(includeDate: Boolean = false): String {
        val parts = mutableListOf(code, name)
        if (includeDate && date.isNotEmpty()) parts += date.replace("/", "-")
        return safeFileName(parts.filter { it.isNotEmpty() }.joinToString(" ")).ifEmpty { safeFileName(code) }.ifEmpty { "item" }
    }
}

object Codes {
    val IMAGE_EXTS = listOf(".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".bmp")
    private val DATE_RE = Regex("(?:^|\\s)(\\d{1,2}/\\d{1,2}(?:/\\d{2,4})?)\\s*$")

    fun parseCodeList(text: String): List<CodeItem> {
        val items = mutableListOf<CodeItem>()
        for (raw in text.lines()) {
            val line = raw.trim()
            if (line.isEmpty()) continue
            val first = line.replaceFirst("\t", " ")
            val code = ProductCode.listCode(first.substringBefore(' '))
            var rest = if (' ' in first) first.substringAfter(' ') else ""
            if (code.none { it.isDigit() }) continue
            rest = Regex("\\s+").replace(rest.replace("\t", " "), " ").trim()
            var date = ""
            DATE_RE.find(rest)?.let { m ->
                date = m.groupValues[1]
                rest = rest.substring(0, m.range.first).trim()
            }
            items += CodeItem(code, rest, date)
        }
        return items
    }

    /** Regex (valid in both Kotlin and JavaScript) matching a message about the code (ProductCode.codePattern). */
    fun codePattern(code: String): String = ProductCode.codePattern(code)

    /** '26/9' -> 26 Sep of this year (last year if that is in the future). */
    fun listDate(text: String, now: LocalDateTime = LocalDateTime.now()): LocalDateTime? {
        val m = Regex("\\s*(\\d{1,2})/(\\d{1,2})(?:/(\\d{2,4}))?\\s*").matchEntire(text) ?: return null
        val day = m.groupValues[1].toInt()
        val month = m.groupValues[2].toInt()
        var year = m.groupValues[3].takeIf { it.isNotEmpty() }?.toInt() ?: now.year
        if (year < 100) year += 2000
        var d = try {
            LocalDate.of(year, month, day).atStartOfDay()
        } catch (e: Exception) {
            return null
        }
        if (m.groupValues[3].isEmpty() && d.isAfter(now.plusDays(1))) d = d.minusYears(1)
        return d
    }

    /** Oldest message time to read: lookbackDays ago, extended to a week before the oldest list date. */
    fun scanSince(items: List<CodeItem>, lookbackDays: Int, now: LocalDateTime = LocalDateTime.now()): LocalDateTime {
        var since = now.minusDays(lookbackDays.toLong())
        for (it in items) {
            val d = listDate(it.date, now) ?: continue
            val w = d.minusDays(7)
            if (w.isBefore(since)) since = w
        }
        return since
    }

    fun extFor(mimetype: String?, data: ByteArray, filename: String? = null): String {
        val mt = (mimetype ?: "").substringBefore(';').trim().lowercase()
        val byMime = mapOf("image/jpeg" to ".jpg", "image/jpg" to ".jpg", "image/png" to ".png", "image/webp" to ".webp",
            "image/gif" to ".gif", "image/heic" to ".heic", "image/heif" to ".heic", "image/bmp" to ".bmp")
        byMime[mt]?.let { return it }
        fun starts(vararg b: Int) = data.size >= b.size && b.indices.all { data[it] == b[it].toByte() }
        if (starts(0xff, 0xd8, 0xff)) return ".jpg"
        if (starts(0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a)) return ".png"
        if (data.size >= 12 && String(data, 0, 4) == "RIFF" && String(data, 8, 4) == "WEBP") return ".webp"
        if (data.size >= 4 && String(data, 0, 4) == "GIF8") return ".gif"
        filename?.let { f ->
            val suffix = "." + f.substringAfterLast('.', "").lowercase()
            if (suffix in IMAGE_EXTS) return suffix
        }
        return ".jpg"
    }

    /** The file saved for this item by an earlier run, if any (names = files in the output folder). */
    fun existingOutput(names: Set<String>, base: String): String? {
        for (n in listOf(base, "$base 1")) for (ext in IMAGE_EXTS) if ("$n$ext" in names) return "$n$ext"
        return null
    }
}

/**
 * Pull order details (name, phones, amount, address) out of a WhatsApp order message -
 * tools/pathao_autofill/order_parser.py. Messages are Bangla/English mixed.
 */
object OrderParser {
    private val LIST_LINE = re("^\\s*(" + ProductCode.PREFIX + ")\\s*" + ProductCode.SEP + "\\s*(\\d{1,6})[\\s+\\-]*$", true)
    // The code at the start of a line with the codes of its list after it ("3-163+164", "2-151-152", "2:152+2-153")
    private val CODE_LINE = re("^\\W*(?:(?:order|code|id|অর্ডার|কোড)\\W*)?" + ProductCode.CHAT_CODE, true)
    // Any code written with a dash, with the codes after it, so its numbers are not read as the amount
    private val CODE_ANY = re("(?<!\\w)" + ProductCode.CHAT_CODE.replaceFirst(ProductCode.SEP, ProductCode.DASH), true)
    val PHONE = re("(?<!\\d)(?:\\+?88[ -]?)?(0?1[3-9](?:[ -]?\\d){8})(?!\\d)")
    private val NAME_LABEL = re(
        "^(?:customer|receiver|recipient|কাস্টমার|গ্রাহক|প্রাপক)?(?:'s|’s|er|ের)?\\s*" +
            "(?:name|নাম)(?![a-zঀ-ং঄-৿])\\s*[:ঃ=\\-–—.।]*\\s*(.*)$", true)
    private val NAME_STOP = re(
        "(?i)(?<![a-z])(?:mobile|mob|phone|cell|contact|number|address|vill|village|district|thana)(?![a-z])" +
            "|মোবাইল|ফোন|নম্বর|নাম্বার|ঠিকানা|গ্রাম|জেলা|থানা|উপজেলা")
    private const val BRANDS = "walton|vivo|oppo|samsung|xiaomi|redmi|realme|nokia|symphony|itel|tecno|infinix|huawei|honor|" +
        "nubia|iphone|apple|oneplus|motorola|moto|poco|lava|zte|benco|maximus|helio|pixel|google|sony|" +
        "lenovo|asus|htc|alcatel|micromax|gionee|meizu|primo|xanon|zenix|galaxy|note|pro|max|plus|ultra|" +
        "mini|lite|prime|neo|cover|case"
    private val NOT_NAME = re(
        "(?i)জেলা|থানা|উপজেলা|গ্রাম|পোস্ট|পোষ্ট|ডাকঘর|ইউনিয়ন|বাজার|রোড|সদর|মোবাইল|ফোন|নম্বর|নাম্বার|মডেল|টাকা|ঠিকানা|বাকি|দাম" +
            "|\\b(?:district|dist|zila|zilla|thana|upazila|upozila|village|vill|post|road|house|holding|sadar|bazar|" +
            "mobile|phone|number|model|tk|taka|due|price|address|cod|total|" + BRANDS + ")\\b")
    private val PRICE_STRONG = re("(?i)(?<![a-z])(?:due|cod|total|collect|condition|amount)(?![a-z])|কন্ডিশন|বাকি|কালেক্ট|মোট")
    private val PRICE_WEAK = re("(?i)(?<![a-z])(?:tk|taka|price|bill|bdt)(?![a-z])|টাকা|৳|/-|/=|দাম|মূল্য")
    private val PURE_NUMBER = re("^(?:৳|tk\\.?|bdt)?\\s*(\\d{2,6})\\s*(?:tk|taka|টাকা|৳|/-|/=|=|bdt)?\\s*\\.?$", true)
    private val NUMBER = re("(?<![A-Za-z\\d])(\\d{1,3}(?:,\\d{3})+|\\d+)(?!\\d)")
    private val NUMBER_ALONE = re("(?<![A-Za-z\\d])(\\d{1,3}(?:,\\d{3})+|\\d+)(?![A-Za-z\\d])")
    private const val PHONE_LABEL = "(?:(?<![a-z])(?:mobile|mob|phone|cell|contact|whatsapp|number|num|no)(?![a-z])\\.?" +
        "|মোবাইল|ফোন|নম্বর|নাম্বার|হোয়াটসঅ্যাপ|যোগাযোগ)"
    private val PHONE_WITH_LABEL = re("(?:$PHONE_LABEL\\s*[:ঃ=\\-–—.।]*\\s*)*" + PHONE.pattern, true)
    private val ONLY_PHONE_LABELS = re("^(?:$PHONE_LABEL|[\\s:ঃ=\\-–—.।,;|/()])*$", true)
    private val ADDRESS_LABEL = re(
        "^(?:(?:customer|receiver|recipient|delivery|full|কাস্টমার|গ্রাহক|প্রাপক|ডেলিভারি|পূর্ণ|সম্পূর্ণ)" +
            "(?:'s|’s|er|ের)?\\s*)*(?:address|addr|ঠিকানা)(?![a-zঀ-ং঄-৿])\\s*[:ঃ=\\-–—.।]*\\s*", true)
    private fun nfc(s: String) = java.text.Normalizer.normalize(s, java.text.Normalizer.Form.NFC)
    private val PHONE_MODEL = re(nfc(
        "(?i)(?<![a-z])(?:model|walton|vivo|oppo|samsung|xiaomi|redmi|realme|nokia|symphony|itel|tecno|infinix|" +
            "huawei|nubia|iphone|oneplus|motorola|moto|poco|lava|zte|benco|maximus|helio|pixel|lenovo|asus|htc|" +
            "alcatel|micromax|gionee|meizu|primo|xanon|zenix|galaxy)(?![a-z])" +
            "|মডেল|ওয়ালটন|ভিভো|অপো|ওপো|স্যামসাং|শাওমি|রেডমি|রিয়েলমি|নোকিয়া|সিম্ফনি|আইটেল|টেকনো|ইনফিনিক্স|" +
            "হুয়াওয়ে|আইফোন|পোকো|মটোরোলা|নুবিয়া"))
    private val MODEL_ONLY = re("(?i)^[a-z]{1,3} ?\\d{1,3}[a-z]{0,2}\\+?(?: (?:pro|max|plus|lite|prime|ultra|neo|power|play|[45]g))*$")
    private val ADDRESS_HINT = re(nfc(
        "(?i)জেলা|থানা|উপজেলা|গ্রাম|পোস্ট|পোষ্ট|ডাকঘর|ইউনিয়ন|বাজার|রোড|সদর|বাসা|বাড়ি|মোড়" +
            "|\\b(?:district|dist|zila|zilla|thana|upazila|upozila|village|vill|post|road|house|holding|sadar|bazar|" +
            "sector|block|lane|area)\\b"))
    private const val EDGE_CHARS = " \t,;:|/\\-–—.।ঃ="

    fun normalizeCode(prefix: String, number: String) = ProductCode.norm(prefix, number)
    fun splitCode(code: String): Pair<String, Int> = ProductCode.split(code)

    /** Codes of a list file, in file order, without duplicates or blank lines. */
    fun readCodeList(text: String): List<String> {
        val seen = LinkedHashSet<String>()
        for (line in text.lines()) {
            val m = LIST_LINE.find(bnToAscii(line.trimEnd('\r'))) ?: continue
            seen += normalizeCode(m.groupValues[1], m.groupValues[2])
        }
        return seen.toList()
    }

    /** Marks a code as done in the list text the way it is done by hand: "3-620<TAB>  01814120910". */
    fun writePhoneToList(text: String, code: String, phone: String): String? {
        val lines = text.split("\n").toMutableList()
        var changed = false
        for (i in lines.indices) {
            val line = lines[i]
            val body = line.trimEnd('\r')
            val m = LIST_LINE.find(bnToAscii(body)) ?: continue
            if (normalizeCode(m.groupValues[1], m.groupValues[2]) == code) {
                lines[i] = body.trimEnd() + "\t  " + phone + line.substring(body.length)
                changed = true
            }
        }
        return if (changed) lines.joinToString("\n") else null
    }

    private fun clean(line: String): String {
        var l = bnToAscii(line).replace('\u00a0', ' ')
        l = Regex("[*~`]|(?<!\\d)_|_(?!\\d)").replace(l, "")   // *bold* / _italic_ markers, not "2_152"
        return Regex("\\s+").replace(l, " ").trim()
    }

    private fun lines(text: String?) = (text ?: "").lines().map { clean(it) }.filter { it.isNotEmpty() }

    /** The order codes a message starts with: ["6-872"], ["3-163", "3-164"] for "3-163+164" or "3-163-164", or []. */
    fun findCodes(text: String?): List<String> {
        for (line in lines(text).take(3)) {
            val m = CODE_LINE.find(line) ?: continue
            if (m.range.first != 0) continue
            return ProductCode.matchCodes(m)
        }
        return emptyList()
    }

    fun findPhones(text: String?): List<String> {
        val phones = mutableListOf<String>()
        for (m in PHONE.findAll(bnToAscii(text ?: ""))) {
            var digits = m.groupValues[1].filter { it.isDigit() }
            if (digits.length == 10) digits = "0$digits"
            if (digits.length == 11 && digits !in phones) phones += digits
        }
        return phones
    }

    private fun cleanName(s0: String): String {
        var s = PHONE.replace(s0, " ")
        NAME_STOP.find(s)?.let { s = s.substring(0, it.range.first) }
        s = Regex("\\s+").replace(s, " ")
        return s.trim { it in " \t,;:|/\\-–—.।ঃ()[]" }
    }

    private fun looksLikeName(line: String): Boolean {
        val s = line.trim()
        return s.length in 2..40 && !Regex("\\d").containsMatchIn(s) && ',' !in s &&
            s.split(Regex("\\s+")).size <= 5 && !NOT_NAME.containsMatchIn(s)
    }

    private fun stripLead(line: String) = re("^[^\\w]+").replace(line, "")

    private fun findName(lines: List<String>): Pair<String, List<Int>> {
        for ((i, line) in lines.withIndex()) {
            val m = NAME_LABEL.find(stripLead(line)) ?: continue
            var name = cleanName(m.groupValues[1])
            var used = listOf(i)
            if (name.isEmpty() && i + 1 < lines.size && looksLikeName(lines[i + 1])) {
                name = cleanName(lines[i + 1])
                used = listOf(i, i + 1)
            }
            if (name.isNotEmpty()) return name to used
        }
        for ((i, line) in lines.take(3).withIndex()) if (looksLikeName(line)) return cleanName(line) to listOf(i)
        return "" to emptyList()
    }

    private fun numbers(line: String, alone: Boolean = false): List<Int> {
        var t = PHONE.replace(line, " ")
        t = CODE_ANY.replace(t, " ")
        t = re("(?i)(tk|taka|bdt|৳)(?=\\d)").replace(t) { it.value + " " }
        val out = mutableListOf<Int>()
        for (m in (if (alone) NUMBER_ALONE else NUMBER).findAll(t)) {
            val v = m.groupValues[1].replace(",", "").toLongOrNull() ?: continue
            if (v in 10..999999) out += v.toInt()
        }
        return out
    }

    private data class Amount(val value: String, val guessed: Boolean, val line: Int)

    private fun findAmount(lines: List<String>): Amount {
        var best: Triple<Int, Int, Int>? = null // rank, line, value
        for ((i, line) in lines.withIndex()) {
            val rank = if (PRICE_STRONG.containsMatchIn(line)) 2 else if (PRICE_WEAK.containsMatchIn(line)) 1 else 0
            if (rank == 0) continue
            val values = numbers(line)
            if (values.isNotEmpty() && (best == null || rank > best.first || (rank == best.first && i > best.second))) {
                best = Triple(rank, i, values.max())
            }
        }
        best?.let { return Amount(it.third.toString(), false, it.second) }
        for (i in lines.indices.reversed()) {
            val m = PURE_NUMBER.matchEntire(lines[i]) ?: continue
            return Amount(m.groupValues[1].toInt().toString(), false, i)
        }
        for (i in lines.indices.reversed().take(2)) {
            val values = numbers(lines[i], alone = true).filter { it >= 50 }
            if (values.isNotEmpty()) return Amount(values.max().toString(), true, i)
        }
        return Amount("", false, -1)
    }

    private fun isPhoneModel(s0: String): Boolean {
        if (MODEL_ONLY.matches(s0)) return true
        val s = nfc(s0)
        return PHONE_MODEL.containsMatchIn(s) && s.split(Regex("\\s+")).size <= 6 && !ADDRESS_HINT.containsMatchIn(s)
    }

    private fun findAddress(lines: List<String>, name: String, nameLines: List<Int>, amount: String, amountLine: Int): String {
        val parts = mutableListOf<String>()
        for ((i, line) in lines.withIndex()) {
            var s = line
            val m = NAME_LABEL.find(stripLead(s))
            if (m != null || i in nameLines) {
                s = m?.groupValues?.get(1) ?: s
                if (name.isNotEmpty()) s = s.replaceFirst(name, " ")
            }
            if (i == amountLine) {
                s = PRICE_WEAK.replace(PRICE_STRONG.replace(s, " "), " ")
                s = NUMBER.replace(s) { if (it.groupValues[1].replace(",", "") == amount) " " else it.value }
            }
            s = PHONE_WITH_LABEL.replace(s, " ")
            s = Regex("\\(\\s*\\)|\\[\\s*]").replace(s, " ")
            s = ADDRESS_LABEL.replace(s.trim { it in EDGE_CHARS }, "")
            s = Regex("\\s*,[\\s,]*").replace(s, ", ")
            s = Regex("\\s+").replace(s, " ").trim { it in EDGE_CHARS }
            if (!re("[^\\W\\d_]").containsMatchIn(s) || ONLY_PHONE_LABELS.matches(s) || isPhoneModel(s)) continue
            if (s !in parts) parts += s
        }
        return parts.joinToString(", ")
    }

    data class Order(val code: String, val name: String, val phone: String, val phone2: String, val address: String,
                     val amount: String, val warnings: List<String>)

    fun parseOrder(text: String?): Order {
        val lines = lines(text)
        var code = ""
        var codeLine = -1
        for ((i, line) in lines.take(3).withIndex()) {
            val m = CODE_LINE.find(line) ?: continue
            if (m.range.first != 0) continue
            code = normalizeCode(m.groupValues[1], m.groupValues[3])
            codeLine = i
            break
        }
        val body = lines.filterIndexed { i, _ -> i != codeLine }
        val phones = findPhones(body.joinToString("\n"))
        val (name, nameLines) = findName(body)
        val amount = findAmount(body)
        val address = findAddress(body, name, nameLines, amount.value, if (amount.guessed) -1 else amount.line)
        val warnings = mutableListOf<String>()
        if (name.isEmpty()) warnings += "name not found"
        if (phones.isEmpty()) warnings += "phone number not found"
        if (address.isEmpty()) warnings += "address not found"
        if (amount.value.isEmpty()) warnings += "amount not found"
        else if (amount.guessed) warnings += "amount ${amount.value} is a guess"
        if (phones.size > 2) warnings += "${phones.size} phone numbers in the message, only the first two are used"
        return Order(code, name, phones.getOrElse(0) { "" }, phones.getOrElse(1) { "" }, address, amount.value, warnings)
    }
}
