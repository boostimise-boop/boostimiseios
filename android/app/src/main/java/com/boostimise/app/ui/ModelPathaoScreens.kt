package com.boostimise.app.ui

import android.app.Activity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.rounded.List
import androidx.compose.material.icons.rounded.DoneAll
import androidx.compose.material.icons.rounded.Forum
import androidx.compose.material.icons.rounded.Inventory2
import androidx.compose.material.icons.rounded.Pause
import androidx.compose.material.icons.rounded.PlayArrow
import androidx.compose.material.icons.rounded.Search
import androidx.compose.material.icons.rounded.SkipNext
import androidx.compose.material.icons.rounded.Smartphone
import androidx.compose.material.icons.rounded.Stop
import androidx.compose.material.icons.rounded.Tune
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
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
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import com.boostimise.app.Tools
import com.boostimise.app.core.OrderParser
import com.boostimise.app.tools.model.ClaudeIdentifier
import com.boostimise.app.tools.model.ModelDetectTool
import com.boostimise.app.tools.pathao.PathaoTool
import com.boostimise.app.wa.WaEngine
import kotlinx.coroutines.launch
import kotlin.math.roundToInt

@Composable
fun ModelDetectScreen() {
    val t = Tools.modelDetect
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    val groups = t.groups()
    LaunchedEffect(groups.keys, WaEngine.chats) { t.autoMatch(WaEngine.chats) }
    val total = groups.values.sumOf { it.size }
    val kinds = t.results.map { modelKind(it) }
    Page {
        WaBanner()
        ControlCard(waKicker(t.running),
            when {
                t.running -> "Finding the phone models of ${plural(total, "code")}"
                t.results.isNotEmpty() -> "${kinds.count { it == "ok" || it == "none" }} of ${plural(t.results.size, "code")} have a model"
                total == 0 -> "Load the code list"
                else -> "${plural(total, "code")} ready"
            },
            if (t.running) t.activity else "",
            ringOf(if (t.running || t.results.isNotEmpty()) (t.progress * total).roundToInt() else 0, total, "codes", t.running),
            listOf(Count("Found", kinds.count { it == "ok" }, Brand.LIVE), Count("Unsure", kinds.count { it == "none" }, Brand.AMBER_ON_NAVY),
                Count("Not found", kinds.count { it == "error" }, Brand.RED_ON_NAVY))) {
            if (t.running) HeroButton("Stop", Icons.Rounded.Stop, outline = true) { t.stop() }
            else HeroButton("Start", Icons.Rounded.PlayArrow, primary = true) { t.start() }
        }
        Section("Code list", "Your product codes list. The models are written next to the codes.",
            Icons.AutoMirrored.Rounded.List, Tint.NEUTRAL) {
            CodeListBox(t.codeText, { t.codeText = it }, placeholder = "5a-401\n5a-402\n5a-403 samsung a70")
            if (groups.isNotEmpty()) Hint("${plural(total, "code")} in ${groups.size} group(s)")
        }
        if (groups.isNotEmpty()) Section("Chat of each code group",
            "A chat whose name contains the group code (e.g. \"(5a-)\") is chosen by itself.", Icons.Rounded.Forum, Tint.SUCCESS) {
            for ((p, codes) in groups) {
                SingleChatPicker("${if (p.isEmpty()) "Orders" else "Group $p-"}  (${codes.size} codes, ${codes.first()} … ${codes.last()})", t.chatFor[p]) { t.chatFor[p] = it }
            }
        }
        Section("Settings", icon = Icons.Rounded.Tune, tint = Tint.WARNING) {
            Select("In the chat", listOf("below" to "Code first, photos below it (until the next code)",
                "above" to "Photos first, code below them (back to the previous code)"), t.mode) { t.mode = it }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Column(Modifier.weight(1f)) { NumberField("Photos per code", t.maxImages, 1..10) { t.maxImages = it } }
                Column(Modifier.weight(1f)) { NumberField("Look back", t.lookbackDays, 1..365, "days") { t.lookbackDays = it } }
            }
            Select("Identify phones with", listOf(
                "text" to "Model names written in the chat or in the photos (free)",
                "claude" to "Written names first, else ask Claude (paid, your API key)",
                "none" to "Don't identify - only save the photos"), t.engine) { t.engine = it }
            if (t.engine == "claude") {
                OutlinedTextField(t.apiKey, { t.apiKey = it }, label = { Text("Claude API key") }, singleLine = true,
                    visualTransformation = PasswordVisualTransformation(), modifier = Modifier.fillMaxWidth())
                Select("Model", ClaudeIdentifier.MODELS.map { it to it }, t.model) { t.model = it }
                Select("Effort", ClaudeIdentifier.EFFORTS.map { it to it }, t.effort) { t.effort = it }
            }
            CheckRow("Save the photos (Pictures/Boostimise/Model Detect/<code>)", t.savePhotos) { t.savePhotos = it }
            CheckRow("Skip codes that already have a model in the list", t.skipDone) { t.skipDone = it }
        }
        if (t.results.isNotEmpty()) Section("Results", "The models are written next to the codes", Icons.Rounded.Smartphone,
            Tint.ACCENT) {
            Chips {
                Button({ copy(ctx, t.resultText()) }) { Text("Copy list with models") }
                OutlinedButton({ scope.launch { saveText(ctx, "productcodes models.txt", t.resultText()) } }) { Text("Save .txt") }
            }
            Column(Modifier.fillMaxWidth()) {
                t.results.forEachIndexed { i, r ->
                    StatusRow(kinds.getOrElse(i) { "" }, r.code, r.model.ifEmpty { "—" },
                        listOf(r.confidence.takeIf { it.isNotEmpty() }?.let { "$it confidence" } ?: "",
                            if (r.images > 0) plural(r.images, "photo") else "").filter { it.isNotEmpty() }.joinToString(" · "),
                        more = listOf(r.note))
                }
            }
        }
        LogBox(t.log)
    }
}

