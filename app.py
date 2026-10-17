"""Boostimise - all-in-one tools for Cover Canvas BD (called CasePilot up to version 3.0, CoverDesk up to 2.1).

One window with a sidebar; each button opens one of the tools (the AI Photo Enhancer, WhatsApp
photo fetchers, model detect, rename by match, Pathao auto-fill, PhotoMatch, SnapRename, and the
account's Dashboard and Plans). Used signed in with Google (core/account.py, core/gate.py).

Run:  run.bat        (or:  .venv\\Scripts\\pythonw.exe app.py)
Installer:  build.bat  ->  dist\\Boostimise-Setup-<version>.exe
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import traceback
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMessageBox

from core import theme
from core.paths import DATA_DIR

INSTANCE_KEY = "CoverCanvasBD.AllInOne"


def setup_logging():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    log_file = DATA_DIR / "app.log"
    try:  # keep the log small: start over when it passes 2 MB
        if log_file.stat().st_size > 2_000_000:
            log_file.replace(log_file.with_suffix(".old.log"))
    except OSError:
        pass
    logging.basicConfig(filename=log_file, level=logging.INFO, encoding="utf-8",
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if sys.stderr is None or sys.stdout is None:  # pythonw: no console, keep prints in the log too
        stream = open(log_file, "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or stream
        sys.stderr = sys.stderr or stream


def install_error_hooks():
    log = logging.getLogger("allinone")

    def on_error(exc_type, exc, tb):
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        log.error("Unhandled error:\n%s", text)
        app = QApplication.instance()
        if app and threading.current_thread() is threading.main_thread():
            QMessageBox.critical(app.activeWindow(), "Unexpected error",
                                 "".join(traceback.format_exception_only(exc_type, exc)).strip()
                                 + f"\n\nDetails are in {DATA_DIR / 'app.log'}.")

    sys.excepthook = on_error
    threading.excepthook = lambda args: log.error(
        "Error in thread %s:\n%s", args.thread.name if args.thread else "?",
        "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)))


def already_running() -> bool:
    """Only one copy may run (two copies can't share the WhatsApp logins). A second start
    asks the first one to come to the front."""
    sock = QLocalSocket()
    sock.connectToServer(INSTANCE_KEY)
    if sock.waitForConnected(400):
        sock.write(b"show")
        sock.flush()
        sock.waitForBytesWritten(400)
        sock.disconnectFromServer()
        return True
    return False


def listen_for_second_start(win):
    server = QLocalServer(win)
    QLocalServer.removeServer(INSTANCE_KEY)  # left over from a crash

    def show():
        conn = server.nextPendingConnection()
        if conn:
            conn.close()
        if win.isMinimized():
            win.showNormal()
        win.show()
        win.raise_()
        win.activateWindow()

    server.newConnection.connect(show)
    server.listen(INSTANCE_KEY)
    return server


def main():
    selftest = sys.argv[1:2] == ["--selftest"]  # used by packaging/build.py, see core/selftest.py
    setup_logging()
    if sys.platform == "win32":
        try:  # own taskbar button and icon instead of python.exe's
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(theme.APP_ID)
        except (AttributeError, OSError):
            pass
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName(theme.APP_NAME)
    app.setApplicationVersion(theme.APP_VERSION)
    if not selftest and already_running():
        return 0
    theme.apply(app)
    install_error_hooks()

    from core.shell import MainWindow  # after the theme, so pages pick up the palette

    win = MainWindow(selftest=selftest)
    if selftest:
        from core import selftest as st

        code = st.run(win, Path(sys.argv[2]))
    else:
        server = listen_for_second_start(win)  # noqa: F841 - kept alive by its parent
        win.show()
        code = app.exec()
    logging.shutdown()
    # Worker threads of the tools are stopped in the pages' closeEvent; don't wait on stragglers.
    if sys.platform == "win32":
        # os._exit still unloads every DLL, and with PySide6 6.12 unloading shiboken crashes (exit code
        # 0xC0000005) once Pathao Auto-fill or PhotoMatch was opened. Everything is saved: just stop.
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE  # a 64-bit handle; ctypes' default int cuts it
        kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.TerminateProcess(kernel32.GetCurrentProcess(), code)
    os._exit(code)


if __name__ == "__main__":
    main()
