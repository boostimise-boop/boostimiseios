package com.boostimise.app.core

import android.content.SharedPreferences
import android.util.Log
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject

/** One reaction of the shop: the emoji and what it means to the seller ("Cancelled"), or "". */
data class ShopReaction(val emoji: String, val label: String)

/**
 * The seller's shop settings (core/shop.py), shared by the website and the Windows, Android and
 * iPhone apps through the account: the Reaction List reactions and what each one means, the
 * WhatsApp chat of each code prefix, and the code style with its orders chat (kept as they come).
 *
 * The phone keeps a copy so the tools work signed out and offline. Signed in, the account's row in
 * shop_profiles counts: no row yet -> this phone's settings are uploaded; this phone's first sync
 * with the account -> the row is kept and chats it doesn't have are added from the phone; after
 * that the row wins, except over a change made here that could not be uploaded yet.
 */
object Shop {
    val DEFAULT_REACTIONS = listOf(ShopReaction("❌", ""), ShopReaction("📵", ""), ShopReaction("😥", ""))
    const val MAX_REACTIONS = 12

    var settings by mutableStateOf(clean(JSONObject()))
        private set
    var shopName by mutableStateOf("")
        private set
    /** Bumped on every change, for screens that copy the settings into their own state. */
    var version by mutableStateOf(0)
        private set

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val prefs: SharedPreferences by lazy { Storage.prefs("shop") }
    private var syncedUser: String? = null
    private var dirty = false
    private var syncing = false
    private var loaded = false
    private var syncedThisRun: String? = null

    // ---- reading -------------------------------------------------------------------------------
    fun reactions(): List<ShopReaction> = reactionsOf(load().settings)
    fun chats(): Map<String, String> = chatsOf(load().settings)
    fun chatFor(prefix: String): String = chatName(chats(), settings.optString("orders_chat"), prefix)
    fun get(key: String): Any? = load().settings.opt(key)

    // ---- changing ------------------------------------------------------------------------------
    /** Keeps the change on this phone and uploads it when signed in. [other]: more settings keys
     *  (code_style, orders_chat …); null removes one. */
    fun save(reactions: List<ShopReaction>? = null, chats: Map<String, String>? = null, name: String? = null,
             other: Map<String, Any?> = emptyMap()) {
        load()
        val s = JSONObject(settings.toString())
        if (reactions != null) s.put("reactions", JSONArray(reactions.map { jsonOf("emoji" to it.emoji, "label" to it.label) }))
        if (chats != null) s.put("chats", JSONObject(chats as Map<*, *>))
        for ((k, v) in other) if (v == null) s.remove(k) else s.put(k, v)
        val new = clean(s)
        val newName = name?.trim()?.take(80) ?: shopName
        if (same(new, settings) && newName == shopName) return
        settings = new
        shopName = newName
        dirty = true
        changed()
        upload()
    }

    fun set(key: String, value: Any?) = save(other = mapOf(key to value))

    // ---- the account's copy --------------------------------------------------------------------
    private fun uid(): String? =
        if (Account.state == Account.State.READY && !Account.offline) Account.user?.id else null

    /** Account says it is ready: sync once per account and app run (later changes come by Realtime). */
    fun onAccountReady() {
        val id = uid() ?: return
        if (syncedThisRun != id) sync()
    }

    fun sync() {
        load()
        val id = uid() ?: return
        if (syncing) return
        syncing = true
        scope.launch {
            val row = try {
                Account.select("shop_profiles", "select=shop_name,settings,updated_at&user_id=eq.$id").firstOrNull()
            } catch (e: Exception) {
                Log.i(TAG, "could not read the shop settings: $e")
                syncing = false
                return@launch
            }
            syncing = false
            if (uid() != id) return@launch
            syncedThisRun = id
            if (row == null || dirty) {
                if (row == null && syncedUser != null && syncedUser != id) {
                    settings = clean(JSONObject())  // another account's settings were on this phone
                    shopName = ""
                    changed()
                }
                upload()
                return@launch
            }
            val server = clean(row.optJSONObject("settings") ?: JSONObject())
            if (syncedUser == null) {  // this phone's first sync: keep its chats the row doesn't have
                val chats = LinkedHashMap(chatsOf(settings)).apply { putAll(chatsOf(server)) }
                val merged = clean(JSONObject(server.toString()).put("chats", JSONObject(chats as Map<*, *>)))
                if (!same(merged, server)) {
                    settings = merged
                    shopName = row.optStr("shop_name") ?: shopName
                    dirty = true
                    changed()
                    upload()
                    return@launch
                }
            }
            adopt(row, id)
        }
    }

