package com.covercanvasbd.boostimise.core

import android.net.Uri
import android.text.Html
import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlin.concurrent.thread

/**
 * The browser's way back after Google sign-in (core/account.py's _CallbackServer): a tiny web
 * server on 127.0.0.1 that only runs while signing in. Chrome on this phone opens
 * http://127.0.0.1:53214/auth/callback?code=..., the app takes the code and the page says how it
 * ended, with a button back to the app.
 */
class AuthCallbackServer(private val onResult: (Map<String, String>) -> Unit) {
    private val server = ServerSocket(Config.AUTH_PORT, 8, InetAddress.getByName("127.0.0.1"))
    @Volatile private var delivered = false
    private val done = CountDownLatch(1)
    @Volatile private var outcome: Pair<Boolean, String> = false to "Sign-in was cancelled."

    fun start() {
        thread(name = "auth-callback", isDaemon = true) {
            while (!server.isClosed) {
                val s = try {
                    server.accept()
                } catch (e: Exception) {
                    break
                }
                thread(isDaemon = true) { handle(s) }
            }
        }
    }

    fun stop() {
        runCatching { server.close() }
    }

    fun finish(ok: Boolean, message: String = "") {
        outcome = ok to message
        done.countDown()
    }

    private fun handle(s: Socket) {
        s.use { sock ->
            sock.soTimeout = 15_000
            val reader = BufferedReader(InputStreamReader(sock.getInputStream()))
            val first = reader.readLine() ?: return
            while (true) {
                val line = reader.readLine() ?: break
                if (line.isEmpty()) break
            }
            val path = first.split(" ").getOrNull(1) ?: "/"
            val uri = Uri.parse("http://127.0.0.1$path")
            if (uri.path != "/auth/callback") {
                respond(sock, 404, "Not found")
                return
            }
            val qs = uri.queryParameterNames.associateWith { uri.getQueryParameter(it).orEmpty() }
            val error = qs["error_description"] ?: qs["error"]
            when {
                qs["code"].isNullOrEmpty() && qs["refresh_token"].isNullOrEmpty() && error == null ->
                    page(sock, "Signing you in…", "One moment…", "", HASH_SCRIPT)
                error != null -> {
                    deliver(qs)
                    page(sock, "Sign-in failed", "${esc(error)}<br><br>Go back to Boostimise and try again.", ERR_ICON, BACK)
                }
                else -> {
                    val (ok, message) = deliver(qs, wait = true)
                    if (ok) page(sock, "You're signed in", "Go back to Boostimise to continue.", OK_ICON,
                        BACK + "<script>setTimeout(function(){location.href='boostimise://signed-in'},600)</script>")
                    else page(sock, "Sign-in failed", "${esc(message)}<br><br>Go back to Boostimise and try again.", ERR_ICON, BACK)
                }
            }
        }
    }

    private fun deliver(qs: Map<String, String>, wait: Boolean = false): Pair<Boolean, String> {
        synchronized(this) {
            if (delivered) return false to "This sign-in link was already used."
            delivered = true
        }
        onResult(qs)
        if (wait && !done.await(30, TimeUnit.SECONDS)) return true to ""
        return outcome
    }

    private fun esc(s: String) = Html.escapeHtml(s)

    private fun page(sock: Socket, title: String, text: String, icon: String, extra: String = "") {
        val body = PAGE.replace("{title}", title).replace("{text}", text).replace("{icon}", icon).replace("{script}", extra)
        respond(sock, 200, body, "text/html; charset=utf-8")
    }

    private fun respond(sock: Socket, status: Int, body: String, type: String = "text/plain; charset=utf-8") {
        val bytes = body.toByteArray()
        val head = "HTTP/1.1 $status ${if (status == 200) "OK" else "Not Found"}\r\nContent-Type: $type\r\n" +
            "Content-Length: ${bytes.size}\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n"
        sock.getOutputStream().apply {
            write(head.toByteArray())
            write(bytes)
            flush()
        }
    }

    companion object {
        private const val PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{title} - Boostimise</title>
<meta name="viewport" content="width=device-width, initial-scale=1"><style>
body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;font-family:system-ui,sans-serif;
background:#fff;color:#0b1730;background-image:radial-gradient(60% 50% at 50% 0%,rgba(33,123,244,.16),transparent 70%)}
main{text-align:center;padding:24px;max-width:360px}h1{font-size:20px;font-weight:600;margin:16px 0 8px}
p{font-size:14px;color:#62748e;line-height:1.5;margin:0}.b{font-weight:700}.b span{color:#0062e9}
a.btn{display:inline-block;margin-top:24px;background:#0a6cf0;color:#fff;text-decoration:none;padding:12px 22px;border-radius:10px;font-weight:600}</style></head>
<body><main>{icon}<h1>{title}</h1><p>{text}</p>{script}<p style="margin-top:24px" class="b">Boost<span>imise</span></p></main></body></html>"""
        private const val OK_ICON = """<svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="#00bc7d" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="m9 12 2 2 4-4"/></svg>"""
        private const val ERR_ICON = """<svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="#e7000b" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 8v4"/><path d="M12 16h.01"/></svg>"""
        private const val BACK = """<a class="btn" href="boostimise://signed-in">Back to Boostimise</a>"""
        // Supabase normally answers with ?code= (PKCE); tokens in the #fragment are handed over as a query.
        private const val HASH_SCRIPT = "<script>if(location.hash.length>1&&!location.search){location.replace(location.pathname+'?'+location.hash.slice(1))}</script>"
    }
}