/** A Model Detect result as a row kind: found ok, unsure "none" (amber), not found "error" (red). */
private fun modelKind(r: ModelDetectTool.Result) = when {
    !r.done -> "working"
    r.model.isEmpty() || r.model == "NOT FOUND" || r.model == "NO IMAGE" -> "error"
    r.confidence == "low" -> "none"
    else -> "ok"
}

/** A Pathao code's status as a row kind (tools/pathao_autofill ROW_KINDS). */
private fun pathaoKind(status: String) = when (status) {
    PathaoTool.FETCHING, PathaoTool.FILLED -> "working"
    PathaoTool.SAVED, PathaoTool.SAVED_EARLIER, PathaoTool.IN_PATHAO -> "ok"
    PathaoTool.NOT_FOUND -> "none"
    PathaoTool.SKIPPED -> "skipped"
    else -> ""
}

/** Pathao's control card: what to do next, the saved / to do / not found counts and the run's buttons. */
@Composable
private fun PathaoControl(t: PathaoTool) {
    val kinds = t.items.map { pathaoKind(it.status) }
    val saved = kinds.count { it == "ok" }
    val kicker = when {
        t.attention.isNotEmpty() -> "NEEDS YOU" to Brand.AMBER_ON_NAVY
        t.checking -> "CHECKING PATHAO" to Brand.SKY
        t.waiting -> "CHECK PATHAO AND SAVE" to Brand.SKY
        t.running -> "WORKING" to Brand.LIVE
        t.items.isNotEmpty() && saved == t.items.size -> "DONE" to Brand.LIVE
        else -> "READY" to Brand.SKY
    }
    ControlCard(kicker, t.attention.ifEmpty { t.state },
        t.cur?.let { "Current order: ${it.code}" } ?: if (t.listName.isNotEmpty()) "List: ${t.listName}" else "",
        ringOf(saved, t.items.size, "saved"),
        listOf(Count("Saved", saved, Brand.LIVE), Count("To do", kinds.count { it == "" || it == "working" }, Brand.SKY),
            Count("Not found", kinds.count { it == "none" }, Brand.RED_ON_NAVY))) {
        if (t.running) HeroButton("Pause", Icons.Rounded.Pause) { t.pause() }
        else HeroButton("Start", Icons.Rounded.PlayArrow, primary = true) { t.start() }
        HeroButton("Saved → Next", Icons.Rounded.DoneAll, enabled = t.cur != null) { t.markSaved() }
        HeroButton("Skip", Icons.Rounded.SkipNext) { t.skip() }
        // which codes of the list already have a Pathao order (by Merchant Order ID); the phone is written in the list
        HeroButton(if (t.checking) "Checking Pathao…" else "Check Pathao", Icons.Rounded.Search,
            enabled = !t.checking && t.items.isNotEmpty()) { t.checkPathao() }
    }
}

