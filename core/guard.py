"""May this copy of Boostimise run? Asked of the server on every start and every few minutes.

The admin panel's "Desktop app" page controls the answer (boostimise/supabase/schema.sql,
desktop_check_in / desktop_verdict): a minimum version, single blocked versions, one switch that
turns every copy off, and single blocked PCs. While the answer is "no" the window shows the
update screen (core/gate.py UpdatePage) instead of the tools.

Each check also records this PC (desktop_installs): a hash of the Windows MachineGuid (never the id
itself), the computer's name, Windows version, CPU, RAM, GPU, screen, the app version and the
signed-in user, so the admin panel can list who uses the app on which PCs.

Offline: the last answer is kept in <data>/account/guard.bin, encrypted for this Windows user
(DPAPI). A "no" stays a "no" offline. A "yes" lasts ``offline_grace_hours`` (set in the admin
panel, 72 by default) after the last successful check; after that the app needs the internet
again. A clock turned back past the last check counts as expired.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import locale
import logging
import os
import platform
import time
import uuid

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QGuiApplication

from . import net, supabase, theme
from .account import _dpapi
from .paths import FROZEN, data_dir

log = logging.getLogger("allinone.guard")

CHECKING, OK, LOCKED = "checking", "ok", "locked"
DEFAULT_INTERVAL_MIN = 30
DEFAULT_GRACE_HOURS = 72
CLOCK_SLACK = 300  # seconds the PC's clock may be behind the last check before it counts as turned back


# ---- this PC ---------------------------------------------------------------------------------
def _reg(path: str, name: str) -> str | None:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
            value = winreg.QueryValueEx(k, name)[0]
            return str(value).strip() or None
    except (ImportError, OSError):
        return None


def machine_id() -> str:
    """sha256 of the Windows MachineGuid (stays the same across reinstalls of the app); a random
    id kept in the data folder when Windows has none."""
    guid = _reg(r"SOFTWARE\Microsoft\Cryptography", "MachineGuid")
    if not guid:
        path = data_dir("account") / "machine_id"
        try:
            guid = path.read_text(encoding="utf-8").strip()
            uuid.UUID(guid)
        except (OSError, ValueError):
            guid = str(uuid.uuid4())
            try:
                path.write_text(guid, encoding="utf-8")
            except OSError:
                pass
    return hashlib.sha256(f"boostimise-pc:{guid.lower()}".encode()).hexdigest()


def _windows_name() -> tuple[str | None, str | None]:
    nt = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion"
    name = _reg(nt, "ProductName")
    build = _reg(nt, "CurrentBuildNumber") or _reg(nt, "CurrentBuild")
    ubr = _reg(nt, "UBR")
    if name and build and build.isdigit() and int(build) >= 22000:
        name = name.replace("Windows 10", "Windows 11")  # Windows 11 still says 10 in ProductName
    display = _reg(nt, "DisplayVersion") or _reg(nt, "ReleaseId")
    if name and display:
        name = f"{name} {display}"
    return name or platform.platform(terse=True), (f"{build}.{ubr}" if build and ubr else build or platform.version())


def _ram_mb() -> int | None:
    class MemoryStatus(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    try:
        stat = MemoryStatus()
        stat.dwLength = ctypes.sizeof(MemoryStatus)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            return int(stat.ullTotalPhys // (1024 * 1024))
    except (AttributeError, OSError):
        pass
    return None


def _gpus() -> str | None:
    """Display adapters from the registry (no WMI, which can take seconds)."""
    names: list[str] = []
    try:
        import winreg

        cls = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, cls) as root:
            for i in range(32):
                try:
                    sub = winreg.EnumKey(root, i)
                except OSError:
                    break
                if not sub.isdigit():
                    continue
                desc = _reg(f"{cls}\\{sub}", "DriverDesc")
                if desc and desc not in names and "Basic Display" not in desc and "Remote" not in desc:
                    names.append(desc)
    except (ImportError, OSError):
        pass
    return ", ".join(names) or None


def _screen() -> str | None:
    """Primary screen in physical pixels (UI thread only)."""
    app = QGuiApplication.instance()
    screen = app.primaryScreen() if app else None
    if screen is None:
        return None
    size, ratio = screen.size(), screen.devicePixelRatio()
    return f"{round(size.width() * ratio)}x{round(size.height() * ratio)} @{round(ratio * 100)}%"


def pc_info(screen: str | None = None) -> dict:
    os_name, os_build = _windows_name()
    try:
        loc = locale.getlocale()[0]
    except ValueError:
        loc = None
    return {
        "install_kind": "setup" if FROZEN else "source",
        "hostname": platform.node() or os.environ.get("COMPUTERNAME"),
        "os_user": os.environ.get("USERNAME"),
        "os_name": os_name,
        "os_build": os_build,
        "arch": platform.machine(),
        "cpu": _reg(r"HARDWARE\DESCRIPTION\System\CentralProcessor\0", "ProcessorNameString") or platform.processor(),
        "cpu_cores": os.cpu_count(),
        "ram_mb": _ram_mb(),
        "gpu": _gpus(),
        "screen": screen,
        "locale": loc,
        "timezone": time.strftime("%Z") or None,
    }


# ---- the last answer, kept for offline starts ------------------------------------------------
class VerdictStore:
    def __init__(self):
        self.path = data_dir("account") / "guard.bin"

    def load(self) -> dict | None:
        try:
            raw = self.path.read_bytes()
            if not raw.startswith(b"DPAPI1"):
                return None
            data = json.loads(_dpapi(raw[6:], protect=False))
            return data if isinstance(data.get("verdict"), dict) else None
        except Exception:  # noqa: BLE001 - missing, damaged or another user's file: ask the server
            return None

    def save(self, verdict: dict, checked_at: float):
        try:
            raw = b"DPAPI1" + _dpapi(json.dumps({"verdict": verdict, "checked_at": checked_at,
                                                 "version": theme.APP_VERSION}).encode(), protect=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_bytes(raw)
            tmp.replace(self.path)
        except OSError as e:
            log.warning("could not save the check result: %s", e)


OFFLINE_VERDICT = {
    "allowed": False, "reason": "offline",
    "message": "Boostimise needs to check for updates. Connect this computer to the internet and try again.",
}


# ---- the guard -------------------------------------------------------------------------------
class Guard(QObject):
    """state: CHECKING (first start, no answer yet) | OK | LOCKED; ``verdict`` holds the details."""

    changed = Signal()

    def __init__(self):
        super().__init__()
        self.state = CHECKING
        self.verdict: dict = {}
        self.account = None
        self.checking = False
        self.store = VerdictStore()
        self._machine: str | None = None
        self._screen: str | None = None
        self._launch_sent = False
        self._notified_update: str | None = None
        self._timer = QTimer(self, singleShot=True, timeout=self.check)
        self._soon = QTimer(self, singleShot=True, interval=3000, timeout=self.check)

    @property
    def update_available(self) -> bool:
        return bool(self.verdict.get("update_available"))

    def start(self, account=None):
        """Starts checking; ``account`` (core/account.py) gives the user and live changes."""
        self.account = account
        self._screen = _screen()
        cached = self.store.load()
        if cached and cached.get("version") == theme.APP_VERSION:
            v = cached["verdict"]
            if not v.get("allowed"):
                self._apply(LOCKED, v)          # a "no" holds until the server says otherwise
            elif self._fresh(cached):
                self._apply(OK, v)              # start at once, confirm in the background
        if account is not None:
            # Releases / access changed in the admin panel ping app_settings. Not on every row: each
            # check-in sets the profile's last_seen_at, whose change would start the next check.
            account.table_changed.connect(lambda table: table == "app_settings" and self._soon.start())
            account.changed.connect(self._from_account_status)
            account.signed_in.connect(self._soon.start)
        self.check()

    def check(self):
        """Asks the server now (no-op while a check is running)."""
        if self.checking:
            return
        self.checking = True
        if self.state == LOCKED:
            self.changed.emit()  # the update screen shows "Checking…"
        launch = not self._launch_sent
        net.run_bg(lambda: self._ask(launch), lambda v: self._on_answer(v, launch), self._on_failed)

    # ---- worker thread -------------------------------------------------------------------
    def _ask(self, launch: bool) -> dict:
        if self._machine is None:
            self._machine = machine_id()
        params = {"p_machine_id": self._machine, "p_version": theme.APP_VERSION,
                  "p_info": pc_info(self._screen), "p_launch": launch}
        token = None
        if self.account is not None and self.account.session:
            try:
                token = self.account.token()
            except Exception:  # noqa: BLE001 - signed out / refresh failed: check in without the user
                token = None
        try:
            return supabase.rpc("desktop_check_in", params, token, timeout=20) or {}
        except net.HttpError as e:
            if token and e.status in (401, 403):
                return supabase.rpc("desktop_check_in", params, None, timeout=20) or {}
            raise

    # ---- UI thread -----------------------------------------------------------------------
    def _on_answer(self, verdict: dict, launch: bool):
        self.checking = False
        if launch:
            self._launch_sent = True
        if "allowed" not in verdict:
            log.warning("unexpected check answer: %s", verdict)
            self._on_failed(net.HttpError(0, "", "unexpected answer"))
            return
        self.store.save(verdict, time.time())
        self._apply(OK if verdict.get("allowed") else LOCKED, verdict)
        self._schedule(verdict)

    def _on_failed(self, e: BaseException):
        self.checking = False
        status = getattr(e, "status", 0)
        if isinstance(e, net.HttpError) and status == 404 and (e.code == "PGRST202" or "desktop_check_in" in (e.message or "")):
            # the database doesn't have the desktop tables yet (old schema.sql): don't lock anyone out
            log.warning("desktop_check_in missing - install the latest supabase/schema.sql")
            self._apply(OK, {"allowed": True, "reason": "unconfigured"})
            self._timer.start(DEFAULT_INTERVAL_MIN * 60_000)
            return
        log.warning("version check failed: %s", e)
        cached = self.store.load()
        if cached and cached.get("version") == theme.APP_VERSION:
            v = cached["verdict"]
            if not v.get("allowed"):
                self._apply(LOCKED, v)
            elif self._fresh(cached):
                self._apply(OK, v)
            else:
                self._apply(LOCKED, OFFLINE_VERDICT)
        else:
            self._apply(LOCKED, OFFLINE_VERDICT)
        self._timer.start(60_000)  # offline: try again in a minute

    def _from_account_status(self):
        """get_my_status answers 'desktop' when this version may not run: lock at once."""
        st = getattr(self.account, "status", None)
        lock = (st.raw.get("desktop") if st and isinstance(st.raw, dict) else None)
        if isinstance(lock, dict) and lock.get("reason") and self.state != LOCKED:
            self._apply(LOCKED, {**self.verdict, **lock, "allowed": False})
            self._soon.start()  # and fetch the full answer (download link, notes)

    def _fresh(self, cached: dict) -> bool:
        grace = cached["verdict"].get("offline_grace_hours", DEFAULT_GRACE_HOURS)
        try:
            grace = float(grace)
        except (TypeError, ValueError):
            grace = DEFAULT_GRACE_HOURS
        age = time.time() - float(cached.get("checked_at") or 0)
        return -CLOCK_SLACK <= age <= grace * 3600

    def _schedule(self, verdict: dict):
        try:
            minutes = max(5, int(verdict.get("check_interval_minutes") or DEFAULT_INTERVAL_MIN))
        except (TypeError, ValueError):
            minutes = DEFAULT_INTERVAL_MIN
        self._timer.start(minutes * 60_000)

    def _apply(self, state: str, verdict: dict):
        changed = state != self.state or verdict != self.verdict
        self.state, self.verdict = state, verdict
        if changed:
            log.info("guard: %s (%s)", state, verdict.get("reason"))
            self.changed.emit()
        elif state == LOCKED:
            self.changed.emit()  # "Checking…" is over
        latest = verdict.get("latest_version")
        if state == OK and self.update_available and latest and latest != self._notified_update:
            self._notified_update = latest
            self._notify_update(latest, verdict.get("download_url"))

    def _notify_update(self, latest: str, url: str | None):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        from . import webui as W

        action = ("Download", lambda: QDesktopServices.openUrl(QUrl(url))) if url else None
        W.toast.info(f"Boostimise {latest} is available (you have {theme.APP_VERSION}).", action, 12000)


_guard: Guard | None = None


def get() -> Guard:
    global _guard
    if _guard is None:
        _guard = Guard()
    return _guard
