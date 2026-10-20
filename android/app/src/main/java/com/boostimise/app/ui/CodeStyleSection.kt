package com.boostimise.app.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.width
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.boostimise.app.core.ProductCode
import com.boostimise.app.core.Shop
import org.json.JSONObject

private val STYLES = listOf(
    "prefix" to "Prefix and number, like 2-558",
    "letters" to "Letters and number, like CC1023 or AB-12",
    "keyword" to "# or a word and number, like #1023",
)
private val SEPS = listOf("" to "CC1023", "-" to "CC-1023", " " to "CC 1023")
private val ERROR = Color(0xFFE7000B)

private fun words(text: String) = text.replace(",", " ").split(Regex("\\s+")).filter { it.isNotEmpty() }

/** The shop's code style on the Shop screen (core/code_style_widgets.py): pick one of the three styles, or
 *  type a few of your codes and let the app pick; paste a message to see the codes it finds. Saved in the
 *  shop settings ("code_style", and "orders_chat" for codes without a prefix). */
@Composable
fun CodeStyleSection() {
    val ctx = LocalContext.current
    var kind by remember { mutableStateOf("prefix") }
    var letters by remember { mutableStateOf("") }
    var sep by remember { mutableStateOf("") }
    var keywords by remember { mutableStateOf("") }
    var ordersChat by remember { mutableStateOf("") }
    var examples by remember { mutableStateOf("") }
    var guessNote by remember { mutableStateOf("") }
    var guessBad by remember { mutableStateOf(false) }
    var sample by remember { mutableStateOf("") }
    var error by remember { mutableStateOf("") }
    var dirty by remember { mutableStateOf(false) }

    fun show(s: ProductCode.Style) {
        kind = s.kind
        if (s.kind == "letters") {
            letters = s.letters.joinToString(", ")
            sep = s.sep
        }
        if (s.kind == "keyword") keywords = s.keywords.joinToString(", ")
    }

    fun load() {
        show(ProductCode.cleanStyle(Shop.get("code_style") as? JSONObject))
        ordersChat = (Shop.get("orders_chat") as? String).orEmpty()
        dirty = false
        error = ""
    }
    // shows what another app saved, unless something is being changed here
    LaunchedEffect(Shop.version) { if (!dirty) load() }

    /** The style as shown (the prefix style while it is not complete). */
    fun typed(): ProductCode.Style = when (kind) {
        "letters" -> ProductCode.cleanStyle(ProductCode.Style("letters", words(letters), sep).toJson())
        "keyword" -> ProductCode.cleanStyle(ProductCode.Style("keyword", keywords = words(keywords)).toJson())
        else -> ProductCode.DEFAULT_STYLE
    }

    fun problems(): List<String> = buildList {
        when (kind) {
            "letters" -> {
                val bad = words(letters).firstOrNull { w -> w.length > 4 || !w.all { it in 'A'..'Z' || it in 'a'..'z' } }
                if (bad != null) add("Letters are 1 to 4 letters A-Z, not “$bad”.")
                else if (words(letters).isEmpty()) add("Type your letters, e.g. CC.")
            }
            "keyword" -> {
                val bad = words(keywords).firstOrNull { w -> w != "#" && !(w.length <= 12 && w.all { it in 'A'..'Z' || it in 'a'..'z' }) }
                if (bad != null) add("Type # or one word of the letters A-Z, not “$bad”.")
                else if (words(keywords).isEmpty()) add("Type # or the word before your numbers, e.g. Order.")
                if (ordersChat.isBlank()) add("Type the name of the WhatsApp chat your orders are in.")
            }
        }
    }

    Section("Code style", "How your product codes are written in WhatsApp.") {
        Select("Style", STYLES, kind) { kind = it; dirty = true }
        if (kind == "letters") {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                OutlinedTextField(letters, { letters = it; dirty = true }, label = { Text("Your letters, e.g. CC, AB") },
                    singleLine = true, modifier = Modifier.weight(1f))
                Box(Modifier.width(132.dp)) { Select("Written", SEPS, sep) { sep = it; dirty = true } }
            }
            Hint("Each letters' codes are in the WhatsApp chat set for those letters below (e.g. CC), like a list of prefix codes.")
        }
        if (kind == "keyword") {
            OutlinedTextField(keywords, { keywords = it; dirty = true }, label = { Text("Your # or word, e.g. #, Order") },
                singleLine = true, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(ordersChat, { ordersChat = it; dirty = true }, label = { Text("Orders chat (WhatsApp chat name)") },
                singleLine = true, modifier = Modifier.fillMaxWidth())
            Hint("These codes have no prefix, so all of them are looked for in this one chat.")
        }
        HorizontalDivider()
        Text("Not sure? Type two or three of your codes", fontWeight = FontWeight.Medium)
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            OutlinedTextField(examples, { examples = it }, label = { Text("e.g. CC1023, CC1050") }, singleLine = true,
                modifier = Modifier.weight(1f))
            OutlinedButton({
                val (style, problem) = ProductCode.guessStyle(examples)
                if (style == null) {
                    guessNote = problem.orEmpty()
                    guessBad = true
                } else {
                    show(style)
                    dirty = true
                    guessNote = "${ProductCode.describe(style)}. Save it if this is right."
                    guessBad = false
                }
            }) { Text("Pick") }
        }
        if (guessNote.isNotEmpty()) Hint(guessNote, if (guessBad) ERROR else Brand.TEXT_2)
        OutlinedTextField(sample, { sample = it }, label = { Text("Try it: paste a message from WhatsApp") }, minLines = 2,
            modifier = Modifier.fillMaxWidth())
        val style = typed()
        val found = remember(style, sample) {
            val r = ProductCode.Reader(style)
            r.codesInText(sample).map { key -> ProductCode.split(key).let { (p, n) -> r.written(p, n.toString()) } }
        }
        Hint(when {
            sample.isBlank() -> "Codes will look like ${ProductCode.example(style)}."
            found.isEmpty() -> "No codes found. A code in this style looks like ${ProductCode.example(style)}."
            else -> "Codes found: ${found.joinToString(", ")}"
        })
        if (error.isNotEmpty()) Hint(error, ERROR)
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button({
                val problems = problems()
                if (problems.isNotEmpty()) {
                    error = problems.joinToString("\n")
                    return@Button
                }
                Shop.save(other = mapOf(
                    "code_style" to if (style == ProductCode.DEFAULT_STYLE) null else style.toJson(),
                    "orders_chat" to ordersChat.trim().ifEmpty { null },
                ))
                load()
                toast(ctx, "Code style saved: ${ProductCode.describe(style)}")
            }, enabled = dirty) { Text("Save code style") }
            OutlinedButton({ load() }, enabled = dirty) { Text("Undo") }
        }
    }
}
