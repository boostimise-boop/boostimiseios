"""Rename by Match (PhotoMatch Renamer): finds each product code in its WhatsApp chat,
compares the photos posted there with the photos in the day folder and renames the
matching photo to "6-852 Nubia a56.jpg". The work itself is in engine.py.
"""
from __future__ import annotations

import json
import os
import queue
from datetime import datetime

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QTextBlockFormat, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QBoxLayout, QButtonGroup, QCheckBox, QComboBox, QFileDialog, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QProgressBar, QPushButton, QRadioButton, QScrollArea, QSizePolicy, QSpinBox,
    QTextEdit, QVBoxLayout, QWidget,
)

from core import theme

from .engine import APP_TITLE, DATA_DIR, LOG_DIR, SETTINGS_FILE, Worker, list_folder, list_groups, parse_products

HELP = [
    "How to use",
    "1. Choose the product list (.txt) and the day folder with the photos (e.g. 24.09.26). Each list "
    "uses its own folder in it: the 6- list the folder \"6-\", the 5a- list the folder \"5a-\" and so on.",
    "2. Press \"Open WhatsApp\" (the first time, scan the QR code with your phone). It looks for the chat "
    "of each list, e.g. \"Customise Phone Case (6-)\" for the 6- list. Check the chats, or choose / type "
    "them yourself, and untick the lists you don't want now.",
    "3. Press \"Start\". Lists are done from the bottom of the file upward (6-, then 5a-, 5- …). Each "
    "list's chat is searched from the newest message up to the first code of that list (e.g. 6-830), the "
    "photos are compared with the list's folder and the matching photo is renamed, e.g. "
    "\"6-852 Nubia a56.jpg\". Keep the WhatsApp window open while it works.",
    "Tip: tick \"Preview only\" the first time to see what would happen without renaming anything.",
]
LOG_COLORS = {"ok": "#15803d", "warn": "#b45309", "err": "#c0392b", "dim": theme.TEXT_2}
ACCENT = theme.ACCENT

# The look comes from core/theme.py; only what is special to this page is here.
STYLE = f"""
QWidget#renamer {{ background: {theme.BG}; }}
QScrollArea, QWidget#settings {{ background: transparent; border: none; }}
QProgressBar {{ min-height: 8px; max-height: 8px; border-radius: 4px; }}
QProgressBar::chunk {{ border-radius: 4px; }}
QLabel#step {{ font-weight: 600; color: {theme.TEXT}; }}
QLabel#hint {{ color: {theme.TEXT_2}; }}
QLabel#warn {{ color: {theme.WARNING}; }}
"""


def load_settings():
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


