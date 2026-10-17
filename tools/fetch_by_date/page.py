"""Fetch by Date: save every photo sent in one WhatsApp chat between two dates and times.

Works in the shared WhatsApp window (core/whatsapp.py): opens the chat, opens its newest photo in
WhatsApp's own image viewer and steps back with Previous, clicking Download on each photo inside the
range, until it reaches one older than From. Photos are saved as
<folder>\\<chat>\\<YYYY-MM-DD>\\<date>_<time>_<hash>.jpg with a download_log.csv per chat; a photo
already saved in an earlier run is skipped (by its contents, recorded in <data>\\fetch_by_date).
Was the separate WhatsApp Image Fetcher app (wpimagefetch).
"""
from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QDate, QSettings, QStandardPaths, Qt, QTime, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox, QCompleter, QDateEdit, QFileDialog, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSplitter, QStyle,
    QTimeEdit, QVBoxLayout, QWidget,
)

from core import theme
from core.paths import data_dir
from core.ui import FlowLayout, scrollable
from tools.whatsapp_fetch.page import STACK_BELOW, STATE_COLORS

from .helpers import forget_history, sanitize_filename
from .worker import FetchByDateWorker

STYLE = f"""
QLabel#hint {{ color: {theme.TEXT_2}; font-size: 9pt; }}
QLabel#count {{ background: {theme.ACCENT_SOFT}; color: {theme.ACCENT_DARK}; font-weight: 600; font-size: 9pt;
               border-radius: 6px; padding: 3px 9px; }}
QProgressBar {{ min-height: 20px; }}
"""
COUNTS = (("found", "Found"), ("downloaded", "Saved"), ("skipped", "Already saved"), ("failed", "Failed"))


def default_folder() -> Path:
    downloads = QStandardPaths.writableLocation(QStandardPaths.DownloadLocation)
    return Path(downloads or Path.home() / "Downloads") / "WhatsApp Images"


