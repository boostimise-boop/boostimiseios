"""The signed-in account: Google sign-in through Supabase Auth, the session, the account status
(quota, plan, profile) and live updates - the website's components/AuthProvider.tsx,
lib/auth-client.ts and lib/account.ts for the desktop.

Signing in: the browser opens Google's account picker (Supabase Auth, PKCE) and comes back to a
tiny web server on 127.0.0.1 (config.AUTH_REDIRECT) that only runs while signing in; the code it
receives is exchanged for a session. The session is kept in <data>/account/session.bin, encrypted
for this Windows user (DPAPI), and refreshed before it expires, so the app stays signed in.

Boostimise can only be used signed in (core/gate.py shows the sign-in, mobile-number and
"suspended" screens). When Supabase can't be reached the last known account is used, so a network
hiccup never locks anyone out of the tools ("soft" sign-in, like the website's quota check);
a session Supabase rejects (signed out elsewhere, account deleted) means signing in again.

Supabase Realtime: the same subscriptions as the website (app_settings, plans, and the user's own
profiles / subscriptions / enhancements rows); any change reloads the status and ticks ``live``.
"""
from __future__ import annotations

import base64
import ctypes
import html
import http.server
import json
import logging
import sys
import threading
import time
import urllib.parse
import uuid
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QImage

from . import config, net, supabase
from .config import AUTH_PORT, AUTH_REDIRECT
from .paths import data_dir
from .realtime import RealtimeChannel

log = logging.getLogger("allinone.account")

LOADING, SIGNED_OUT, ONBOARDING, BLOCKED, READY = "loading", "signed_out", "onboarding", "blocked", "ready"
REFRESH_MARGIN = 300        # refresh the session 5 minutes before it expires
SIGN_IN_TIMEOUT_MS = 10 * 60 * 1000


# ---- data ------------------------------------------------------------------------------------
@dataclass
class Quota:
    kind: str                  # "guest" | "user"
    limit: int | None          # None = unlimited
    used: int
    remaining: int | None      # None = unlimited
    resets_at: str


def to_quota(q: dict | None) -> Quota | None:
    if not q or not q.get("kind"):
        return None
    return Quota(q["kind"], q.get("limit"), int(q.get("used") or 0), q.get("remaining"), q.get("resets_at") or "")


@dataclass
class Status:
    quota: Quota | None
    limits: dict | None        # {"guest": 5, "user": None, "timezone": "UTC"}
    plan: dict | None          # {"id", "name", "expires_at"}
    profile: dict | None       # {"full_name", "avatar", "phone", "phone_country", "blocked", "onboarded"}
    country: str | None
    raw: dict


def to_status(data: dict | None) -> Status:
    data = data or {}
    p = data.get("profile")
    profile = None if not p else {
        "full_name": p.get("full_name"), "avatar": p.get("avatar_url"), "phone": p.get("phone"),
        "phone_country": p.get("phone_country"), "blocked": bool(p.get("blocked")), "onboarded": bool(p.get("onboarded")),
    }
    plan = data.get("plan")
    return Status(to_quota(data.get("quota")), data.get("limits"),
                  {"id": plan["id"], "name": plan["name"], "expires_at": plan.get("expires_at")} if plan else None,
                  profile, data.get("country"), data)


def user_from(u: dict | None) -> dict | None:
    if not u:
        return None
    meta = u.get("user_metadata") or {}
    return {"id": u["id"], "email": u.get("email"), "name": meta.get("full_name") or meta.get("name"),
            "avatar": meta.get("avatar_url") or meta.get("picture")}


def format_reset_in(iso: str) -> str:
    from datetime import datetime
    try:
        ms = (datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() - time.time()) * 1000
    except ValueError:
        return "soon"
    if ms <= 0:
        return "soon"
    h, m = int(ms // 3_600_000), int(ms % 3_600_000 // 60_000)
    return f"{h}h {m}m" if h > 0 else f"{m}m"


# ---- session file (DPAPI) --------------------------------------------------------------------
class _Blob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]


_ENTROPY = b"Boostimise desktop session v1"


def _dpapi(data: bytes, protect: bool) -> bytes:
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    buf = ctypes.create_string_buffer(data, len(data))
    ent = ctypes.create_string_buffer(_ENTROPY, len(_ENTROPY))
    blob_in = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_ent = _Blob(len(_ENTROPY), ctypes.cast(ent, ctypes.POINTER(ctypes.c_char)))
    out = _Blob()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    args = (ctypes.byref(blob_in), "Boostimise" if protect else None, ctypes.byref(blob_ent), None, None, 0x1,
            ctypes.byref(out))
    if not fn(*args):
        raise OSError("DPAPI failed")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


