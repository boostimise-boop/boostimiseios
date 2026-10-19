package com.covercanvasbd.boostimise.wa

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import androidx.compose.runtime.mutableStateMapOf
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import com.covercanvasbd.boostimise.MainActivity
import com.covercanvasbd.boostimise.R

/**
 * Which tools are working right now (a green dot on their button, like the Windows app's sidebar),
 * and a foreground service with a notification while any of them is, so Android keeps the app
 * (and its WhatsApp Web) running when you switch to another app.
 */
object Jobs {
    val busy = mutableStateMapOf<String, String>()   // tool key -> what it is doing
    private lateinit var app: Context

    fun init(context: Context) {
        app = context.applicationContext
    }

    fun begin(tool: String, what: String) {
        busy[tool] = what
        update()
    }

    fun progress(tool: String, what: String) {
        if (tool in busy) {
            busy[tool] = what
            update()
        }
    }

    fun end(tool: String) {
        busy.remove(tool)
        update()
    }

    private var serviceOn = false

    private fun update() {
        val intent = Intent(app, JobService::class.java)
        if (busy.isEmpty()) {
            // Not stopService(): a job that ends at once would stop the service before it called
            // startForeground(), and Android kills the app for that. The service stops itself instead.
            if (!serviceOn) return
            serviceOn = false
            intent.putExtra("stop", true)
        } else {
            serviceOn = true
            intent.putExtra("text", busy.values.first())
        }
        runCatching { app.startForegroundService(intent) }
    }
}

class JobService : Service() {
    private var wake: PowerManager.WakeLock? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel(CHANNEL, "Tools at work", NotificationManager.IMPORTANCE_LOW))
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val n: Notification = NotificationCompat.Builder(this, CHANNEL)
            .setSmallIcon(R.drawable.ic_launcher_foreground)
            .setContentTitle("Boostimise is working")
            .setContentText(intent?.getStringExtra("text") ?: "A tool is running")
            .setContentIntent(open)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .build()
        ServiceCompat.startForeground(this, 1, n,
            if (Build.VERSION.SDK_INT >= 29) ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC else 0)
        if (intent?.getBooleanExtra("stop", false) == true) {
            ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
            stopSelf(startId)   // a tool that started again in the meantime keeps it running
            return START_NOT_STICKY
        }
        if (wake == null) {
            wake = getSystemService(PowerManager::class.java)
                .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "Boostimise:tools").apply { acquire(3 * 60 * 60 * 1000L) }
        }
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        wake?.let { if (it.isHeld) it.release() }
        wake = null
        super.onDestroy()
    }

    companion object {
        private const val CHANNEL = "jobs"
    }
}
