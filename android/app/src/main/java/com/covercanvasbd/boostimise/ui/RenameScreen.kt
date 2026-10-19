package com.covercanvasbd.boostimise.ui

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.covercanvasbd.boostimise.Tools
import com.covercanvasbd.boostimise.tools.fetch.NEIGHBOUR_MODES

@Composable
fun RenameScreen() {
    val t = Tools.renameByMatch
    var askUndo by remember { mutableStateOf(false) }
    val pick = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocumentTree()) { uri ->
        if (uri != null) t.setFolder(uri)
    }
    Page {
        WaBanner()
        Section("1  Product list", "Lines like \"6-852 Nubia a56\". Each code is looked up in WhatsApp and the matching " +
            "photo in your folder is renamed \"6-852 Nubia a56.jpg\".") {
            CodeListBox(t.codeText, { t.codeText = it; t.parse() }, placeholder = "6-852 Nubia a56\n6-853 Redmi 13C")
            if (t.rows.isNotEmpty()) Hint("${t.rows.size} codes in ${t.lists().size} list(s): " + t.lists().joinToString(", ") { it.first })
            if (t.skippedCodes.isNotEmpty()) Hint("No product name, skipped: ${t.skippedCodes.joinToString(", ")}", Brand.WARNING)
        }
        Section("2  Photo folder", "The folder with the photos to rename. A folder with list folders (\"6-\", \"5a-\") " +
            "uses each list's own folder.") {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(t.folderLabel.ifEmpty { "No folder chosen" }, Modifier.weight(1f), fontWeight = FontWeight.Medium,
                    color = if (t.folderLabel.isEmpty()) Brand.MUTED else Brand.NAVY)
                OutlinedButton({ pick.launch(t.tree) }, enabled = !t.running) { Text(if (t.tree == null) "Choose" else "Change") }
            }
            Hint("Android asks once to let Boostimise use the folder (\"Use this folder\" › Allow).")
            CheckRow("Include sub folders", t.subFolders, enabled = !t.running) { t.subFolders = it }
        }
        Section("3  WhatsApp") {
            ChatFilterPicker(t.chat) { t.chat = it }
            CheckRow("A chat for each list (6-, 5a- …)", t.perList, enabled = !t.running) { t.perList = it; if (it) t.autoFillListChats() }
            if (t.perList) ListChats(t.lists(), t.listChats, t.chat) { t.autoFillListChats() }
            Select("Take", NEIGHBOUR_MODES, t.neighbors) { t.neighbors = it }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Column(Modifier.weight(1f)) { NumberField("Album gap", t.gapMinutes, 1..240, "min") { t.gapMinutes = it } }
                Column(Modifier.weight(1f)) { NumberField("Look back", t.lookbackDays, 1..365, "days") { t.lookbackDays = it } }
            }
        }
        Section("4  Renaming") {
            NumberField("Match limit (0 = same file, 10 = normal)", t.threshold, 0..30) { t.threshold = it }
            Hint("One photo becomes \"2-226 Realme P3 Ultra.jpg\"; 2 or more go into a folder \"2-221 Oppo a6 pro 5g\" " +
                "as img1.jpg, img2.jpg …. Photos already named with a code are renamed again unless they already have " +
                "the name in the list.")
            CheckRow("Add the (note) from the WhatsApp caption", t.addNote, enabled = !t.running) { t.addNote = it }
            CheckRow("Skip codes whose photo already has its name", t.skipDone, enabled = !t.running) { t.skipDone = it }
            CheckRow("Preview only (nothing is renamed)", t.dryRun, enabled = !t.running) { t.dryRun = it }
        }
        Section {
            RunBar(t.running, t.progress, t.activity, t.summary, startLabel = if (t.dryRun) "Preview" else "Start",
                onStart = { t.start() }, onStop = { t.stop() })
            if (t.undoWhen.isNotEmpty() && !t.running) TextButton({ askUndo = true }) { Text("Undo last rename (${t.undoWhen})") }
        }
        if (t.rows.isNotEmpty()) Section("Results") {
            for (r in t.rows) Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth().padding(vertical = 2.dp)) {
                StatusDot(r.kind)
                Spacer(Modifier.width(8.dp))
                Column(Modifier.weight(1f)) {
                    Text(r.product.label, fontWeight = FontWeight.Medium, fontSize = 14.sp)
                    if (r.text.isNotEmpty()) Text(r.text, color = Brand.TEXT_2, fontSize = 12.sp)
                }
            }
        }
        LogBox(t.log)
    }
    if (askUndo) AlertDialog(onDismissRequest = { askUndo = false },
        confirmButton = { TextButton({ askUndo = false; t.undo() }) { Text("Undo") } },
        dismissButton = { TextButton({ askUndo = false }) { Text("Cancel") } },
        title = { Text("Undo the last rename?") },
        text = { Text("The photos renamed on ${t.undoWhen} get their old names back; the copies it made are removed.") })
}
