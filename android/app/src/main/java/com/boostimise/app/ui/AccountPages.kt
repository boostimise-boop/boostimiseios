package com.boostimise.app.ui

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
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
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.boostimise.app.core.Account
import com.boostimise.app.core.Supabase
import com.boostimise.app.core.jsonOf
import com.boostimise.app.core.objects
import kotlinx.coroutines.launch
import org.json.JSONObject
import java.time.OffsetDateTime
import java.time.format.DateTimeFormatter

private val MODE_LABEL = mapOf("auto" to "Auto", "face" to "Face Enhance", "restore" to "Photo Restore", "upscale" to "Upscale")

private fun parseDt(iso: String?): OffsetDateTime? = runCatching { OffsetDateTime.parse(iso) }.getOrNull()

private fun isActive(s: JSONObject): Boolean {
    val exp = s.optString("expires_at").takeIf { it.isNotEmpty() && it != "null" }
    return s.optString("status") == "active" && (exp == null || (parseDt(exp)?.isAfter(OffsetDateTime.now()) ?: true))
}

@Composable
fun DashboardScreen() {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    var data by remember { mutableStateOf<Map<String, Any?>?>(null) }
    var editPhone by remember { mutableStateOf(false) }
    val uid = Account.user?.id
    suspend fun load() {
        if (uid == null || Account.offline) return
        try {
            data = mapOf(
                "stats" to (Account.rpc("my_enhancement_stats", jsonOf("p_days" to 30)) as? JSONObject ?: JSONObject()),
                "recent" to Account.select("enhancements", "select=id,file_name,mode,scale,input_width,input_height,output_width," +
                    "output_height,status,processing_seconds,created_at&order=created_at.desc&limit=12"),
                "subs" to Account.select("subscriptions", "select=id,plan_id,status,expires_at,plans(name)&order=created_at.desc&limit=5"),
                "profile" to Account.select("profiles", "select=email,phone_e164,phone_country,created_at&id=eq.$uid").firstOrNull(),
            )
        } catch (e: Exception) {
            toast(ctx, "Dashboard: ${e.message}")
        }
    }
    LaunchedEffect(Account.liveTick, uid) { load() }
    Page {
        Text("Hi, ${Account.firstName().ifEmpty { "there" }}", fontSize = 22.sp, fontWeight = FontWeight.Bold)
        val d = data
        @Suppress("UNCHECKED_CAST") val subs = (d?.get("subs") as? List<JSONObject>).orEmpty()
        val active = subs.firstOrNull { isActive(it) }
        val q = Account.quota
        val limit = q?.limit ?: Account.limits.user
        Chips {
            Badge(active?.optJSONObject("plans")?.optString("name") ?: Account.plan?.name ?: "Free account")
            if (limit == null) Badge("Unlimited daily enhancements")
            Badge(if (Account.realtimeState == "subscribed" && !Account.offline) "● Live" else "Offline")
        }
        val stats = d?.get("stats") as? JSONObject ?: JSONObject()
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Tile("Today", stats.optInt("today"), if (limit == null) "No daily limit" else "${q?.remaining ?: limit} of $limit left", Modifier.weight(1f))
            Tile("This week", stats.optInt("week"), "Since Monday", Modifier.weight(1f))
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Tile("This month", stats.optInt("month"), java.time.LocalDate.now().month.name.lowercase().replaceFirstChar { it.uppercase() }, Modifier.weight(1f))
            Tile("All time", stats.optInt("total"), "Enhancements", Modifier.weight(1f))
        }
        val daily = stats.optJSONArray("daily").objects()
        Section("Per day", "Last 30 days" + (daily.maxOfOrNull { it.optInt("count") }?.takeIf { it > 0 }?.let { " · best day $it" } ?: "")) {
            if (stats.optInt("total") == 0) Hint("No enhancements yet - your daily numbers show here.")
            else Bars(daily.map { it.optInt("count") })
        }
        Section("Your plan") {
            Text(active?.optJSONObject("plans")?.optString("name") ?: "Free account", fontWeight = FontWeight.SemiBold)
            val until = active?.optString("expires_at")?.let { parseDt(it) }?.let { " Active until ${it.format(DateTimeFormatter.ofPattern("dd/MM/yyyy"))}." } ?: ""
            Hint((if (limit == null) "Unlimited enhancements every day." else "$limit enhancements per day.") + until)
            val pending = subs.firstOrNull { it.optString("status") == "pending" }
            if (pending != null) {
                Hint("${pending.optJSONObject("plans")?.optString("name") ?: pending.optString("plan_id")} requested - waiting for approval")
                TextButton({
                    scope.launch {
                        try {
                            Account.rpc("cancel_my_subscription_request", jsonOf("p_id" to pending.optString("id")))
                            toast(ctx, "Request withdrawn")
                            load()
                        } catch (e: Exception) {
                            toast(ctx, "Could not cancel the request")
                        }
                    }
                }) { Text("Withdraw request") }
            }
            OutlinedButton({ Nav.go(Nav.Screen.PLANS) }) { Text(if (active != null) "Change" else "View plans") }
        }
        val prof = d?.get("profile") as? JSONObject
        Section("Account") {
            Hint("Email: ${prof?.optString("email")?.takeIf { it.isNotEmpty() } ?: Account.user?.email ?: ""}")
            Row { Hint("Mobile: ${prof?.optString("phone_e164")?.takeIf { it.isNotEmpty() && it != "null" } ?: "—"}  "); TextButton({ editPhone = true }) { Text("Change") } }
            parseDt(prof?.optString("created_at"))?.let { Hint("Member since ${it.format(DateTimeFormatter.ofPattern("MMMM yyyy"))}") }
        }
        @Suppress("UNCHECKED_CAST") val recent = (d?.get("recent") as? List<JSONObject>).orEmpty()
        Section("History", "Your latest ${recent.size} photos") {
            if (recent.isEmpty()) Hint("Nothing yet.")
            for (e in recent) {
                Column(Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
                    Text(e.optString("file_name").ifEmpty { "Photo" }, fontWeight = FontWeight.Medium, fontSize = 14.sp, maxLines = 1)
                    var size = if (e.optInt("input_width") > 0) "${e.optInt("input_width")}×${e.optInt("input_height")}" else "-"
                    if (e.optInt("output_width") > 0) size += " → ${e.optInt("output_width")}×${e.optInt("output_height")}"
                    Hint("${MODE_LABEL[e.optString("mode")] ?: "-"} · ${e.optInt("scale")}× · $size · ${e.optString("status")} · " +
                        (parseDt(e.optString("created_at"))?.format(DateTimeFormatter.ofPattern("dd/MM HH:mm")) ?: ""))
                }
                HorizontalDivider()
            }
        }
    }
    if (editPhone) {
        val prof = data?.get("profile") as? JSONObject
        AlertDialog(onDismissRequest = { editPhone = false }, confirmButton = { TextButton({ editPhone = false }) { Text("Cancel") } },
            title = { Text("Change mobile number") }, text = {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    PhoneForm(prof?.optString("phone_country"), prof?.optString("phone_e164"), submitLabel = "Save") {
                        toast(ctx, "Mobile number updated")
                        editPhone = false
                        scope.launch { load() }
                    }
                }
            })
    }
}