    private fun upload() {
        val id = uid() ?: return
        val sent = JSONObject(settings.toString())
        val name = shopName.ifBlank { null }
        scope.launch {
            val row = try {
                Account.rpc("save_my_shop", jsonOf("p_settings" to sent, "p_shop_name" to name)) as? JSONObject
            } catch (e: Exception) {
                Log.i(TAG, "could not save the shop settings: $e")
                return@launch
            }
            if (uid() != id) return@launch
            if (same(sent, settings)) dirty = false
            if (row != null && !dirty) adopt(row, id) else {
                syncedUser = id
                write()
            }
        }
    }

    private fun adopt(row: JSONObject, id: String) {
        val new = clean(row.optJSONObject("settings") ?: JSONObject())
        val name = row.optStr("shop_name") ?: ""
        val differs = !same(new, settings) || name != shopName
        settings = new
        shopName = name
        syncedUser = id
        dirty = false
        if (differs) changed() else write()
    }

    private fun changed() {
        write()
        version++
        applyCodeStyle()
    }

    /** The tools read codes in the shop's style (settings["code_style"]; missing = prefix style). Called
     *  when the settings load and on every change. ProductCode.setStyle(settings.optJSONObject("code_style"))
     *  goes here once core/ProductCode.kt has it. */
    private fun applyCodeStyle() {
        ProductCode.setStyle(settings.optJSONObject("code_style"))
    }

    // ---- this phone's copy ---------------------------------------------------------------------
    private fun load(): Shop {
        if (loaded) return this
        loaded = true
        val saved = Http.parseJson(prefs.getString("settings", null)) as? JSONObject
        if (saved != null) {
            settings = clean(saved)
            shopName = prefs.getString("shop_name", "")!!
            syncedUser = prefs.getString("synced_user", null)
            dirty = prefs.getBoolean("dirty", false)
        } else {
            // Pathao Auto-fill's chat names from before the shop settings start this phone's copy
            val old = Http.parseJson(Storage.prefs("pathao_autofill").getString("chat_names", null)) as? JSONObject
            settings = clean(jsonOf("chats" to (old ?: JSONObject())))
        }
        applyCodeStyle()
        return this
    }

    private fun write() {
        prefs.edit().putString("settings", settings.toString()).putString("shop_name", shopName)
            .putString("synced_user", syncedUser).putBoolean("dirty", dirty).apply()
    }

    // ---- helpers -------------------------------------------------------------------------------
    fun chatName(chats: Map<String, String>, ordersChat: String, prefix: String): String {
        val p = prefix.lowercase()
        if (p.isEmpty()) return ordersChat.trim()
        return (chats[p] ?: "$p-").trim()
    }

    fun reactionsOf(s: JSONObject): List<ShopReaction> =
        s.optJSONArray("reactions").objects().map { ShopReaction(it.optString("emoji"), it.optString("label")) }

    fun chatsOf(s: JSONObject): Map<String, String> {
        val c = s.optJSONObject("chats") ?: return emptyMap()
        return c.keys().asSequence().associateWith { c.optString(it) }
    }

    /** Reactions each once and not empty; chats with lower-case prefixes, without "<prefix>-" names. */
    fun clean(s: JSONObject): JSONObject {
        val out = JSONObject(s.toString())
        val reactions = if (s.has("reactions")) {
            val seen = mutableSetOf<String>()
            s.optJSONArray("reactions").objects().mapNotNull {
                val e = it.optString("emoji").trim()
                if (e.isEmpty() || !seen.add(e)) null else jsonOf("emoji" to e, "label" to it.optString("label").trim().take(40))
            }.take(MAX_REACTIONS)
        } else DEFAULT_REACTIONS.map { jsonOf("emoji" to it.emoji, "label" to it.label) }
        out.put("reactions", JSONArray(reactions))
        val chats = JSONObject()
        s.optJSONObject("chats")?.let { c ->
            for (k in c.keys()) {
                val p = k.trim().lowercase()
                val n = c.optString(k).trim()
                if (p.isNotEmpty() && n.isNotEmpty() && n != "$p-") chats.put(p, n.take(120))
            }
        }
        out.put("chats", chats)
        return out
    }

    private fun canon(v: Any?): Any? = when (v) {
        is JSONObject -> v.keys().asSequence().sorted().associateWith { canon(v.opt(it)) }
        is JSONArray -> (0 until v.length()).map { canon(v.opt(it)) }
        JSONObject.NULL -> null
        else -> v
    }

    private fun same(a: JSONObject, b: JSONObject) = canon(a) == canon(b)

    private const val TAG = "Shop"
}
