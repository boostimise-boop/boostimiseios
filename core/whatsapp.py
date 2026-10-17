"""The one browser window that every tool uses for WhatsApp Web (and Pathao Auto-fill for Pathao).

WhatsApp Web works in one tab per login, so when each tool opened its own browser, each one needed
its own QR scan. Now the first tool that needs WhatsApp starts Microsoft Edge (Chrome if there is
no Edge) with the profile <data>/browser and a remote-debugging port, and every tool connects to
that window with Playwright's connect_over_cdp and works in its one WhatsApp tab. One QR scan logs
in every tool, and the profile keeps the login for the next start.

The window stays open when a tool (or Boostimise) closes, like a browser the user opened. If the user
closes it, the next tool that needs WhatsApp opens it again, still logged in.

`turn(tool)` gives the WhatsApp tab to one tool at a time for work that clicks and scrolls in it,
so two tools never scroll WhatsApp against each other: the second one waits.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path

from .paths import DATA_DIR

WA_URL = "https://web.whatsapp.com/"
PORT = 9333  # not Pathao's old 9222, so a window of the old per-tool profile is never mistaken for it
PROFILE = DATA_DIR / "browser"
FLAGS = [
    "--no-first-run", "--no-default-browser-check", "--start-maximized",
    # synced extensions (translate, screenshot tools...) stop WhatsApp Web's QR from loading
    "--disable-extensions",
    # the tools keep reading WhatsApp while its window is behind Boostimise
    "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
    "--disable-backgrounding-occluded-windows",
]

_start_lock = threading.Lock()
_tab_lock = threading.Lock()
_turn = threading.RLock()
_holder = {"tool": ""}


class BrowserError(RuntimeError):
    """The shared browser could not be started; the message says what to do."""


def find_browser() -> str | None:
    local = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.join(local, r"Google\Chrome\Application\chrome.exe") if local else "",
    ]
    return next((c for c in candidates if c and os.path.isfile(c)), None)


def _get(path: str, port: int | None = None):
    """The JSON answer of the browser's debugging port, or None when nothing listens there."""
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{port or PORT}{path}", timeout=1.5) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None


def is_running(port: int | None = None) -> bool:
    return _get("/json/version", port) is not None


def launch(port: int | None = None, profile: Path | None = None, extra_args=()) -> subprocess.Popen:
    """Starts Edge/Chrome listening on `port`, detached so it outlives Boostimise."""
    port, profile = port or PORT, profile or PROFILE
    exe = find_browser()
    if not exe:
        raise BrowserError("Microsoft Edge (or Google Chrome) was not found on this PC.")
    profile.mkdir(parents=True, exist_ok=True)
    flags = 0
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    return subprocess.Popen(
        [exe, f"--remote-debugging-port={port}", f"--user-data-dir={profile}", *FLAGS, *extra_args, "about:blank"],
        creationflags=flags)


def wait_running(port: int | None = None, timeout: float = 30) -> bool:
    end = time.time() + timeout
    while not is_running(port):
        if time.time() > end:
            return False
        time.sleep(0.3)
    return True


def _adopt_old_login():
    """First start of the shared window: Pathao Auto-fill's Edge profile becomes the shared one,
    so its WhatsApp and Pathao logins are kept. (It already worked like this, just for itself.)"""
    old = DATA_DIR / "pathao_autofill" / "browser_profile_edge"
    if PROFILE.exists() or not old.is_dir() or "msedge" not in (find_browser() or ""):
        return
    try:
        old.rename(PROFILE)
    except OSError:
        pass  # still open in the old auto-fill window: the shared window starts with a new login


def endpoint(log=print) -> str:
    """Address of the shared browser for connect_over_cdp. Starts the browser if it is not open."""
    with _start_lock:
        if not is_running():
            _adopt_old_login()
            log("Opening the WhatsApp browser window (shared by all tools)...")
            launch()
            if not wait_running():
                raise BrowserError("The WhatsApp browser window did not start. Close every Edge window that "
                                   "Boostimise opened, then try again.")
    return f"http://127.0.0.1:{PORT}"


