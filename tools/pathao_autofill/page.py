"""
WhatsApp -> Pathao auto-fill
============================

Loads a list of order codes (6-830, 5a-380, ...), finds each code's order message in
WhatsApp Web (prefix "6" -> chat "6-", "5a" -> chat "5a-", ...) and fills Pathao's
"New Delivery" form: Merchant Order ID, Amount to Collect, Recipient's Phone,
Secondary Phone (when the message has two numbers), Recipient's Name and Address.

You check the form, finish whatever else Pathao needs and click Save. As soon as the
save goes through, the next code is filled. Codes are taken from the bottom of the file
upwards (can be switched off). After each save the phone is written next to its code in the
list file ("3-620<TAB>  01814120910"), which marks the code as done there.

"Check Pathao" looks the loaded codes up in Pathao's orders (by Merchant Order ID), only in each
prefix's current round of codes (5-1, 5-2... again after 5-117): the codes found there are marked as saved with their order's phone, which is also written in the list.
"""
import copy
import os
import queue
from datetime import datetime

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QPlainTextEdit, QSizePolicy, QTableWidget, QTableWidgetItem, QWidget,
)

from core import look, shell, shop, theme
from core.look import KIND
from core.ui import FlowLayout

from .autofill_engine import (
    APP_DIR, FETCHING, FILLED, IN_PATHAO, LOG_FILE, NOT_FOUND, PENDING, SAVED, SAVED_EARLIER, SAVED_LOG, SKIPPED,
    Worker, load_settings, read_saved_codes, save_settings,
)
from .order_parser import read_code_list, split_code

# a code's status as a row kind (core/look.py KINDS): its square and colour in the list
ROW_KINDS = {FETCHING: "working", FILLED: "working", SAVED: "ok", SAVED_EARLIER: "ok", IN_PATHAO: "ok",
             NOT_FOUND: "none", SKIPPED: "skipped"}
# set_state's levels: the control card's top line and its colour on navy
LEVELS = {"info": ("READY", theme.SKY), "busy": ("WORKING", theme.LIVE), "wait": ("CHECK PATHAO AND SAVE", theme.SKY),
          "ok": ("DONE", theme.LIVE), "warn": ("NEEDS YOU", look.AMBER_ON_NAVY)}
COLUMNS = ["Code", "Status", "Name", "Phone", "Phone 2", "Amount"]
NARROW_TABLE = 520  # table width (px) under which Phone 2 and Amount are hidden (the order card shows them)