class RenamerPage(QWidget):
    busy_changed = Signal(bool)

    def __init__(self):
        super().__init__(objectName="renamer")
        self.setAttribute(Qt.WA_StyledBackground)
        self.setStyleSheet(STYLE)
        self.ui = queue.Queue()
        self.worker = Worker(self.ui)
        self.worker.start()
        self.busy = False

        s = load_settings()
        saved = s.get("lists")
        self.saved_lists = saved if isinstance(saved, dict) else {}
        self.chat_names = list(s.get("chat_names", []))
        self.list_vars = {}      # group -> {"on": bool, "chat": str}
        self.list_boxes = []
        self.groups = []         # groups of the current file, bottom list first
        self.groups_data = {}
        self.mode = None         # "wide", "medium" or "narrow" (page width)

        self._build()
        self.txt_edit.setText(s.get("txt", ""))
        self.folder_edit.setText(s.get("folder", ""))
        self.rec_check.setChecked(s.get("recursive", False))
        self.threshold_spin.setValue(int(s.get("threshold", 10)))
        self.max_spin.setValue(int(s.get("max_messages", 3000)))
        self.neighbors_check.setChecked(s.get("neighbors", True))
        (self.multi_folder if s.get("multi", "folder") == "folder" else self.multi_number).setChecked(True)
        self.note_check.setChecked(s.get("add_note", False))
        self.dry_check.setChecked(s.get("dry_run", False))
        self.txt_edit.textChanged.connect(lambda _: self._show_codes())
        self.folder_edit.textChanged.connect(lambda _: self._show_codes())
        self._show_codes()
        self._show_help()

        self.poll_timer = QTimer(self, interval=80)
        self.poll_timer.timeout.connect(self._poll)
        self.poll_timer.start()

    # ---- layout
    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 6, 12, 12)
        outer.setSpacing(6)

        # Settings: they get a scroll bar when the window is too short for them
        self.body = QWidget(objectName="settings")
        self.body_lay = QHBoxLayout(self.body)
        self.body_lay.setContentsMargins(0, 0, 4, 0)
        self.area = QScrollArea(widgetResizable=True, frameShape=QFrame.NoFrame)
        self.area.setWidget(self.body)
        self.area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(self.area)

        self.sec_in = QWidget()
        self.grid_in = QGridLayout(self.sec_in)
        self.grid_in.setContentsMargins(0, 8, 0, 0)
        self.grid_in.setColumnStretch(1, 1)
        self.lbl_txt = QLabel("1. Product code list (.txt)", objectName="step")
        self.txt_edit = QLineEdit()
        self.btn_txt = QPushButton("Browse…")
        self.btn_txt.clicked.connect(self._pick_txt)
        self.lbl_codes = QLabel(objectName="hint")
        self.lbl_codes.setWordWrap(True)
        self.lbl_folder = QLabel("2. Day folder with the photos", objectName="step")
        self.folder_edit = QLineEdit()
        self.btn_folder = QPushButton("Browse…")
        self.btn_folder.clicked.connect(self._pick_folder)
        self.rec_check = QCheckBox("Include all subfolders")
        self.box_lists = QGroupBox("3. WhatsApp chat for each list (done from the bottom list up)")
        lists_lay = QVBoxLayout(self.box_lists)
        self.lists_frame = QWidget()
        self.lists_grid = QGridLayout(self.lists_frame)
        self.lists_grid.setContentsMargins(0, 0, 0, 0)
        self.lists_grid.setVerticalSpacing(3)
        lists_lay.addWidget(self.lists_frame)
        hint = QLabel('"Open WhatsApp" fills these in (chat names with "(6-)", "(5a-)" …). '
                      'You can also choose or type a chat.', objectName="hint")
        hint.setWordWrap(True)
        lists_lay.addWidget(hint)

        self.sec_opt = QGroupBox("Options")
        self.opt_grid = QGridLayout(self.sec_opt)
        self.opt_grid.setRowStretch(2, 1)  # keep the options at the top of a tall box
        self.opt_a, self.opt_b = QWidget(), QWidget()
        a = QVBoxLayout(self.opt_a)
        a.setContentsMargins(0, 0, 0, 0)
        r1 = QHBoxLayout()
        r1.addWidget(QLabel("Match strictness"))
        self.threshold_spin = QSpinBox()
        self.threshold_spin.setRange(0, 30)
        r1.addWidget(self.threshold_spin)
        r1.addWidget(QLabel("(lower = stricter)", objectName="hint"))
        r1.addStretch(1)
        a.addLayout(r1)
        r2 = QHBoxLayout()
        r2.addWidget(QLabel("Search at most"))
        self.max_spin = QSpinBox()
        self.max_spin.setRange(100, 100000)
        self.max_spin.setSingleStep(500)
        r2.addWidget(self.max_spin)
        r2.addWidget(QLabel("messages per chat"))
        r2.addStretch(1)
        a.addLayout(r2)
        self.neighbors_check = QCheckBox("Also use caption-less photos sent next to the code")
        a.addWidget(self.neighbors_check)
        b = QVBoxLayout(self.opt_b)
        b.setContentsMargins(0, 0, 0, 0)
        b.addWidget(QLabel("If a code has 2 or more photos:"))
        self.multi_group = QButtonGroup(self)
        self.multi_folder = QRadioButton('put them in a folder "6-834 Infinix hot 30"')
        self.multi_number = QRadioButton('number them "… (1).jpg", "… (2).jpg"')
        for rb in (self.multi_folder, self.multi_number):
            self.multi_group.addButton(rb)
            row = QHBoxLayout()
            row.addSpacing(18)
            row.addWidget(rb)
            b.addLayout(row)
        self.note_check = QCheckBox("Add the (note) from the WhatsApp caption")
        b.addWidget(self.note_check)
        self.dry_check = QCheckBox("Preview only (change nothing)")
        b.addWidget(self.dry_check)

        # Buttons, progress and status
        self.bar = QGridLayout()
        self.bar.setColumnStretch(0, 1)
        self.bar_left, self.bar_right = QHBoxLayout(), QHBoxLayout()
        self.b_open = QPushButton("Open WhatsApp")
        self.b_open.clicked.connect(self._open_whatsapp)
        self.b_start = QPushButton("Start", objectName="primary")
        self.b_start.clicked.connect(self._start)
        self.b_stop = QPushButton("Stop")
        self.b_stop.setEnabled(False)
        self.b_stop.clicked.connect(self._stop)
        for w in (self.b_open, self.b_start, self.b_stop):
            self.bar_left.addWidget(w)
        self.bar_left.addStretch(1)
        self.b_undo = QPushButton("Undo last rename")
        self.b_undo.clicked.connect(self._undo)
        self.b_reset = QPushButton("Reset")
        self.b_reset.clicked.connect(self._reset)
        self.bar_right.addWidget(self.b_undo)
        self.bar_right.addWidget(self.b_reset)
        self.bar_right.addStretch(1)
        self.pb = QProgressBar()
        self.pb.setTextVisible(False)
        self.pb.setRange(0, 1)
        self.pb.setValue(0)
        self.lbl_status = QLabel("Ready.")
        self.lbl_status.setWordWrap(True)
        outer.addLayout(self.bar)

        # Log: takes all the height that is left
        self.log_view = QTextEdit(readOnly=True)
        self.log_view.setFont(QFont(["Segoe UI", "Nirmala UI"], 10))
        self.log_view.setMinimumHeight(60)
        outer.addWidget(self.log_view, 1)
        self._layout("wide")

    def _layout(self, mode):
        """Arrange the page for its width. wide: options on the right; medium: options below
        in two columns; narrow: everything in one column, labels above their boxes."""
        self.mode = mode
        narrow = mode == "narrow"
        for lay in (self.body_lay, self.opt_grid, self.grid_in, self.bar):
            while lay.count():
                item = lay.takeAt(0)
                if item.layout():
                    item.layout().setParent(None)  # so it can be added again below
        self.body_lay.setDirection(QBoxLayout.Direction.LeftToRight if mode == "wide"
                                   else QBoxLayout.Direction.TopToBottom)
        self.body_lay.addWidget(self.sec_in, 1)
        self.body_lay.addWidget(self.sec_opt, 0)

        if mode == "medium":
            self.opt_grid.addWidget(self.opt_a, 0, 0, Qt.AlignTop)
            self.opt_grid.addWidget(self.opt_b, 0, 1, Qt.AlignTop)
            self.opt_grid.setColumnStretch(1, 1)
        else:
            self.opt_grid.addWidget(self.opt_a, 0, 0, Qt.AlignTop)
            self.opt_grid.addWidget(self.opt_b, 1, 0, Qt.AlignTop)
            self.opt_grid.setColumnStretch(1, 0)

        g = self.grid_in
        if narrow:
            g.addWidget(self.lbl_txt, 0, 0, 1, 3)
            g.addWidget(self.txt_edit, 1, 0, 1, 2)
            g.addWidget(self.btn_txt, 1, 2)
            g.addWidget(self.lbl_codes, 2, 0, 1, 3)
            g.addWidget(self.lbl_folder, 3, 0, 1, 3)
            g.addWidget(self.folder_edit, 4, 0, 1, 2)
            g.addWidget(self.btn_folder, 4, 2)
            g.addWidget(self.rec_check, 5, 0, 1, 3)
            g.addWidget(self.box_lists, 6, 0, 1, 3)
        else:
            g.addWidget(self.lbl_txt, 0, 0)
            g.addWidget(self.txt_edit, 0, 1)
            g.addWidget(self.btn_txt, 0, 2)
            g.addWidget(self.lbl_codes, 1, 1, 1, 2)
            g.addWidget(self.lbl_folder, 2, 0)
            g.addWidget(self.folder_edit, 2, 1)
            g.addWidget(self.btn_folder, 2, 2)
            g.addWidget(self.rec_check, 3, 1)
            g.addWidget(self.box_lists, 4, 0, 1, 3)

        self.bar.addLayout(self.bar_left, 0, 0)
        if narrow:
            self.bar.addLayout(self.bar_right, 1, 0)
        else:
            self.bar.addLayout(self.bar_right, 0, 1)
        self.bar.addWidget(self.pb, 2, 0, 1, 2)
        self.bar.addWidget(self.lbl_status, 3, 0, 1, 2)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        width = event.size().width()
        mode = "wide" if width >= 980 else "medium" if width >= 680 else "narrow"
        if mode != self.mode:
            self._layout(mode)
            self._build_lists(self.groups_data)
        QTimer.singleShot(0, self._fit_height)

    def _fit_height(self):
        """Settings get the height they need, but the log always keeps some room;
        when the page is too short the settings scroll instead."""
        need = self.body.sizeHint().height() + 4
        bar = self.bar.sizeHint().height()
        room = self.height() - bar - 40 - 130  # padding, then the log
        height = max(min(need, room), min(need, 140))
        if self.area.height() != height:
            self.area.setFixedHeight(height)

    def _build_lists(self, groups):
        """One row per list in the file: tick box, its codes, the WhatsApp chat and its folder.
        On a narrow page the codes go on a second line."""
        while self.lists_grid.count():
            item = self.lists_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.list_boxes = []
        self.groups_data = groups
        self.groups = list(groups)
        narrow = self.mode == "narrow"
        chat_col = 1 if narrow else 2
        for c in range(4):
            self.lists_grid.setColumnStretch(c, 1 if c == chat_col else 0)
        day = self.folder_edit.text().strip()
        if not groups:
            self.lists_grid.addWidget(QLabel("Choose the product list first.", objectName="hint"), 0, 0, 1, 4)
            QTimer.singleShot(0, self._fit_height)
            return
        for i, (g, ps) in enumerate(groups.items()):
            if g not in self.list_vars:
                saved = self.saved_lists.get(g, {})
                self.list_vars[g] = {"on": saved.get("on", True), "chat": saved.get("chat", "")}
            v = self.list_vars[g]
            row = i * 2 if narrow else i
            folder = list_folder(day, g) if day and os.path.isdir(day) else None
            check = QCheckBox(f"{g} list")
            check.setChecked(v["on"])
            check.toggled.connect(lambda on, v=v: v.__setitem__("on", on))
            codes = QLabel(f"{len(ps)} codes, {ps[0].code} up to {ps[-1].code}", objectName="hint")
            box = QComboBox()
            box.setEditable(True)
            box.setInsertPolicy(QComboBox.NoInsert)
            box.addItems(self.chat_names)
            box.setCurrentText(v["chat"])
            box.setMinimumContentsLength(16 if narrow else 30)
            box.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            box.editTextChanged.connect(lambda text, v=v: v.__setitem__("chat", text))
            where = QLabel(f"folder {folder.name}" if folder else "no folder" if day else "",
                           objectName="hint" if folder else "warn")
            self.lists_grid.addWidget(check, row, 0)
            self.lists_grid.addWidget(box, row, chat_col)
            self.lists_grid.addWidget(where, row, chat_col + 1)
            if narrow:
                self.lists_grid.addWidget(codes, row + 1, 1, 1, 2)
            else:
                self.lists_grid.addWidget(codes, row, 1)
            self.list_boxes.append(box)
        QTimer.singleShot(0, self._fit_height)

    # ---- helpers
    def _show_help(self):
        # one paragraph per step, the numbered ones with a hanging indent
        for line in HELP:
            self._log(line, "dim", hang=line[:1].isdigit())

    def _log(self, msg, tag="", hang=False):
        cursor = self.log_view.textCursor()
        cursor.movePosition(QTextCursor.End)
        block = QTextBlockFormat()
        block.setTopMargin(2)
        if hang:
            block.setLeftMargin(24)
            block.setTextIndent(-16)
        char = QTextCharFormat()
        char.setForeground(QColor(LOG_COLORS.get(tag, "#1d1f23")))
        char.setFontWeight(QFont.Bold if tag == "head" else QFont.Normal)
        if not self.log_view.document().isEmpty():
            cursor.insertBlock(block, char)
        else:
            cursor.setBlockFormat(block)
        cursor.insertText(msg, char)
        self.log_view.setTextCursor(cursor)
        self.log_view.ensureCursorVisible()

    def _show_codes(self):
        path = self.txt_edit.text().strip()
        groups = {}
        text = ""
        if path and os.path.isfile(path):
            try:
                products, skipped = parse_products(path)
                groups = list_groups(products)
                if products:
                    text = f"{len(products)} codes in {len(groups)} list(s)"
                    if skipped:
                        text += f", {len(skipped)} without a name are skipped"
                else:
                    text = "No product codes found in this file."
            except OSError as e:
                text = f"Cannot read the file: {e}"
        self.lbl_codes.setText(text)
        self.lbl_codes.setVisible(bool(text))
        self._build_lists(groups)

    def _pick_txt(self):
        p, _ = QFileDialog.getOpenFileName(self, "Product code list", os.path.dirname(self.txt_edit.text()),
                                           "Text files (*.txt);;All files (*.*)")
        if p:
            self.txt_edit.setText(os.path.normpath(p))

    def _pick_folder(self):
        p = QFileDialog.getExistingDirectory(self, "Folder with the photos", self.folder_edit.text())
        if p:
            self.folder_edit.setText(os.path.normpath(p))

    def _options(self):
        return {
            "txt": self.txt_edit.text().strip(),
            "folder": self.folder_edit.text().strip(),
            "recursive": self.rec_check.isChecked(),
            "threshold": max(0, self.threshold_spin.value()),
            "max_messages": max(100, self.max_spin.value()),
            "neighbors": self.neighbors_check.isChecked(),
            "multi": "folder" if self.multi_folder.isChecked() else "number",
            "add_note": self.note_check.isChecked(),
            "dry_run": self.dry_check.isChecked(),
            "lists": [{"group": g, "on": self.list_vars[g]["on"], "chat": self.list_vars[g]["chat"].strip()}
                      for g in self.groups],
        }

    def _save_settings(self):
        opts = self._options()
        for l in opts["lists"]:
            self.saved_lists[l["group"]] = {"on": l["on"], "chat": l["chat"]}
        opts["lists"] = self.saved_lists
        opts["chat_names"] = self.chat_names[:300]
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            SETTINGS_FILE.write_text(json.dumps(opts, indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    # ---- buttons
    def _open_whatsapp(self):
        self._save_settings()
        self.worker.submit(self.worker.job_open_whatsapp, list(self.groups))

    def _start(self):
        opts = self._options()
        if not os.path.isfile(opts["txt"]):
            QMessageBox.warning(self, APP_TITLE, "Please choose the product code list (.txt).")
            return
        if not os.path.isdir(opts["folder"]):
            QMessageBox.warning(self, APP_TITLE, "Please choose the folder with the photos.")
            return
        on = [l for l in opts["lists"] if l["on"]]
        if not on:
            QMessageBox.warning(self, APP_TITLE, "Tick at least one list.")
            return
        no_chat = [l["group"] for l in on if not l["chat"]]
        if no_chat and len(on) > 1:
            QMessageBox.warning(self, APP_TITLE, "Choose the WhatsApp chat for these lists: " + ", ".join(no_chat) +
                                '\n\n(Press "Open WhatsApp" to fill them in, or untick them.)')
            return
        self._save_settings()
        self.worker.submit(self.worker.job_run, opts)

    def _stop(self):
        self.worker.stop_event.set()
        self.lbl_status.setText("Stopping…")

    def _undo(self):
        logs = sorted(p for p in LOG_DIR.glob("rename_*.csv") if not p.name.endswith(".undone.csv"))
        if not logs:
            QMessageBox.information(self, APP_TITLE, "There is nothing to undo.")
            return
        when = datetime.fromtimestamp(logs[-1].stat().st_mtime).strftime("%d-%m-%Y %H:%M")
        if QMessageBox.question(self, APP_TITLE, f"Undo the renaming done on {when}?") == QMessageBox.Yes:
            self.worker.submit(self.worker.job_undo)

    def _reset(self):
        """Back to a clean start: empty fields, no chats, default options, empty log.
        (Renamed files stay renamed - that is what "Undo last rename" is for.)"""
        if QMessageBox.question(self, APP_TITLE, "Reset everything?\n\nThe list file, folder, chats and options "
                                                 "are cleared. Renamed photos are not changed.") != QMessageBox.Yes:
            return
        self.saved_lists, self.list_vars, self.chat_names = {}, {}, []
        self.txt_edit.setText("")
        self.folder_edit.setText("")
        self.rec_check.setChecked(False)
        self.threshold_spin.setValue(10)
        self.max_spin.setValue(3000)
        self.neighbors_check.setChecked(True)
        self.multi_folder.setChecked(True)
        self.note_check.setChecked(False)
        self.dry_check.setChecked(False)
        self._show_codes()
        self.log_view.clear()
        self._show_help()
        self.pb.setRange(0, 1)
        self.pb.setValue(0)
        self.lbl_status.setText("Ready.")
        self._save_settings()

    def is_busy(self) -> bool:
        return self.busy

    def _set_busy(self, busy):
        self.busy = busy
        self.busy_changed.emit(busy)
        for w in (self.b_open, self.b_start, self.b_undo, self.b_reset):
            w.setEnabled(not busy)
        self.b_stop.setEnabled(busy)

    def _got_chats(self, names, found):
        self.chat_names = list(names)
        for box in self.list_boxes:
            text = box.currentText()
            box.blockSignals(True)
            box.clear()
            box.addItems(self.chat_names)
            box.setCurrentText(text)
            box.blockSignals(False)
        for g, name in found.items():
            v = self.list_vars.get(g)
            if v and v["chat"].strip() not in self.chat_names:
                v["chat"] = name
                if g in self.groups:
                    self.list_boxes[self.groups.index(g)].setCurrentText(name)
        self._save_settings()

    def _poll(self):
        try:
            while True:
                kind, val = self.ui.get_nowait()
                if kind == "log":
                    self._log(*val)
                elif kind == "status":
                    self.lbl_status.setText(val)
                elif kind == "busy":
                    self._set_busy(val)
                elif kind == "chats":
                    self._got_chats(*val)
                elif kind == "progress":
                    value, maximum = val
                    if maximum:
                        self.pb.setRange(0, maximum)
                        self.pb.setValue(value)
                    else:
                        self.pb.setRange(0, 0)  # moving bar
        except queue.Empty:
            pass

    def closeEvent(self, event):
        self._save_settings()
        self.worker.stop_event.set()
        self.worker.jobs.put(None)
        self.worker.join(timeout=8)
        super().closeEvent(event)
