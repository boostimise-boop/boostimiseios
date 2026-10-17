"""`Boostimise.exe --selftest <report.txt>`: opens every tool and loads what the tools only load
later (the style sheet's glyphs, OCR models, OpenVINO and the bundled AI model, the Playwright driver driving a hidden
Edge, the WhatsApp scripts, Supabase over HTTPS and Realtime), writes one line per check to the
report and quits with 1 if anything failed ("skip" lines, e.g. no internet, are not failures).

packaging/build.py runs it on the packaged app, so a file or module that PyInstaller missed
shows up at build time instead of on somebody's PC.
"""
from __future__ import annotations

import logging
import sys
import traceback
from pathlib import Path


def ui_glyphs():
    """The logo and the check marks and arrows the style sheet draws (assets/*.svg, read by Qt's
    SVG plugin)."""
    from PySide6.QtGui import QImage

    from core import theme
    from core.paths import ASSETS

    bad = [name for name in ("logo.png", "logo-full.png", *theme.UI_ICONS) if QImage(str(ASSETS / name)).isNull()]
    assert not bad, f"missing or unreadable {bad}"


def playwright_driver():
    from playwright._impl._driver import compute_driver_executable

    missing = [p for p in compute_driver_executable() if not Path(p).is_file()]
    assert not missing, f"missing {missing}"


def browser():
    """The bundled Playwright driver really drives the PC's Edge (Chrome if there is no Edge)."""
    from playwright.sync_api import Error, sync_playwright

    with sync_playwright() as pw:
        try:
            b = pw.chromium.launch(channel="msedge", headless=True)
        except Error:
            b = pw.chromium.launch(channel="chrome", headless=True)
        page = b.new_page()
        page.set_content("<p>ok</p>")
        assert page.inner_text("p") == "ok"
        b.close()


def shared_browser():
    """The tools' way into the shared WhatsApp window: Edge started with a debugging port, then
    connect_over_cdp (a hidden one here, with a throwaway profile and a free port)."""
    import socket
    import tempfile

    from playwright.sync_api import sync_playwright

    from core import whatsapp

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:  # Edge may still hold a file
        proc = whatsapp.launch(port, Path(tmp), ["--headless=new"])
        try:
            assert whatsapp.wait_running(port), "the browser did not open its debugging port"
            with sync_playwright() as pw:
                b = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
                page = b.contexts[0].new_page()
                page.set_content("<p>ok</p>")
                assert page.inner_text("p") == "ok"
                b.new_browser_cdp_session().send("Browser.close")
            proc.wait(15)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(15)


def whatsapp_scripts():
    from tools.whatsapp_fetch.worker import BRIDGES

    missing = [str(p) for p in BRIDGES.values() if not p.is_file()]
    assert not missing, f"missing {missing}"


def ocr():
    from rapidocr_onnxruntime import RapidOCR

    RapidOCR()


def openvino_model():
    import openvino as ov

    from tools.photo_match.matcher import BUNDLED_AI_MODEL

    core = ov.Core()
    core.compile_model(core.read_model(BUNDLED_AI_MODEL), "CPU")


def heif():
    from pi_heif import register_heif_opener

    register_heif_opener()


def claude_api():
    import anthropic

    anthropic.Anthropic(api_key="selftest")


def account_config():
    """The Supabase key shipped in the app must be the publishable one, never the secret key."""
    import base64
    import json

    from core import config

    key = config.SUPABASE_KEY
    assert config.SUPABASE_URL.startswith("https://") and key, "Supabase URL / key missing"
    assert not key.startswith("sb_secret_"), "a SECRET Supabase key is in core/config.py"
    if key.count(".") == 2:  # legacy JWT key: must be the anon role
        part = key.split(".")[1]
        role = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))).get("role")
        assert role == "anon", f"the Supabase key has role {role!r}, not anon"


def account_libs():
    """Phone numbers (every country), the flags, Realtime's WebSocket, the sign-in screens."""
    import phonenumbers
    from PySide6.QtSvg import QSvgRenderer
    from PySide6.QtWebSockets import QWebSocket  # noqa: F401

    from core.account_widgets import all_countries, flag_pixmap
    from core.paths import ASSETS

    ex = phonenumbers.example_number_for_type("BD", phonenumbers.PhoneNumberType.MOBILE)
    assert phonenumbers.is_valid_number(ex), "phone metadata missing"
    assert len(all_countries()) > 200, "country list incomplete"
    assert QSvgRenderer(str(ASSETS / "flags" / "BD.svg")).isValid(), "flags missing"
    assert not flag_pixmap("US").isNull()


class Skip(Exception):
    """A check that can't run on this PC right now (no internet): reported, not counted as a failure."""


def online_services():
    """The packaged app really reaches Supabase: HTTPS through Python's TLS (accounts, quota) and a
    Realtime WebSocket through Qt's TLS plugin (live updates), joined as a guest on the public plans."""
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtNetwork import QHostInfo

    from core import supabase
    from core.config import SUPABASE_URL
    from core.realtime import RealtimeChannel

    host = SUPABASE_URL.split("//", 1)[1].split("/")[0]
    if QHostInfo.fromName(host).error() != QHostInfo.HostInfoError.NoError:
        raise Skip("no internet: can't look up Supabase")
    limits = supabase.public_limits()
    assert "guest" in limits, f"unexpected answer {limits!r}"

    channel = RealtimeChannel("selftest", [{"event": "*", "schema": "public", "table": "plans"}])
    loop = QEventLoop()
    states: list[str] = []
    channel.state.connect(lambda s: (states.append(s), s == "subscribed" and loop.quit()))
    QTimer.singleShot(20_000, loop.quit)
    channel.start(None)
    loop.exec()
    subscribed = channel.subscribed
    error = channel.ws.errorString()
    channel.stop()
    assert subscribed, f"Realtime not subscribed within 20 s (states {states}, socket: {error})"


CHECKS = [ui_glyphs, playwright_driver, browser, shared_browser, whatsapp_scripts, ocr, openvino_model, heif, claude_api,
          account_config, account_libs, online_services]


class _Collect(logging.Handler):
    def __init__(self):
        super().__init__(logging.ERROR)
        self.messages: list[str] = []

    def emit(self, record):
        self.messages.append(record.getMessage())


def run(win, report: Path) -> int:
    lines: list[str] = []
    errors = _Collect()
    logging.getLogger().addHandler(errors)
    # No message boxes: nobody is there to close them.
    sys.excepthook = lambda *exc: errors.messages.append("".join(traceback.format_exception(*exc)))

    for key, tool in win.tools.items():
        before = len(errors.messages)
        win.open_tool(key)
        if key in win.pages:
            lines.append(f"ok    tool {tool.title}")
        else:
            lines.append(f"FAIL  tool {tool.title}\n" + "\n".join(errors.messages[before:]))
    for check in CHECKS:
        try:
            check()
            lines.append(f"ok    {check.__name__}")
        except Skip as e:
            lines.append(f"skip  {check.__name__} ({e})")
        except Exception:  # noqa: BLE001 - report everything
            lines.append(f"FAIL  {check.__name__}\n{traceback.format_exc()}")
    win.close()
    stray = [m for m in errors.messages if not any(m in line for line in lines)]
    lines += [f"FAIL  error while running\n{m}" for m in stray]

    failed = sum(line.startswith("FAIL") for line in lines)
    skipped = sum(line.startswith("skip") for line in lines)
    lines.append(f"{len(lines) - failed - skipped} passed, {failed} failed" + (f", {skipped} skipped" if skipped else ""))
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 1 if failed else 0
