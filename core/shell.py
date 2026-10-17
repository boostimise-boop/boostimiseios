"""The main window: a sidebar with one button per tool, and the tool pages next to it.

Pages are built the first time their button is clicked and keep running in the background
while another tool is shown, so a WhatsApp fetch can go on while you rename photos.

Every start is fresh: the first tool is shown and no tool reloads the lists or codes of the last
session (a finished list would only greet the user with "no codes found"). File and folder paths,
chat names, options and the WhatsApp / Pathao logins are remembered.

The window fits any screen: the sidebar folds to icons when the window is narrow, its tool list
scrolls when the window is short, and every page sits in a scroll area that only scrolls when
the window is smaller than the page can shrink.

Boostimise is used signed in with Google (core/account.py): until the account is ready the
window shows the sign-in screens (core/gate.py) instead of the sidebar and tools. Signing out,
or an account suspended by an admin (seen live through Supabase Realtime), closes every tool.
The same happens when this version may no longer run (core/guard.py: an update is required, the
app was switched off from the admin panel, or it has been offline too long): the update screen
replaces the tools until the server says yes again.
"""
from __future__ import annotations

import importlib
import logging
import os
import sys
import traceback

from PySide6.QtCore import QPoint, QRectF, QSettings, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QKeySequence, QLinearGradient, QPainter, QPainterPath, QShortcut
from PySide6.QtWidgets import (
    QAbstractButton, QApplication, QFrame, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPlainTextEdit,
    QPushButton, QScrollArea, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)

from tools import TOOLS, Tool

from . import account as acc
from . import guard as gd
from . import theme, ui, webui
from .account_widgets import AccountChip, account_menu
from .gate import Gate
from .paths import DATA_DIR

log = logging.getLogger("allinone")

SIDEBAR_TOP = "#15284d"     # the logo's navy, lighter at the top
SIDEBAR_BOTTOM = "#0a1429"
HOVER_BG = QColor(255, 255, 255, 20)
TEXT = "#c6d2e8"
MUTED = "#7f90b0"
BUSY = "#34d399"
WIDE, NARROW = 240, 68
AUTO_COLLAPSE_BELOW = 1180  # window width (px) under which the sidebar folds to icons by itself
MIN_WINDOW = QSize(720, 480)
SHORTCUTS = 9  # Ctrl+1 ... Ctrl+9 for the first nine tools


class NavButton(QAbstractButton):
    """A sidebar entry: icon, title and a green dot while the tool is working."""

    def __init__(self, icon: str, title: str, checkable=True):
        super().__init__()
        self.icon_name, self.title = icon, title
        self.busy = False
        self.collapsed = False
        self.setCheckable(checkable)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(42)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFocusPolicy(Qt.NoFocus)

    def set_busy(self, busy: bool):
        self.busy = bool(busy)
        self.update()

    def set_collapsed(self, collapsed: bool):
        self.collapsed = collapsed
        self.setToolTip(self.title if collapsed else "")
        self.update()

    def sizeHint(self):
        return QSize(WIDE, 42)

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        box = QRectF(self.rect()).adjusted(10, 3, -10, -3)
        checked, hover = self.isChecked(), self.underMouse()
        if checked or hover:
            path = QPainterPath()
            path.addRoundedRect(box, 10, 10)
            if checked:
                fill = QLinearGradient(box.topLeft(), box.bottomRight())
                fill.setColorAt(0, QColor(theme.ACCENT))
                fill.setColorAt(1, QColor(theme.ACCENT_2))
                p.fillPath(path, fill)
            else:
                p.fillPath(path, HOVER_BG)
        color = "#ffffff" if checked or hover else TEXT
        ratio = self.devicePixelRatioF()
        pm = theme.icon_pixmap(self.icon_name, color, 18, ratio)
        icon_x = box.center().x() - 9 if self.collapsed else box.left() + 13
        p.drawPixmap(int(icon_x), int(box.center().y() - 9), pm)
        if not self.collapsed:
            p.setPen(QColor(color))
            font = QFont(self.font())
            font.setPointSizeF(10)
            font.setWeight(QFont.DemiBold if checked else QFont.Normal)
            p.setFont(font)
            text_box = box.adjusted(44, 0, -24, 0)
            p.drawText(text_box, Qt.AlignVCenter | Qt.AlignLeft,
                       p.fontMetrics().elidedText(self.title, Qt.ElideRight, int(text_box.width())))
        if self.busy:
            dot = (QRectF(icon_x + 14, box.center().y() - 13, 8, 8) if self.collapsed
                   else QRectF(box.right() - 18, box.center().y() - 4, 8, 8))
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(52, 211, 153, 70))
            p.drawEllipse(dot.adjusted(-3, -3, 3, 3))
            p.setBrush(QColor(BUSY))
            p.drawEllipse(dot)
        p.end()


