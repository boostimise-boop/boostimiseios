"""
Cover Canvas BD - WhatsApp -> Pathao auto-fill
==============================================

Loads a list of order codes (6-830, 5a-380, ...), finds each code's order message in
WhatsApp Web (prefix "6" -> chat "6-", "5a" -> chat "5a-", ...) and fills Pathao's
"New Delivery" form: Merchant Order ID, Amount to Collect, Recipient's Phone,
Secondary Phone (when the message has two numbers), Recipient's Name and Address.

You check the form, finish whatever else Pathao needs and click Save. As soon as the
save goes through, the next code is filled. Codes are taken from the bottom of the file
upwards (can be switched off). After each save the phone is written next to its code in the
list file ("3-620<TAB>  01814120910"), which marks the code as done there.
"""
import copy
import os
import queue
from datetime import datetime

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QFileDialog, QFormLayout, QFrame, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSplitter, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from core import shell, theme
from core.ui import FlowLayout

from .autofill_engine import (
    APP_DIR, FETCHING, FILLED, LOG_FILE, NOT_FOUND, PENDING, SAVED, SAVED_EARLIER, SAVED_LOG, SKIPPED,
    Worker, load_settings, read_saved_codes, save_settings,
)
from .order_parser import read_code_list, split_code

ROW_COLORS = {FETCHING: "#fff6d6", FILLED: "#fff6d6", SAVED: "#dcfce7", SAVED_EARLIER: "#effaf1",
              NOT_FOUND: "#fee2e2"}
BANNER_COLORS = {"info": (theme.ACCENT_SOFT, theme.ACCENT_DARK), "busy": ("#fff6d6", "#5c4700"), "wait": ("#e9eef5", "#2c3e5a"),
                 "ok": ("#dcfce7", "#14532d"), "warn": ("#fee2e2", "#7a1010")}
COLUMNS = ["Code", "Status", "Name", "Phone", "Phone 2", "Amount"]
STACK_BELOW = 860  # window width (px) under which the order card moves below the code list
NARROW_TABLE = 520  # table width (px) under which Phone 2 and Amount are hidden (the order card shows them)

# The look comes from core/theme.py; only what is special to this page is here.
STYLE = f"""
QMainWindow, QWidget#root {{ background: {theme.BG}; }}
QTableWidget::item:selected {{ background: {theme.ACCENT_SOFT}; color: {theme.TEXT}; }}
QProgressBar {{ min-width: 170px; min-height: 20px; }}
QScrollArea {{ border: none; }}
"""