class FetchByDatePage(QMainWindow):
    busy_changed = Signal(bool)

    def __init__(self):
        super().__init__()
        self.data_dir = data_dir("fetch_by_date")
        self.manifest_dir = self.data_dir / "downloads_manifest"
        self.setStyleSheet(STYLE)
        self.settings = QSettings(str(self.data_dir / "settings.ini"), QSettings.IniFormat)
        self.worker: FetchByDateWorker | None = None
        self.wa_state = "idle"
        self.job_running = False
        self.pending_job: tuple | None = None

        self._build_ui()
        self._load_settings()
        self._set_state("idle", "Not connected")
        self._update_buttons()

    # ---- UI --------------------------------------------------------------------
    def _build_ui(self):
        # 1. chat
        chat_box = QGroupBox("1  Chat")
        self.chat_combo = QComboBox()
        self.chat_combo.setEditable(True)
        self.chat_combo.setInsertPolicy(QComboBox.NoInsert)
        self.chat_combo.lineEdit().setPlaceholderText("Group or chat name")
        self.chat_combo.completer().setFilterMode(Qt.MatchContains)
        self.chat_combo.completer().setCompletionMode(QCompleter.PopupCompletion)
        self.chats_btn = QPushButton()
        self.chats_btn.setIcon(self.style().standardIcon(QStyle.SP_BrowserReload))
        self.chats_btn.setFixedWidth(36)
        self.chats_btn.setToolTip("Reload the chat list from WhatsApp")
        self.chats_btn.clicked.connect(lambda: self.worker and self.worker.refresh_chats())
        chat_row = QHBoxLayout()
        chat_row.addWidget(self.chat_combo, 1)
        chat_row.addWidget(self.chats_btn)
        chat_hint = QLabel("The list has the chats WhatsApp shows at the top of its chat list. If yours is not "
                           "there, type its exact name.")
        chat_hint.setObjectName("hint")
        chat_hint.setWordWrap(True)
        chat_lay = QVBoxLayout(chat_box)
        chat_lay.addLayout(chat_row)
        chat_lay.addWidget(chat_hint)

        # 2. date and time range
        range_box = QGroupBox("2  Date and time")
        self.from_date = QDateEdit(calendarPopup=True, displayFormat="dd/MM/yyyy")
        self.from_time = QTimeEdit(displayFormat="HH:mm:ss")
        self.to_date = QDateEdit(calendarPopup=True, displayFormat="dd/MM/yyyy")
        self.to_time = QTimeEdit(displayFormat="HH:mm:ss")
        grid = QGridLayout()
        grid.addWidget(QLabel("From"), 0, 0)
        grid.addWidget(self.from_date, 0, 1)
        grid.addWidget(self.from_time, 0, 2)
        grid.addWidget(QLabel("To"), 1, 0)
        grid.addWidget(self.to_date, 1, 1)
        grid.addWidget(self.to_time, 1, 2)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)
        quick = FlowLayout()
        for label, back, days in (("Today", 0, 1), ("Yesterday", 1, 1), ("Last 7 days", 6, 7), ("Last 30 days", 29, 30)):
            btn = QPushButton(label)
            btn.clicked.connect(lambda _=False, b=back, d=days: self._set_days(b, d))
            quick.addWidget(btn)
        range_hint = QLabel("Both ends are included, to the second. The times start at 00:00:00 and "
                            "23:59:59, so leaving them gives whole days.")
        range_hint.setObjectName("hint")
        range_hint.setWordWrap(True)
        range_lay = QVBoxLayout(range_box)
        range_lay.addLayout(grid)
        range_lay.addLayout(quick)
        range_lay.addWidget(range_hint)

        # 3. where to save
        save_box = QGroupBox("3  Save to")
        self.folder_edit = QLineEdit()
        browse_btn = QPushButton("Change…")
        browse_btn.clicked.connect(self._browse)
        self.open_btn = QPushButton("Open")
        self.open_btn.setToolTip("Open the folder of the chosen chat (or the main folder)")
        self.open_btn.clicked.connect(self._open_folder)
        folder_row = QHBoxLayout()
        folder_row.addWidget(self.folder_edit, 1)
        folder_row.addWidget(browse_btn)
        folder_row.addWidget(self.open_btn)
        save_hint = QLabel("Each chat gets a folder with one sub-folder per day and a download_log.csv. "
                           "Photos already in the folder are skipped; deleted ones are saved again.")
        save_hint.setObjectName("hint")
        save_hint.setWordWrap(True)
        self.forget_btn = QPushButton("Forget saved photos…")
        self.forget_btn.setToolTip("Forget which photos were already saved, so the next fetch saves them again.\n"
                                   "The photo files and the WhatsApp login are kept.")
        self.forget_btn.clicked.connect(self._forget)
        forget_row = QHBoxLayout()
        forget_row.addStretch()
        forget_row.addWidget(self.forget_btn)
        save_lay = QVBoxLayout(save_box)
        save_lay.addLayout(folder_row)
        save_lay.addWidget(save_hint)
        save_lay.addLayout(forget_row)

        # 4. run
        run_box = QGroupBox("4  Run")
        self.state_dot = QLabel("●")
        self.state_label = QLabel()
        self.state_label.setWordWrap(True)
        state_row = QHBoxLayout()
        state_row.addWidget(self.state_dot)
        state_row.addWidget(self.state_label, 1)
        self.connect_btn = QPushButton("Open WhatsApp")
        self.connect_btn.clicked.connect(self._toggle_connection)
        self.start_btn = QPushButton("Start")
        self.start_btn.setObjectName("primary")
        self.start_btn.clicked.connect(self._start)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.clicked.connect(self._stop)
        btn_row = FlowLayout()  # wraps onto two lines in a narrow column
        for b in (self.connect_btn, self.start_btn, self.stop_btn):
            btn_row.addWidget(b)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.count_labels = {}
        count_row = FlowLayout()
        for key, _ in COUNTS:
            self.count_labels[key] = QLabel(objectName="count")
            count_row.addWidget(self.count_labels[key])
        self._on_counts({})
        wa_hint = QLabel("WhatsApp opens in one Edge window that every tool shares; scan its QR code once. "
                         "While a fetch runs, leave that window alone: this tool is clicking in it.")
        wa_hint.setObjectName("hint")
        wa_hint.setWordWrap(True)
        run_lay = QVBoxLayout(run_box)
        run_lay.addLayout(state_row)
        run_lay.addLayout(btn_row)
        run_lay.addWidget(self.progress)
        run_lay.addLayout(count_row)
        run_lay.addWidget(wa_hint)

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 4, 0)
        for box in (chat_box, range_box, save_box, run_box):
            lv.addWidget(box)
        lv.addStretch()

        log_box = QGroupBox("Log")
        self.log_edit = QPlainTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setMaximumBlockCount(20_000)  # long runs log a line per photo
        self.log_edit.setFont(QFont("Consolas", 9))
        QVBoxLayout(log_box).addWidget(self.log_edit)
        log_box.setMinimumSize(260, 220)

        # The settings column scrolls when the window is too short for it (a 768 px laptop screen).
        self.main_split = QSplitter(Qt.Horizontal, childrenCollapsible=False)
        self.main_split.addWidget(scrollable(left))
        self.main_split.addWidget(log_box)
        self.main_split.setStretchFactor(1, 1)
        self.main_split.setSizes([480, 840])

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
        self.chat_combo.setCurrentText(s.value("chat", ""))
        self.folder_edit.setText(s.value("folder", str(default_folder())))
        self._set_days(29, 30)  # every start is fresh: the last 30 days

    def _save_settings(self):
        s = self.settings
        s.setValue("chat", self.chat_combo.currentText().strip())
        s.setValue("folder", self.folder_edit.text().strip())
        s.sync()

    def _set_days(self, back: int, days: int):
        """From `back` days ago at 00:00:00 to the end of the `days`-th day."""
        first = QDate.currentDate().addDays(-back)
        self.from_date.setDate(first)
        self.from_time.setTime(QTime(0, 0, 0))
        self.to_date.setDate(first.addDays(days - 1))
        self.to_time.setTime(QTime(23, 59, 59))

    def _folder(self) -> Path:
        return Path(self.folder_edit.text().strip() or default_folder())

    def _browse(self):
        path = QFileDialog.getExistingDirectory(self, "Save the photos in", str(self._folder()))
        if path:
            self.folder_edit.setText(str(Path(path)))
            self._save_settings()

    def _open_folder(self):
        chat = self.chat_combo.currentText().strip()
        folder = self._folder() / sanitize_filename(chat) if chat else self._folder()
        if not folder.is_dir():
            folder = self._folder()
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(str(folder))  # noqa: S606 - Windows-only

    def _forget(self):
        if QMessageBox.question(
                self, "Forget saved photos",
                "Forget which photos were already saved (every chat)?\n\n"
                "A repeat fetch then saves them again, also the ones still in the folder. "
                "The photo files and the WhatsApp login are kept.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        n = forget_history(self.manifest_dir)
        self._log(f"Forgot the saved photos of {n} chat(s).")

    # ---- actions ---------------------------------------------------------------
    def _start(self):
        if self.job_running:
            return
        chat = self.chat_combo.currentText().strip()
        if not chat:
            QMessageBox.warning(self, "No chat", "Pick or type the group or chat to fetch from first.")
            return
        from_dt = datetime.combine(self.from_date.date().toPython(), self.from_time.time().toPython())
        to_dt = datetime.combine(self.to_date.date().toPython(), self.to_time.time().toPython())
        if from_dt > to_dt:
            QMessageBox.warning(self, "Dates", "'From' must not be after 'To'.")
            return
        folder = self._folder()
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            QMessageBox.warning(self, "Save to", f"Can't use the folder {folder}:\n{e}")
            return
        self._save_settings()
        self._on_counts({})
        self.job_running = True
        self.progress.setRange(0, 0)  # the number of photos isn't known up front
        self._log(f"Fetching '{chat}' from {from_dt:%d/%m/%Y %H:%M:%S} to {to_dt:%d/%m/%Y %H:%M:%S}…")
        job = (chat, from_dt, to_dt, folder)
        if self.worker and self.wa_state == "ready":
            self.worker.submit_job(*job)
        else:
            self.pending_job = job
            self._log("Will start as soon as WhatsApp Web is connected.")
            if not self.worker:
                self._connect()
        self._update_buttons()

    def _stop(self):
        if self.pending_job:
            self.pending_job = None
            self._job_ended(False, "Cancelled.")
        elif self.worker and self.job_running:
            self.worker.stop_job()
            self._log("Stopping… (photos already saved stay saved)")
            self.stop_btn.setEnabled(False)

    def _toggle_connection(self):
        if self.worker:
            self._set_state("loading", "Disconnecting…")
            self.worker.shutdown()
        else:
            self._connect()

    def _connect(self):
        self.worker = FetchByDateWorker(self.manifest_dir)
        self.worker.log.connect(self._log)
        self.worker.state_changed.connect(self._on_state)
        self.worker.chats_loaded.connect(self._on_chats)
        self.worker.counts_changed.connect(self._on_counts)
        self.worker.activity.connect(lambda text: self.statusBar().showMessage(text))
        self.worker.job_finished.connect(self._job_ended)
        self.worker.finished.connect(self._on_worker_finished)
        self.worker.start()
        self._update_buttons()

    # ---- worker callbacks ------------------------------------------------------
    def _on_state(self, state: str, message: str):
        self._set_state(state, message)
        if state == "ready" and self.pending_job and self.worker:
            job, self.pending_job = self.pending_job, None
            self.worker.submit_job(*job)
        self._update_buttons()

    def _on_chats(self, chats: list):
        current = self.chat_combo.currentText()
        self.chat_combo.blockSignals(True)
        self.chat_combo.clear()
        self.chat_combo.addItems(chats)
        self.chat_combo.setCurrentText(current)
        self.chat_combo.blockSignals(False)
        self._log(f"{len(chats)} chats loaded into the list.")

    def _on_counts(self, counts: dict):
        for key, label in COUNTS:
            self.count_labels[key].setText(f"{label}: {counts.get(key, 0)}")

    def _job_ended(self, success: bool, summary: str):
        if not self.job_running:
            return
        self.job_running = False
        self.progress.setRange(0, 1)
        self.progress.setValue(1 if success else 0)
        self._log(summary)
        self.statusBar().showMessage(summary, 15000)
        self._update_buttons()

    def _on_worker_finished(self):
        w, self.worker = self.worker, None
        if w:
            w.deleteLater()
        if self.job_running:
            self.pending_job = None
            self._job_ended(False, "WhatsApp was closed.")
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
        running = self.job_running
        connected = self.worker is not None
        # disconnecting leaves the shared WhatsApp window open for the other tools
        self.connect_btn.setText("Disconnect" if connected else "Open WhatsApp")
        self.connect_btn.setEnabled(not running)
        self.start_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)
        self.chats_btn.setEnabled(connected and self.wa_state == "ready" and not running)
        self.forget_btn.setEnabled(not running)
        for w in (self.chat_combo, self.from_date, self.from_time, self.to_date, self.to_time, self.folder_edit):
            w.setEnabled(not running)

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