@Composable
fun PathaoScreen(activity: Activity) {
    val t = Tools.pathao
    val scope = rememberCoroutineScope()
    val ctx = LocalContext.current
    var settings by remember { mutableStateOf(false) }
    var showList by remember { mutableStateOf(false) }
    var showLog by remember { mutableStateOf(false) }
    val open = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) scope.launch { runCatching { t.openList(uri) }.onFailure { toast(ctx, "Could not open the list") } }
    }
    Column(Modifier.fillMaxSize()) {
        // folded: one slim bar, the Pathao page gets the rest of the screen
        if (t.compact) Column(Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 4.dp)) {
            Text(t.cur?.let { "${it.code} · ${it.status}" } ?: t.state, fontSize = 13.sp, maxLines = 1,
                overflow = TextOverflow.Ellipsis)
            if (t.attention.isNotEmpty()) Hint(t.attention, Brand.ERROR)
            // the buttons wrap on a narrow screen or with a large font, instead of being squeezed
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.weight(1f)) {
                    Chips {
                        if (t.running) TextButton({ t.pause() }) { Text("Pause") } else TextButton({ t.start() }) { Text("Start") }
                        TextButton({ t.markSaved() }, enabled = t.cur != null) { Text("Saved → Next") }
                        TextButton({ t.skip() }) { Text("Skip") }
                    }
                }
                TextButton({ t.fold(false) }) { Text("More ▾") }
            }
        }
        // the controls: at most half of the screen, the Pathao page below
        else Column(Modifier.fillMaxWidth().heightIn(max = 420.dp).verticalScroll(rememberScrollState()).padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.weight(1f)) { WaBanner() }
                TextButton({ t.fold(true) }) { Text("Less ▴") }
            }
            PathaoControl(t)
            Chips {
                OutlinedButton({ open.launch(arrayOf("text/*")) }) { Text("Open list") }
                OutlinedButton({ paste(ctx)?.let { t.pasteList(it) } }) { Text("Paste list") }
                TextButton({ settings = true }) { Text("Settings") }
            }
            t.cur?.let { item -> CurrentOrder(item, t) }
            Chips {
                TextButton({ showList = !showList }) { Text(if (showList) "Hide list" else "Show list") }
                TextButton({ showLog = !showLog }) { Text(if (showLog) "Hide log" else "Show log") }
                if (t.listName == "pasted list") TextButton({ copy(ctx, t.listText) }) { Text("Copy list") }
            }
            if (showList) Section("Codes", if (t.listName.isNotEmpty()) t.listName else null, Icons.AutoMirrored.Rounded.List,
                Tint.NEUTRAL) {
                Column(Modifier.fillMaxWidth()) {
                    for (it in t.items) StatusRow(pathaoKind(it.status), it.code, status = it.status,
                        trailing = if (!t.running && it.status in setOf(PathaoTool.NOT_FOUND, PathaoTool.SKIPPED, PathaoTool.PENDING)) {
                            { TextButton({ t.reread(it) }) { Text("Do now") } }
                        } else null)
                }
            }
            if (showLog) for (l in t.log.lines.takeLast(60)) Text(l, fontSize = 11.sp)
        }
        HorizontalDivider()
        AndroidView(factory = { t.view(activity) }, modifier = Modifier.fillMaxWidth().weight(1f))
    }
    if (settings) PathaoSettings(t) { settings = false }
}