@Composable
private fun Badge(text: String) {
    Text(text, Modifier.background(Brand.ACCENT_SOFT, RoundedCornerShape(50)).padding(horizontal = 10.dp, vertical = 4.dp),
        color = Brand.ACCENT_DARK, fontSize = 12.sp, fontWeight = FontWeight.Medium)
}

@Composable
private fun Tile(title: String, value: Int, hint: String, modifier: Modifier) {
    Column(modifier.background(Brand.SURFACE, RoundedCornerShape(12.dp)).border(1.dp, Brand.BORDER, RoundedCornerShape(12.dp)).padding(12.dp)) {
        Text(title, color = Brand.TEXT_2, fontSize = 12.sp)
        Text("%,d".format(value), fontSize = 24.sp, fontWeight = FontWeight.Bold)
        Text(hint, color = Brand.MUTED, fontSize = 11.sp)
    }
}

@Composable
private fun Bars(values: List<Int>) {
    val top = (values.maxOrNull() ?: 0).coerceAtLeast(1)
    Canvas(Modifier.fillMaxWidth().height(140.dp)) {
        val n = values.size.coerceAtLeast(1)
        val slot = size.width / n
        values.forEachIndexed { i, v ->
            val h = size.height * v / top
            drawRoundRect(Brand.ACCENT, Offset(i * slot + slot * 0.15f, size.height - h), Size(slot * 0.7f, h), CornerRadius(3f, 3f))
        }
    }
}

private val INTERVAL = mapOf("month" to "/ month", "year" to "/ year", "lifetime" to "one-time")
private val SYMBOLS = mapOf("USD" to "$", "EUR" to "€", "GBP" to "£", "BDT" to "৳", "INR" to "₹", "JPY" to "¥", "AUD" to "A$", "CAD" to "CA$")

