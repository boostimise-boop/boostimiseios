package com.covercanvasbd.boostimise.ui

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.widget.Toast
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowDropDown
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Checkbox
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.covercanvasbd.boostimise.core.Storage
import com.covercanvasbd.boostimise.tools.fetch.ChatFilter
import com.covercanvasbd.boostimise.tools.fetch.ToolLog
import com.covercanvasbd.boostimise.wa.WaChat
import com.covercanvasbd.boostimise.wa.WaEngine
import kotlinx.coroutines.launch

@Composable
fun Page(content: @Composable ColumnScope.() -> Unit) {
    Column(
        Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
        content = content,
    )
}

@Composable
fun Section(title: String? = null, hint: String? = null, content: @Composable ColumnScope.() -> Unit) {
    Surface(shape = RoundedCornerShape(14.dp), color = Brand.SURFACE, modifier = Modifier.fillMaxWidth()
        .border(1.dp, Brand.BORDER, RoundedCornerShape(14.dp))) {
        Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            if (title != null) Text(title, fontWeight = FontWeight.SemiBold, fontSize = 16.sp)
            if (hint != null) Text(hint, color = Brand.TEXT_2, fontSize = 13.sp)
            content()
        }
    }
}

@Composable
fun Hint(text: String, color: Color = Brand.TEXT_2) = Text(text, color = color, fontSize = 13.sp)

@Composable
fun CheckRow(label: String, checked: Boolean, enabled: Boolean = true, onChange: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth().clickable(enabled = enabled) { onChange(!checked) }, verticalAlignment = Alignment.CenterVertically) {
        Checkbox(checked, onChange, enabled = enabled)
        Text(label, fontSize = 14.sp)
    }
}

@Composable
fun NumberField(label: String, value: Int, range: IntRange, suffix: String = "", onChange: (Int) -> Unit) {
    var text by remember(value) { mutableStateOf(value.toString()) }
    OutlinedTextField(
        value = text,
        onValueChange = { t ->
            text = t.filter { it.isDigit() }.take(4)
            text.toIntOrNull()?.let { onChange(it.coerceIn(range)) }
        },
        label = { Text(label) },
        suffix = { if (suffix.isNotEmpty()) Text(suffix) },
        singleLine = true,
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
        modifier = Modifier.fillMaxWidth(),
    )
}

@Composable
fun <T> Select(label: String, options: List<Pair<T, String>>, value: T, onChange: (T) -> Unit) {
    var open by remember { mutableStateOf(false) }
    Box {
        OutlinedTextField(
            value = options.firstOrNull { it.first == value }?.second ?: "",
            onValueChange = {}, readOnly = true, label = { Text(label) },
            trailingIcon = { Icon(Icons.Filled.ArrowDropDown, null) },
            modifier = Modifier.fillMaxWidth(),
        )
        Box(Modifier.matchParentSize().clickable { open = true })
        DropdownMenu(open, { open = false }) {
            options.forEach { (k, v) -> DropdownMenuItem(text = { Text(v) }, onClick = { onChange(k); open = false }) }
        }
    }
}

/** The pasted code list, or a .txt file opened from the phone. */
@Composable
fun CodeListBox(text: String, onChange: (String) -> Unit, placeholder: String = "2-558\tRealme C85\t26/9\n2-559\tSamsung A70") {
    val scope = rememberCoroutineScope()
    val ctx = LocalContext.current
    val open = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) scope.launch {
            runCatching { onChange(Storage.readText(uri)) }.onFailure { toast(ctx, "Could not read the file") }
        }
    }
    // A long list is shown folded: a scrolling text box inside the scrolling page catches the swipes
    // that start on it, so the page would not move. Tap it to edit.
    var editing by remember { mutableStateOf(false) }
    var focused by remember { mutableStateOf(false) }
    val focus = remember { FocusRequester() }
    val lines = remember(text) { text.lines().filter { it.isNotBlank() } }
    if (lines.size > 6 && !editing) {
        Column(Modifier.fillMaxWidth().border(1.dp, Brand.BORDER, RoundedCornerShape(4.dp)).clickable { editing = true }
            .padding(14.dp)) {
            Text(lines.take(3).joinToString("\n"), style = MaterialTheme.typography.bodyMedium, maxLines = 3)
            Hint("… ${lines.size} lines · tap to edit", Brand.ACCENT)
        }
    } else {
        OutlinedTextField(text, onChange, label = { Text("Code list (one per line)") }, placeholder = { Text(placeholder) },
            modifier = Modifier.fillMaxWidth().heightIn(min = 120.dp, max = 260.dp).focusRequester(focus).onFocusChanged {
                if (it.isFocused) editing = true            // typing a short list past 6 lines must not fold it
                else if (focused) editing = false
                focused = it.isFocused
            }, textStyle = MaterialTheme.typography.bodyMedium)
        LaunchedEffect(editing) { if (editing) focus.requestFocus() }
    }
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        OutlinedButton({ open.launch(arrayOf("text/*")); editing = false }) { Text("Open .txt") }
        OutlinedButton({ paste(ctx)?.let(onChange); editing = false }) { Text("Paste") }
        if (editing && lines.size > 6) TextButton({ editing = false }) { Text("Done") }
        if (text.isNotEmpty()) TextButton({ onChange("") }) { Text("Clear") }
    }
}

