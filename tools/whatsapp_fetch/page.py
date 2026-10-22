"""Fetch by Code: search WhatsApp Web for product codes and save their images.

The page has the look of core/look.py: a navy control card with the run's progress, counts and
buttons, the code list as chips, the settings as cards, and the results as a list of rows.
Fetch & Enhance (tools/fetch_enhance/page.py) is this page with more settings.

Settings are kept in data/fetch_by_code; WhatsApp is the window all tools share (core/whatsapp.py).
"""
from __future__ import annotations

import json
import os
import time
from collections import Counter
from pathlib import Path

from PySide6.QtCore import QEvent, QSettings, QStandardPaths, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QCompleter, QFileDialog, QHeaderView, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit, QSpinBox, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from core import look, theme
from core.look import KIND
from core.paths import data_dir
from core.ui import FlowLayout

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
# result filters: key, label, the row kinds it shows (None: every row)
FILTERS = [("all", "All", None), ("ok", "Saved", {"ok"}), ("working", "In progress", {"working", "queued"}),
           ("skipped", "Skipped", {"skipped"}), ("none", "No photos", {"none", "error"})]
DONE_KINDS = ("ok", "skipped", "none", "error")
STACK_BELOW = look.STACK_BELOW


def user_dir(location: QStandardPaths.StandardLocation, fallback: str) -> Path:
    return Path(QStandardPaths.writableLocation(location) or Path.home() / fallback)


def default_browse_dir() -> Path:
    return user_dir(QStandardPaths.DocumentsLocation, "Documents")