STYLE = look.STYLE + f"""
QLabel#orderCode {{ color: {theme.ACCENT}; font-size: 22px; font-weight: 800; }}
QPlainTextEdit#message {{ background: {theme.SURFACE_ALT}; border: 1px solid {look.LINE}; border-radius: 12px;
    padding: 8px 10px; }}
"""


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = load_settings()
        self.shop = shop.get()
        self.settings["chat_names"] = self.shop.chats()  # shared with the other apps (Shop settings)
        self.settings["orders_chat"] = self.shop.settings.get("orders_chat") or ""
        self.cmd_q, self.evt_q = queue.Queue(), queue.Queue()
        self.worker = Worker(self.cmd_q, self.evt_q, copy.deepcopy(self.settings))
        self.worker.start()
        self.codes = []
        self.rows = {}  # code -> table row
        self.chat_edits = {}  # prefix -> QLineEdit
        self.panel_code = None

        self.push_timer = QTimer(self, singleShot=True, interval=600)
        self.push_timer.timeout.connect(self.push_settings)
        self.shop.changed.connect(self._on_shop_changed)

        self.setStyleSheet(STYLE)
        self._build()
        self.pump_timer = QTimer(self, interval=100)
        self.pump_timer.timeout.connect(self.pump)
        self.pump_timer.start()
        # A fresh start doesn't load the list of the last session: Load / Reload or Start does.

    # -- layout: the look of core/look.py ---------------------------------
    def _build(self):
        self.setWindowTitle("WhatsApp to Pathao auto-fill")
        self.setMinimumWidth(320)  # no least height: a short window scrolls the page (core/ui.py ScrollPage)
        self.browser_pill = look.WaPill()
        connect = look.button("Open / Connect browser", "outline", "globe", theme.NAVY)
        connect.clicked.connect(lambda: self.send("connect"))
        self.setup_btn = look.button("Setup", "outline",
                                     tip="Show or hide the Setup and chat-name cards (hide them to fit a small window)")
        self.setup_btn.setCheckable(True)
        self.setup_btn.setChecked(self.settings.get("show_setup", True))
        self.setup_btn.toggled.connect(self.show_setup)
        self._show_browser("Browser: not connected yet", False)

        # the run: progress, what is happening, the buttons (with their keys)
        self.hero = look.Hero("saved", [("saved", "Saved", theme.LIVE), ("todo", "To do", theme.SKY),
                                        ("none", "Not found", look.RED_ON_NAVY)], hint_text="")
        self.progress = self.hero.progress
        start = look.button("Start", "heroStart", "play", theme.NAVY, "F5")
        start.clicked.connect(self.start)
        self.hero.add(start)
        for text, icon, cmd, key in [("Pause", "pause", "pause", "F6"), ("Saved → Next", "check-check", "mark_saved", "F8"),
                                     ("Skip", "skip-forward", "skip", "F9"),
                                     ("Read WhatsApp again", "refresh-cw", "refetch", None)]:
            b = look.button(text, "heroGhost", icon, "#ffffff", key or "")
            b.clicked.connect(lambda _=False, c=cmd: self.send(c))
            if key:
                QShortcut(QKeySequence(key), self, activated=lambda c=cmd: self.send(c))
            self.hero.add(b)
        check = look.button("Check Pathao", "heroGhost", "search", "#ffffff")
        check.setToolTip("Finds which codes of the list already have a Pathao order (the code is its Merchant\n"
                         "Order ID; only orders since the prefix's codes last started again from 1),\n"
                         "marks them as saved with that order's phone and, when 'Write phone\n"
                         "in the list' is on, writes the phone next to each of them in the list file.")
        check.clicked.connect(self.check_pathao)
        self.hero.add(check)
        QShortcut(QKeySequence("F5"), self, activated=self.start)

        # the order on the Pathao form
        self.card = card = look.Card("package", theme.ACCENT_SOFT, theme.ACCENT, "Current order",
                                     "Double-click a code to fill it now")
        self.panel_lbl = QLabel("-", objectName="orderCode")
        card.head_right.addWidget(self.panel_lbl, 0, Qt.AlignTop)
        self.field_edits = {}
        for key, title in [("name", "Name"), ("phone", "Phone"), ("phone2", "Phone 2"), ("address", "Address"),
                           ("amount", "Amount to collect")]:
            if key == "address":
                edit = QPlainTextEdit(objectName="codes")
                edit.setTabChangesFocus(True)
                edit.setFixedHeight(70)
            else:
                edit = QLineEdit(objectName="field")
            self.field_edits[key] = edit
        f = self.field_edits
        card.lay.addLayout(look.column(look.field("Name"), f["name"]))
        card.lay.addLayout(look.row(look.column(look.field("Phone"), f["phone"]),
                                    look.column(look.field("Phone 2"), f["phone2"]), spacing=10))
        card.lay.addLayout(look.column(look.field("Address"), f["address"]))
        card.lay.addLayout(look.column(look.field("Amount to collect"), f["amount"]))
        self.msg_view = QPlainTextEdit(readOnly=True, objectName="message")
        self.msg_view.setFont(QFont(["Nirmala UI", "Segoe UI"], 11))
        self.msg_view.setMinimumHeight(90)
        card.lay.addLayout(look.column(look.field("WhatsApp message"), self.msg_view))
        fill = look.button("Fill Pathao with these values", "soft", "truck")
        fill.clicked.connect(self.refill)
        copy_btn = look.button("Copy message", "plain", "copy", theme.TEXT_2)
        copy_btn.clicked.connect(self.copy_message)
        card.lay.addLayout(look.row(fill, copy_btn, None))

        # setup
        self.setup_box = look.Card("sliders-horizontal", theme.WARNING_SOFT, theme.WARNING, "Setup")
        self.path_edit = QLineEdit(self.settings["list_file"])
        browse = look.button("Browse", "mini")
        browse.clicked.connect(self.browse)
        load = look.button("Load / Reload", "mini")
        load.clicked.connect(self.load_list)
        self.setup_box.lay.addLayout(look.column(look.field("Code list"),
                                                 look.path_box(self.path_edit, browse, load, icon="file-text")))
        self.bottom_check = look.ToggleRow("list", theme.NEUTRAL_SOFT, theme.NAVY, "Start from the bottom of the file")
        self.bottom_check.setChecked(self.settings["start_from_bottom"])
        self.bottom_check.toggled.connect(lambda _: self.codes and self.load_list())  # re-sort a loaded list
        self.top_check = look.ToggleRow("maximize", theme.ACCENT_SOFT, theme.ACCENT, "Keep this window on top",
                                        "Above the browser while this tool is open")
        self.top_check.setChecked(self.settings.get("always_on_top", False))
        self.top_check.toggled.connect(self.set_on_top)
        self.phone_check = look.ToggleRow("phone", theme.SUCCESS_SOFT, theme.SUCCESS, "Write the phone in the list",
                                          "After each Save")
        self.phone_check.setToolTip('After each save, the phone is added next to its code in the list file\n'
                                    '("3-620<TAB>  01814120910"), which marks the code as done there.')
        self.phone_check.setChecked(self.settings.get("write_phone_to_list", True))
        self.phone_check.toggled.connect(lambda _: self.push_settings())
        self.setup_box.lay.addLayout(look.column(self.bottom_check, self.top_check, self.phone_check, spacing=2))
        reset = look.button("Reset saved history", "plain", "rotate-ccw", theme.TEXT_2)
        reset.clicked.connect(self.reset_history)
        self.setup_box.lay.addLayout(look.row(None, reset))

        self.chat_box = look.Card("message-circle", theme.SUCCESS_SOFT, theme.SUCCESS, "Chat for each code prefix",
                                  "The WhatsApp chat each prefix's order messages are in")
        chats = QWidget()
        self.chat_flow = FlowLayout(chats, hspace=10, vspace=8)
        self.chat_box.lay.addWidget(chats)

        # the codes
        codes = look.Card("list", theme.NEUTRAL_SOFT, theme.NAVY, "Codes", "Double-click a code to fill it now")
        codes.lay.setContentsMargins(18, 18, 18, 10)
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setMinimumHeight(100)
        self.table.installEventFilter(self)
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        look.rows_table(self.table, {0: "code", 1: "status", 2: "name", 4: "muted", 5: "muted"}, 1, height=50)
        header = self.table.horizontalHeader()
        for col, mode in enumerate([QHeaderView.ResizeToContents, QHeaderView.Stretch, QHeaderView.Stretch,
                                    QHeaderView.ResizeToContents, QHeaderView.ResizeToContents,
                                    QHeaderView.ResizeToContents]):
            header.setSectionResizeMode(col, mode)
        self.table.setColumnWidth(0, 130)
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        self.table.cellDoubleClicked.connect(self.on_double_click)
        codes.lay.addWidget(self.table, 1)

        # The log folds away under its own head.
        self.log_card = look.LogCard(QPlainTextEdit(readOnly=True), height=110)
        self.log_view = self.log_card.edit
        self.log_view.setMaximumBlockCount(2000)

        self.look = look.ToolLayout(
            look.Header("pathao_autofill", [self.browser_pill, connect, self.setup_btn]), self.hero,
            [("order", card), ("setup", self.setup_box), ("setup", self.chat_box)], [(codes, 1), (self.log_card, 0)],
            [("order", "Order"), ("setup", "Setup"), ("codes", "Codes")], "codes", left_width=(420, 370))
        self.setCentralWidget(self.look)
        self.set_state("Load the code list, then press Start.", "info")
        self.show_setup(self.setup_btn.isChecked())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.look.resize_to(event.size().width())

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Resize and obj is self.table:
            narrow = event.size().width() < NARROW_TABLE
            for col in (4, 5):  # Phone 2, Amount
                self.table.setColumnHidden(col, narrow)
        return super().eventFilter(obj, event)

    def _show_browser(self, text, ok):
        bg, fg, dot = ((theme.SUCCESS_SOFT, theme.SUCCESS_DARK, theme.SUCCESS) if ok
                       else (theme.NEUTRAL_SOFT, theme.TEXT_2, theme.MUTED))
        self.browser_pill.set_look(bg, fg, dot, "Browser connected" if ok else "Browser not connected", tip=text)

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
            edit = QLineEdit(names.get(p) or f"{p}-", objectName="field")
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
        self.shop.set_chats(names)
        self.settings["list_file"] = self.path_edit.text().strip()
        self.settings["start_from_bottom"] = self.bottom_check.isChecked()
        self.settings["always_on_top"] = self.top_check.isChecked()
        self.settings["write_phone_to_list"] = self.phone_check.isChecked()
        save_settings(self.settings)
        self.send("settings", copy.deepcopy(self.settings))

    def _on_shop_changed(self):
        """Chat names changed in Shop settings or in another Boostimise app."""
        names, orders_chat = self.shop.chats(), self.shop.settings.get("orders_chat") or ""
        if names == self.settings.get("chat_names") and orders_chat == self.settings.get("orders_chat"):
            return
        self.settings["chat_names"], self.settings["orders_chat"] = names, orders_chat
        for p, edit in self.chat_edits.items():
            want = names.get(p) or f"{p}-"
            if edit.text().strip() != want and not edit.hasFocus():
                edit.blockSignals(True)
                edit.setText(want)
                edit.blockSignals(False)
                self._fit_to_text(edit)
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

    def check_pathao(self):
        if not self.codes:
            self.load_list()
            if not self.codes:
                return
        self.push_settings()
        self.send("check_pathao")

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
        for card in (self.setup_box, self.chat_box):
            card.setProperty("folded", not on)  # core/look.py ToolLayout keeps a folded card hidden
        self.look.refresh_cards()
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
            self.hero.sub.setText(f"Current order: {code}")
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
            self._show_browser(ev[1], ev[2])
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
        for c, v in enumerate(values):
            item = QTableWidgetItem(v)
            if c == 1:
                item.setData(KIND, ROW_KINDS.get(status, ""))
            self.table.setItem(r, c, item)

    def set_state(self, text, level="info"):
        kicker, color = LEVELS.get(level, LEVELS["info"])
        self.hero.set_status(kicker, color, text, f"Current order: {self.panel_code}" if self.panel_code else "")

    def update_progress(self):
        saved = sum(1 for c, r in self.rows.items() if self.table.item(r, 1) and self.table.item(r, 1).text().startswith("Saved"))
        self.progress.setRange(0, max(1, len(self.rows)))
        self.progress.setValue(saved)
        kinds = [self.table.item(r, 1).data(KIND) or "" for r in self.rows.values() if self.table.item(r, 1)]
        self.hero.counts["saved"].set_value(kinds.count("ok"))
        self.hero.counts["todo"].set_value(sum(k in ("", "working") for k in kinds))
        self.hero.counts["none"].set_value(kinds.count("none"))

    def log(self, msg):
        line = f"{datetime.now():%H:%M:%S}  {msg}"
        self.log_view.appendPlainText(line)
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"{datetime.now():%Y-%m-%d} {line}\n")
        except OSError:
            pass