class Sidebar(QFrame):
    tool_clicked = Signal(str)
    collapse_toggled = Signal(bool)
    account_clicked = Signal(object)

    def __init__(self, tools: list[Tool]):
        super().__init__(objectName="sidebar")
        self.setStyleSheet(f"""
            QFrame#sidebar {{ background: qlineargradient(x1:0, y1:0, x2:0.35, y2:1,
                                stop:0 {SIDEBAR_TOP}, stop:1 {SIDEBAR_BOTTOM}); }}
            QLabel {{ background: transparent; }}
            QLabel#appname {{ color: #ffffff; font-size: 13pt; font-weight: 700; }}
            QLabel#tagline {{ color: {MUTED}; font-size: 8.5pt; }}
            QLabel#section {{ color: {MUTED}; font-size: 8pt; font-weight: 700; letter-spacing: 1.5px;
                              padding: 14px 0 4px 22px; }}
            QFrame#rule {{ background: rgba(255, 255, 255, 28); margin: 12px 18px 6px 18px; }}
            QFrame#footrule {{ background: rgba(255, 255, 255, 22); margin: 0 16px 6px 16px; }}
            QLabel#version {{ color: {MUTED}; font-size: 8pt; padding: 2px 0 8px 22px; }}
            QScrollArea#navscroll, QWidget#nav {{ background: transparent; border: none; }}
            QScrollArea#navscroll QScrollBar:vertical {{ background: transparent; width: 6px; margin: 2px 1px; }}
            QScrollArea#navscroll QScrollBar::handle:vertical {{ background: rgba(255, 255, 255, 50);
                              border-radius: 2px; min-height: 24px; }}
        """)
        self.buttons: dict[str, NavButton] = {}
        self.wide_only: list[QWidget] = []  # hidden when collapsed
        self.narrow_only: list[QWidget] = []

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 16, 0, 6)
        lay.setSpacing(2)

        head = QHBoxLayout()
        head.setContentsMargins(16, 0, 12, 10)
        head.setSpacing(10)
        self.logo = QLabel()
        self.logo.setPixmap(theme.app_icon().pixmap(36, 36))
        self.logo.setToolTip(f"{theme.APP_NAME} {theme.APP_VERSION}")
        head.addWidget(self.logo)
        names = QVBoxLayout()
        names.setSpacing(0)
        # The name in the wordmark's two colours, and a tagline that only wraps between its parts.
        plain = theme.APP_NAME.removesuffix(theme.APP_NAME_BLUE)
        blue = theme.APP_NAME[len(plain):]
        names.addWidget(QLabel(f'{plain}<span style="color: {theme.SKY};">{blue}</span>', objectName="appname"))
        tagline = " • ".join(part.replace(" ", " ") for part in theme.APP_TAGLINE.split(" • "))
        names.addWidget(QLabel(tagline, objectName="tagline", wordWrap=True))
        name_box = QWidget()
        name_box.setLayout(names)
        names.setContentsMargins(0, 0, 0, 0)
        head.addWidget(name_box, 1)
        self.wide_only.append(name_box)
        lay.addLayout(head)

        # The tool list scrolls when the window is too short for all of it.
        nav = QWidget(objectName="nav")
        nav_lay = QVBoxLayout(nav)
        nav_lay.setContentsMargins(0, 0, 0, 0)
        nav_lay.setSpacing(2)
        section = None
        for n, tool in enumerate(tools, 1):
            if tool.section != section:
                section = tool.section
                label = QLabel(section.upper(), objectName="section")
                rule = QFrame(objectName="rule")
                rule.setFixedHeight(13)
                nav_lay.addWidget(label)
                nav_lay.addWidget(rule)
                self.wide_only.append(label)
                self.narrow_only.append(rule)
            btn = NavButton(tool.icon, tool.title)
            btn.setToolTip(f"{tool.description}\n(Ctrl+{n})" if n <= SHORTCUTS else tool.description)
            btn.clicked.connect(lambda _=False, k=tool.key: self.tool_clicked.emit(k))
            self.buttons[tool.key] = btn
            nav_lay.addWidget(btn)
        nav_lay.addStretch(1)
        scroll = QScrollArea(objectName="navscroll")
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(nav)
        nav.setAutoFillBackground(False)
        scroll.viewport().setAutoFillBackground(False)
        lay.addWidget(scroll, 1)

        foot = QFrame(objectName="footrule")
        foot.setFixedHeight(1)
        lay.addWidget(foot)
        self.account_chip = AccountChip(self.account_clicked.emit)
        lay.addWidget(self.account_chip)
        self.data_btn = NavButton("folder", "Open data folder", checkable=False)
        self.data_btn.clicked.connect(lambda: os.startfile(DATA_DIR))
        lay.addWidget(self.data_btn)
        self.collapse_btn = NavButton("collapse", "Collapse sidebar", checkable=False)
        self.collapse_btn.clicked.connect(lambda: self.set_collapsed(not self.collapsed, emit=True))
        lay.addWidget(self.collapse_btn)
        version = QLabel(f"v{theme.APP_VERSION}", objectName="version")
        lay.addWidget(version)
        self.wide_only.append(version)
        self.collapsed = False
        self.set_collapsed(False)

    def set_collapsed(self, collapsed: bool, emit=False):
        self.collapsed = collapsed
        self.setFixedWidth(NARROW if collapsed else WIDE)
        for w in self.wide_only:
            w.setVisible(not collapsed)
        for w in self.narrow_only:
            w.setVisible(collapsed)
        for btn in [*self.buttons.values(), self.data_btn, self.collapse_btn, self.account_chip]:
            btn.set_collapsed(collapsed)
        self.collapse_btn.icon_name = "expand" if collapsed else "collapse"
        self.collapse_btn.title = "Expand sidebar" if collapsed else "Collapse sidebar"
        self.collapse_btn.setToolTip(self.collapse_btn.title if collapsed else "")
        if emit:
            self.collapse_toggled.emit(collapsed)

    def select(self, key: str):
        for k, btn in self.buttons.items():
            btn.setChecked(k == key)


