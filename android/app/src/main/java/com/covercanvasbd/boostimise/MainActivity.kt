package com.covercanvasbd.boostimise

import android.Manifest
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.IntentCompat
import com.covercanvasbd.boostimise.core.AppGuard
import com.covercanvasbd.boostimise.ui.BoostimiseApp
import com.covercanvasbd.boostimise.ui.Nav

class MainActivity : ComponentActivity() {
    private val notifications = registerForActivityResult(ActivityResultContracts.RequestPermission()) {}

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (Build.VERSION.SDK_INT >= 33) notifications.launch(Manifest.permission.POST_NOTIFICATIONS)
        handle(intent)
        setContent { BoostimiseApp(this) }
    }

    override fun onResume() {
        super.onResume()
        AppGuard.onForeground()  // checks again when the last check is older than a minute
    }

    override fun onPause() {
        super.onPause()
        AppGuard.onBackground()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handle(intent)
    }

    /** Photos shared from WhatsApp / the gallery go to the Photo Enhancer. */
    private fun handle(intent: Intent?) {
        intent ?: return
        val uris: List<Uri> = when (intent.action) {
            Intent.ACTION_SEND -> listOfNotNull(IntentCompat.getParcelableExtra(intent, Intent.EXTRA_STREAM, Uri::class.java))
            Intent.ACTION_SEND_MULTIPLE ->
                IntentCompat.getParcelableArrayListExtra(intent, Intent.EXTRA_STREAM, Uri::class.java).orEmpty()
            else -> emptyList()
        }
        if (uris.isNotEmpty()) {
            Tools.enhancer.add(uris)
            Nav.go(Nav.Screen.ENHANCER)
        }
    }
}
