"""Fetch by Date: save every photo sent in one WhatsApp chat between two dates and times.

Works in the shared WhatsApp window (core/whatsapp.py): opens the chat, opens its newest photo in
WhatsApp's own image viewer and steps back with Previous, clicking Download on each photo inside the
range, until it reaches one older than From. Photos are saved as
<folder>\\<chat>\\<YYYY-MM-DD>\\<date>_<time>_<hash>.jpg with a download_log.csv per chat; a photo
already saved in an earlier run is skipped (by its contents, recorded in <data>\\fetch_by_date).
Was the separate WhatsApp Image Fetcher app (wpimagefetch).

The page has the look of core/look.py: the run on a navy control card, the chat, dates and folder
as cards, and the log beside them.
"""
from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QDate, QSettings, QStandardPaths, Qt, QTime, Signal
from PySide6.QtWidgets import (
    QComboBox, QCompleter, QDateEdit, QFileDialog, QGridLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPlainTextEdit, QTimeEdit,
)

from core import look, theme
from core.paths import data_dir
from core.ui import FlowLayout

from .helpers import forget_history, sanitize_filename
from .worker import FetchByDateWorker

# the worker's counts, and how the control card shows them
COUNTS = (("found", "Found", theme.SKY), ("downloaded", "Saved", theme.LIVE),
          ("skipped", "Already saved", theme.ON_NAVY), ("failed", "Failed", look.RED_ON_NAVY))


def default_folder() -> Path:
    downloads = QStandardPaths.writableLocation(QStandardPaths.DownloadLocation)
    return Path(downloads or Path.home() / "Downloads") / "WhatsApp Images"