class SessionStore:
    def __init__(self):
        self.path = data_dir("account") / "session.bin"

    def load(self) -> dict | None:
        try:
            raw = self.path.read_bytes()
        except OSError:
            return None
        try:
            if raw.startswith(b"DPAPI1"):
                data = _dpapi(raw[6:], protect=False)
            elif raw.startswith(b"PLAIN1"):
                data = base64.b64decode(raw[6:])
            else:
                return None
            session = json.loads(data)
            return session if session.get("refresh_token") else None
        except Exception:  # noqa: BLE001 - another Windows user's file, or damaged: sign in again
            log.warning("Saved session could not be read; signing in again")
            return None

    def save(self, session: dict):
        data = json.dumps(session).encode()
        try:
            raw = b"DPAPI1" + _dpapi(data, protect=True) if sys.platform == "win32" else b"PLAIN1" + base64.b64encode(data)
        except OSError:
            raw = b"PLAIN1" + base64.b64encode(data)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_bytes(raw)
        tmp.replace(self.path)

    def clear(self):
        self.path.unlink(missing_ok=True)


def guest_id() -> str:
    """The id the website keeps per browser (bm_gid); the database functions still take one."""
    path = data_dir("account") / "guest_id"
    try:
        gid = path.read_text(encoding="utf-8").strip()
        uuid.UUID(gid)
        return gid
    except (OSError, ValueError):
        gid = str(uuid.uuid4())
        try:
            path.write_text(gid, encoding="utf-8")
        except OSError:
            pass
        return gid


