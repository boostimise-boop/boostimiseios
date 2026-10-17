"""Reaction List: the codes whose WhatsApp messages were reacted with ❌, 📵 or 😥.

Reads the chats in the shared WhatsApp window (core/whatsapp.py) back to the "Look back" date and
lists every code whose message, or a photo sent right after it, has the chosen reaction, with the
phone number written in the message (order messages have one). With codes pasted, only those are
looked for. The list can be copied ("1. 2-558 Realme C85 ❌ 01734484040") or saved as a .txt.
"""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QComboBox, QCompleter, QFileDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QSpinBox, QSplitter, QStyle, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from core import theme
from core.paths import data_dir
from core.ui import FlowLayout, scrollable
from tools.whatsapp_fetch.helpers import CodeItem, parse_code_list
from tools.whatsapp_fetch.page import ALL_CHATS, ALL_GROUPS, STACK_BELOW, STATE_COLORS, default_browse_dir

from .helpers import CHOICES, Found, code_sort_key, format_list, normalize
from .worker import ReactionListWorker

COLS = ["#", "Code", "Name", "Reaction", "Phone", "Chat", "Date"]
COL_NUM, COL_NAME, COL_REACT, COL_CHAT = 0, 2, 3, 5

STYLE = f"""
QLabel#hint {{ color: {theme.TEXT_2}; font-size: 9pt; }}
QLabel#count {{ background: {theme.ACCENT_SOFT}; color: {theme.ACCENT_DARK}; font-weight: 600; font-size: 9pt;
               border-radius: 6px; padding: 3px 9px; }}
QPushButton#choice {{ font-size: 14pt; padding: 4px 16px; }}
QPushButton#choice:checked {{ border: 2px solid {theme.ACCENT}; }}
QProgressBar {{ min-height: 20px; }}
"""