def connect(pw, log=print):
    """Connects Playwright `pw` (a sync_playwright() of the calling thread) -> (browser, context).
    To let go of the window without closing it, stop `pw`."""
    browser = pw.chromium.connect_over_cdp(endpoint(log))
    ctx = browser.contexts[0] if browser.contexts else browser.new_context()
    return browser, ctx


def _is_wa(url: str | None) -> bool:
    return bool(url) and url.startswith(WA_URL)


def whatsapp_page(ctx):
    """The WhatsApp Web tab of the shared window; opens it when there is none. Never a second one:
    WhatsApp Web stops working in the first tab when another one opens."""
    with _tab_lock:
        end = time.time() + 10
        while True:
            page = next((p for p in ctx.pages if _is_wa(p.url)), None)
            if page is not None:
                return page
            # another tool may have just opened it: wait until this connection has taken it in
            others = _get("/json/list") or []
            if not any(t.get("type") == "page" and _is_wa(t.get("url")) for t in others) or time.time() > end:
                break
            ctx.cookies(WA_URL)  # a round trip, so Playwright handles the browser's news
            time.sleep(0.2)
        pages = ctx.pages
        # the empty tab the window opened with (Edge may have put a sync notice next to it)
        page = pages[0] if pages and pages[0].url in ("", "about:blank") else ctx.new_page()
        page.goto(WA_URL, wait_until="domcontentloaded", timeout=90_000)
        return page


def clear_http_cache(page):
    """Empties the browser's HTTP cache. The WhatsApp login (cookies, IndexedDB, local storage) stays."""
    cdp = page.context.new_cdp_session(page)
    try:
        cdp.send("Network.clearBrowserCache")
    finally:
        cdp.detach()


class LoadWatch:
    """Gets a WhatsApp tab out of a stuck loading screen. A script of WhatsApp Web that the browser
    cached incompletely (cut off at a multiple of 64 KB) fails to parse, and the page then shows its
    logo and "End-to-end encrypted" for ever, with no QR code and no chats. A tool that waits for
    WhatsApp calls tick(True) on every round while it sees neither the QR code nor the chat list, and
    tick(False) otherwise. After STUCK_AFTER seconds of loading it clears the HTTP cache and reloads
    the tab, once."""

    STUCK_AFTER = 45

    def __init__(self, page, log=print):
        self.page, self.log = page, log
        self.since: float | None = None
        self.done = False

    def tick(self, loading: bool):
        if not loading:
            self.since = None
            return
        now = time.time()
        if self.since is None:
            self.since = now
        elif not self.done and now - self.since > self.STUCK_AFTER:
            self.done = True
            self.log("WhatsApp Web is stuck loading: clearing the browser's cache and reloading it "
                     "(the login is kept)...")
            try:
                clear_http_cache(self.page)
                self.page.reload(wait_until="domcontentloaded")
            except Exception as e:  # noqa: BLE001 - the tool's own wait goes on
                self.log(f"Could not reload WhatsApp Web: {str(e).splitlines()[0] if str(e) else e!r}")
            self.since = None


@contextmanager
def turn(tool: str, check=None, waiting=None):
    """Hold the WhatsApp tab while `tool` clicks and scrolls in it. If another tool holds it, this
    waits: waiting(other tool's name) is called once, check() is called while waiting (it raises
    to give up, e.g. when the user presses Stop). Gives True if it had to wait."""
    told = False
    while not _turn.acquire(timeout=0.3):
        if not told and waiting:
            told = True
            waiting(_holder["tool"] or "another tool")
        if check:
            check()
    before = _holder["tool"]
    _holder["tool"] = tool
    try:
        yield told
    finally:
        _holder["tool"] = before
        _turn.release()
