"""Background thread for Fetch by Date: connects to the shared WhatsApp window (core/whatsapp.py)
and runs the fetcher there. Playwright's sync API must be driven from the one thread that made it,
so every command (chats, job, quit) is queued to this thread."""
from __future__ import annotations

import queue
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from core import whatsapp

from .fetcher import WhatsAppFetcher

TOOL = "Fetch by Date"


class Stopped(Exception):
    pass


class FetchByDateWorker(QThread):
    log = Signal(str)
    state_changed = Signal(str, str)  # state, message: starting | loading | qr | ready | error | closed
    chats_loaded = Signal(list)  # chat titles
    counts_changed = Signal(dict)  # found, downloaded, skipped, failed
    activity = Signal(str)  # what is happening right now (status bar)
    job_finished = Signal(bool, str)  # success, summary

    def __init__(self, manifest_dir: Path):
        super().__init__()
        self.manifest_dir = manifest_dir
        self.fetcher: WhatsAppFetcher | None = None
        self._commands: queue.Queue = queue.Queue()
        self._stop_job = threading.Event()
        self._quit = threading.Event()
        self._failed = False
        self._last_chat: str | None = None

    # ---- called from the GUI thread -------------------------------------------------
    def submit_job(self, chat: str, from_dt: datetime, to_dt: datetime, folder: Path):
        self._stop_job.clear()
        self._commands.put(("job", chat, from_dt, to_dt, folder))

    def refresh_chats(self):
        self._commands.put(("chats",))

    def stop_job(self):
        self._stop_job.set()

    def shutdown(self):
        self._quit.set()
        self._stop_job.set()
        self._commands.put(("quit",))

    # ---- thread body ---------------------------------------------------------------
    def run(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self._fail("Playwright is not installed. Run:  pip install playwright")
            return
        try:
            with sync_playwright() as pw:  # leaving it only disconnects: the window stays for other tools
                self.state_changed.emit("starting", "Starting browser…")
                browser, ctx = whatsapp.connect(pw, self.log.emit)
                page = whatsapp.whatsapp_page(ctx)
                self.fetcher = WhatsAppFetcher(browser, page, self.manifest_dir, on_event=self._on_event)
                self.log.emit(f"Connected to the WhatsApp window ({whatsapp.WA_URL}).")
                try:
                    self._main_loop()
                finally:
                    self.fetcher.shutdown()
        except Exception as e:  # noqa: BLE001 - report anything to the GUI
            if not self._quit.is_set() and not self._is_closed_error(e):
                self.log.emit(traceback.format_exc())
                self._fail(self._friendly_error(e))
        finally:
            if not self._failed:
                self.state_changed.emit("closed", "Disconnected from WhatsApp")

    def _main_loop(self):
        if not self._wait_for_login():
            return
        self.state_changed.emit("ready", "Connected")
        self._send_chats()
        while not self._quit.is_set():
            try:
                cmd = self._commands.get_nowait()
            except queue.Empty:
                self.fetcher.page.wait_for_timeout(400)  # also keeps Playwright's event pipe drained
                continue
            if cmd[0] == "quit":
                break
            if cmd[0] == "chats":
                self._send_chats()
            elif cmd[0] == "job":
                self._run_job(*cmd[1:])

    def _wait_for_login(self) -> bool:
        last = None
        watch = whatsapp.LoadWatch(self.fetcher.page, self.log.emit)
        while not self._quit.is_set():
            if self.fetcher.is_logged_in():
                self.log.emit("Logged in to WhatsApp Web.")
                return True
            state = "qr" if self.fetcher.shows_qr() else "loading"
            watch.tick(state == "loading")
            if state != last:
                last = state
                if state == "qr":
                    self.state_changed.emit("qr", "Scan the QR code in the WhatsApp window "
                                                  "(phone: WhatsApp › Linked devices)")
                else:
                    self.state_changed.emit("loading", "Loading WhatsApp Web…")
            self.fetcher.page.wait_for_timeout(1000)
        return False

    # ---- jobs ------------------------------------------------------------------------
    def _check(self):
        if self._stop_job.is_set() or self._quit.is_set():
            raise Stopped

    def _turn(self):
        """Opening chats and the image viewer clicks and scrolls in the WhatsApp tab, which other
        tools share: wait while one of them is doing the same."""
        return whatsapp.turn(TOOL, self._check, lambda other: self.activity.emit(
            f"Waiting for {other} to finish with WhatsApp…"))

    def _send_chats(self):
        try:
            with self._turn():
                chats = self.fetcher.list_chats()
            self.chats_loaded.emit(chats)
        except Stopped:
            pass
        except Exception as e:  # noqa: BLE001
            if self._is_closed_error(e):
                raise
            self.log.emit(f"Could not read the chat list: {e}")

    def _run_job(self, chat: str, from_dt: datetime, to_dt: datetime, folder: Path):
        started = time.time()
        try:
            with self._turn():
                if chat != self._last_chat:
                    # A different chat (or the first one since the start): drop leftover copies.
                    self._last_chat = chat
                    removed = self.fetcher.clear_temp()
                    if removed:
                        self.log.emit(f"Removed {removed} temporary download(s) left from earlier runs.")
                self.fetcher.fetch_images(chat, from_dt, to_dt, folder, self._stop_job)
        except Stopped:
            self.job_finished.emit(False, "Stopped before WhatsApp was free.")
        except Exception as e:  # noqa: BLE001
            if self._is_closed_error(e):
                self.job_finished.emit(False, "WhatsApp was closed.")
                raise
            self.log.emit(traceback.format_exc())
            self.job_finished.emit(False, f"Error: {e}")
        else:
            self.log.emit(f"Took {int(time.time() - started)}s.")
        self.activity.emit("")

    def _on_event(self, event: str, payload: dict) -> None:
        if event == "status":
            self.activity.emit(payload.get("text", ""))
        elif event == "log":
            self.log.emit(payload.get("text", ""))
        elif event == "counts":
            self.counts_changed.emit(payload)
        elif event == "finished":
            self.job_finished.emit(payload.get("success", False), payload.get("message", ""))

    # ---- helpers ---------------------------------------------------------------------
    def _fail(self, message: str):
        self._failed = True
        self.state_changed.emit("error", message)
        self.log.emit(message)

    @staticmethod
    def _is_closed_error(e: Exception) -> bool:
        msg = str(e)
        return any(s in msg for s in ("Target page, context or browser has been closed",
                                      "Target closed", "Browser has been closed", "Connection closed"))

    @staticmethod
    def _friendly_error(e: Exception) -> str:
        if isinstance(e, whatsapp.BrowserError):
            return str(e)
        msg = str(e).strip().splitlines()[0] if str(e).strip() else repr(e)
        return f"Browser error: {msg}"
