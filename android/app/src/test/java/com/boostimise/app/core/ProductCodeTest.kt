package com.boostimise.app.core

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * ProductCode.kt against core/code_samples.json: the sample messages and the codes each code style must
 * find in them, the same file the Windows app, the iPhone app and the website are checked against.
 * Run: gradlew testDebugUnitTest
 */
class ProductCodeTest {
    private val samples: JSONObject by lazy {
        var dir: File? = File("").absoluteFile
        while (dir != null && !File(dir, "core/code_samples.json").exists()) dir = dir.parentFile
        JSONObject(File(dir ?: error("core/code_samples.json not found above ${File("").absolutePath}"),
            "core/code_samples.json").readText())
    }

    private fun canon(v: Any?): String = when (v) {
        null, JSONObject.NULL -> "null"
        is JSONObject -> v.keys().asSequence().sorted().joinToString(",", "{", "}") { "\"$it\":" + canon(v.opt(it)) }
        is JSONArray -> (0 until v.length()).joinToString(",", "[", "]") { canon(v.opt(it)) }
        is Map<*, *> -> canon(JSONObject(v))
        is Collection<*> -> canon(JSONArray(v))
        is Pair<*, *> -> canon(JSONArray(listOf(v.first, v.second)))
        is String -> JSONObject.quote(v)
        is Number -> v.toLong().toString()
        else -> v.toString()
    }

    private fun strings(a: JSONArray) = (0 until a.length()).map { a.getString(it) }

    private fun <T> JSONArray.each(f: (JSONObject) -> T) = (0 until length()).map { f(getJSONObject(it)) }

    @Test
    fun readsEverySampleLikeTheOtherApps() {
        val styles = samples.getJSONObject("styles")
        val readers = styles.keys().asSequence().associateWith { ProductCode.Reader(ProductCode.cleanStyle(styles.getJSONObject(it))) }
        val bad = mutableListOf<String>()
        fun expect(what: String, got: Any?, want: Any?) {
            if (canon(got) != canon(want)) bad += "$what: got ${canon(got)}, want ${canon(want)}"
        }

        samples.getJSONArray("messages").each { s ->
            expect("[${s.getString("style")}] codes in ${s.getString("text")}",
                readers.getValue(s.getString("style")).codesInText(s.getString("text")), strings(s.getJSONArray("codes")))
        }
        samples.getJSONArray("order_codes").each { s ->
            expect("[${s.getString("style")}] order codes of ${s.getString("text")}",
                readers.getValue(s.getString("style")).orderCodes(s.getString("text")), strings(s.getJSONArray("codes")))
        }
        samples.getJSONArray("list_lines").each { s ->
            expect("[${s.getString("style")}] list line ${s.getString("line")}",
                readers.getValue(s.getString("style")).splitListLine(s.getString("line")), s.getString("code") to s.getString("rest"))
        }
        samples.getJSONArray("normalize").each { s ->
            expect("[${s.getString("style")}] key of ${s.getString("code")}",
                readers.getValue(s.getString("style")).normalize(s.getString("code")), s.getString("key"))
        }
        samples.getJSONArray("code_parts").each { s ->
            val parts = readers.getValue(s.getString("style")).codeParts(s.getString("code"))
            expect("[${s.getString("style")}] parts of ${s.getString("code")}",
                parts?.let { mapOf("prefix" to it.first, "number" to it.second) } ?: emptyMap<String, Any>(), s.getJSONObject("parts"))
        }
        samples.getJSONArray("patterns").each { s ->
            val code = s.getString("code")
            val pattern = re(readers.getValue(s.getString("style")).codePattern(code), true)
            for (t in strings(s.getJSONArray("match"))) if (!pattern.containsMatchIn(t)) bad += "[${s.getString("style")}] the pattern of $code does not match $t"
            for (t in strings(s.getJSONArray("no_match"))) if (pattern.containsMatchIn(t)) bad += "[${s.getString("style")}] the pattern of $code matches $t"
        }
        samples.getJSONArray("any_codes").each { s ->
            val any = re(readers.getValue(s.getString("style")).jsAnyCode, true)
            expect("[${s.getString("style")}] any codes in ${s.getString("text")}",
                any.findAll(s.getString("text")).map { ProductCode.norm(it.groupValues[1], it.groupValues[2]) }.distinct().toList(),
                strings(s.getJSONArray("codes")))
        }
        samples.getJSONArray("sort").each { s ->
            val want = strings(s.getJSONArray("codes"))
            expect("[${s.getString("style")}] sorted", want.reversed().sortedWith(readers.getValue(s.getString("style")).order), want)
        }
        samples.getJSONArray("examples").each { s ->
            expect("[${s.getString("style")}] example",
                ProductCode.example(ProductCode.cleanStyle(styles.getJSONObject(s.getString("style")))), s.getString("example"))
        }
        samples.getJSONArray("guess").each { s ->
            val (style, problem) = ProductCode.guessStyle(s.getString("examples"))
            expect("guess ${s.getString("examples")}", style?.toJson(), s.opt("style"))
            if (style == null && problem.isNullOrEmpty()) bad += "guess ${s.getString("examples")}: no style and no reason"
        }
        samples.getJSONArray("clean_style").each { s ->
            expect("clean ${s.opt("in")}", ProductCode.cleanStyle(s.optJSONObject("in")).toJson(), s.getJSONObject("out"))
        }
        assertTrue("ProductCode.kt reads these samples wrong:\n" + bad.joinToString("\n"), bad.isEmpty())
    }
}