class MessagePage(QWidget):
    """Shown while a tool is loading, or instead of it when it failed to start."""

    def __init__(self, title: str, text: str = "", details: str = "", retry=None, icon: str = ""):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(40, 40, 40, 40)
        lay.setSpacing(10)
        lay.addStretch(1)
        if icon:
            badge = QLabel()
            badge.setFixedSize(64, 64)
            badge.setAlignment(Qt.AlignCenter)
            badge.setStyleSheet(f"background: {theme.ACCENT_SOFT}; border-radius: 18px;")
            badge.setPixmap(theme.icon_pixmap(icon, theme.ACCENT, 30, self.devicePixelRatioF()))
            row = QHBoxLayout()
            row.addStretch(1)
            row.addWidget(badge)
            row.addStretch(1)
            lay.addLayout(row)
        head = QLabel(title)
        head.setStyleSheet(f"font-size: 15pt; font-weight: 600; color: {theme.TEXT};")
        head.setAlignment(Qt.AlignCenter)
        head.setWordWrap(True)
        lay.addWidget(head)
        if text:
            body = QLabel(text, wordWrap=True)
            body.setStyleSheet(f"color: {theme.TEXT_2};")
            body.setAlignment(Qt.AlignCenter)
            lay.addWidget(body)
        if details:
            box = QPlainTextEdit(details, readOnly=True)
            box.setFont(QFont("Consolas", 9))
            box.setMaximumHeight(260)
            lay.addWidget(box)
        if retry:
            btn = QPushButton("Try again", objectName="primary")
            btn.clicked.connect(retry)
            row = QHBoxLayout()
            row.addStretch(1)
            row.addWidget(btn)
            row.addStretch(1)
            lay.addLayout(row)
        lay.addStretch(2)