class MainWindow(QMainWindow):
    busy_changed = Signal(bool)
    START_TEXT = ("Fetch all", "Fetch selected")  # the control card's two start buttons

    def __init__(self, title: str, data_name: str):
        super().__init__()
        self.title = title
        self.tool_key = data_name  # the tool's key in tools/__init__.py
        self.data_dir = data_dir(data_name)
        self.setStyleSheet(look.STYLE)
        self.settings = QSettings(str(self.data_dir / "settings.ini"), QSettings.IniFormat)
        self.worker: WhatsAppWorker | None = None
        self.items: list[CodeItem] = []
        self.wa_state = "idle"
        self.wa_message = "Not connected"
        self.job_running = False
        self.pending_job: tuple[list, dict] | None = None
        self.working_row: int | None = None  # the code being fetched now, and its status
        self.working_text = ""
        self.activity = ""                   # what the worker does right now (its status bar line)
        self.code_view = "codes"             # the code list as chips, or as the text to edit
        self.filter = "all"
        self._parse_timer = QTimer(self, singleShot=True, interval=300)
        self._parse_timer.timeout.connect(self._parse_list)

        self._build_ui()
        self._load_settings()
        self._parse_list()
        self._set_state("idle", "Not connected")
        self._update_buttons()

    # ---- UI --------------------------------------------------------------------
    def _build_ui(self):
        self.statusBar().hide()  # its messages show in the control card
        self.statusBar().messageChanged.connect(self._on_activity)
        self.wa_pill = look.WaPill()
        self.connect_btn = look.button("Open WhatsApp", "outline", "message-circle", theme.NAVY)
        self.connect_btn.clicked.connect(self._toggle_connection)

        self.hero = look.Hero("codes", [("ok", "Saved", theme.LIVE), ("working", "In progress", theme.SKY),
                                        ("skipped", "Skipped", theme.ON_NAVY), ("none", "No photos", look.AMBER_ON_NAVY)])
        self.progress = self.hero.progress
        self.start_btn = look.button(self.START_TEXT[0], "heroStart", "play", theme.NAVY)
        self.start_btn.clicked.connect(lambda: self._start(selected_only=False))
        self.sel_btn = look.button(self.START_TEXT[1], "heroGhost", tip="Only the codes selected in the results")
        self.sel_btn.clicked.connect(lambda: self._start(selected_only=True))
        self.stop_btn = look.button("Stop", "heroOutline", "square", "#ffffff")
        self.stop_btn.clicked.connect(self._stop)
        out_open_btn = look.button("", "heroIcon", "folder-open", "#ffffff", "Open output folder")
        out_open_btn.clicked.connect(self._open_output)
        self.hero.add(self.start_btn, self.sel_btn, self.stop_btn, out_open_btn)

        self.code_card = self._build_codes()
        self.settings_card = self._build_settings()
        results = self._build_results()
        self.log_card = look.LogCard(QPlainTextEdit())
        self.log_edit = self.log_card.edit
        self.log_edit.setReadOnly(True)
        self.log_edit.setMaximumBlockCount(5000)

        self.look = look.ToolLayout(
            look.Header(self.tool_key, [self.wa_pill, self.connect_btn]), self.hero,
            [("codes", self.code_card), ("settings", self.settings_card)], [(results, 1), (self.log_card, 0)],
            [("codes", "Code list"), ("settings", "Settings"), ("results", "Results")], "results",
            on_mode=self._on_mode)
        self.setCentralWidget(self.look)

    def _build_codes(self) -> look.Card:
        self.count_label = QLabel()
        card = look.Card("list", theme.ACCENT_SOFT, theme.ACCENT, "Code list", self.count_label)
        self.code_seg = look.Segmented([("codes", "Codes"), ("text", "Text")])
        self.code_seg.changed.connect(self._set_code_view)
        card.head_right.addWidget(self.code_seg, 0, Qt.AlignTop)
        self.chips = look.CodeChips()
        self.list_edit = QPlainTextEdit(objectName="codes")
        self.list_edit.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.list_edit.setPlaceholderText(
            "Paste your list here (one per line), or open a .txt file:\n\n"
            "2-558\tRealme C85\t26/9\n2-560\tInfinix note 40 5g\t26/9\n3-621\tVivo x27 Pro\t26/9")
        self.list_edit.setMinimumHeight(170)
        self.list_edit.textChanged.connect(self._parse_timer.start)
        self.list_edit.installEventFilter(self)
        card.lay.addWidget(self.chips)
        card.lay.addWidget(self.list_edit)
        edit_btn = look.button("Edit list", "soft", "pencil")
        edit_btn.clicked.connect(lambda: self._set_code_view("text"))
        open_btn = look.button("Open .txt…", "soft", "file-text")
        open_btn.clicked.connect(self._open_txt)
        clear_btn = look.button("Clear", "plain")
        clear_btn.clicked.connect(self.list_edit.clear)
        card.lay.addLayout(look.row(edit_btn, open_btn, clear_btn, None))
        return card

    def _build_settings(self) -> look.Card:
        self.settings_summary = QLabel()
        card = look.Card("sliders-horizontal", theme.WARNING_SOFT, theme.WARNING, "Settings", self.settings_summary)

        self.out_edit = QLineEdit()
        out_btn = look.button("Change", "mini")
        out_btn.clicked.connect(self._pick_output)
        card.lay.addLayout(look.column(look.field("Save to"), look.path_box(self.out_edit, out_btn)))

        # Search in: all chats, all groups, the ticked chats, or one chat
        self.chat_combo = QComboBox()
        self.chat_combo.setEditable(True)
        self.chat_combo.setInsertPolicy(QComboBox.NoInsert)
        self.chat_combo.addItems([ALL_CHATS, ALL_GROUPS, PICKED])
        self.chat_combo.completer().setFilterMode(Qt.MatchContains)
        self.chat_combo.completer().setCompletionMode(QCompleter.PopupCompletion)
        self.chat_combo.setToolTip("Search all chats, only the chats you tick (Ticked chats), or one chat/group "
                                   "(type part of its name).")
        self.chat_combo.currentTextChanged.connect(self._show_picker)
        self.chat_combo.currentTextChanged.connect(lambda _: self._refresh_settings_summary())
        self.chats_btn = look.button("", "iconBtn", "refresh-cw", theme.TEXT_2, "Reload the chat list from WhatsApp")
        self.chats_btn.clicked.connect(lambda: self.worker and self.worker.refresh_chats())
        card.lay.addLayout(look.column(look.row(look.field("Search in"), None, self.chats_btn), self.chat_combo,
                                       self._build_picker()))

        self._extra_settings(card)

        # More options: how photos are taken, and how far back
        self.more_btn = look.DisclosureButton("More options")
        adv = QWidget()
        adv.hide()
        self.more_btn.toggled.connect(adv.setVisible)
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
        for spin in (self.gap_spin, self.lookback_spin):
            spin.valueChanged.connect(lambda _: self._refresh_settings_summary())
        self.date_check = QCheckBox("Add the date to names  (26/9 → 26-9)")
        self.date_check.toggled.connect(self._parse_timer.start)
        self.skip_check = QCheckBox("Skip codes that are already saved")
        al = QVBoxLayout(adv)
        al.setContentsMargins(0, 0, 0, 4)
        al.setSpacing(12)
        al.addLayout(look.column(look.field("Take"), self.mode_combo))
        al.addLayout(look.row(look.column(look.field("Album gap"), self.gap_spin),
                              look.column(look.field("Look back"), self.lookback_spin), spacing=10))
        al.addWidget(self.date_check)
        al.addWidget(self.skip_check)
        card.lay.addLayout(look.column(look.rule(), self.more_btn, adv, spacing=4))
        return card

    def _build_picker(self) -> QWidget:
        """Ticked chats: a searchable list of the chats with a tick box each."""
        self.picker = QWidget()
        self.pick_filter = QLineEdit(placeholderText="Search chats…")
        self.pick_filter.setClearButtonEnabled(True)
        self.pick_filter.textChanged.connect(self._filter_picker)
        self.pick_list = QListWidget()
        self.pick_list.setMinimumHeight(150)
        self.pick_list.itemChanged.connect(self._on_pick_changed)
        self.pick_count = QLabel(objectName="hint")
        untick_btn = look.button("Untick all", "plain")
        untick_btn.clicked.connect(self._untick_all)
        self.ticked_only_btn = look.button("Show ticked only", "plain")
        self.ticked_only_btn.setCheckable(True)
        self.ticked_only_btn.toggled.connect(self._filter_picker)
        flow = FlowLayout()
        for w in (self.pick_count, untick_btn, self.ticked_only_btn):
            flow.addWidget(w)
        lay = QVBoxLayout(self.picker)
        lay.setContentsMargins(0, 4, 0, 0)
        lay.addWidget(self.pick_filter)
        lay.addWidget(self.pick_list)
        lay.addLayout(flow)
        self.picker.setVisible(False)
        return self.picker

    def _build_results(self) -> look.Card:
        card = look.Card("image", theme.SUCCESS_SOFT, theme.SUCCESS, "Results", "Double-click a row to open its images")
        card.lay.setContentsMargins(18, 18, 18, 6)
        self.filters = look.FilterPills([(k, label) for k, label, _ in FILTERS])
        self.filters.changed.connect(self._set_filter)
        card.lay.addWidget(self.filters)
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.itemDoubleClicked.connect(self._open_row)
        look.rows_table(self.table, {0: "code", 1: "name", 2: "pill", 3: "status", 4: "path"}, COL_STATUS)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.Fixed)
        hdr.setSectionResizeMode(1, QHeaderView.Stretch)
        hdr.setSectionResizeMode(3, QHeaderView.Stretch)
        hdr.setSectionResizeMode(4, QHeaderView.Interactive)
        self.table.setColumnWidth(0, 136)
        self.table.setColumnWidth(4, 170)
        self.table.setColumnHidden(2, True)  # the date is on the code's chip
        card.lay.addWidget(self.table, 1)
        return card

    def _on_mode(self, mode: str):
        self.table.setColumnHidden(COL_PATH, mode != "wide")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.look.resize_to(event.size().width())

    # ---- what a tool built on this page adds ------------------------------------------
    def _extra_settings(self, card: look.Card):
        """More settings, shown on the Settings card before "More options"."""

    def _settings_summary(self) -> list[str]:
        chat = self.chat_combo.currentText().strip() or ALL_CHATS
        return [f"Search in {chat}"]

    def _hero_override(self) -> tuple[str, str] | None:
        """(title, line under it) for the control card while something else than fetching runs."""
        return None

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
        self._refresh_settings_summary()

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

    def _refresh_settings_summary(self):
        if hasattr(self, "more_btn"):
            self.settings_summary.setText(" · ".join(self._settings_summary()))
            self.more_btn.summary.setText(f"{self.gap_spin.value()} min gap · {self.lookback_spin.value()} days back")

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
            prev = self._existing(out_dir, it.base_name(include_date)) if out_dir.is_dir() else None
            if prev:
                self._set_row(r, "skipped", "Saved earlier", str(prev))
        self.chips.set_items(self.items)
        n = len(self.items)
        self.count_label.setText(f"{look.plain_count(n, 'code')} · {look.plain_count(self.chips.groups, 'list')}" if n
                                 else "Paste your list (one per line), or open a .txt file")
        self.progress.setRange(0, max(n, 1))
        self.progress.setValue(0)
        self.look.set_tab_label("codes", f"Code list  {n}" if n else "Code list")
        self._refresh_code_view()
        self._refresh_counts()
        self._update_buttons()

    def _set_row(self, row: int, kind: str, text: str, path: str):
        if row >= self.table.rowCount():
            return
        status = self.table.item(row, COL_STATUS)
        status.setText(text)
        status.setToolTip(text)
        kind = kind or ("queued" if text == "Queued" else "")
        status.setData(KIND, kind)
        if path or kind in ("working", "none", "error", ""):
            cell = self.table.item(row, COL_PATH)
            cell.setText(Path(path).name if path else "")
            cell.setToolTip(path)
            cell.setData(Qt.UserRole, path)
        if kind == "working":
            self.working_row, self.working_text = row, text
            self.table.scrollToItem(status)
        self._refresh_counts()
        self._filter_row(row)
        self._refresh_hero()

    def _open_row(self, item: QTableWidgetItem):
        path = self.table.item(item.row(), COL_PATH).data(Qt.UserRole)
        if path and Path(path).exists():
            os.startfile(path)  # noqa: S606 - open in Explorer / image viewer

    def eventFilter(self, obj, event):
        if obj is self.list_edit and event.type() == QEvent.FocusOut:
            QTimer.singleShot(0, self._refresh_code_view)
        return super().eventFilter(obj, event)

    def _set_code_view(self, view: str):
        self.code_view = view
        self._refresh_code_view()
        if view == "text":
            self.list_edit.setFocus()

    def _refresh_code_view(self):
        """Chips while there are codes and the text is not being edited; else the text."""
        chips = self.code_view == "codes" and bool(self.items) and not self.list_edit.hasFocus()
        self.chips.setVisible(chips)
        self.list_edit.setVisible(not chips)
        self.code_seg.set("codes" if chips else "text")

    # ---- results, counts and the control card ---------------------------------------------
    def _kinds(self) -> Counter:
        return Counter(self.table.item(r, COL_STATUS).data(KIND) or "" for r in range(self.table.rowCount())
                       if self.table.item(r, COL_STATUS) is not None)

    def _refresh_counts(self):
        n = self._kinds()
        for key, label, kinds in FILTERS:
            count = self.table.rowCount() if kinds is None else sum(n[k] for k in kinds)
            self.filters.set_count(key, count)
            if key in self.hero.counts:
                self.hero.counts[key].set_value(count)
        done = sum(n[k] for k in DONE_KINDS)
        self.look.set_tab_label("results", f"Results  {done}/{self.table.rowCount()}" if self.table.rowCount()
                                else "Results")

    def _set_filter(self, key: str):
        self.filter = key
        for r in range(self.table.rowCount()):
            self._filter_row(r)

    def _filter_row(self, row: int):
        kinds = next(f[2] for f in FILTERS if f[0] == self.filter)
        item = self.table.item(row, COL_STATUS)
        self.table.setRowHidden(row, kinds is not None and (item is None or item.data(KIND) not in kinds))

    def _on_activity(self, text: str):
        self.activity = text
        self._refresh_hero()

    def _refresh_hero(self):
        """The control card's words: what is happening, on which code, and what it needs next."""
        n = len(self.items)
        kicker, color = look.wa_kicker(self.wa_state, self.job_running)
        done = sum(self._kinds()[k] for k in DONE_KINDS)
        override = self._hero_override() if self.job_running else None
        if override:
            title, sub = override
        elif self.job_running and self.working_row is not None and self.working_row < n:
            it = self.items[self.working_row]
            title, sub = f"{it.code} {it.name}".strip(), self.working_text
        elif self.job_running:
            title, sub = "Starting…", self.activity or self.wa_message
        elif not n:
            title, sub = "Paste your code list", self.wa_message
        elif done:
            title, sub = f"{done} of {look.plain_count(n, 'code')} done", self.wa_message
        else:
            title, sub = f"{look.plain_count(n, 'code')} ready", self.wa_message
        self.hero.set_status(kicker, color, title, sub)
        self.hero.show_hint(not self.job_running and self.wa_state in ("idle", "closed"))

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
                QMessageBox.information(self, "Nothing selected", "Select one or more rows in the results first.")
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
            **self._extra_options(),
        }
        self._save_settings()
        self.job_running = True
        self.working_row, self.activity = None, ""
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
        self.worker = self._new_worker()
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

    # ---- what a tool built on this page changes ----------------------------------
    def _new_worker(self) -> WhatsAppWorker:
        return WhatsAppWorker("basic")

    def _extra_options(self) -> dict:
        return {}

    def _existing(self, out_dir: Path, base: str) -> Path | None:
        return existing_output(out_dir, base)

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
        self.wa_state, self.wa_message = state, message
        self.wa_pill.set_state(state, message)
        self._refresh_hero()

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


def make_by_code() -> MainWindow:
    return MainWindow("Fetch by Code", "fetch_by_code")