# ---- the browser's way back ------------------------------------------------------------------
_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{title} - Boostimise</title>
<meta name="viewport" content="width=device-width, initial-scale=1"><style>
body{{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;font-family:"Segoe UI",system-ui,sans-serif;
background:#fff;color:#0b1730}}
main{{text-align:center;padding:24px;max-width:360px}}h1{{font-size:20px;font-weight:600;margin:16px 0 8px}}
p{{font-size:14px;color:#62748e;line-height:1.5;margin:0}}.b{{font-weight:700}}.b span{{color:#0062e9}}</style></head>
<body><main>{icon}<h1>{title}</h1><p>{text}</p><p style="margin-top:24px" class="b">Boost<span>imise</span></p></main>{script}</body></html>"""
_OK_ICON = ('<svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="#00bc7d" stroke-width="2" '
            'stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="m9 12 2 2 4-4"/></svg>')
_ERR_ICON = ('<svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="#e7000b" stroke-width="2" '
             'stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 8v4"/>'
             '<path d="M12 16h.01"/></svg>')
# Supabase normally answers with ?code= (PKCE); if it ever sends the tokens in the #fragment instead,
# the page hands them to the app as a query string (fragments never reach a server).
_HASH_SCRIPT = ("<script>if(location.hash.length>1&&!location.search){location.replace("
                "location.pathname+'?'+location.hash.slice(1))}</script>")


class _Handler(http.server.BaseHTTPRequestHandler):
    server: "_CallbackServer"

    def log_message(self, *_):  # quiet
        pass

    def do_GET(self):
        parts = urllib.parse.urlsplit(self.path)
        if parts.path != "/auth/callback":
            self.send_error(404)
            return
        qs = {k: v[0] for k, v in urllib.parse.parse_qs(parts.query).items()}
        error = qs.get("error_description") or qs.get("error")
        if not qs.get("code") and not qs.get("refresh_token") and not error:
            self._page("Signing you in…", "One moment…", "", _HASH_SCRIPT)
            return
        if error:
            self.server.deliver(qs)
            self._page("Sign-in failed", f"{html.escape(error)}<br><br>Go back to Boostimise and try again.", _ERR_ICON)
            return
        # answer once the app has turned the code into a session, so the page tells the truth
        ok, message = self.server.deliver(qs, wait=True)
        if ok:
            self._page("You're signed in", "You can close this window and continue in Boostimise.", _OK_ICON,
                       "<script>setTimeout(function(){window.close()},1500)</script>")
        else:
            self._page("Sign-in failed", f"{html.escape(message)}<br><br>Go back to Boostimise and try again.", _ERR_ICON)

    def _page(self, title, text, icon, script=""):
        body = _PAGE.format(title=title, text=text, icon=icon, script=script).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


class _CallbackServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, on_result):
        super().__init__(("127.0.0.1", AUTH_PORT), _Handler)
        self.on_result = on_result
        self._delivered = False
        self._done = threading.Event()
        self._outcome = (False, "Sign-in was cancelled.")

    def deliver(self, qs: dict, wait: bool = False) -> tuple[bool, str]:
        """Hands the redirect to the app; with ``wait``, returns how signing in ended."""
        if self._delivered:
            return False, "This sign-in link was already used."
        self._delivered = True
        self.on_result(qs)
        if wait and not self._done.wait(30):
            return True, ""  # still working: the app shows the result
        return self._outcome

    def finish(self, ok: bool, message: str = ""):
        self._outcome = (ok, message)
        self._done.set()

    def start(self):
        threading.Thread(target=self.serve_forever, name="auth-callback", daemon=True).start()

    def stop(self):
        threading.Thread(target=lambda: (self.shutdown(), self.server_close()), daemon=True).start()


# ---- the account ------------------------------------------------------------------------------
class Account(QObject):
    state_changed = Signal(str)       # LOADING | SIGNED_OUT | ONBOARDING | BLOCKED | READY
    changed = Signal()                # user / status / avatar changed
    live = Signal(int)                # a row changed in the database (Supabase Realtime)
    table_changed = Signal(str)       # ... in this table
    sign_in_state = Signal(str, str)  # "idle" | "waiting" | "working" | "error", message
    signed_in = Signal()
    signed_out = Signal(str)          # reason ("" = the user signed out)

    def __init__(self):
        super().__init__()
        self.state = LOADING
        self.store = SessionStore()
        self.session: dict | None = None
        self.user: dict | None = None
        self.status: Status | None = None
        self.configured = True
        self.offline = False
        self.public_limits = {"guest": 5, "user": None, "timezone": "UTC"}
        self.avatar_image: QImage | None = None
        self.live_tick = 0
        self.realtime_state = "closed"
        self._lock = threading.RLock()
        self._server: _CallbackServer | None = None
        self._verifier: str | None = None
        self._avatar_url: str | None = None
        self._gid = guest_id()
        self._channel: RealtimeChannel | None = None

        self._refresh_timer = QTimer(self, singleShot=True, timeout=self._timed_refresh)
        self._retry_timer = QTimer(self, singleShot=True, interval=30_000, timeout=self.refresh)
        self._reload_timer = QTimer(self, singleShot=True, interval=400, timeout=self.refresh)
        self._sign_in_timeout = QTimer(self, singleShot=True, interval=SIGN_IN_TIMEOUT_MS,
                                       timeout=lambda: self.cancel_sign_in("Sign-in timed out. Please try again."))

    # ---- me ------------------------------------------------------------------------------
    @property
    def guest_id(self) -> str:
        return self._gid

    @property
    def quota(self) -> Quota | None:
        return self.status.quota if self.status else None

    @property
    def limits(self) -> dict:
        return (self.status.limits if self.status and self.status.limits else None) or self.public_limits

    @property
    def profile(self) -> dict | None:
        return self.status.profile if self.status else None

    @property
    def plan(self) -> dict | None:
        return self.status.plan if self.status else None

    def display_user(self) -> dict | None:
        """The Google user with the profile's name / photo when it has them (like the website's `me`)."""
        if not self.user:
            return None
        u = dict(self.user)
        if self.profile:
            u["name"] = self.profile.get("full_name") or u["name"]
            u["avatar"] = self.profile.get("avatar") or u["avatar"]
        return u

    def first_name(self) -> str:
        u = self.display_user() or {}
        return (u.get("name") or "").split(" ")[0]

    # ---- startup -------------------------------------------------------------------------
    def start(self):
        net.run_bg(supabase.public_limits, self._on_public_limits, lambda e: None)
        saved = self.store.load()
        if not saved:
            self._set_state(SIGNED_OUT)
            return
        self.session = saved
        self.user = user_from(saved.get("user"))
        self._set_state(LOADING)
        net.run_bg(self._restore, self._on_restored, self._on_restore_failed)

    def _on_public_limits(self, limits: dict):
        self.public_limits = limits
        self.changed.emit()

    def _restore(self):
        """Startup: a fresh session from the saved refresh token, then the account status."""
        self._refresh_session(force=True)
        return self._fetch_status()

    def _on_restored(self, status: Status):
        self.offline = False
        self._apply_status(status)
        self._after_sign_in()

    def _on_restore_failed(self, e: BaseException):
        if isinstance(e, supabase.AuthRejected):
            self._end_session("Your session has ended. Please sign in again.")
            return
        # offline (or Supabase down): carry on with the account as it was last time
        log.warning("Account check failed, working offline: %s", e)
        self.offline = True
        cached = (self.session or {}).get("status")
        self._apply_status(to_status(cached) if cached else None)
        self._after_sign_in(start_realtime=False)
        self._retry_timer.start()

    # ---- sign in -------------------------------------------------------------------------
    def sign_in(self):
        """Opens Google's sign-in in the browser; the result comes back to the local callback."""
        self.cancel_sign_in(None)
        verifier, challenge = supabase.pkce_pair()
        try:
            self._server = _CallbackServer(lambda qs: net.ui(lambda: self._on_callback(qs)))
        except OSError as e:
            log.error("Sign-in callback port %s is not free: %s", AUTH_PORT, e)
            self.sign_in_state.emit("error", f"Another program is using port {AUTH_PORT}, which sign-in needs. "
                                              "Close it (or restart Windows) and try again.")
            return
        self._server.start()
        self._verifier = verifier
        self._sign_in_timeout.start()
        QDesktopServices.openUrl(QUrl(supabase.google_authorize_url(AUTH_REDIRECT, challenge)))
        self.sign_in_state.emit("waiting", "")

    def cancel_sign_in(self, message: str | None = None):
        self._sign_in_timeout.stop()
        if self._server is not None:
            self._server.stop()
            self._server = None
        self._verifier = None
        if message is not None:
            self.sign_in_state.emit("error" if message else "idle", message)

    def _on_callback(self, qs: dict):
        verifier = self._verifier
        server = self._server
        self._server = None  # keeps answering this one request, then stops
        self.cancel_sign_in(None)
        if server is not None:
            server.stop()
        error = qs.get("error_description") or qs.get("error")
        if error:
            self.sign_in_state.emit("error", error)
            return
        self._pending_server = server
        self.sign_in_state.emit("working", "")
        if qs.get("refresh_token"):  # implicit flow fallback: the tokens themselves
            session = {"access_token": qs.get("access_token"), "refresh_token": qs["refresh_token"],
                       "expires_in": int(qs.get("expires_in") or 3600)}
            work = lambda: self._adopt_and_fetch(session, need_user=True)  # noqa: E731
        else:
            work = lambda: self._adopt_and_fetch(supabase.exchange_code(qs.get("code", ""), verifier or ""))  # noqa: E731
        net.run_bg(work, self._on_signed_in, self._on_sign_in_failed)

    def _adopt_and_fetch(self, session: dict, need_user: bool = False) -> Status:
        if need_user:
            session["user"] = net.request("GET", f"{supabase.SUPABASE_URL}/auth/v1/user",
                                          headers=supabase.headers(session["access_token"])).json()
        with self._lock:
            self._store_session(session)
        return self._fetch_status()

    def _finish_page(self, ok: bool, message: str = ""):
        server, self._pending_server = getattr(self, "_pending_server", None), None
        if server is not None:
            server.finish(ok, message)

    def _on_signed_in(self, status: Status):
        self._finish_page(True)
        self.offline = False
        self.sign_in_state.emit("idle", "")
        self._apply_status(status)
        self._after_sign_in()
        self.signed_in.emit()

    def _on_sign_in_failed(self, e: BaseException):
        log.warning("Sign-in failed: %s", e)
        msg = e.message if isinstance(e, net.HttpError) and e.status else (
            "Could not reach Boostimise. Check your internet connection and try again."
            if isinstance(e, net.HttpError) else f"Could not sign you in ({e}). Please try again.")
        with self._lock:
            self.session = None
        self.store.clear()
        self._finish_page(False, msg)
        self.sign_in_state.emit("error", msg)

    def _after_sign_in(self, start_realtime: bool = True):
        self._schedule_refresh()
        self._load_avatar()
        if start_realtime:
            self._start_realtime()
        self.changed.emit()

    # ---- sign out ------------------------------------------------------------------------
    def sign_out(self):
        token = (self.session or {}).get("access_token")
        if token:
            net.run_bg(lambda: supabase.logout(token), None, lambda e: None)
        self._end_session("")

    def _end_session(self, reason: str):
        self._stop_realtime()
        self._refresh_timer.stop()
        self._retry_timer.stop()
        with self._lock:
            self.session = None
        self.store.clear()
        self.user = None
        self.status = None
        self.avatar_image = None
        self._avatar_url = None
        self.offline = False
        self._set_state(SIGNED_OUT)
        self.signed_out.emit(reason)
        self.changed.emit()
        net.run_bg(supabase.public_limits, self._on_public_limits, lambda e: None)

    # ---- session -------------------------------------------------------------------------
    def _store_session(self, s: dict):
        """Keeps a new session (call with the lock held)."""
        expires_in = int(s.get("expires_in") or 3600)
        user = s.get("user") or (self.session or {}).get("user")
        self.session = {"access_token": s["access_token"], "refresh_token": s["refresh_token"],
                        "expires_at": time.time() + expires_in, "user": user,
                        "status": (self.session or {}).get("status")}
        self.user = user_from(user)
        self.store.save(self.session)

    def _refresh_session(self, force: bool = False):
        """New access token when it runs out within a minute (or now); any thread."""
        with self._lock:
            s = self.session
            if not s:
                raise supabase.AuthRejected("Not signed in")
            if not force and s.get("expires_at", 0) - time.time() > 60:
                return
            self._store_session(supabase.refresh(s["refresh_token"]))
        net.ui(self._on_token_refreshed)

    def _on_token_refreshed(self):
        self._schedule_refresh()
        if self._channel is not None:
            self._channel.set_token(self.token_now())

    def token_now(self) -> str | None:
        with self._lock:
            return (self.session or {}).get("access_token")

    def token(self) -> str:
        """A valid access token (refreshing it first when needed). For worker threads."""
        self._refresh_session()
        tok = self.token_now()
        if not tok:
            raise supabase.AuthRejected("Not signed in")
        return tok

    def _schedule_refresh(self):
        s = self.session
        if not s:
            return
        delay = max(30.0, s.get("expires_at", 0) - time.time() - REFRESH_MARGIN)
        self._refresh_timer.start(int(min(delay, 3600) * 1000))

    def _timed_refresh(self):
        def failed(e):
            if isinstance(e, supabase.AuthRejected):
                self._end_session("Your session has ended. Please sign in again.")
            else:
                self._refresh_timer.start(30_000)
        net.run_bg(lambda: self._refresh_session(force=True), None, failed)

    # ---- status --------------------------------------------------------------------------
    def _fetch_status(self) -> Status:
        try:
            data = supabase.rpc("get_my_status", {"p_guest_id": self._gid}, self.token())
        except net.HttpError as e:
            if e.status in (401, 403) and "JWT" in (e.message or ""):
                self._refresh_session(force=True)
                data = supabase.rpc("get_my_status", {"p_guest_id": self._gid}, self.token())
            elif e.status and e.status not in (401, 403):
                # the database isn't set up (schema.sql not installed): use the app without it
                log.warning("account status unavailable (is supabase/schema.sql installed?): %s", e.message)
                st = to_status(None)
                st.raw = {"_unconfigured": True}
                return st
            else:
                raise
        return to_status(data)

    def refresh(self):
        """Reloads the status (quota, plan, profile) - also after Realtime changes."""
        if not self.session:
            return
        net.run_bg(self._fetch_status, self._on_status, self._on_status_failed)

    def _on_status(self, status: Status):
        if self.offline:
            self.offline = False
            self._start_realtime()
        self._apply_status(status)

    def _on_status_failed(self, e: BaseException):
        if isinstance(e, supabase.AuthRejected):
            self._end_session("Your session has ended. Please sign in again.")
        elif isinstance(e, net.HttpError) and e.status == 0:
            self.offline = True
            self._retry_timer.start()
            self.changed.emit()

    def _apply_status(self, status: Status | None):
        if not self.session:
            return
        self.configured = bool(status) and not status.raw.get("_unconfigured")
        self.status = status if self.configured else None
        if self.configured:
            with self._lock:
                if self.session:
                    self.session["status"] = status.raw
                    self.store.save(self.session)
        p = self.profile
        if p and p.get("blocked"):
            self._set_state(BLOCKED)
        elif p and not p.get("onboarded"):
            self._set_state(ONBOARDING)
        else:
            self._set_state(READY)  # signed in (or the account database isn't reachable / set up)
        self._load_avatar()
        self.changed.emit()

    def set_quota(self, q: Quota | None):
        """Update the quota shown without a round trip (after a reservation)."""
        if q is not None and self.status is not None:
            self.status.quota = q
            self.changed.emit()

    def _set_state(self, state: str):
        if state != self.state:
            self.state = state
            self.state_changed.emit(state)

    # ---- avatar --------------------------------------------------------------------------
    def _load_avatar(self):
        url = (self.display_user() or {}).get("avatar")
        if not url or url == self._avatar_url:
            return
        self._avatar_url = url

        def fetch():
            return QImage.fromData(net.request("GET", url, timeout=20).data)

        def done(img: QImage):
            if not img.isNull() and url == self._avatar_url:
                self.avatar_image = img
                self.changed.emit()
        net.run_bg(fetch, done, lambda e: None)

    # ---- realtime ------------------------------------------------------------------------
    def _start_realtime(self):
        self._stop_realtime()
        uid = (self.user or {}).get("id")
        if not uid or not self.configured:
            return
        changes = [{"event": "*", "schema": "public", "table": "app_settings"},
                   {"event": "*", "schema": "public", "table": "plans"},
                   {"event": "*", "schema": "public", "table": "profiles", "filter": f"id=eq.{uid}"},
                   {"event": "*", "schema": "public", "table": "subscriptions", "filter": f"user_id=eq.{uid}"},
                   {"event": "*", "schema": "public", "table": "enhancements", "filter": f"user_id=eq.{uid}"},
                   {"event": "*", "schema": "public", "table": "shop_profiles", "filter": f"user_id=eq.{uid}"}]
        ch = RealtimeChannel(f"account:{uid}", changes, self)
        ch.changed.connect(self._on_live_change)
        ch.state.connect(self._on_realtime_state)
        self._channel = ch
        ch.start(self.token_now())

    def _stop_realtime(self):
        if self._channel is not None:
            self._channel.stop()
            self._channel.deleteLater()
            self._channel = None

    def _on_realtime_state(self, state: str):
        self.realtime_state = state
        self.changed.emit()

    def _on_live_change(self, table: str):
        if table == "app_settings":  # the enhancement server may have moved (npm run set-api)
            net.run_bg(lambda: config.refresh_api_base(0), None, lambda e: None)
        self.table_changed.emit(table)
        self.live_tick += 1
        self.live.emit(self.live_tick)
        self._reload_timer.start()

    # ---- database functions (call from worker threads) -----------------------------------
    def rpc(self, fn: str, params: dict | None = None) -> Any:
        return supabase.rpc(fn, params, self.token())

    def select(self, table: str, query: str) -> list[dict]:
        return supabase.select(table, query, self.token())

    def reserve_enhancement(self, file_name: str, file_size: int, mode: str, scale: int,
                            width: int | None, height: int | None) -> dict:
        """Take one enhancement from today's quota. Network / database problems never block the
        user (the enhancement just isn't recorded); only an explicit "no" from the database does.
        Returns {"ok": True, "id", "quota"} or {"ok": False, "code", "message", "quota"}."""
        try:
            data = self.rpc("reserve_my_enhancement", {
                "p_guest_id": self._gid, "p_file_name": file_name[:255], "p_file_size": file_size,
                "p_mode": mode, "p_scale": scale, "p_input_width": width, "p_input_height": height,
                "p_device_type": "desktop"})
        except Exception as e:  # noqa: BLE001
            log.warning("quota check unavailable: %s", e)
            return {"ok": True, "id": None, "quota": None}
        data = data or {}
        if data.get("allowed"):
            return {"ok": True, "id": data.get("id"), "quota": to_quota(data)}
        reason = data.get("reason")
        code = reason if reason in ("blocked", "onboarding", "update_required") else "limit"
        message = ((data.get("message") or "Please update Boostimise.") if code == "update_required"
                   else "Your account has been suspended." if code == "blocked"
                   else "Add your mobile number to continue." if code == "onboarding"
                   else "You've reached today's limit for your plan." if data.get("kind") == "user"
                   else f"You've used all {data.get('limit') or ''} free enhancements for today.")
        return {"ok": False, "code": code, "message": message, "quota": to_quota(data)}

    def finish_enhancement(self, record_id: str, status: str | None = None, job_id: str | None = None,
                           output_width: int | None = None, output_height: int | None = None,
                           seconds: float | None = None, error: str | None = None):
        try:
            self.rpc("finish_my_enhancement", {
                "p_id": record_id, "p_guest_id": self._gid, "p_status": status, "p_job_id": job_id,
                "p_output_width": output_width, "p_output_height": output_height, "p_seconds": seconds,
                "p_error": error})
        except Exception as e:  # noqa: BLE001
            log.warning("could not record enhancement result: %s", e)


_account: Account | None = None


def get() -> Account:
    """The app's one account object (made on first use, on the UI thread)."""
    global _account
    if _account is None:
        _account = Account()
    return _account