class Page(QWidget):
    """The window's contents. It asks for the height its layout needs at a given width (wrapped rows
    included), so the scroll area around it only scrolls when the window is too small to show it all."""

    def heightForWidth(self, width):
        return self.layout().minimumHeightForWidth(width)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = load_settings()
        self.cmd_q, self.evt_q = queue.Queue(), queue.Queue()
        self.worker = Worker(self.cmd_q, self.evt_q, copy.deepcopy(self.settings))
        self.worker.start()
        self.codes = []
        self.rows = {}  # code -> table row
        self.chat_edits = {}  # prefix -> QLineEdit
        self.panel_code = None

        self.push_timer = QTimer(self, singleShot=True, interval=600)
        self.push_timer.timeout.connect(self.push_settings)

        self.setStyleSheet(STYLE)
        self._build()
        self.pump_timer = QTimer(self, interval=100)
        self.pump_timer.timeout.connect(self.pump)
        self.pump_timer.start()
        # A fresh start doesn't load the list of the last session: Load / Reload or Start does.

    # -- layout ------------------------------------------------------------
    def _build(self):
        self.setWindowTitle("Cover Canvas BD - WhatsApp to Pathao auto-fill")
        self.setMinimumSize(320, 260)
        # Rows of controls wrap onto more lines as the window narrows; when even that doesn't fit,
        # the scroll area scrolls instead of cutting the window off.
        root = Page(objectName="root")
        scroll = QScrollArea(widgetResizable=True, frameShape=QFrame.NoFrame)
        scroll.setWidget(root)
        self.setCentralWidget(scroll)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(12, 4, 12, 12)
        outer.setSpacing(8)

        self.setup_box = QGroupBox("Setup")
        sv = QVBoxLayout(self.setup_box)
        row = FlowLayout()
        row.addWidget(QLabel("Code list"))
        self.path_edit = QLineEdit(self.settings["list_file"])
        self.path_edit.setMinimumWidth(220)
        row.addWidget(self.path_edit)
        browse = QPushButton("Browse...")
        browse.clicked.connect(self.browse)
        row.addWidget(browse)
        load = QPushButton("Load / Reload")
        load.clicked.connect(self.load_list)
        row.addWidget(load)
        sv.addLayout(row)

        row = FlowLayout(hspace=16)
        connect = QPushButton("Open / Connect browser")
        connect.clicked.connect(lambda: self.send("connect"))
        row.addWidget(connect)
        self.browser_lbl = QLabel("Browser: not connected yet")
        self.browser_lbl.setStyleSheet(f"color: {theme.ERROR};")
        row.addWidget(self.browser_lbl)
        row.addStretch()
        self.bottom_check = QCheckBox("Start from bottom of the file")
        self.bottom_check.setChecked(self.settings["start_from_bottom"])
        self.bottom_check.toggled.connect(lambda _: self.codes and self.load_list())  # re-sort a loaded list
        row.addWidget(self.bottom_check)
        self.top_check = QCheckBox("Keep this window on top")
        self.top_check.setChecked(self.settings.get("always_on_top", False))
        self.top_check.toggled.connect(self.set_on_top)
        row.addWidget(self.top_check)
        self.phone_check = QCheckBox("Write phone in the list after Save")
        self.phone_check.setToolTip('After each save, the phone is added next to its code in the list file\n'
                                    '("3-620<TAB>  01814120910"), which marks the code as done there.')
        self.phone_check.setChecked(self.settings.get("write_phone_to_list", True))
        self.phone_check.toggled.connect(lambda _: self.push_settings())
        row.addWidget(self.phone_check)
        sv.addLayout(row)
        outer.addWidget(self.setup_box)

        self.chat_box = QGroupBox("WhatsApp chat name for each code prefix")
        self.chat_flow = FlowLayout(self.chat_box, hspace=16, vspace=8)
        outer.addWidget(self.chat_box)

        bar = FlowLayout()
        start = QPushButton("▶  Start", objectName="primary")
        start.setToolTip("F5")
        start.clicked.connect(self.start)
        bar.addWidget(start)
        for text, cmd, key in [("⏸  Pause", "pause", "F6"), ("✓  Saved → Next", "mark_saved", "F8"),
                               ("⏭  Skip", "skip", "F9"), ("↻  Read WhatsApp again", "refetch", None)]:
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, c=cmd: self.send(c))
            if key:
                b.setToolTip(key)
                QShortcut(QKeySequence(key), self, activated=lambda c=cmd: self.send(c))
            bar.addWidget(b)
        QShortcut(QKeySequence("F5"), self, activated=self.start)
        bar.addStretch()
        self.progress = QProgressBar()
        self.progress.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.progress.setFormat("Saved %v / %m")
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        bar.addWidget(self.progress)
        reset = QPushButton("Reset saved history")
        reset.clicked.connect(self.reset_history)
        bar.addWidget(reset)
        self.setup_btn = QPushButton("⚙  Setup", checkable=True)
        self.setup_btn.setToolTip("Show or hide the Setup and chat-name boxes (hide them to fit a small window)")
        self.setup_btn.setChecked(self.settings.get("show_setup", True))
        self.setup_btn.toggled.connect(self.show_setup)
        bar.addWidget(self.setup_btn)
        outer.addLayout(bar)

        self.banner = QLabel(wordWrap=True)
        self.banner.setMinimumHeight(38)
        outer.addWidget(self.banner)
        self.set_state("Load the code list, then press Start.", "info")

        self.split = QSplitter(Qt.Horizontal, childrenCollapsible=False)  # turns vertical in a narrow window
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setMinimumHeight(100)
        self.table.installEventFilter(self)
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setShowGrid(False)
        header = self.table.horizontalHeader()
        for col, mode in enumerate([QHeaderView.ResizeToContents, QHeaderView.ResizeToContents, QHeaderView.Stretch,
                                    QHeaderView.ResizeToContents, QHeaderView.ResizeToContents,
                                    QHeaderView.ResizeToContents]):
            header.setSectionResizeMode(col, mode)
        self.table.cellDoubleClicked.connect(self.on_double_click)
        lv.addWidget(self.table)
        hint = QLabel("Double-click a code to fill it now.")
        hint.setStyleSheet(f"color: {theme.TEXT_2};")
        lv.addWidget(hint)
        self.split.addWidget(left)

        self.card = card = QGroupBox("Current order")
        card.installEventFilter(self)
        cv = QVBoxLayout(card)
        top = FlowLayout(hspace=8)
        self.panel_lbl = QLabel("-")
        self.panel_lbl.setStyleSheet(f"font-size: 20px; font-weight: 700; color: {theme.ACCENT};")
        top.addWidget(self.panel_lbl)
        top.addStretch()
        fill = QPushButton("Fill Pathao with these values")
        fill.clicked.connect(self.refill)
        top.addWidget(fill)
        copy_btn = QPushButton("Copy message")
        copy_btn.clicked.connect(self.copy_message)
        top.addWidget(copy_btn)
        cv.addLayout(top)
        form = QFormLayout()
        self.field_edits = {}
        for key, title in [("name", "Name"), ("phone", "Phone"), ("phone2", "Phone 2"), ("address", "Address"),
                           ("amount", "Amount to collect")]:
            if key == "address":
                edit = QPlainTextEdit()
                edit.setTabChangesFocus(True)
                edit.setFixedHeight(64)
            else:
                edit = QLineEdit()
            form.addRow(title, edit)
            self.field_edits[key] = edit
        cv.addLayout(form)
        cv.addWidget(QLabel("WhatsApp message"))
        self.msg_view = QPlainTextEdit(readOnly=True)
        self.msg_view.setFont(QFont(["Nirmala UI", "Segoe UI"], 11))
        self.msg_view.setMinimumHeight(48)
        cv.addWidget(self.msg_view, 1)
        self.split.addWidget(card)
        self.split.setStretchFactor(0, 3)
        self.split.setStretchFactor(1, 2)

        # The log sits under a splitter handle: drag it to resize the log, or all the way down to hide it.
        self.log_view = QPlainTextEdit(readOnly=True)
        self.log_view.setMaximumBlockCount(2000)
        self.log_view.setFont(QFont("Consolas", 9))
        self.log_view.setMinimumHeight(44)
        vsplit = QSplitter(Qt.Vertical)
        vsplit.addWidget(self.split)
        vsplit.addWidget(self.log_view)
        vsplit.setCollapsible(0, False)
        vsplit.setStretchFactor(0, 1)
        vsplit.setSizes([600, 130])
        outer.addWidget(vsplit, 1)
        self.show_setup(self.setup_btn.isChecked())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        orientation = Qt.Vertical if event.size().width() < STACK_BELOW else Qt.Horizontal
        if self.split.orientation() != orientation:
            stacked = orientation == Qt.Vertical
            self.split.setOrientation(orientation)
            self.split.setSizes([1000, 1000] if stacked else [3000, 2000])
            self.table.setMinimumHeight(200 if stacked else 100)  # stacked, the page scrolls: keep a few rows in view

    def eventFilter(self, obj, event):
        if event.type() != QEvent.Resize:
            pass
        elif obj is self.table:
            narrow = event.size().width() < NARROW_TABLE
            for col in (4, 5):  # Phone 2, Amount
                self.table.setColumnHidden(col, narrow)
        elif obj is self.card:
            # the splitter only knows the card's one-line height; keep room for its top row when it wraps
            m = self.card.contentsMargins()
            need = self.card.layout().minimumHeightForWidth(event.size().width() - m.left() - m.right())
            self.card.setMinimumHeight(need + m.top() + m.bottom())
        return super().eventFilter(obj, event)

    def _build_chat_names(self, prefixes):
        while self.chat_flow.count():
            w = self.chat_flow.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.chat_edits = {}
        names = self.settings.setdefault("chat_names", {})
        for p in prefixes:
            pair = QWidget(sizePolicy=QSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed))
            h = QHBoxLayout(pair)
            h.setContentsMargins(0, 0, 0, 0)
            h.addWidget(QLabel(f"{p}  →"))
            edit = QLineEdit(names.get(p) or f"{p}-")
            self._fit_to_text(edit)
            edit.textChanged.connect(lambda _, e=edit: (self._fit_to_text(e), self.push_timer.start()))
            h.addWidget(edit)
            self.chat_flow.addWidget(pair)
            self.chat_edits[p] = edit

    @staticmethod
    def _fit_to_text(edit):
        """Sizes a chat-name box to its text, so whole chat names show and the pairs wrap tidily."""
        width = edit.fontMetrics().horizontalAdvance(edit.text()) + 30
        edit.setFixedWidth(max(80, min(width, 260)))

    # -- actions -----------------------------------------------------------
    def send(self, *cmd):
        self.cmd_q.put(cmd)

    def push_settings(self):
        names = self.settings.setdefault("chat_names", {})
        for p, edit in self.chat_edits.items():
            name = edit.text().strip()
            if name and name != f"{p}-":
                names[p] = name
            else:
                names.pop(p, None)
        self.settings["list_file"] = self.path_edit.text().strip()
        self.settings["start_from_bottom"] = self.bottom_check.isChecked()
        self.settings["always_on_top"] = self.top_check.isChecked()
        self.settings["write_phone_to_list"] = self.phone_check.isChecked()
        save_settings(self.settings)
        self.send("settings", copy.deepcopy(self.settings))

    def browse(self):
        start = os.path.dirname(self.path_edit.text()) or APP_DIR
        path, _ = QFileDialog.getOpenFileName(self, "Code list", start, "Text files (*.txt);;All files (*.*)")
        if path:
            self.path_edit.setText(os.path.normpath(path))
            self.load_list()

    def load_list(self):
        path = self.path_edit.text().strip()
        if not os.path.isfile(path):
            QMessageBox.warning(self, "Code list", f"File not found:\n{path}")
            return
        codes = read_code_list(path)
        if not codes:
            self.set_state("No codes left to do in the code list. Add new codes, then press Load / Reload.", "warn")
            QMessageBox.warning(self, "Code list", "No codes left to do in that file.\n\n"
                                "A code is done once a phone number (or a mark like ------) is written next to "
                                "it. Put the new codes in the file, each on its own line like 6-830, then press "
                                "Load / Reload.")
            return
        file_order_prefixes = list(dict.fromkeys(split_code(c)[0] for c in codes))
        if self.bottom_check.isChecked():
            codes.reverse()
        saved = read_saved_codes()
        rows = [(c, SAVED_EARLIER if c in saved else PENDING) for c in codes]
        self.codes = codes
        self.rows = {}
        self.table.setRowCount(len(rows))
        for r, (code, status) in enumerate(rows):
            self.rows[code] = r
            self._set_row(code, status, None)
        self._build_chat_names(file_order_prefixes)
        self.push_settings()
        self.send("load", rows, path)
        earlier = sum(1 for _, s in rows if s != PENDING)
        first = next((c for c, s in rows if s == PENDING), "-")
        self.log(f"Loaded {len(codes)} codes from {os.path.basename(path)}; {earlier} were saved earlier. First to do: {first}")
        self.set_state("List loaded. Press Start (the browser opens by itself if needed).", "info")
        self.update_progress()

    def start(self):
        if not self.codes:
            self.load_list()
            if not self.codes:
                return
        self.push_settings()
        self.send("start")

    def refill(self):
        if not self.panel_code:
            QMessageBox.information(self, "Fill Pathao", "Double-click a code in the list first.")
            return
        self.send("refill", self.panel_code, {k: self._field_text(e).strip() for k, e in self.field_edits.items()})

    @staticmethod
    def _field_text(edit):
        return edit.toPlainText() if isinstance(edit, QPlainTextEdit) else edit.text()

    def copy_message(self):
        QApplication.clipboard().setText(self.msg_view.toPlainText().strip())

    def on_double_click(self, row, _col):
        code = self.table.item(row, 0).text()
        status = self.table.item(row, 1).text()
        if status.startswith("Saved") and QMessageBox.question(
                self, "Already saved", f"{code} is already marked as saved.\nFill it in Pathao again?") != QMessageBox.Yes:
            return
        self.push_settings()
        self.send("jump", code)

    def reset_history(self):
        if not os.path.isfile(SAVED_LOG):
            QMessageBox.information(self, "Saved history", "There is no saved history yet.")
            return
        if QMessageBox.question(self, "Reset saved history",
                                "Forget which codes were already saved?\n"
                                "(The old history is kept as a backup file in the data folder.)") != QMessageBox.Yes:
            return
        os.replace(SAVED_LOG, SAVED_LOG[:-4] + datetime.now().strftime("_backup_%Y%m%d_%H%M%S.txt"))
        self.load_list()

    def set_on_top(self, on):
        """Keeps the window above the browser while this tool is shown."""
        self.settings["always_on_top"] = bool(on)
        save_settings(self.settings)
        if self.isVisible():
            shell.set_on_top(self, bool(on))

    def page_shown(self):
        if self.top_check.isChecked():
            shell.set_on_top(self, True)

    def page_hidden(self):
        if self.top_check.isChecked():
            shell.set_on_top(self, False)

    def show_setup(self, on):
        self.setup_box.setVisible(on)
        self.chat_box.setVisible(on)
        if self.settings.get("show_setup", True) != on:
            self.settings["show_setup"] = on
            save_settings(self.settings)

    def closeEvent(self, event):
        self.push_settings()
        self.send("quit")
        self.worker.join(timeout=3)
        super().closeEvent(event)

    # -- events from the worker -------------------------------------------
    def pump(self):
        try:
            while True:
                self.handle_event(self.evt_q.get_nowait())
        except queue.Empty:
            pass

    def handle_event(self, ev):
        kind = ev[0]
        if kind == "log":
            self.log(ev[1])
        elif kind == "row":
            self._set_row(ev[1], ev[2], ev[3])
            self.update_progress()
        elif kind == "current":
            _, code, data, raw = ev
            self.panel_code = code
            self.panel_lbl.setText(code)
            for k, edit in self.field_edits.items():
                text = (data or {}).get(k, "")
                edit.setPlainText(text) if isinstance(edit, QPlainTextEdit) else edit.setText(text)
            self.msg_view.setPlainText(raw or "")
            if code in self.rows:
                self.table.selectRow(self.rows[code])
                self.table.scrollToItem(self.table.item(self.rows[code], 0), QAbstractItemView.PositionAtCenter)
        elif kind == "state":
            self.set_state(ev[1], ev[2])
        elif kind == "browser":
            self.browser_lbl.setText(ev[1])
            self.browser_lbl.setStyleSheet(f"color: {theme.SUCCESS}; font-weight: 600;" if ev[2] else f"color: {theme.ERROR};")
        elif kind == "attention":
            self.set_state(ev[1], "warn")
            self.log("⚠ " + ev[1])
            QApplication.beep()
            shell.bring_to_front(self)
        elif kind == "learned":
            self.settings[ev[1]] = ev[2]
            save_settings(self.settings)
            self.log(f"Remembered the Pathao New Delivery address: {ev[2]}")

    def _set_row(self, code, status, data):
        r = self.rows.get(code)
        if r is None:
            return
        d = data or {}
        values = [code, status, d.get("name", ""), d.get("phone", ""), d.get("phone2", ""), d.get("amount", "")]
        if data is None and self.table.item(r, 2):  # keep what we already know about this code
            values[2:] = [self.table.item(r, c).text() for c in range(2, 6)]
        bg = QColor(ROW_COLORS.get(status, "#ffffff"))
        fg = QColor(theme.MUTED if status == SKIPPED else theme.TEXT)
        for c, v in enumerate(values):
            item = QTableWidgetItem(v)
            item.setBackground(bg)
            item.setForeground(fg)
            if c == 0:
                item.setFont(QFont("Segoe UI", 10, QFont.DemiBold))
            self.table.setItem(r, c, item)

    def set_state(self, text, level="info"):
        bg, fg = BANNER_COLORS.get(level, BANNER_COLORS["info"])
        self.banner.setText(text)
        self.banner.setStyleSheet(f"background: {bg}; color: {fg}; border-radius: 10px; padding: 9px 14px; "
                                  "font-size: 13px; font-weight: 600;")

    def update_progress(self):
        saved = sum(1 for c, r in self.rows.items() if self.table.item(r, 1) and self.table.item(r, 1).text().startswith("Saved"))
        self.progress.setRange(0, max(1, len(self.rows)))
        self.progress.setValue(saved)

    def log(self, msg):
        line = f"{datetime.now():%H:%M:%S}  {msg}"
        self.log_view.appendPlainText(line)
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"{datetime.now():%Y-%m-%d} {line}\n")
        except OSError:
            pass