@Composable
private fun CurrentOrder(item: PathaoTool.Item, t: PathaoTool) {
    val o = item.order ?: return
    var name by remember(o) { mutableStateOf(o.name) }
    var phone by remember(o) { mutableStateOf(o.phone) }
    var phone2 by remember(o) { mutableStateOf(o.phone2) }
    var amount by remember(o) { mutableStateOf(o.amount) }
    var address by remember(o) { mutableStateOf(o.address) }
    Section("Current order", "${item.code} · ${item.status}", Icons.Rounded.Inventory2, Tint.ACCENT) {
        if (item.raw.isNotEmpty()) Text(item.raw.take(400), Modifier.fillMaxWidth().background(Brand.BG, RoundedCornerShape(12.dp))
            .padding(10.dp), fontSize = 12.sp, color = Brand.TEXT_2)
        Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            OutlinedTextField(name, { name = it }, label = { Text("Name") }, singleLine = true, modifier = Modifier.weight(1f))
            OutlinedTextField(amount, { amount = it }, label = { Text("Amount") }, singleLine = true, modifier = Modifier.width(100.dp))
        }
        Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            OutlinedTextField(phone, { phone = it }, label = { Text("Phone") }, singleLine = true, modifier = Modifier.weight(1f))
            OutlinedTextField(phone2, { phone2 = it }, label = { Text("Phone 2") }, singleLine = true, modifier = Modifier.weight(1f))
        }
        OutlinedTextField(address, { address = it }, label = { Text("Address") }, modifier = Modifier.fillMaxWidth())
        Chips {
            Button({ t.refill(o.copy(name = name, phone = phone, phone2 = phone2, amount = amount, address = address)) }) { Text("Fill again") }
            TextButton({ t.reread(item) }) { Text("Read WhatsApp again") }
        }
    }
}

@Composable
private fun PathaoSettings(t: PathaoTool, onClose: () -> Unit) {
    AlertDialog(onDismissRequest = onClose, confirmButton = { TextButton({ t.saveSettings(); onClose() }) { Text("Save") } },
        title = { Text("Pathao Auto-fill settings") }, text = {
            LaunchedEffect(Unit) { t.loadChatNames() }
            Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Hint("The WhatsApp chat of each code group (the same as in Shop settings and your other Boostimise apps):")
                t.chatNames.forEachIndexed { i, (p, n) ->
                    OutlinedTextField(n, { t.chatNames[i] = p to it }, label = { Text("$p-") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                }
                var newPrefix by remember { mutableStateOf("") }
                Row(verticalAlignment = Alignment.CenterVertically) {
                    OutlinedTextField(newPrefix, { newPrefix = it.trim().lowercase() }, label = { Text("Add group") }, singleLine = true,
                        modifier = Modifier.weight(1f))
                    TextButton({ if (newPrefix.isNotEmpty()) { t.chatNames += newPrefix to "$newPrefix-"; newPrefix = "" } }) { Text("Add") }
                }
                NumberField("Read order messages of the last", t.lookbackDays, 1..365, "days") { t.lookbackDays = it }
                CheckRow("Start from the bottom of the list", t.startFromBottom) { t.startFromBottom = it }
                CheckRow("After a save, write the phone next to the code in the list", t.writePhone) { t.writePhone = it }
            }
        })
}

@Suppress("unused")
private fun OrderParser.Order.summary() = listOf(name, phone, amount).joinToString(" · ")
