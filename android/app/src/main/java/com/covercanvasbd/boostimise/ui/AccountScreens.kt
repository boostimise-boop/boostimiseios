package com.covercanvasbd.boostimise.ui

import android.app.Activity
import android.content.Intent
import android.net.Uri
import androidx.browser.customtabs.CustomTabsIntent
import androidx.compose.foundation.Image
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.covercanvasbd.boostimise.R
import com.covercanvasbd.boostimise.core.Account
import com.covercanvasbd.boostimise.core.Config
import com.covercanvasbd.boostimise.core.Supabase
import com.covercanvasbd.boostimise.core.jsonOf
import com.google.i18n.phonenumbers.NumberParseException
import com.google.i18n.phonenumbers.PhoneNumberUtil
import kotlinx.coroutines.launch
import java.util.Locale

@Composable
private fun Centered(content: @Composable () -> Unit) {
    Box(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(24.dp), contentAlignment = Alignment.Center) {
        Column(Modifier.fillMaxWidth().padding(top = 48.dp), horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(16.dp)) {
            Image(painterResource(R.drawable.logo_full), "Boostimise", Modifier.width(200.dp))
            content()
        }
    }
}

@Composable
fun LoadingScreen() = Centered { CircularProgressIndicator() }

@Composable
fun SignInScreen(activity: Activity) {
    Centered {
        Section {
            Text("Sign in to Boostimise", fontSize = 20.sp, fontWeight = FontWeight.Bold, modifier = Modifier.fillMaxWidth(),
                textAlign = TextAlign.Center)
            Hint("Free account - no password to remember. The same account as on boostimise.com and the PC app.")
            if (Account.signedOutReason.isNotEmpty()) Hint(Account.signedOutReason, Brand.ERROR)
            if (Account.signInState == "error") Hint(Account.signInMessage, Brand.ERROR)
            when (Account.signInState) {
                "waiting" -> {
                    Hint("Choose your Google account in the browser, then come back here.")
                    OutlinedButton({ Account.cancelSignIn("") }, Modifier.fillMaxWidth()) { Text("Cancel") }
                }
                "working" -> Row(verticalAlignment = Alignment.CenterVertically) {
                    CircularProgressIndicator(Modifier.width(20.dp))
                    Text("  Signing you in…")
                }
                else -> Button({
                    Account.beginSignIn()?.let { url ->
                        runCatching { CustomTabsIntent.Builder().build().launchUrl(activity, Uri.parse(url)) }
                            .onFailure { activity.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url))) }
                    }
                }, Modifier.fillMaxWidth()) { Text("Continue with Google") }
            }
            val limits = Account.publicLimits
            Hint("Free account: ${limits.user?.let { "$it photo enhancements per day" } ?: "unlimited photo enhancements"}.")
            Row(horizontalArrangement = Arrangement.Center, modifier = Modifier.fillMaxWidth()) {
                TextButton({ activity.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(Config.TERMS_URL))) }) { Text("Terms") }
                TextButton({ activity.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(Config.PRIVACY_URL))) }) { Text("Privacy") }
            }
        }
    }
}

@Composable
fun OnboardingScreen() {
    Centered {
        Section("Add your mobile number", "Once, like on the website. We use it only for your account.") {
            PhoneForm(Account.status?.country, null, askName = Account.displayName().isNullOrBlank()) { Account.refresh() }
            TextButton({ Account.signOut() }) { Text("Sign out") }
        }
    }
}

@Composable
fun BlockedScreen() {
    Centered {
        Section("Account suspended", "Your Boostimise account has been suspended. Contact Cover Canvas BD if you think this is a mistake.") {
            Button({ Account.refresh() }) { Text("Check again") }
            TextButton({ Account.signOut() }) { Text("Sign out") }
        }
    }
}

private fun flag(cc: String) = if (cc.length == 2) cc.uppercase().map { Character.toChars(0x1F1E6 + (it - 'A')).concatToString() }
    .joinToString("") else "🌐"

/** The mobile number with its country code (core/account_widgets.py's PhoneForm). */
@Composable
fun PhoneForm(country0: String?, phone0: String?, askName: Boolean = false, submitLabel: String = "Continue", onSaved: (String) -> Unit) {
    val util = remember { PhoneNumberUtil.getInstance() }
    var country by remember { mutableStateOf((country0 ?: "BD").uppercase().takeIf { it in util.supportedRegions } ?: "BD") }
    var phone by remember { mutableStateOf(phone0?.let { p -> runCatching { util.getNationalSignificantNumber(util.parse(p, country)) }.getOrNull() } ?: "") }
    var name by remember { mutableStateOf("") }
    var error by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var picking by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    val dial = util.getCountryCodeForRegion(country)
    if (askName) OutlinedTextField(name, { name = it }, label = { Text("Your name") }, singleLine = true, modifier = Modifier.fillMaxWidth())
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
        OutlinedButton({ picking = true }) { Text("${flag(country)} +$dial") }
        OutlinedTextField(phone, { phone = it }, label = { Text("Mobile number") }, singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Phone), modifier = Modifier.weight(1f))
    }
    if (error.isNotEmpty()) Hint(error, Brand.ERROR)
    Button(enabled = !busy, onClick = {
        error = ""
        val p = try {
            util.parse(phone, country).takeIf { util.isValidNumber(it) }
        } catch (e: NumberParseException) {
            null
        }
        if (p == null) {
            error = "Enter a valid mobile number for the selected country."
            return@Button
        }
        val e164 = util.format(p, PhoneNumberUtil.PhoneNumberFormat.E164)
        busy = true
        scope.launch {
            try {
                val res = Account.rpc("save_my_profile", jsonOf(
                    "p_country" to (util.getRegionCodeForNumber(p) ?: country), "p_dial_code" to "+${p.countryCode}",
                    "p_national" to util.getNationalSignificantNumber(p), "p_e164" to e164,
                    "p_full_name" to if (askName) name else null))
                onSaved((res as? String) ?: e164)
            } catch (e: Exception) {
                error = Supabase.dbMessage(e, "Could not save your number. Please try again.")
            } finally {
                busy = false
            }
        }
    }, modifier = Modifier.fillMaxWidth()) { Text(if (busy) "Saving…" else submitLabel) }

    if (picking) {
        var q by remember { mutableStateOf("") }
        val all = remember {
            util.supportedRegions.map { it to Locale("", it).displayCountry }.sortedBy { it.second }
        }
        AlertDialog(onDismissRequest = { picking = false }, confirmButton = { TextButton({ picking = false }) { Text("Close") } },
            title = { Text("Country") }, text = {
                Column {
                    OutlinedTextField(q, { q = it }, label = { Text("Search") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                    LazyColumn(Modifier.heightIn(max = 380.dp)) {
                        items(all.filter { q.isBlank() || it.second.contains(q, true) || it.first.equals(q, true) }) { (cc, n) ->
                            Text("${flag(cc)}  $n  +${util.getCountryCodeForRegion(cc)}",
                                Modifier.fillMaxWidth().clickable { country = cc; picking = false }.padding(vertical = 10.dp))
                        }
                    }
                }
            })
    }
}