class MainWindow(QMainWindow):
    def __init__(self, selftest: bool = False):
        super().__init__()
        self.setWindowTitle(f"{theme.APP_NAME} – {theme.APP_TAGLINE}")
        self.setMinimumSize(MIN_WINDOW)
        self.settings = QSettings(str(DATA_DIR / "app.ini"), QSettings.IniFormat)
        self.tools = {t.key: t for t in TOOLS}
        self.pages: dict[str, QWidget] = {}  # tools that have been opened
        self.slots: dict[str, QWidget] = {}  # what the stack shows for each tool (page, or loading / error)
        self.current: str | None = None
        # The sidebar is narrow when the user folded it, or by itself while the window is narrow.
        self.sidebar_pref = self.settings.value("sidebar_collapsed", False, type=bool)
        self.narrow = False

        self.sidebar = Sidebar(TOOLS)
        self.sidebar.tool_clicked.connect(self.open_tool)
        self.sidebar.collapse_toggled.connect(self._on_collapse_clicked)
        self.sidebar.account_clicked.connect(self._account_menu)
        self.stack = QStackedWidget()
        self.app_area = QWidget()
        row = QHBoxLayout(self.app_area)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(self.sidebar)
        row.addWidget(self.stack, 1)
        # the sign-in screens, or the sidebar and tools once the account is ready
        self.root = QStackedWidget()
        self.gate = Gate()
        self.root.addWidget(self.gate)
        self.root.addWidget(self.app_area)
        self.setCentralWidget(self.root)
        webui.toast.install(self)

        for n, tool in enumerate(TOOLS[:SHORTCUTS], 1):
            QShortcut(QKeySequence(f"Ctrl+{n}"), self, activated=lambda k=tool.key: self._shortcut(k))

        self.sidebar.set_collapsed(self.sidebar_pref)
        self._restore_geometry()
        self.settings.remove("last_tool")  # kept by versions before 3.0.1, which reopened it
        self.account = acc.get()
        if selftest:  # no account in the build's self-test: every tool must open anyway
            self.root.setCurrentWidget(self.app_area)
            self.open_tool(TOOLS[0].key)
            return
        self.guard = gd.get()
        self.account.state_changed.connect(self._on_account_state)
        self.account.signed_in.connect(self._on_signed_in)
        self.guard.changed.connect(self._on_account_state)
        self.guard.start(self.account)
        self._on_account_state()
        self.account.start()

    # ---- account ---------------------------------------------------------------------
    def _on_account_state(self, _state: str | None = None):
        """Shows the tools, or the gate's screen for the version check and the account."""
        state = self.account.state
        if self.guard.state == gd.LOCKED:
            if self.slots:
                self._close_pages()
            self.gate.show_update()
            self.root.setCurrentWidget(self.gate)
            self.setWindowTitle(f"{theme.APP_NAME} – {theme.APP_TAGLINE}")
            return
        if self.guard.state == gd.CHECKING:
            state = acc.LOADING  # first start: wait for the server's answer
        if state == acc.READY:
            self.root.setCurrentWidget(self.app_area)
            if self.current is None:
                self.open_tool(TOOLS[0].key)
            else:
                self.setWindowTitle(f"{self.tools[self.current].title} – {theme.APP_NAME}")
            return
        if state in (acc.SIGNED_OUT, acc.BLOCKED) and self.slots:
            self._close_pages()
        self.gate.show_state(state)
        self.root.setCurrentWidget(self.gate)
        self.setWindowTitle(f"{theme.APP_NAME} – {theme.APP_TAGLINE}")

    def _on_signed_in(self):
        if self.isMinimized():
            self.showNormal()
        self.raise_()
        self.activateWindow()
        webui.toast.success("Signed in")

    def _shortcut(self, key: str):
        if self.root.currentWidget() is self.app_area:
            self.open_tool(key)

    def _account_menu(self, chip: QWidget):
        menu = account_menu(self, [("sparkles", "Enhance photos", lambda: self.open_tool("photo_enhancer")),
                                   ("layout-dashboard", "Dashboard", lambda: self.open_tool("dashboard")),
                                   ("crown", "Plans", lambda: self.open_tool("plans"))], self.sign_out)
        hint = menu.sizeHint()
        menu.exec(chip.mapToGlobal(QPoint(8, -hint.height() + 4)))

    def sign_out(self):
        busy = [self.tools[k].title for k, p in self.pages.items() if page_is_busy(p)]
        if busy and QMessageBox.question(
                self, "Sign out", f"{' and '.join(busy)} {'is' if len(busy) == 1 else 'are'} still working.\n\n"
                                  "Stop and sign out anyway?") != QMessageBox.Yes:
            return
        self.account.sign_out()
        webui.toast("Signed out")

    def _close_pages(self):
        """Signed out / suspended: every tool stops, and the next sign-in starts them fresh."""
        for key, page in list(self.pages.items()):
            try:
                page.close()
            except Exception:
                log.exception("Error while closing %s", key)
        for slot in self.slots.values():
            self.stack.removeWidget(slot)
            slot.deleteLater()
        self.pages.clear()
        self.slots.clear()
        self.current = None
        for btn in self.sidebar.buttons.values():
            btn.set_busy(False)

    def _restore_geometry(self):
        """Last session's size and place, kept inside this screen (the last session may have been
        on a bigger monitor); the first time: most of the screen, centred."""
        free = self.screen().availableGeometry()
        geometry = self.settings.value("geometry")
        if geometry and self.restoreGeometry(geometry):
            if self.isMaximized() or self.isFullScreen():
                return
            frame = self.frameGeometry()
            extra_w, extra_h = frame.width() - self.width(), frame.height() - self.height()
            width = min(self.width(), free.width() - extra_w)
            height = min(self.height(), free.height() - extra_h)
            if (width, height) != (self.width(), self.height()):
                self.resize(max(width, MIN_WINDOW.width()), max(height, MIN_WINDOW.height()))
            frame = self.frameGeometry()
            if not free.contains(frame):
                x = min(max(frame.x(), free.left()), max(free.left(), free.right() - frame.width() + 1))
                y = min(max(frame.y(), free.top()), max(free.top(), free.bottom() - frame.height() + 1))
                self.move(x, y)
            return
        width = max(MIN_WINDOW.width(), min(1480, int(free.width() * 0.9)))
        height = max(MIN_WINDOW.height(), min(940, int(free.height() * 0.9)))
        self.resize(min(width, free.width()), min(height, free.height()))
        self.move(free.center().x() - self.width() // 2, free.center().y() - self.height() // 2 - 10)

    # ---- sidebar ---------------------------------------------------------------------
    def _on_collapse_clicked(self, collapsed: bool):
        if not self.narrow:  # a choice made in a wide window is remembered
            self.sidebar_pref = collapsed
            self.settings.setValue("sidebar_collapsed", collapsed)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        narrow = event.size().width() < AUTO_COLLAPSE_BELOW
        if narrow != self.narrow:
            self.narrow = narrow
            self.sidebar.set_collapsed(self.sidebar_pref or narrow)

    # ---- pages -----------------------------------------------------------------------
    def open_tool(self, key: str):
        if key == self.current and key in self.slots:
            self.sidebar.select(key)
            return
        old = self.pages.get(self.current)
        if old is not None and hasattr(old, "page_hidden"):
            old.page_hidden()
        self.current = key
        self.sidebar.select(key)
        self.setWindowTitle(f"{self.tools[key].title} – {theme.APP_NAME}")
        if key not in self.slots:
            self._load(key)
        self.stack.setCurrentWidget(self.slots[key])
        page = self.pages.get(key)
        if page is not None and hasattr(page, "page_shown"):
            page.page_shown()

    def _load(self, key: str):
        tool = self.tools[key]
        loading = MessagePage(f"Opening {tool.title}…", tool.description, icon=tool.icon)
        self._set_slot(key, loading)
        self.stack.setCurrentWidget(self.slots[key])
        QApplication.processEvents()  # show "Opening…" while the tool's modules import
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            module, _, attr = tool.factory.partition(":")
            page = getattr(importlib.import_module(module), attr)()
            ui.make_responsive(page)
        except Exception:
            details = traceback.format_exc()
            log.error("Could not open %s:\n%s", tool.title, details)
            self._set_slot(key, MessagePage(f"{tool.title} could not start",
                                            f"The error is below (also in {DATA_DIR / 'app.log'}).",
                                            details, retry=lambda: self._retry(key), icon=tool.icon))
            return
        finally:
            QApplication.restoreOverrideCursor()
        self.pages[key] = page
        self._set_slot(key, page)
        busy_changed = getattr(page, "busy_changed", None)
        if busy_changed is not None:
            busy_changed.connect(self.sidebar.buttons[key].set_busy)

    def _set_slot(self, key: str, widget: QWidget):
        """The stack shows each page inside a scroll area, so a page that can't shrink any further
        scrolls instead of making the whole window (every tool) bigger than the screen."""
        old = self.slots.get(key)
        slot = ui.ScrollPage(widget)
        self.slots[key] = slot
        self.stack.addWidget(slot)
        if old is not None:
            self.stack.removeWidget(old)
            old.deleteLater()

    def _retry(self, key: str):
        self.slots.pop(key, None)
        self.current = None
        self.open_tool(key)

    def show_page(self, page: QWidget):
        key = next((k for k, p in self.pages.items() if p is page), None)
        if key:
            self.open_tool(key)

    # ---- quitting --------------------------------------------------------------------
    def closeEvent(self, event):
        busy = [self.tools[k].title for k, p in self.pages.items() if page_is_busy(p)]
        if busy and QMessageBox.question(
                self, "Quit", f"{' and '.join(busy)} {'is' if len(busy) == 1 else 'are'} still working.\n\n"
                              "Stop and quit anyway?") != QMessageBox.Yes:
            event.ignore()
            return
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.sync()
        self.hide()
        for key, page in self.pages.items():
            try:
                page.close()  # the page's closeEvent saves its settings and stops its worker
            except Exception:
                log.exception("Error while closing %s", key)
        event.accept()


def page_is_busy(page: QWidget) -> bool:
    busy = getattr(page, "is_busy", False)  # a method on most pages, a plain flag on some
    return bool(busy() if callable(busy) else busy)


def find_shell(widget: QWidget) -> MainWindow | None:
    win = widget.window()
    return win if isinstance(win, MainWindow) else None


def bring_to_front(page: QWidget):
    """Show this tool's page and bring the window forward (a tool needs the user)."""
    win = page.window()
    shell = find_shell(page)
    if shell:
        shell.show_page(page)
    if win.isMinimized():
        win.showNormal()
    win.raise_()
    win.activateWindow()


def set_on_top(page: QWidget, on: bool):
    """Keep the whole window above other windows (used by Pathao Auto-fill while it is shown)."""
    win = page.window()
    if win is page and not win.isVisible():
        return
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND] + [ctypes.c_int] * 4 + [wintypes.UINT]
        swp_nosize, swp_nomove, swp_noactivate = 0x1, 0x2, 0x10
        user32.SetWindowPos(int(win.winId()), -1 if on else -2, 0, 0, 0, 0,
                            swp_nosize | swp_nomove | swp_noactivate)
    elif bool(win.windowFlags() & Qt.WindowStaysOnTopHint) != on:
        win.setWindowFlag(Qt.WindowStaysOnTopHint, on)
        win.show()
