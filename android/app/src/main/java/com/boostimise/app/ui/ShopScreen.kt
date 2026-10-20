package com.boostimise.app.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material3.Button
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import com.boostimise.app.core.Account
import com.boostimise.app.core.Shop
import com.boostimise.app.core.ShopReaction
import com.boostimise.app.tools.reactions.ReactionEmojis

/** Shop settings (tools/account/shop.py): the shop's name, the reactions with what each one means,
 *  and the WhatsApp chat of each code list. Kept in the account, so every Boostimise app uses them. */
@Composable
fun ShopScreen() {
    val ctx = LocalContext.current
    var name by remember { mutableStateOf("") }
    val reactions = remember { mutableStateListOf<Pair<String, String>>() }
    val chats = remember { mutableStateListOf<Pair<String, String>>() }
    var dirty by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf("") }

    fun load() {
        name = Shop.shopName
        reactions.clear()
        reactions.addAll(Shop.reactions().map { it.emoji to it.label })
        chats.clear()
        chats.addAll(Shop.chats().toList().sortedBy { it.first })
        dirty = false
        error = ""
    }
    // shows what another app saved, unless something is being typed here
    LaunchedEffect(Shop.version) { if (!dirty) load() }

    Page {
        Hint(if (Account.state == Account.State.READY && !Account.offline)
            "Saved in your Boostimise account: the website and the Windows, Android and iPhone apps all use these settings."
        else "Saved on this phone. They are shared with your other Boostimise apps once you are online.")
        Section("Your shop") {
            OutlinedTextField(name, { name = it; dirty = true }, label = { Text("Shop name") }, singleLine = true,
                modifier = Modifier.fillMaxWidth())
        }
        CodeStyleSection()
        Section("Reactions", "The reactions Reaction List looks for, and what each one means to you (e.g. ❌ Cancelled). " +
                "The meaning is written next to the reaction in the list.") {
            reactions.forEachIndexed { i, (e, l) ->
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    OutlinedTextField(e, { reactions[i] = it to l; dirty = true }, label = { Text("Emoji") }, singleLine = true,
                        modifier = Modifier.width(84.dp))
                    OutlinedTextField(l, { reactions[i] = e to it; dirty = true }, label = { Text("What it means") },
                        singleLine = true, modifier = Modifier.weight(1f))
                    IconButton({ reactions.removeAt(i); dirty = true }) { Icon(Icons.Filled.Close, "Remove") }
                }
            }
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                OutlinedButton({ reactions += "" to ""; dirty = true }) { Text("Add reaction") }
                TextButton({
                    reactions.clear()
                    reactions.addAll(Shop.DEFAULT_REACTIONS.map { it.emoji to it.label })
                    dirty = true
                }) { Text("Use ❌ 📵 😥") }
            }
        }
        Section("WhatsApp chats", "The chat where each code list's orders are posted, e.g. the 6- codes in “My Shop (6-)”. " +
                "A list without a chat here uses the chat named like the list (“6-”).") {
            chats.forEachIndexed { i, (p, n) ->
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    OutlinedTextField(p, { chats[i] = it to n; dirty = true }, label = { Text("List") }, singleLine = true,
                        modifier = Modifier.width(84.dp))
                    OutlinedTextField(n, { chats[i] = p to it; dirty = true }, label = { Text("Chat name") }, singleLine = true,
                        modifier = Modifier.weight(1f))
                    IconButton({ chats.removeAt(i); dirty = true }) { Icon(Icons.Filled.Close, "Remove") }
                }
            }
            OutlinedButton({ chats += "" to ""; dirty = true }) { Text("Add chat") }
        }
        if (error.isNotEmpty()) Hint(error, Color(0xFFE7000B))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button({
                val problems = mutableListOf<String>()
                val r = reactions.mapNotNull { (e, l) ->
                    if (e.isBlank() && l.isBlank()) return@mapNotNull null
                    val parts = ReactionEmojis.split(e)
                    if (parts.size != 1 || parts[0].all { it.code < 128 }) {
                        problems += "“${e.ifBlank { l }}”: put one emoji in the Emoji box."
                        null
                    } else ShopReaction(parts[0], l.trim())
                }
                if (r.isEmpty()) problems += "Add at least one reaction."
                val c = linkedMapOf<String, String>()
                for ((p, n) in chats) {
                    val prefix = p.trim().trimEnd('-').lowercase()
                    if (prefix.isEmpty() && n.isBlank()) continue
                    if (prefix.isEmpty() || n.isBlank()) problems += "Each chat needs both the list (e.g. 6) and the chat's name."
                    else c[prefix] = n.trim()
                }
                if (problems.isNotEmpty()) {
                    error = problems.distinct().joinToString("\n")
                    return@Button
                }
                Shop.save(reactions = r, chats = c, name = name)
                load()
                toast(ctx, "Shop settings saved")
            }, enabled = dirty) { Text("Save") }
            OutlinedButton({ load() }, enabled = dirty) { Text("Undo changes") }
        }
    }
}