/** "Search in": all chats, all groups, a name, one chat, or (with [ticked], the chats kept for it) the ticked chats. */
@Composable
fun ChatFilterPicker(filter: ChatFilter, ticked: MutableList<Pair<String, String>>? = null, onChange: (ChatFilter) -> Unit) {
    var open by remember { mutableStateOf(false) }
    var ticking by remember { mutableStateOf(false) }
    Box {
        OutlinedTextField(filter.label, {}, readOnly = true, label = { Text("Search in") },
            trailingIcon = { Icon(Icons.Filled.ArrowDropDown, null) }, modifier = Modifier.fillMaxWidth())
        Box(Modifier.matchParentSize().clickable { open = true })
    }
    if (ticked != null && filter.ids.isNotEmpty()) Hint(ticked.joinToString(", ") { it.second.ifEmpty { it.first } })
    if (open) ChatDialog(true, { open = false }, ticked?.let { { open = false; ticking = true } }) { pick ->
        onChange(pick)
        open = false
    }
    if (ticking && ticked != null) TickChatsDialog(ticked, { ticking = false }) {
        onChange(ChatFilter(ids = ticked.map { it.first }))
        ticking = false
    }
}

/** Pick a chat (or, with [filters], all chats / all groups / a name to search for; with [onTicked], ticked chats). */
@Composable
fun ChatDialog(filters: Boolean, onDismiss: () -> Unit, onTicked: (() -> Unit)? = null, onPick: (ChatFilter) -> Unit) {
    var q by remember { mutableStateOf("") }
    val chats = WaEngine.chats.filter { q.isBlank() || it.title.contains(q, ignoreCase = true) }.take(300)
    AlertDialog(onDismissRequest = onDismiss, confirmButton = { TextButton(onDismiss) { Text("Close") } },
        title = { Text("Choose a chat") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                OutlinedTextField(q, { q = it }, label = { Text("Search chats") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                if (WaEngine.chats.isEmpty()) Hint("Open WhatsApp first (the WhatsApp page in the menu) to load your chats.")
                LazyColumn(Modifier.heightIn(max = 380.dp)) {
                    if (filters) {
                        item { ChatLine("All chats") { onPick(ChatFilter()) } }
                        item { ChatLine("All groups") { onPick(ChatFilter(groupsOnly = true)) } }
                        if (onTicked != null) item { ChatLine("Ticked chats…", "choose several") { onTicked() } }
                        if (q.isNotBlank()) item { ChatLine("Chats whose name contains \"$q\"") { onPick(ChatFilter(title = q)) } }
                        item { HorizontalDivider() }
                    }
                    items(chats, key = { it.id }) { c -> ChatLine(c.title, if (c.isGroup) "group" else null) { onPick(ChatFilter(c.id, c.title)) } }
                }
            }
        })
}

/** Tick the chats to search in; [ticked] (id, title of each) is updated when they are confirmed. */
@Composable
fun TickChatsDialog(ticked: MutableList<Pair<String, String>>, onDismiss: () -> Unit, onDone: () -> Unit) {
    val sel = remember { mutableStateListOf(*ticked.toTypedArray()) }
    var q by remember { mutableStateOf("") }
    var tickedOnly by remember { mutableStateOf(false) }
    val ids = sel.map { it.first }.toSet()
    // the chats ticked before first (even if WhatsApp has not listed them yet), then WhatsApp's order
    val rows = remember(WaEngine.chats) {
        val known = WaEngine.chats.associateBy { it.id }
        val before = ticked.map { it.first }.toSet()
        ticked.map { (id, title) -> id to (known[id]?.title ?: title) } +
            WaEngine.chats.filter { it.id !in before }.map { it.id to it.title }
    }
    val shown = rows.filter { (id, title) -> (q.isBlank() || title.contains(q, ignoreCase = true)) && (!tickedOnly || id in ids) }
    AlertDialog(onDismissRequest = onDismiss,
        confirmButton = {
            TextButton({ ticked.clear(); ticked.addAll(sel); onDone() }, enabled = sel.isNotEmpty()) { Text("Search these") }
        },
        dismissButton = { TextButton(onDismiss) { Text("Cancel") } },
        title = { Text("Ticked chats") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                OutlinedTextField(q, { q = it }, label = { Text("Search chats") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                if (WaEngine.chats.isEmpty()) Hint("Open WhatsApp first (the WhatsApp page in the menu) to load your chats.")
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(if (sel.isEmpty()) "Tick the chats to search in" else "${sel.size} chat(s) ticked",
                        Modifier.weight(1f), color = Brand.TEXT_2, fontSize = 13.sp)
                    if (sel.isNotEmpty()) TextButton({ sel.clear() }) { Text("Untick all") }
                    TextButton({ tickedOnly = !tickedOnly }) { Text(if (tickedOnly) "Show all" else "Ticked only") }
                }
                LazyColumn(Modifier.heightIn(max = 380.dp)) {
                    items(shown, key = { it.first }) { (id, title) ->
                        val on = id in ids
                        val toggle = { if (on) sel.removeAll { it.first == id } else sel.add(id to title) }
                        Row(Modifier.fillMaxWidth().clickable { toggle() }, verticalAlignment = Alignment.CenterVertically) {
                            Checkbox(on, { toggle() })
                            Text(title.ifEmpty { id }, Modifier.weight(1f), fontSize = 15.sp)
                            if (id.endsWith("@g.us")) Text("group", color = Brand.MUTED, fontSize = 12.sp)
                        }
                    }
                }
            }
        })
}

@Composable
private fun ChatLine(title: String, tag: String? = null, onClick: () -> Unit) {
    Row(Modifier.fillMaxWidth().clickable(onClick = onClick).padding(vertical = 10.dp, horizontal = 4.dp),
        verticalAlignment = Alignment.CenterVertically) {
        Text(title, Modifier.weight(1f), fontSize = 15.sp)
        if (tag != null) Text(tag, color = Brand.MUTED, fontSize = 12.sp)
    }
}

@Composable
fun SingleChatPicker(label: String, chat: WaChat?, onPick: (WaChat) -> Unit) {
    var open by remember { mutableStateOf(false) }
    Box {
        OutlinedTextField(chat?.title ?: "Choose…", {}, readOnly = true, label = { Text(label) },
            trailingIcon = { Icon(Icons.Filled.ArrowDropDown, null) }, modifier = Modifier.fillMaxWidth())
        Box(Modifier.matchParentSize().clickable { open = true })
    }
    if (open) ChatDialog(false, { open = false }) { f ->
        WaEngine.chats.firstOrNull { it.id == f.id }?.let(onPick)
        open = false
    }
}

/** The chat of one list of codes ("6- list"); [fallback] is shown when it has none. */
@Composable
fun ListChatPicker(label: String, chat: ChatFilter?, fallback: String, onPick: (ChatFilter) -> Unit) {
    var open by remember { mutableStateOf(false) }
    Box {
        OutlinedTextField(chat?.title ?: fallback, {}, readOnly = true, label = { Text(label) },
            trailingIcon = { Icon(Icons.Filled.ArrowDropDown, null) }, modifier = Modifier.fillMaxWidth())
        Box(Modifier.matchParentSize().clickable { open = true })
    }
    if (open) ChatDialog(false, { open = false }) { f ->
        onPick(f)
        open = false
    }
}

/** The WhatsApp state, and a way to the WhatsApp page when it is not ready. */
@Composable
fun WaBanner() {
    val ready = WaEngine.phase == WaEngine.Phase.READY
    val color = when (WaEngine.phase) {
        WaEngine.Phase.READY -> Brand.SUCCESS
        WaEngine.Phase.QR -> Brand.WARNING
        WaEngine.Phase.ERROR -> Brand.ERROR
        else -> Brand.ACCENT
    }
    Row(Modifier.fillMaxWidth().background(Brand.ACCENT_SOFT, RoundedCornerShape(10.dp)).padding(10.dp),
        verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(10.dp).background(color, CircleShape))
        Spacer(Modifier.width(8.dp))
        Text(if (WaEngine.phase == WaEngine.Phase.OFF) "WhatsApp is not open" else "WhatsApp: ${WaEngine.message}" +
            if (WaEngine.holder.isNotEmpty()) " · ${WaEngine.holder} is using it" else "",
            Modifier.weight(1f), fontSize = 13.sp)
        if (!ready) TextButton({ WaEngine.open(); Nav.go(Nav.Screen.WHATSAPP) }) { Text("Open") }
    }
}

