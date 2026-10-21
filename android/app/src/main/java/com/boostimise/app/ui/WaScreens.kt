package com.boostimise.app.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.boostimise.app.Tools
import com.boostimise.app.tools.fetch.ChatFilter
import com.boostimise.app.tools.fetch.ENHANCE_SCALES
import com.boostimise.app.tools.fetch.FetchTool
import com.boostimise.app.tools.fetch.NEIGHBOUR_MODES
import com.boostimise.app.tools.reactions.CUSTOM
import com.boostimise.app.wa.WaEngine
import kotlinx.coroutines.launch
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** Above the WhatsApp Web page: its state and how to link this phone. */
@Composable
fun WhatsAppHeader() {
    var help by remember { mutableStateOf(false) }
    var forget by remember { mutableStateOf(false) }
    var details by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()
    val ctx = LocalContext.current
    Column(Modifier.fillMaxWidth().padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        WaBanner()
        if (WaEngine.phase == WaEngine.Phase.QR) {
            Hint("Below, tap \"Log in with phone number\", enter this phone's WhatsApp number and you get an 8-letter code. " +
                "Then in WhatsApp: ⋮ › Linked devices › Link a device › \"Link with phone number instead\", and type the code.")
        }
        Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
            OutlinedButton({ WaEngine.reload() }) { Text("Reload") }
            TextButton({ scope.launch { details = WaEngine.diagnostics() } }) { Text("Details") }
            TextButton({ help = true }) { Text("Help") }
            TextButton({ forget = true }) { Text("Log out") }
        }
    }
    details?.let { text ->
        AlertDialog(onDismissRequest = { details = null },
            confirmButton = { TextButton({ copy(ctx, text); details = null }) { Text("Copy") } },
            dismissButton = {
                Row {
                    TextButton({ WaEngine.reload(clearCache = true); details = null }) { Text("Clear cache & reload") }
                    TextButton({ details = null }) { Text("Close") }
                }
            },
            title = { Text("WhatsApp Web details") },
            text = { Text(text, fontSize = 11.sp, modifier = Modifier.verticalScroll(rememberScrollState())) })
    }
    if (help) AlertDialog(onDismissRequest = { help = false }, confirmButton = { TextButton({ help = false }) { Text("OK") } },
        title = { Text("WhatsApp in Boostimise") }, text = {
            Text("All tools share this one WhatsApp Web, linked once to your WhatsApp like a PC. It counts as one of WhatsApp's " +
                "linked devices next to your PC's. Pinch to zoom the page. Keep Boostimise open while a tool reads chats: " +
                "a notification shows while one is working.")
        })
    if (forget) AlertDialog(onDismissRequest = { forget = false },
        confirmButton = { TextButton({ WaEngine.forgetLogin(); forget = false }) { Text("Log out") } },
        dismissButton = { TextButton({ forget = false }) { Text("Cancel") } },
        title = { Text("Log out of WhatsApp Web?") }, text = { Text("Every tool will need WhatsApp linked again.") })
}

@Composable
fun FetchScreen(t: FetchTool,
                codesHint: String = "Lines like \"2-558  Realme C85  26/9\" - the photos are saved as \"2-558 Realme C85.jpg\".",
                extra: @Composable ColumnScope.() -> Unit = {}) {
    val ctx = LocalContext.current
    Page {
        WaBanner()
        Section("1  Codes", codesHint) {
            CodeListBox(t.codeText, { t.codeText = it; t.parse() })
            if (t.rows.isNotEmpty()) Hint("${t.rows.size} codes")
        }
        Section("2  Settings") {
            ChatFilterPicker(t.chat) { t.chat = it }
            CheckRow("A chat for each list (6-, 5a- …)", t.perList) { t.perList = it; if (it) t.autoFillListChats() }
            if (t.perList) ListChats(t.lists(), t.listChats, t.chat) { t.autoFillListChats() }
            Select("Take", NEIGHBOUR_MODES, t.neighbors) { t.neighbors = it }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Column(Modifier.weight(1f)) { NumberField("Album gap", t.gapMinutes, 1..240, "min") { t.gapMinutes = it } }
                Column(Modifier.weight(1f)) { NumberField("Look back", t.lookbackDays, 1..365, "days") { t.lookbackDays = it } }
            }
            CheckRow("Add the date to names (26/9 → 26-9)", t.includeDate) { t.includeDate = it }
            CheckRow("Skip codes that are already saved", t.skipExisting) { t.skipExisting = it }
            extra()
            androidx.compose.material3.OutlinedTextField(t.folder, { t.folder = it }, label = { Text("Save in Pictures/Boostimise/") },
                singleLine = true, modifier = Modifier.fillMaxWidth())
        }
        Section("3  Run") {
            RunBar(t.running, t.progress, t.activity, t.summary, onStart = { t.start() }, onStop = { t.stop() })
        }
        if (t.rows.isNotEmpty()) Section("Results") {
            for (r in t.rows) Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth()) {
                StatusDot(r.kind)
                Spacer(Modifier.width(8.dp))
                Column(Modifier.weight(1f)) {
                    Text(listOf(r.item.code, r.item.name, r.item.date).filter { it.isNotEmpty() }.joinToString("  "),
                        fontWeight = FontWeight.Medium, fontSize = 14.sp)
                    if (r.text.isNotEmpty()) Text(r.text, color = Brand.TEXT_2, fontSize = 12.sp)
                }
                if (!t.running && r.kind in setOf("none", "error", "")) TextButton({ t.start(listOf(r)) }) { Text("Retry") }
            }
            Hint("Saved in the gallery: Pictures/Boostimise/${t.folder}")
        }
        LogBox(t.log)
    }
}