class ReactionListPage(QMainWindow):
    busy_changed = Signal(bool)

    def __init__(self):
        super().__init__()
        self.data_dir = data_dir("reaction_list")
        self.setStyleSheet(STYLE)
        self.settings = QSettings(str(self.data_dir / "settings.ini"), QSettings.IniFormat)
        self.worker: ReactionListWorker | None = None
        self.items: list[CodeItem] = []
        self.list_codes: dict[str, tuple[int, CodeItem]] = {}  # the pasted codes of the run, by normalize()
        self.results: dict[str, Found] = {}
        self.wanted: tuple[str, ...] = CHOICES[-1][2]  # reactions of the run, in the order they are shown
        self.wa_state = "idle"
        self.job_running = False
        self.pending_job: tuple[list, dict] | None = None
        self._parse_timer = QTimer(self, singleShot=True, interval=300)
        self._parse_timer.timeout.connect(self._parse_list)

        self._build_ui()
        self._load_settings()
        self._parse_list()
        self._set_state("idle", "Not connected")
        self._update_buttons()

    # ---- UI --------------------------------------------------------------------
    def _build_ui(self):
        # 1. codes (optional)
        list_box = QGroupBox("1  Codes  (optional)")
        self.list_edit = QPlainTextEdit()
        self.list_edit.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.list_edit.setPlaceholderText(
            "Leave empty to list every code that got the reaction.\n"
            "Or paste codes (one per line) to look for only those:\n\n"
            "2-558\tRealme C85\t26/9\n2-560\tInfinix note 40 5g\t26/9")
        self.list_edit.textChanged.connect(self._parse_timer.start)
        open_btn = QPushButton("Open .txt…")
        open_btn.clicked.connect(self._open_txt)
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self.list_edit.clear)
        self.count_label = QLabel(objectName="count")
        row = QHBoxLayout()
        row.addWidget(open_btn)
        row.addWidget(clear_btn)
        row.addStretch()
        row.addWidget(self.count_label)
        lay = QVBoxLayout(list_box)
        lay.addWidget(self.list_edit, 1)
        lay.addLayout(row)

        # 2. which reaction, where
        set_box = QGroupBox("2  Reaction")
        self.choice_group = QButtonGroup(self)
        choice_row = FlowLayout()
        for i, (_, label, _, tip) in enumerate(CHOICES):
            btn = QPushButton(label, objectName="choice", checkable=True)
            btn.setToolTip(tip)
            btn.setCursor(Qt.PointingHandCursor)
            self.choice_group.addButton(btn, i)
            choice_row.addWidget(btn)

        self.chat_combo = QComboBox()
        self.chat_combo.setEditable(True)
        self.chat_combo.setInsertPolicy(QComboBox.NoInsert)
        self.chat_combo.addItems([ALL_CHATS, ALL_GROUPS])
        self.chat_combo.completer().setFilterMode(Qt.MatchContains)
        self.chat_combo.completer().setCompletionMode(QCompleter.PopupCompletion)
        self.chat_combo.setToolTip("Limit the search to one chat/group (type part of its name), or search all chats.")
        self.chats_btn = QPushButton()
        self.chats_btn.setIcon(self.style().standardIcon(QStyle.SP_BrowserReload))
        self.chats_btn.setFixedWidth(36)
        self.chats_btn.setToolTip("Reload the chat list from WhatsApp")
        self.chats_btn.clicked.connect(lambda: self.worker and self.worker.refresh_chats())
        chat_row = QHBoxLayout()
        chat_row.addWidget(self.chat_combo, 1)
        chat_row.addWidget(self.chats_btn)
        self.lookback_spin = QSpinBox()
        self.lookback_spin.setRange(1, 365)
        self.lookback_spin.setSuffix(" days")
        self.lookback_spin.setToolTip(
            "How far back to read each chat. With codes pasted it is extended automatically\n"
            "to a week before the oldest date in your list (e.g. 26/9 → from 19/9).")
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.addRow("Search in", chat_row)
        form.addRow("Look back", self.lookback_spin)
        hint = QLabel("Counts a reaction on the message with the code, on its album, or on a photo "
                      "the same person sent right after it.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        set_lay = QVBoxLayout(set_box)
        set_lay.addLayout(choice_row)
        set_lay.addLayout(form)
        set_lay.addWidget(hint)

        # 3. run
        run_box = QGroupBox("3  Run")
        self.state_dot = QLabel("●")
        self.state_label = QLabel()
        self.state_label.setWordWrap(True)
        state_row = QHBoxLayout()
        state_row.addWidget(self.state_dot)
        state_row.addWidget(self.state_label, 1)
        self.connect_btn = QPushButton("Open WhatsApp")
        self.connect_btn.clicked.connect(self._toggle_connection)
        self.start_btn = QPushButton("Fetch list")
        self.start_btn.setObjectName("primary")
        self.start_btn.clicked.connect(self._start)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.clicked.connect(self._stop)
        btn_row = FlowLayout()  # wraps onto two lines in a narrow column
        for b in (self.connect_btn, self.start_btn, self.stop_btn):
            btn_row.addWidget(b)
        self.progress = QProgressBar()
        self.progress.setFormat("%v / %m chats")
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        wa_hint = QLabel("WhatsApp opens in one Edge window that every tool shares. First time: scan the QR "
                         "code there (WhatsApp › Linked devices); the login is remembered for all tools.")
        wa_hint.setObjectName("hint")
        wa_hint.setWordWrap(True)
        run_lay = QVBoxLayout(run_box)
        run_lay.addLayout(state_row)
        run_lay.addLayout(btn_row)
        run_lay.addWidget(self.progress)
        run_lay.addWidget(wa_hint)

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 4, 0)
        lv.addWidget(list_box, 1)
        lv.addWidget(set_box)
        lv.addWidget(run_box)
        self.list_edit.setMinimumHeight(84)

        # results + log
        self.results_box = QGroupBox("Results")
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)  # the # column numbers the rows
        self.table.verticalHeader().setDefaultSectionSize(30)
        hdr = self.table.horizontalHeader()
        for c in range(len(COLS)):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(COL_NAME, QHeaderView.Interactive)
        hdr.setSectionResizeMode(COL_CHAT, QHeaderView.Stretch)
        self.table.setColumnWidth(COL_NAME, 180)
        self.react_font = QFont(self.font())
        self.react_font.setPointSize(13)
        self.found_label = QLabel(objectName="count")
        self.copy_btn = QPushButton("Copy list")
        self.copy_btn.setToolTip("Copy the list to paste it anywhere:  1. 2-558 Realme C85 ❌ 01734484040")
        self.copy_btn.clicked.connect(self._copy)
        self.save_btn = QPushButton("Save .txt…")
        self.save_btn.clicked.connect(self._save)
        res_row = QHBoxLayout()
        res_row.addWidget(self.found_label)
        res_row.addStretch()
        res_row.addWidget(self.copy_btn)
        res_row.addWidget(self.save_btn)
        res_lay = QVBoxLayout(self.results_box)
        res_lay.addWidget(self.table)
        res_lay.addLayout(res_row)

        log_box = QGroupBox("Log")
        self.log_edit = QPlainTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setMaximumBlockCount(5000)
        self.log_edit.setFont(QFont("Consolas", 9))
        QVBoxLayout(log_box).addWidget(self.log_edit)

        self.right_split = QSplitter(Qt.Vertical)
        self.right_split.addWidget(self.results_box)
        self.right_split.addWidget(log_box)
        self.right_split.setSizes([520, 220])

        # The settings column scrolls when the window is too short for it (a 768 px laptop screen).
        self.main_split = QSplitter(Qt.Horizontal, childrenCollapsible=False)
        self.main_split.addWidget(scrollable(left))
        self.main_split.addWidget(self.right_split)
        self.main_split.setStretchFactor(1, 1)
        self.main_split.setSizes([480, 840])
        self.right_split.setMinimumSize(260, 220)

        central = QWidget()
        cl = QVBoxLayout(central)
        cl.setContentsMargins(12, 6, 12, 10)
        cl.addWidget(self.main_split)
        self.setCentralWidget(central)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        stacked = event.size().width() < STACK_BELOW
        if stacked != (self.main_split.orientation() == Qt.Vertical):
            self.main_split.setOrientation(Qt.Vertical if stacked else Qt.Horizontal)
            self.main_split.setSizes([1000, 1000] if stacked else [480, 840])

    # ---- settings --------------------------------------------------------------
    def _load_settings(self):
        s = self.settings
        self.chat_combo.setCurrentText(s.value("chat", ALL_CHATS))
        self.lookback_spin.setValue(int(s.value("lookback_days", 10)))
        keys = [c[0] for c in CHOICES]
        choice = s.value("reaction", "all")
        self.choice_group.button(keys.index(choice) if choice in keys else len(CHOICES) - 1).setChecked(True)

    def _save_settings(self):
        s = self.settings
        s.setValue("chat", self.chat_combo.currentText())
        s.setValue("lookback_days", self.lookback_spin.value())
        s.setValue("reaction", self._choice()[0])
        s.sync()

    def _choice(self) -> tuple:
        return CHOICES[max(self.choice_group.checkedId(), 0)]

    # ---- code list / results ---------------------------------------------------
    def _open_txt(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open code list", str(default_browse_dir()),
                                              "Text files (*.txt *.tsv *.csv);;All files (*)")
        if not path:
            return
        raw = Path(path).read_bytes()
        for enc in ("utf-8-sig", "utf-16", "cp1252"):
            try:
                self.list_edit.setPlainText(raw.decode(enc))
                break
            except UnicodeDecodeError:
                continue
        self._log(f"Loaded {path}")

    def _parse_list(self):
        if self.job_running:
            return
        self.items = parse_code_list(self.list_edit.toPlainText())
        self.count_label.setText(f"{len(self.items)} code(s)" if self.items else "Every code")

    def _rows(self) -> list[Found]:
        if self.list_codes:
            return sorted(self.results.values(), key=lambda f: f.order)
        return sorted(self.results.values(), key=lambda f: code_sort_key(f.code))

    def _refresh_table(self):
        rows = self._rows()
        self.table.setColumnHidden(COL_NAME, not self.list_codes)  # names come from the pasted list
        self.table.setRowCount(len(rows))
        for r, f in enumerate(rows):
            when = datetime.fromtimestamp(f.t).strftime("%d/%m %H:%M") if f.t else ""
            values = [str(r + 1), f.code, f.name, f.react(self.wanted), ", ".join(f.phones), ", ".join(f.chats), when]
            for c, text in enumerate(values):
                cell = QTableWidgetItem(text)
                if c == COL_REACT:
                    cell.setFont(self.react_font)
                    cell.setTextAlignment(Qt.AlignCenter)
                elif c == COL_NUM:
                    cell.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                elif c == COL_CHAT:
                    cell.setToolTip(text)
                self.table.setItem(r, c, cell)
        self.found_label.setText(f"{len(rows)} code(s)")
        self._update_buttons()

    def _on_found(self, entries: list):
        for e in entries:
            f = self.results.get(e["code"])
            if f is None:
                order, item = self.list_codes.get(e["code"], (0, None))
                f = Found(code=item.code if item else e["code"], name=item.name if item else "", order=order)
                self.results[e["code"]] = f
            f.add(e)
        self._refresh_table()

    def _copy(self):
        rows = self._rows()
        QGuiApplication.clipboard().setText(format_list(rows, self.wanted))
        self.statusBar().showMessage(f"Copied {len(rows)} code(s).", 5000)

    def _save(self):
        folder = self.settings.value("save_dir", str(default_browse_dir()))
        name = f"Reaction list {datetime.now():%d-%m-%Y}.txt"
        path, _ = QFileDialog.getSaveFileName(self, "Save list", str(Path(folder) / name), "Text files (*.txt)")
        if not path:
            return
        try:
            Path(path).write_text(format_list(self._rows(), self.wanted), encoding="utf-8-sig")
        except OSError as e:
            QMessageBox.warning(self, "Not saved", f"Could not save {path}:\n{e}")
            return
        self.settings.setValue("save_dir", str(Path(path).parent))
        self._log(f"Saved {len(self._rows())} code(s) to {path}")

    # ---- actions ---------------------------------------------------------------
    def _chat_filter(self) -> dict:
        text = self.chat_combo.currentText().strip()
        if not text or text.lower() == ALL_CHATS.lower():
            return {}
        if text.lower() == ALL_GROUPS.lower():
            return {"groups_only": True}
        idx = self.chat_combo.findText(text, Qt.MatchFixedString)
        if idx > 0 and self.chat_combo.itemData(idx):
            return {"id": self.chat_combo.itemData(idx), "title": text}
        return {"title": text}

    def _start(self):
        if self.job_running:
            return
        self._parse_list()
        _, label, emojis, _ = self._choice()
        self.wanted = emojis
        self.list_codes = {}
        for i, item in enumerate(self.items):
            self.list_codes.setdefault(normalize(item.code), (i, item))
        self.results = {}
        self._refresh_table()
        self.results_box.setTitle(f"Results  (reacted {label})")
        options = {
            "reactions": "".join(emojis),
            "lookback_days": self.lookback_spin.value(),
            "chat": self._chat_filter(),
        }
        rows = list(enumerate(self.items))
        self._save_settings()
        self.job_running = True
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        if self.worker and self.wa_state == "ready":
            self.worker.submit_job(rows, options)
        else:
            self.pending_job = (rows, options)
            self._log("Will start as soon as WhatsApp Web is connected.")
            if not self.worker:
                self._connect()
        self._update_buttons()

    def _stop(self):
        if self.pending_job:
            self.pending_job = None
            self._job_ended("Cancelled.")
        elif self.worker and self.job_running:
            self.worker.stop_job()
            self._log("Stopping after the current chat…")

    def _toggle_connection(self):
        if self.worker:
            self._set_state("loading", "Disconnecting…")
            self.worker.shutdown()
        else:
            self._connect()

    def _connect(self):
        self.worker = ReactionListWorker()
        self.worker.log.connect(self._log)
        self.worker.state_changed.connect(self._on_state)
        self.worker.chats_loaded.connect(self._on_chats)
        self.worker.found.connect(self._on_found)
        self.worker.progress.connect(self._on_progress)
        self.worker.activity.connect(lambda text: self.statusBar().showMessage(text))
        self.worker.job_finished.connect(self._job_ended)
        self.worker.finished.connect(self._on_worker_finished)
        self.worker.start()
        self._update_buttons()

    # ---- worker callbacks ------------------------------------------------------
    def _on_state(self, state: str, message: str):
        self._set_state(state, message)
        if state == "ready" and self.pending_job and self.worker:
            rows, opts = self.pending_job
            self.pending_job = None
            self.worker.submit_job(rows, opts)
        self._update_buttons()

    def _on_chats(self, chats: list):
        current = self.chat_combo.currentText()
        self.chat_combo.blockSignals(True)
        self.chat_combo.clear()
        self.chat_combo.addItems([ALL_CHATS, ALL_GROUPS])
        for c in chats:
            self.chat_combo.addItem(c["title"], c["id"])
        self.chat_combo.setCurrentText(current or ALL_CHATS)
        self.chat_combo.blockSignals(False)
        self._log(f"{len(chats)} chats loaded into 'Search in'.")

    def _on_progress(self, done: int, total: int):
        self.progress.setRange(0, max(total, 1))
        self.progress.setValue(done)

    def _job_ended(self, summary: str):
        self.job_running = False
        self.statusBar().showMessage(summary, 15000)
        self._update_buttons()

    def _on_worker_finished(self):
        w, self.worker = self.worker, None
        if w:
            w.deleteLater()
        if self.job_running:
            self.pending_job = None
            self._job_ended("WhatsApp was closed.")
        if self.wa_state != "error":
            self._set_state("idle", "Not connected")
        self._update_buttons()

    # ---- helpers ---------------------------------------------------------------
    def _set_state(self, state: str, message: str):
        self.wa_state = state
        color = STATE_COLORS.get(state, "#9aa0a6")
        self.state_dot.setStyleSheet(f"color: {color}; font-size: 14pt;")
        self.state_label.setText(message)
        self.state_label.setStyleSheet(f"color: {color if state in ('qr', 'error') else theme.TEXT};")

    def is_busy(self) -> bool:
        return self.job_running

    def _update_buttons(self):
        if getattr(self, "_shown_busy", None) != self.job_running:
            self._shown_busy = self.job_running
            self.busy_changed.emit(self.job_running)
        connected = self.worker is not None
        # disconnecting leaves the shared WhatsApp window open for the other tools
        self.connect_btn.setText("Disconnect" if connected else "Open WhatsApp")
        self.connect_btn.setEnabled(not self.job_running)
        self.start_btn.setEnabled(not self.job_running)
        self.stop_btn.setEnabled(self.job_running)
        self.chats_btn.setEnabled(connected and self.wa_state == "ready" and not self.job_running)
        self.list_edit.setReadOnly(self.job_running)
        for btn in self.choice_group.buttons():
            btn.setEnabled(not self.job_running)
        has_rows = bool(self.results)
        self.copy_btn.setEnabled(has_rows)
        self.save_btn.setEnabled(has_rows)

    def _log(self, text: str):
        stamp = time.strftime("%H:%M:%S")
        for line in str(text).rstrip().splitlines() or [""]:
            self.log_edit.appendPlainText(f"{stamp}  {line}")

    def closeEvent(self, event):
        self._save_settings()
        if self.worker:
            self.worker.shutdown()
            self.worker.wait(10_000)
        event.accept()
