package com.boostimise.app.core

import android.os.Handler
import android.os.Looper
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONArray
import org.json.JSONObject

/**
 * Supabase Realtime (core/realtime.py): live "a row changed" events over one WebSocket (Phoenix
 * channels, JSON v1), the same subscriptions the website makes. Reconnects by itself; callbacks run
 * on the main thread.
 */
class RealtimeChannel(
    name: String,
    private val changes: List<JSONObject>,
    private val onChange: (String) -> Unit,
    private val onState: (String) -> Unit,
) {
    private val topic = "realtime:$name"
    private val main = Handler(Looper.getMainLooper())
    private var ws: WebSocket? = null
    private var token: String? = null
    private var stopped = true
    private var ref = 0
    private var joinRef: String? = null
    private var pendingHeartbeat: String? = null
    private var attempt = 0
    var subscribed = false
        private set

    private val heartbeat = object : Runnable {
        override fun run() {
            if (stopped) return
            if (pendingHeartbeat != null) {
                // no answer to the last one: the connection is dead
                pendingHeartbeat = null
                ws?.cancel()
                return
            }
            pendingHeartbeat = push("phoenix", "heartbeat", JSONObject())
            main.postDelayed(this, HEARTBEAT_MS)
        }
    }
    private val reconnect = Runnable { open() }

    fun start(token: String?) {
        this.token = token
        stopped = false
        attempt = 0
        open()
    }

    fun stop() {
        stopped = true
        main.removeCallbacks(reconnect)
        main.removeCallbacks(heartbeat)
        if (subscribed) push(topic, "phx_leave", JSONObject())
        subscribed = false
        ws?.close(1000, null)
        ws = null
        onState("closed")
    }

    fun setToken(token: String?) {
        this.token = token
        if (subscribed && token != null) push(topic, "access_token", jsonOf("access_token" to token))
    }

    private fun url(): String {
        val base = Config.SUPABASE_URL.replace("https://", "wss://")
        return "$base/realtime/v1/websocket?apikey=${Config.SUPABASE_KEY}&vsn=1.0.0"
    }

    private fun open() {
        if (stopped) return
        onState("connecting")
        ws?.cancel()
        pendingHeartbeat = null
        val req = Request.Builder().url(url()).header("User-Agent", Config.USER_AGENT).build()
        val listener = object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                main.post { if (webSocket === ws) onConnected() }
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                main.post { if (webSocket === ws) onMessage(text) }
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                main.post { if (webSocket === ws) onDisconnected() }
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                main.post { if (webSocket === ws) onDisconnected() }
            }
        }
        ws = Http.client.newWebSocket(req, listener)
    }

    private fun nextRef(): String = (++ref).toString()

    private fun push(topic: String, event: String, payload: JSONObject, join: Boolean = false): String {
        val r = nextRef()
        val msg = jsonOf("topic" to topic, "event" to event, "payload" to payload, "ref" to r)
        if (join) joinRef = r
        if (joinRef != null && topic == this.topic) msg.put("join_ref", joinRef)
        ws?.send(msg.toString())
        return r
    }

    private fun onConnected() {
        main.removeCallbacks(reconnect)
        main.removeCallbacks(heartbeat)
        main.postDelayed(heartbeat, HEARTBEAT_MS)
        val config = jsonOf(
            "broadcast" to jsonOf("ack" to false, "self" to false),
            "presence" to jsonOf("key" to "", "enabled" to false),
            "postgres_changes" to JSONArray(changes),
            "private" to false,
        )
        val payload = jsonOf("config" to config)
        token?.let { payload.put("access_token", it) }
        push(topic, "phx_join", payload, join = true)
    }

    private fun onDisconnected() {
        subscribed = false
        main.removeCallbacks(heartbeat)
        if (stopped) return
        onState("error")
        if (!main.hasCallbacks(reconnect)) {
            val delay = BACKOFF_MS[minOf(attempt, BACKOFF_MS.size - 1)]
            attempt++
            main.postDelayed(reconnect, delay)
        }
    }

    private fun onMessage(text: String) {
        val msg = Http.parseJson(text) as? JSONObject ?: return
        val t = msg.optString("topic")
        val event = msg.optString("event")
        val payload = msg.optJSONObject("payload") ?: JSONObject()
        if (t == "phoenix" && event == "phx_reply") {
            if (msg.optString("ref") == pendingHeartbeat) pendingHeartbeat = null
            return
        }
        if (t != topic) return
        when {
            event == "phx_reply" && msg.optString("ref") == joinRef -> {
                if (payload.optString("status") == "ok") {
                    subscribed = true
                    attempt = 0
                    onState("subscribed")
                } else {
                    onState("error")
                    ws?.cancel()
                }
            }
            event == "postgres_changes" -> onChange(payload.optJSONObject("data")?.optString("table").orEmpty())
            event == "phx_error" || event == "phx_close" -> {
                subscribed = false
                if (!stopped) ws?.cancel()
            }
        }
    }

    companion object {
        private const val HEARTBEAT_MS = 25_000L
        private val BACKOFF_MS = longArrayOf(1_000, 2_000, 5_000, 10_000, 30_000)
    }
}