class FetchByDatePage(QMainWindow):
    busy_changed = Signal(bool)

    def __init__(self):
        super().__init__()
        self.data_dir = data_dir("fetch_by_date")
        self.manifest_dir = self.data_dir / "downloads_manifest"
        self.setStyleSheet(look.STYLE)
        self.settings = QSettings(str(self.data_dir / "settings.ini"), QSettings.IniFormat)
        self.worker: FetchByDateWorker | None = None
        self.wa_state = "idle"
        self.wa_message = "Not connected"
        self.job_running = False
        self.pending_job: tuple | None = None
        self.counts: dict = {}
        self.activity = ""
        self.last_summary = ""

        self._build_ui()
        self._load_settings()
        self._set_state("idle", "Not connected")
        self._update_buttons()

    # ---- UI --------------------------------------------------------------------
    def _build_ui(self):
        self.statusBar().hide()  # its messages show in the control card
        self.statusBar().messageChanged.connect(self._on_activity)
        self.wa_pill = look.WaPill()
        self.connect_btn = look.button("Open WhatsApp", "outline", "message-circle", theme.NAVY)
        self.connect_btn.clicked.connect(self._toggle_connection)

        self.hero = look.Hero("photos", [(k, label, color) for k, label, color in COUNTS], counts_always=True,
                              hint_text=look.WHATSAPP_HINT + " While a fetch runs, leave that window alone: "
                                                             "this tool is clicking in it.")
        self.progress = self.hero.progress
        self.start_btn = look.button("Start", "heroStart", "play", theme.NAVY)
        self.start_btn.clicked.connect(self._start)
        self.stop_btn = look.button("Stop", "heroOutline", "square", "#ffffff")
        self.stop_btn.clicked.connect(self._stop)
        folder_btn = look.button("", "heroIcon", "folder-open", "#ffffff", "Open the chat's folder")
        folder_btn.clicked.connect(self._open_folder)
        self.hero.add(self.start_btn, self.stop_btn, folder_btn)

        # the chat
        chat_card = look.Card("message-circle", theme.ACCENT_SOFT, theme.ACCENT, "Chat",
                              "The group or chat to save the photos of")
        self.chat_combo = QComboBox()
        self.chat_combo.setEditable(True)
        self.chat_combo.setInsertPolicy(QComboBox.NoInsert)
        self.chat_combo.lineEdit().setPlaceholderText("Group or chat name")
        self.chat_combo.completer().setFilterMode(Qt.MatchContains)
        self.chat_combo.completer().setCompletionMode(QCompleter.PopupCompletion)
        self.chat_combo.currentTextChanged.connect(lambda _: self._refresh_hero())
        self.chats_btn = look.button("", "iconBtn", "refresh-cw", theme.TEXT_2, "Reload the chat list from WhatsApp")
        self.chats_btn.clicked.connect(lambda: self.worker and self.worker.refresh_chats())
        chat_card.head_right.addWidget(self.chats_btn, 0, Qt.AlignTop)
        chat_card.lay.addWidget(self.chat_combo)
        chat_card.lay.addWidget(look.hint("The list has the chats WhatsApp shows at the top of its chat list. "
                                          "If yours is not there, type its exact name."))

        # the date and time range
        self.range_card = look.Card("calendar", theme.WARNING_SOFT, theme.WARNING, "Date and time")
        self.from_date = QDateEdit(calendarPopup=True, displayFormat="dd/MM/yyyy")
        self.from_time = QTimeEdit(displayFormat="HH:mm:ss")
        self.to_date = QDateEdit(calendarPopup=True, displayFormat="dd/MM/yyyy")
        self.to_time = QTimeEdit(displayFormat="HH:mm:ss")
        for w in (self.from_date, self.from_time, self.to_date, self.to_time):
            (w.dateChanged if isinstance(w, QDateEdit) else w.timeChanged).connect(lambda _: self._show_range())
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(10)
        grid.addWidget(look.field("From"), 0, 0)
        grid.addWidget(self.from_date, 0, 1)
        grid.addWidget(self.from_time, 0, 2)
        grid.addWidget(look.field("To"), 1, 0)
        grid.addWidget(self.to_date, 1, 1)
        grid.addWidget(self.to_time, 1, 2)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)
        quick = FlowLayout(hspace=6, vspace=6)
        for label, back, days in (("Today", 0, 1), ("Yesterday", 1, 1), ("Last 7 days", 6, 7), ("Last 30 days", 29, 30)):
            btn = look.button(label, "soft")
            btn.clicked.connect(lambda _=False, b=back, d=days: self._set_days(b, d))
            quick.addWidget(btn)
        self.range_card.lay.addLayout(grid)
        self.range_card.lay.addLayout(quick)
        self.range_card.lay.addWidget(look.hint("Both ends are included, to the second. The times start at 00:00:00 "
                                                "and 23:59:59, so leaving them gives whole days."))

        # where to save
        save_card = look.Card("folder", theme.SUCCESS_SOFT, theme.SUCCESS, "Save to",
                              "A folder per chat, a sub-folder per day")
        self.folder_edit = QLineEdit()
        browse_btn = look.button("Change", "mini")
        browse_btn.clicked.connect(self._browse)
        self.open_btn = look.button("Open", "mini", tip="Open the folder of the chosen chat (or the main folder)")
        self.open_btn.clicked.connect(self._open_folder)
        self.forget_btn = look.button("Forget saved photos…", "plain", "rotate-ccw", theme.TEXT_2)
        self.forget_btn.setToolTip("Forget which photos were already saved, so the next fetch saves them again.\n"
                                   "The photo files and the WhatsApp login are kept.")
        self.forget_btn.clicked.connect(self._forget)
        save_card.lay.addWidget(look.path_box(self.folder_edit, browse_btn, self.open_btn))
        save_card.lay.addWidget(look.hint("Each chat gets a folder with one sub-folder per day and a download_log.csv. "
                                          "Photos already in the folder are skipped; deleted ones are saved again."))
        save_card.lay.addLayout(look.row(None, self.forget_btn))

        self.log_card = look.LogCard(QPlainTextEdit(), height=220, grow=True)
        self.log_edit = self.log_card.edit
        self.log_edit.setReadOnly(True)
        self.log_edit.setMaximumBlockCount(20_000)  # long runs log a line per photo

        self.look = look.ToolLayout(
            look.Header("fetch_by_date", [self.wa_pill, self.connect_btn]), self.hero,
            [("setup", chat_card), ("setup", self.range_card), ("save", save_card)], [(self.log_card, 1)],
            [("setup", "Chat & dates"), ("save", "Save to"), ("log", "Log")], "log")
        self.setCentralWidget(self.look)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.look.resize_to(event.size().width())

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

    def _range_text(self) -> str:
        return (f"{self.from_date.date().toString('dd/MM/yyyy')} {self.from_time.time().toString('HH:mm')}  →  "
                f"{self.to_date.date().toString('dd/MM/yyyy')} {self.to_time.time().toString('HH:mm')}")

    def _show_range(self):
        self.range_card.set_subtitle(self._range_text())
        self._refresh_hero()

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
        self.job_running = True
        self.activity = self.last_summary = ""
        self._on_counts({})
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
        """The counts on the control card, and its ring: the photos dealt with of those found so far
        (going round while none is found yet)."""
        self.counts = dict(counts)
        for key, _, _ in COUNTS:
            self.hero.counts[key].set_value(counts.get(key, 0))
        found = counts.get("found", 0)
        done = sum(counts.get(k, 0) for k in ("downloaded", "skipped", "failed"))
        if self.job_running and not found:
            self.progress.setRange(0, 0)
        else:
            self.progress.setRange(0, max(found, 1))
            self.progress.setValue(min(done, max(found, 1)))
        self._refresh_hero()

    def _on_activity(self, text: str):
        self.activity = text
        self._refresh_hero()

    def _job_ended(self, success: bool, summary: str):
        if not self.job_running:
            return
        self.job_running = False
        self.last_summary = summary
        self._on_counts(self.counts)
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
        self.wa_state, self.wa_message = state, message
        self.wa_pill.set_state(state, message)
        self._refresh_hero()

    def _refresh_hero(self):
        if not hasattr(self, "hero"):
            return
        chat = self.chat_combo.currentText().strip()
        kicker, color = look.wa_kicker(self.wa_state, self.job_running)
        if self.job_running:
            title, sub = f"Fetching {chat}", self.activity or self._range_text()
        elif not chat:
            title, sub = "Pick a chat", self.wa_message
        else:
            title, sub = chat, self.last_summary or self._range_text()
        self.hero.set_status(kicker, color, title, sub)
        self.hero.show_hint(not self.job_running and self.wa_state in ("idle", "closed"))

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
        self._refresh_hero()

    def _log(self, text: str):
        stamp = time.strftime("%H:%M:%S")
        for line in str(text).rstrip().splitlines() or [""]:
            self.log_edit.appendPlainText(f"{stamp}  {line}")
        self.log_card.set_lines(self.log_edit.blockCount())

    def closeEvent(self, event):
        self._save_settings()
        if self.worker:
            self.worker.shutdown()
            self.worker.wait(10_000)
        event.accept()
