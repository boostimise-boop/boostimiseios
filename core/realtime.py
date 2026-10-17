"""Supabase Realtime: live "a row changed" events over one WebSocket (Phoenix channels protocol,
JSON v1), the same subscriptions the website's AuthProvider makes. Runs on the UI thread's event
loop (QWebSocket); reconnects by itself and keeps its access token current.
"""
from __future__ import annotations

import json
import logging
import urllib.parse

from PySide6.QtCore import QByteArray, QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import QAbstractSocket, QNetworkRequest
from PySide6.QtWebSockets import QWebSocket

from .config import SUPABASE_KEY, SUPABASE_URL
from .net import USER_AGENT

log = logging.getLogger("allinone.realtime")

HEARTBEAT_MS = 25_000
HEARTBEAT_TIMEOUT_MS = 12_000
BACKOFF_MS = (1_000, 2_000, 5_000, 10_000, 30_000)


def socket_url() -> str:
    base = SUPABASE_URL.replace("https://", "wss://").replace("http://", "ws://")
    return f"{base}/realtime/v1/websocket?" + urllib.parse.urlencode({"apikey": SUPABASE_KEY, "vsn": "1.0.0"})


class RealtimeChannel(QObject):
    """One channel with postgres_changes subscriptions, e.g.
    {"event": "*", "schema": "public", "table": "profiles", "filter": "id=eq.<uid>"}."""

    changed = Signal(str)   # table name of a change
    state = Signal(str)     # connecting | subscribed | error | closed

    def __init__(self, name: str, changes: list[dict], parent: QObject | None = None):
        super().__init__(parent)
        self.topic = f"realtime:{name}"
        self.changes = changes
        self.token: str | None = None
        self.subscribed = False
        self._stopped = True
        self._ref = 0
        self._join_ref: str | None = None
        self._pending_heartbeat: str | None = None
        self._attempt = 0

        self.ws = QWebSocket(parent=self)
        self.ws.connected.connect(self._on_connected)
        self.ws.disconnected.connect(self._on_disconnected)
        self.ws.textMessageReceived.connect(self._on_message)
        self.ws.errorOccurred.connect(self._on_error)

        self.heartbeat = QTimer(self, interval=HEARTBEAT_MS, timeout=self._send_heartbeat)
        self.heartbeat_timeout = QTimer(self, singleShot=True, interval=HEARTBEAT_TIMEOUT_MS,
                                        timeout=self._on_heartbeat_timeout)
        self.reconnect_timer = QTimer(self, singleShot=True, timeout=self._open)

    # ---- public --------------------------------------------------------------------------
    def start(self, token: str | None):
        self.token = token
        self._stopped = False
        self._attempt = 0
        self._open()

    def stop(self):
        self._stopped = True
        self.reconnect_timer.stop()
        self.heartbeat.stop()
        self.heartbeat_timeout.stop()
        if self.subscribed:
            self._push(self.topic, "phx_leave", {})
        self.subscribed = False
        self.ws.abort()
        self.state.emit("closed")

    def set_token(self, token: str | None):
        """New access token after a session refresh: Realtime keeps checking RLS with it."""
        self.token = token
        if self.subscribed and token:
            self._push(self.topic, "access_token", {"access_token": token})

    # ---- socket --------------------------------------------------------------------------
    def _open(self):
        if self._stopped:
            return
        self.state.emit("connecting")
        req = QNetworkRequest(QUrl(socket_url()))
        req.setRawHeader(QByteArray(b"User-Agent"), QByteArray(USER_AGENT.encode()))
        self.ws.blockSignals(True)  # dropping an old connection is not a disconnect to react to
        self.ws.abort()
        self.ws.blockSignals(False)
        self._pending_heartbeat = None
        self.ws.open(req)

    def _schedule_reconnect(self):
        if self._stopped or self.reconnect_timer.isActive():
            return
        delay = BACKOFF_MS[min(self._attempt, len(BACKOFF_MS) - 1)]
        self._attempt += 1
        self.reconnect_timer.start(delay)

    def _next_ref(self) -> str:
        self._ref += 1
        return str(self._ref)

    def _push(self, topic: str, event: str, payload: dict, join: bool = False) -> str:
        ref = self._next_ref()
        msg = {"topic": topic, "event": event, "payload": payload, "ref": ref}
        if join:
            self._join_ref = ref
        if self._join_ref and topic == self.topic:
            msg["join_ref"] = self._join_ref
        if self.ws.state() == QAbstractSocket.ConnectedState:
            self.ws.sendTextMessage(json.dumps(msg))
        return ref

    def _on_connected(self):
        self.reconnect_timer.stop()
        self.heartbeat.start()
        payload = {"config": {"broadcast": {"ack": False, "self": False},
                              "presence": {"key": "", "enabled": False},
                              "postgres_changes": self.changes, "private": False}}
        if self.token:
            payload["access_token"] = self.token
        self._push(self.topic, "phx_join", payload, join=True)

    def _on_disconnected(self):
        was = self.subscribed
        self.subscribed = False
        self.heartbeat.stop()
        self.heartbeat_timeout.stop()
        if not self._stopped:
            if was:
                log.info("Realtime disconnected, reconnecting")
            self.state.emit("error")
            self._schedule_reconnect()

    def _on_error(self, _err):
        log.info("Realtime socket error: %s", self.ws.errorString())

    def _send_heartbeat(self):
        if self._pending_heartbeat is not None:
            return
        self._pending_heartbeat = self._push("phoenix", "heartbeat", {})
        self.heartbeat_timeout.start()

    def _on_heartbeat_timeout(self):
        log.info("Realtime heartbeat timed out, reconnecting")
        self._pending_heartbeat = None
        self.ws.abort()  # -> disconnected -> reconnect

    def _on_message(self, text: str):
        try:
            msg = json.loads(text)
        except ValueError:
            return
        topic, event, payload = msg.get("topic"), msg.get("event"), msg.get("payload") or {}
        if topic == "phoenix" and event == "phx_reply":
            if msg.get("ref") == self._pending_heartbeat:
                self._pending_heartbeat = None
                self.heartbeat_timeout.stop()
            return
        if topic != self.topic:
            return
        if event == "phx_reply" and msg.get("ref") == self._join_ref:
            if payload.get("status") == "ok":
                self.subscribed = True
                self._attempt = 0
                self.state.emit("subscribed")
            else:
                log.warning("Realtime join refused: %s", payload.get("response"))
                self.state.emit("error")
                self.ws.abort()
        elif event == "postgres_changes":
            data = payload.get("data") or {}
            self.changed.emit(str(data.get("table") or ""))
        elif event in ("phx_error", "phx_close"):
            self.subscribed = False
            if not self._stopped:
                self.ws.abort()
        elif event == "system" and payload.get("status") == "error":
            log.warning("Realtime: %s", payload.get("message"))
