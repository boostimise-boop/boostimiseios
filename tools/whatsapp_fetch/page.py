"""Fetch by Code: search WhatsApp Web for product codes and save their images.

Settings are kept in data/fetch_by_code; WhatsApp is the window all tools share (core/whatsapp.py).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from PySide6.QtCore import QSettings, QStandardPaths, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QCompleter, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QSpinBox, QSplitter, QStyle, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from core import theme
from core.paths import data_dir
from core.ui import FlowLayout, scrollable

from .helpers import CodeItem, existing_output, parse_code_list
from .worker import WhatsAppWorker

ALL_CHATS = "All chats"
ALL_GROUPS = "All groups"
PICKED = "Ticked chats"  # the chats ticked in the list under "Search in"
PICK_ID = Qt.UserRole + 1  # chat id of a row in that list
COLS = ["Code", "Name", "Date", "Status", "Saved to"]
COL_STATUS, COL_PATH = 3, 4

NEIGHBOUR_MODES = [
    ("Captioned with the code, + uncaptioned ones sent right after", "after"),
    ("Only images whose caption contains the code", "none"),
    ("Captioned with the code, + uncaptioned ones sent right before", "before"),
    ("Captioned with the code, + uncaptioned ones before and after", "both"),
]

STATE_COLORS = {
    "idle": "#9aa0a6", "closed": "#9aa0a6", "starting": "#1a73e8", "loading": "#1a73e8",
    "qr": "#e37400", "ready": "#1e8e3e", "error": "#d93025",
}
ROW_COLORS = {  # kind: (text, background)
    "working": ("#1967d2", "#e8f0fe"),
    "ok": ("#137333", "#e6f4ea"),
    "none": ("#b06000", "#fef7e0"),
    "error": ("#c5221f", "#fce8e6"),
    "skipped": ("#5f6368", "#f1f3f4"),
}

# The look comes from core/theme.py; only what is special to this page is here.
STYLE = f"""
QLabel#hint {{ color: {theme.TEXT_2}; font-size: 9pt; }}
QLabel#count {{ background: {theme.ACCENT_SOFT}; color: {theme.ACCENT_DARK}; font-weight: 600; font-size: 9pt;
               border-radius: 6px; padding: 3px 9px; }}