@Composable
fun RunBar(running: Boolean, progress: Float, activity: String, summary: String, startLabel: String = "Start",
           onStart: () -> Unit, onStop: () -> Unit) {
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
        if (running) OutlinedButton(onStop) { Text("Stop") } else Button(onStart) { Text(startLabel) }
    }
    if (running) LinearProgressIndicator(progress = { progress.coerceIn(0f, 1f) }, modifier = Modifier.fillMaxWidth())
    if (activity.isNotEmpty()) Hint(activity)
    if (summary.isNotEmpty()) Text(summary, fontSize = 14.sp, fontWeight = FontWeight.Medium)
}

@Composable
fun LogBox(log: ToolLog) {
    var open by remember { mutableStateOf(false) }
    Section {
        Row(Modifier.fillMaxWidth().clickable { open = !open }, verticalAlignment = Alignment.CenterVertically) {
            Text("Log (${log.lines.size})", Modifier.weight(1f), fontWeight = FontWeight.SemiBold)
            Text(if (open) "Hide" else "Show", color = Brand.ACCENT)
        }
        if (open) {
            val ctx = LocalContext.current
            Column(Modifier.fillMaxWidth().heightIn(max = 320.dp).verticalScroll(rememberScrollState())) {
                for (l in log.lines.takeLast(200)) Text(l, fontFamily = FontFamily.Monospace, fontSize = 11.sp)
            }
            Row { TextButton({ copy(ctx, log.lines.joinToString("\n")) }) { Text("Copy log") }; TextButton({ log.clear() }) { Text("Clear") } }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun Chips(content: @Composable () -> Unit) = FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp),
    verticalArrangement = Arrangement.spacedBy(6.dp)) { content() }

@Composable
fun StatusDot(kind: String) {
    val c = when (kind) {
        "ok" -> Brand.SUCCESS
        "working" -> Brand.ACCENT
        "none" -> Brand.WARNING
        "error" -> Brand.ERROR
        "skipped" -> Brand.MUTED
        else -> Color(0xFFD0D6E0)
    }
    Box(Modifier.size(10.dp).background(c, CircleShape))
}

fun toast(ctx: Context, text: String) = Toast.makeText(ctx, text, Toast.LENGTH_SHORT).show()

fun copy(ctx: Context, text: String) {
    ctx.getSystemService(ClipboardManager::class.java).setPrimaryClip(ClipData.newPlainText("Boostimise", text))
    toast(ctx, "Copied")
}

fun paste(ctx: Context): String? =
    ctx.getSystemService(ClipboardManager::class.java).primaryClip?.takeIf { it.itemCount > 0 }?.getItemAt(0)?.coerceToText(ctx)?.toString()

/** Saves a text file in Download/Boostimise. */
suspend fun saveText(ctx: Context, name: String, text: String) {
    try {
        Storage.save(Storage.downloadsPath(), name, text.toByteArray())
        toast(ctx, "Saved to Download/Boostimise/$name")
    } catch (e: Exception) {
        toast(ctx, "Could not save: ${e.message}")
    }
}