/** One chat picker per list in the code list, filled in from chat names ("My Shop (6-)"). */
@Composable
fun ListChats(lists: List<Pair<String, Int>>, chats: MutableMap<String, ChatFilter>, fallback: ChatFilter,
                      autoFill: () -> Unit) {
    LaunchedEffect(WaEngine.chats, lists) { autoFill() }
    if (lists.isEmpty()) {
        Hint("Paste the code list above: each list (6-, 5a- …) then gets its chat here.")
        return
    }
    for ((list, count) in lists) {
        ListChatPicker("$list list · $count code" + if (count > 1) "s" else "", chats[list],
            "No \"($list)\" chat: searched in ${fallback.label}") { chats[list] = it }
    }
    Hint("Filled in from chat names with \"(6-)\", \"(5a-)\" …. Tap one to choose another chat. " +
        "Each list's codes are searched only in its chat.")
}

@Composable
fun ReactionListScreen() {
    val t = Tools.reactionList
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    Page {
        WaBanner()
        Section("Reaction") {
            Chips {
                for (c in t.choices) {
                    if (c.key == t.choice.key) Button({ }) { Text(c.label) } else OutlinedButton({ t.choiceKey = c.key }) { Text(c.label) }
                }
            }
            androidx.compose.material3.OutlinedTextField(t.customText, { t.customText = it; t.choiceKey = CUSTOM },
                label = { Text("Custom reactions") }, placeholder = { Text("Type your emojis, e.g. 👍 ❤️ 🔥") },
                singleLine = true, enabled = !t.running, modifier = Modifier.fillMaxWidth())
            Hint("😥 also counts 😢 (WhatsApp's built-in sad reaction). Skin tones count as one reaction (👍🏻 = 👍).")
        }
        Section("Codes (optional)", "Leave empty to list every code that got the reaction in the chats read.") {
            CodeListBox(t.codeText, { t.codeText = it })
        }
        Section("Settings") {
            ChatFilterPicker(t.chat) { t.chat = it }
            CheckRow("A chat for each list (6-, 5a- …)", t.perList, enabled = !t.running) { t.perList = it; if (it) t.autoFillListChats() }
            if (t.perList) ListChats(remember(t.codeText) { t.lists() }, t.listChats, t.chat) { t.autoFillListChats() }
            NumberField("Look back", t.lookbackDays, 1..365, "days") { t.lookbackDays = it }
            Text("Fetch", fontWeight = FontWeight.Medium)
            Chips {
                for ((photos, label) in listOf(false to "List only", true to "List + photos")) {
                    if (photos == t.withPhotos) Button({ }) { Text(label) }
                    else OutlinedButton({ t.withPhotos = photos }, enabled = !t.running) { Text(label) }
                }
            }
            if (t.withPhotos) {
                androidx.compose.material3.OutlinedTextField(t.folder, { t.folder = it }, label = { Text("Save in Pictures/Boostimise/") },
                    singleLine = true, enabled = !t.running, modifier = Modifier.fillMaxWidth())
                Hint("Saves each listed code's reacted photos as \"2-558 Realme C85.jpg\". A code whose message has the " +
                    "reaction gets all its photos; otherwise only the photos with the reaction.")
            }
        }
        Section {
            RunBar(t.running, t.progress, t.activity, t.summary, startLabel = if (t.withPhotos) "Fetch list + photos" else "Fetch list", onStart = { t.start() }, onStop = { t.stop() })
        }
        if (t.results.isNotEmpty()) Section("${t.results.size} codes") {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button({ copy(ctx, t.formatList()) }) { Text("Copy list") }
                OutlinedButton({
                    scope.launch {
                        saveText(ctx, "Reaction list ${SimpleDateFormat("dd-MM-yyyy", Locale.US).format(Date())}.txt", t.formatList())
                    }
                }) { Text("Save .txt") }
            }
            t.results.forEachIndexed { n, f ->
                Column(Modifier.fillMaxWidth().background(if (n % 2 == 0) Brand.BG else Brand.SURFACE).padding(6.dp)) {
                    Text("${n + 1}. ${f.code} ${f.name} ${f.react(t.shown, t.labels)}", fontWeight = FontWeight.Medium)
                    if (f.phones.isNotEmpty()) Text(f.phones.joinToString(", "), fontSize = 13.sp)
                    Text(f.chats.joinToString(", "), color = Brand.MUTED, fontSize = 12.sp)
                    if (f.photos.isNotEmpty()) Text("Photos: ${f.photos}", color = Brand.TEXT_2, fontSize = 12.sp)
                }
            }
            if (t.results.any { it.photos.isNotEmpty() }) Hint("Photos saved in the gallery: Pictures/Boostimise/${t.folder}")
        }
        LogBox(t.log)
    }
}

@Composable
fun FetchEnhanceScreen() {
    val t = Tools.fetchEnhance
    FetchScreen(t, "Lines like \"2-558  Realme C85\" - each code's photos of people are enhanced and saved as " +
        "\"2-558 Realme C85.jpg\"; 2 or more go into a folder \"2-558 Realme C85\" as img1.jpg, img2.jpg …") {
        Select("Enhance", ENHANCE_SCALES, t.scale) { t.scale = it }
        CheckRow("Only photos of people (skip phones, screenshots …)", t.peopleOnly) { t.peopleOnly = it }
        CheckRow("Add the (note) from the WhatsApp caption", t.addNote) { t.addNote = it }
        Hint("Each photo is enhanced on the Boostimise server like in the Photo Enhancer, and counts in your daily " +
            "enhancements. The names come from the code list straight away (the Windows app can also check them " +
            "with Rename by Match).")
    }
    if (t.denied.isNotEmpty()) LimitDialog(t.denied) { t.denied = "" }
}