QProgressBar {{ min-height: 20px; }}
"""
STACK_BELOW = 760  # page width (px) under which the results go below the settings instead of beside them


def user_dir(location: QStandardPaths.StandardLocation, fallback: str) -> Path:
    return Path(QStandardPaths.writableLocation(location) or Path.home() / fallback)


def default_browse_dir() -> Path:
    return user_dir(QStandardPaths.DocumentsLocation, "Documents")


class MainWindow(QMainWindow):
    busy_changed = Signal(bool)

    def __init__(self, title: str, data_name: str):
        super().__init__()
        self.title = title
        self.data_dir = data_dir(data_name)
        self.setStyleSheet(STYLE)
        self.settings = QSettings(str(self.data_dir / "settings.ini"), QSettings.IniFormat)
        self.worker: WhatsAppWorker | None = None
        self.items: list[CodeItem] = []
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
        # 1. code list
        list_box = QGroupBox("1  Code list")
        self.list_edit = QPlainTextEdit()
        self.list_edit.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.list_edit.setPlaceholderText(
            "Paste your list here (one per line), or open a .txt file:\n\n"
            "2-558\tRealme C85\t26/9\n2-560\tInfinix note 40 5g\t26/9\n3-621\tVivo x27 Pro\t26/9")
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

        # 2. settings
        set_box = QGroupBox("2  Settings")
        self.out_edit = QLineEdit()
        out_btn = QPushButton("Browse…")
        out_btn.clicked.connect(self._pick_output)
        out_row = QHBoxLayout()
        out_row.addWidget(self.out_edit, 1)
        out_row.addWidget(out_btn)

        self.chat_combo = QComboBox()
        self.chat_combo.setEditable(True)
        self.chat_combo.setInsertPolicy(QComboBox.NoInsert)
        self.chat_combo.addItems([ALL_CHATS, ALL_GROUPS, PICKED])
        self.chat_combo.completer().setFilterMode(Qt.MatchContains)
        self.chat_combo.completer().setCompletionMode(QCompleter.PopupCompletion)
        self.chat_combo.setToolTip("Search all chats, only the chats you tick (Ticked chats), or one chat/group "
                                   "(type part of its name).")
        self.chat_combo.currentTextChanged.connect(self._show_picker)
        self.chats_btn = QPushButton()
        self.chats_btn.setIcon(self.style().standardIcon(QStyle.SP_BrowserReload))
        self.chats_btn.setFixedWidth(36)
        self.chats_btn.setToolTip("Reload the chat list from WhatsApp")
        self.chats_btn.clicked.connect(lambda: self.worker and self.worker.refresh_chats())
        chat_row = QHBoxLayout()
        chat_row.addWidget(self.chat_combo, 1)
        chat_row.addWidget(self.chats_btn)

        # Ticked chats: a searchable list of the chats with a tick box each.
        self.picker = QWidget()
        self.pick_filter = QLineEdit(placeholderText="Search chats…")
        self.pick_filter.setClearButtonEnabled(True)
        self.pick_filter.textChanged.connect(self._filter_picker)
        self.pick_list = QListWidget()
        self.pick_list.setMinimumHeight(150)
        self.pick_list.itemChanged.connect(self._on_pick_changed)
        self.pick_count = QLabel(objectName="hint")
        untick_btn = QPushButton("Untick all")
        untick_btn.clicked.connect(self._untick_all)
        self.ticked_only_btn = QPushButton("Show ticked only", checkable=True)
        self.ticked_only_btn.toggled.connect(self._filter_picker)
        pick_row = FlowLayout()
        pick_row.addWidget(self.pick_count)
        pick_row.addWidget(untick_btn)
        pick_row.addWidget(self.ticked_only_btn)
        pick_lay = QVBoxLayout(self.picker)
        pick_lay.setContentsMargins(0, 0, 0, 0)
        pick_lay.addWidget(self.pick_filter)
        pick_lay.addWidget(self.pick_list)
        pick_lay.addLayout(pick_row)
        self.picker.setVisible(False)

        self.mode_combo = QComboBox()
        for label, key in NEIGHBOUR_MODES:
            self.mode_combo.addItem(label, key)
        self.mode_combo.setToolTip(
            "WhatsApp puts a caption on only one photo of an album. The 'sent right after' mode also takes the\n"
            "other photos the same person sent together with it (or right after a text message with the code).")
        self.gap_spin = QSpinBox()
        self.gap_spin.setRange(1, 240)
        self.gap_spin.setSuffix(" min")
        self.gap_spin.setToolTip("Uncaptioned photos only count if sent within this time of the previous one.")
        self.lookback_spin = QSpinBox()
        self.lookback_spin.setRange(1, 365)
        self.lookback_spin.setSuffix(" days")
        self.lookback_spin.setToolTip(
            "How far back to read each chat. It is extended automatically to a week\n"
            "before the oldest date in your list (e.g. 26/9 → from 19/9). A group whose codes\n"
            "have already moved past a code (4a-206, 4a-207… for 4a-205) is read further back\n"
            "for it, up to 30 days more.")
        self.date_check = QCheckBox("Add the date to names  (26/9 → 26-9)")
        self.date_check.toggled.connect(self._parse_timer.start)
        self.skip_check = QCheckBox("Skip codes that are already saved")

        form = QFormLayout(set_box)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.addRow("Save to", out_row)
        form.addRow("Search in", chat_row)
        form.addRow("", self.picker)
        form.addRow("Take", self.mode_combo)
        form.addRow("Album gap", self.gap_spin)
        form.addRow("Look back", self.lookback_spin)
        form.addRow("", self.date_check)
        form.addRow("", self.skip_check)

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
        self.start_btn = QPushButton("Fetch all")
        self.start_btn.setObjectName("primary")
        self.start_btn.clicked.connect(lambda: self._start(selected_only=False))
        self.sel_btn = QPushButton("Fetch selected")
        self.sel_btn.clicked.connect(lambda: self._start(selected_only=True))
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.clicked.connect(self._stop)
        btn_row = FlowLayout()  # wraps onto two lines in a narrow column
        for b in (self.connect_btn, self.start_btn, self.sel_btn, self.stop_btn):
            btn_row.addWidget(b)
        self.progress = QProgressBar()
        self.progress.setFormat("%v / %m")
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        out_open_btn = QPushButton("Open output folder")
        out_open_btn.clicked.connect(self._open_output)
        prog_row = QHBoxLayout()
        prog_row.addWidget(self.progress, 1)
        prog_row.addWidget(out_open_btn)
        hint = QLabel("WhatsApp opens in one Edge window that every tool shares. First time: scan the QR "
                      "code there (WhatsApp › Linked devices); the login is remembered for all tools.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        run_lay = QVBoxLayout(run_box)
        run_lay.addLayout(state_row)
        run_lay.addLayout(btn_row)
        run_lay.addLayout(prog_row)
        run_lay.addWidget(hint)

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 4, 0)
        lv.addWidget(list_box, 1)
        lv.addWidget(set_box)
        lv.addWidget(run_box)
        self.list_edit.setMinimumHeight(84)

        # results + log
        table_box = QGroupBox("Results  (double-click a row to open its images)")
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setDefaultSectionSize(28)
        self.table.itemDoubleClicked.connect(self._open_row)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.Interactive)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.Interactive)
        hdr.setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.setColumnWidth(1, 260)
        self.table.setColumnWidth(3, 230)
        QVBoxLayout(table_box).addWidget(self.table)

        log_box = QGroupBox("Log")
        self.log_edit = QPlainTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setMaximumBlockCount(5000)
        self.log_edit.setFont(QFont("Consolas", 9))
        QVBoxLayout(log_box).addWidget(self.log_edit)

        self.right_split = QSplitter(Qt.Vertical)
        self.right_split.addWidget(table_box)
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
        s.remove("list_text")  # the pasted codes are not kept: every start is fresh (core/shell.py)
        self.out_edit.setText(s.value("output_dir", str(self.default_output_dir())))
        try:  # shown before WhatsApp is connected; the full chat list replaces it then
            picked = [c for c in json.loads(s.value("picked_chats", "[]")) if c.get("id")]
        except (ValueError, TypeError, AttributeError):
            picked = []
        self._set_pick_rows(picked, {c["id"] for c in picked})
        self.chat_combo.setCurrentText(s.value("chat", ALL_CHATS))
        self._show_picker(self.chat_combo.currentText())
        idx = self.mode_combo.findData(s.value("mode", "after"))
        self.mode_combo.setCurrentIndex(max(idx, 0))
        self.gap_spin.setValue(int(s.value("gap_minutes", 5)))
        self.lookback_spin.setValue(int(s.value("lookback_days", 10)))
        self.date_check.setChecked(s.value("include_date", False, type=bool))
        self.skip_check.setChecked(s.value("skip_existing", True, type=bool))

    def _save_settings(self):
        s = self.settings
        s.setValue("output_dir", self.out_edit.text().strip())
        s.setValue("chat", self.chat_combo.currentText())
        s.setValue("picked_chats", json.dumps(self._picked(), ensure_ascii=False))
        s.setValue("mode", self.mode_combo.currentData())
        s.setValue("gap_minutes", self.gap_spin.value())
        s.setValue("lookback_days", self.lookback_spin.value())
        s.setValue("include_date", self.date_check.isChecked())
        s.setValue("skip_existing", self.skip_check.isChecked())
        s.sync()

    def default_output_dir(self) -> Path:
        return user_dir(QStandardPaths.PicturesLocation, "Pictures") / self.title

    # ---- code list / table -----------------------------------------------------
    def _open_txt(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open code list", str(default_browse_dir()),"Text files (*.txt *.tsv *.csv);;All files (*)")
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
        out_dir = Path(self.out_edit.text().strip() or ".")
        include_date = self.date_check.isChecked()
        self.table.setRowCount(len(self.items))
        for r, it in enumerate(self.items):
            for c, text in enumerate([it.code, it.name, it.date, "", ""]):
                self.table.setItem(r, c, QTableWidgetItem(text))
            prev = existing_output(out_dir, it.base_name(include_date)) if out_dir.is_dir() else None
            if prev:
                self._set_row(r, "skipped", "Saved earlier", str(prev))
        self.count_label.setText(f"{len(self.items)} code(s)")
        self._update_buttons()

    def _set_row(self, row: int, kind: str, text: str, path: str):
        if row >= self.table.rowCount():
            return
        status = self.table.item(row, COL_STATUS)
        status.setText(text)
        status.setToolTip(text)
        fg, bg = ROW_COLORS.get(kind, ("#202124", "#ffffff"))
        status.setForeground(QColor(fg))
        status.setBackground(QColor(bg))
        if path or kind in ("working", "none", "error", ""):
            cell = self.table.item(row, COL_PATH)
            cell.setText(Path(path).name if path else "")
            cell.setToolTip(path)
            cell.setData(Qt.UserRole, path)
        if kind == "working":
            self.table.scrollToItem(status)

    def _open_row(self, item: QTableWidgetItem):
        path = self.table.item(item.row(), COL_PATH).data(Qt.UserRole)
        if path and Path(path).exists():
            os.startfile(path)  # noqa: S606 - open in Explorer / image viewer

    # ---- actions ---------------------------------------------------------------
    def _pick_output(self):
        path = QFileDialog.getExistingDirectory(self, "Save images to", self.out_edit.text() or str(self.default_output_dir()))
        if path:
            self.out_edit.setText(path)
            self._parse_list()

    def _open_output(self):
        out = Path(self.out_edit.text().strip() or self.default_output_dir())
        out.mkdir(parents=True, exist_ok=True)
        os.startfile(out)  # noqa: S606

    # ---- ticked chats ------------------------------------------------------------
    def _set_pick_rows(self, chats: list[dict], ticked: set[str]):
        """Fill the list with `chats` ({id, title}), ticked ones first."""
        self.pick_list.blockSignals(True)
        self.pick_list.clear()
        for c in sorted(chats, key=lambda c: c["id"] not in ticked):  # stable: keeps WhatsApp's order
            it = QListWidgetItem(c.get("title") or c["id"])
            it.setData(PICK_ID, c["id"])
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if c["id"] in ticked else Qt.Unchecked)
            self.pick_list.addItem(it)
        self.pick_list.blockSignals(False)
        self._on_pick_changed()

    def _picked(self) -> list[dict]:
        items = (self.pick_list.item(i) for i in range(self.pick_list.count()))
        return [{"id": it.data(PICK_ID), "title": it.text()} for it in items if it.checkState() == Qt.Checked]

    def _on_pick_changed(self, *_):
        n = len(self._picked())
        self.pick_count.setText(f"{n} chat(s) ticked" if n else "Tick the chats to search in")
        if self.ticked_only_btn.isChecked():
            self._filter_picker()

    def _filter_picker(self, *_):
        text = self.pick_filter.text().strip().lower()
        ticked_only = self.ticked_only_btn.isChecked()
        for i in range(self.pick_list.count()):
            it = self.pick_list.item(i)
            it.setHidden(bool(text and text not in it.text().lower())
                         or (ticked_only and it.checkState() != Qt.Checked))

    def _untick_all(self):
        self.pick_list.blockSignals(True)
        for i in range(self.pick_list.count()):
            self.pick_list.item(i).setCheckState(Qt.Unchecked)
        self.pick_list.blockSignals(False)
        self._on_pick_changed()

    def _show_picker(self, text: str):
        self.picker.setVisible(text.strip().lower() == PICKED.lower())

    def _chat_filter(self) -> dict:
        text = self.chat_combo.currentText().strip()
        if not text or text.lower() == ALL_CHATS.lower():
            return {}
        if text.lower() == ALL_GROUPS.lower():
            return {"groups_only": True}
        if text.lower() == PICKED.lower():
            return {"ids": [c["id"] for c in self._picked()]}
        idx = self.chat_combo.findText(text, Qt.MatchFixedString)
        if idx >= 0 and self.chat_combo.itemData(idx):
            return {"id": self.chat_combo.itemData(idx), "title": text}
        return {"title": text}

    def _start(self, selected_only: bool):
        if self.job_running:
            return
        self._parse_list()
        if not self.items:
            QMessageBox.information(self, "No codes", "Paste your code list or open a .txt file first.")
            return
        out = self.out_edit.text().strip()
        if not out:
            QMessageBox.information(self, "Output folder", "Choose a folder to save the images in.")
            return
        chat = self._chat_filter()
        if "ids" in chat and not chat["ids"]:
            QMessageBox.information(self, "No chats ticked", "Tick the chats to search in, under 'Search in'"
                                    + ("." if self.pick_list.count() else " (Open WhatsApp first to list them)."))
            return
        if selected_only:
            rows = sorted({i.row() for i in self.table.selectionModel().selectedRows()})
            if not rows:
                QMessageBox.information(self, "Nothing selected", "Select one or more rows in the table first.")
                return
        else:
            rows = list(range(len(self.items)))
        job_rows = [(r, self.items[r]) for r in rows]
        for r, _ in job_rows:
            self._set_row(r, "", "Queued", "")
        options = {
            "output_dir": out,
            "include_date": self.date_check.isChecked(),
            "skip_existing": self.skip_check.isChecked(),
            "neighbors": self.mode_combo.currentData(),
            "gap_minutes": self.gap_spin.value(),
            "lookback_days": self.lookback_spin.value(),
            "chat": chat,
        }
        self._save_settings()
        self.job_running = True
        self.progress.setRange(0, len(job_rows))
        self.progress.setValue(0)
        if self.worker and self.wa_state == "ready":
            self.worker.submit_job(job_rows, options)
        else:
            self.pending_job = (job_rows, options)
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
            self._log("Stopping after the current code…")

    def _toggle_connection(self):
        if self.worker:
            self._set_state("loading", "Disconnecting…")
            self.worker.shutdown()
        else:
            self._connect()

    def _connect(self):
        self.worker = WhatsAppWorker("basic")
        self.worker.log.connect(self._log)
        self.worker.state_changed.connect(self._on_state)
        self.worker.chats_loaded.connect(self._on_chats)
        self.worker.row_status.connect(self._set_row)
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
        self.chat_combo.addItems([ALL_CHATS, ALL_GROUPS, PICKED])
        for c in chats:
            self.chat_combo.addItem(c["title"], c["id"])
        self.chat_combo.setCurrentText(current or ALL_CHATS)
        self.chat_combo.blockSignals(False)
        picked = self._picked()
        ids = {c["id"] for c in chats}
        # a ticked chat WhatsApp did not list (left, or not synced yet) stays ticked at the top
        self._set_pick_rows([c for c in picked if c["id"] not in ids] + chats, {c["id"] for c in picked})
        self._filter_picker()
        self._log(f"{len(chats)} chats loaded into 'Search in'.")

    def _on_progress(self, done: int, total: int):
        self.progress.setRange(0, max(total, 1))
        self.progress.setValue(done)

    def _job_ended(self, summary: str):
        self.job_running = False
        for r in range(self.table.rowCount()):
            if self.table.item(r, COL_STATUS).text() == "Queued":
                self._set_row(r, "", "", "")
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
        can_start = not self.job_running and bool(self.items)
        self.start_btn.setEnabled(can_start)
        self.sel_btn.setEnabled(can_start)
        self.stop_btn.setEnabled(self.job_running)
        self.chats_btn.setEnabled(connected and self.wa_state == "ready" and not self.job_running)
        self.list_edit.setReadOnly(self.job_running)
        self.picker.setEnabled(not self.job_running)

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


def make_by_code() -> MainWindow:
    return MainWindow("Fetch by Code", "fetch_by_code")