private fun price(cents: Int, currency: String): String {
    val amount = cents / 100.0
    val text = if (cents % 100 != 0) "%,.2f".format(amount) else "%,.0f".format(amount)
    return SYMBOLS[currency.uppercase()]?.let { "$it$text" } ?: "%.2f %s".format(amount, currency)
}

@Composable
fun PlansScreen() {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    var plans by remember { mutableStateOf<List<JSONObject>>(emptyList()) }
    var subs by remember { mutableStateOf<List<JSONObject>>(emptyList()) }
    var request by remember { mutableStateOf<JSONObject?>(null) }
    suspend fun load() {
        if (Account.user == null || Account.offline) return
        runCatching {
            plans = Account.select("plans", "select=id,name,description,price_cents,currency,billing_interval,daily_limit,features&is_active=eq.true&order=sort_order")
            subs = Account.select("subscriptions", "select=plan_id,status,expires_at&status=in.(active,pending)")
        }
    }
    LaunchedEffect(Account.liveTick) { load() }
    val userLimit = Account.limits.user
    val activePlan = subs.firstOrNull { isActive(it) }?.optString("plan_id")
    val pendingPlan = subs.firstOrNull { it.optString("status") == "pending" }?.optString("plan_id")
    Page {
        Text("Simple pricing", fontSize = 26.sp, fontWeight = FontWeight.Bold)
        Hint("Every Boostimise account is free with Google sign-in. Enhance " +
            if (userLimit == null) "without a daily limit." else "more every day with a paid plan.")
        Section("Free account", "Sign in with Google") {
            Text("Free", fontSize = 26.sp, fontWeight = FontWeight.Bold)
            for (f in listOf(if (userLimit == null) "Unlimited photos every day" else "$userLimit photos per day", "AI Upscale 2× / 4×",
                "Face & photo restoration", "Daily, weekly & monthly stats", "Enhancement history")) Hint("✓ $f")
            OutlinedButton({}, enabled = false) { Text(if (activePlan != null) "Included" else "Current plan") }
        }
        for (p in plans) {
            val pr = price(p.optInt("price_cents"), p.optString("currency", "USD"))
            Section(p.optString("name"), p.optString("description").takeIf { it.isNotEmpty() && it != "null" }) {
                Row { Text(pr, fontSize = 26.sp, fontWeight = FontWeight.Bold); Text("  " + (INTERVAL[p.optString("billing_interval")] ?: ""), color = Brand.MUTED) }
                Hint("✓ " + if (p.isNull("daily_limit")) "Unlimited photos every day" else "${p.optInt("daily_limit")} photos per day")
                p.optJSONArray("features")?.let { a -> for (i in 0 until a.length()) Hint("✓ ${a.optString(i)}") }
                val id = p.optString("id")
                when {
                    activePlan == id -> OutlinedButton({}, enabled = false) { Text("Current plan") }
                    pendingPlan == id -> OutlinedButton({}, enabled = false) { Text("Requested - awaiting approval") }
                    else -> Button({ request = p }, enabled = pendingPlan == null, modifier = Modifier.fillMaxWidth()) { Text("Get ${p.optString("name")}") }
                }
            }
        }
        Hint("Daily enhancements reset every day at midnight (${Account.limits.timezone})." +
            if (plans.isNotEmpty()) " Paid plans are activated by our team once your payment is confirmed." else "")
    }
    request?.let { p ->
        var note by remember { mutableStateOf("") }
        var busy by remember { mutableStateOf(false) }
        AlertDialog(onDismissRequest = { request = null },
            title = { Text("Request ${p.optString("name")}") },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Hint("${price(p.optInt("price_cents"), p.optString("currency", "USD"))} ${INTERVAL[p.optString("billing_interval")] ?: ""}. " +
                        "Our team activates your plan as soon as your payment is confirmed - you'll see it on your dashboard.")
                    OutlinedTextField(note, { note = it.take(500) }, label = { Text("Payment reference or message (optional)") },
                        placeholder = { Text("e.g. transaction ID") }, modifier = Modifier.fillMaxWidth())
                }
            },
            confirmButton = {
                TextButton(enabled = !busy, onClick = {
                    busy = true
                    scope.launch {
                        try {
                            Account.rpc("request_my_subscription", jsonOf("p_plan_id" to p.optString("id"), "p_note" to note.trim().ifEmpty { null }))
                            toast(ctx, "Request sent - we'll activate your plan once payment is confirmed.")
                            request = null
                            load()
                        } catch (e: Exception) {
                            toast(ctx, Supabase.dbMessage(e, "Could not send your request"))
                        } finally {
                            busy = false
                        }
                    }
                }) { Text("Send request") }
            },
            dismissButton = { TextButton({ request = null }) { Text("Cancel") } })
    }
}
