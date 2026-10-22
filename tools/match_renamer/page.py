"""Rename by Match (PhotoMatch Renamer): finds each product code in its WhatsApp chat,
compares the photos posted there with the photos in the day folder and renames the
matching photo to "6-852 Nubia a56.jpg". The work itself is in engine.py.
"""
from __future__ import annotations

import json
import os
import queue
from datetime import datetime

from PySide6.QtCore import QTimer, Signal
from PySide6.QtGui import QColor, QFont, QTextBlockFormat, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QGridLayout, QLabel, QLineEdit, QMessageBox, QSizePolicy, QSpinBox, QTextEdit,
    QVBoxLayout, QWidget,
)

from core import look, shop, theme

from .engine import (APP_TITLE, DATA_DIR, LOG_DIR, SETTINGS_FILE, Worker, group_label, list_folder, list_groups,
                     parse_products)

HELP = [
    "How to use",
    "1. Choose the product list (.txt) and the day folder with the photos (e.g. 24.09.26). Each list "
    "uses its own folder in it: the 6- list the folder \"6-\", the 5a- list the folder \"5a-\" and so on.",
    "2. Press \"Open WhatsApp\" (the first time, scan the QR code with your phone). It looks for the chat "
    "of each list, e.g. \"My Shop (6-)\" for the 6- list. Check the chats, or choose / type "
    "them yourself, and untick the lists you don't want now.",
    "3. Press \"Start\". Lists are done from the bottom of the file upward (6-, then 5a-, 5- …). Each "
    "list's chat is searched from the newest message up to the first code of that list (e.g. 6-830), the "
    "photos are compared with the list's folder and the matching photo is renamed, e.g. "
    "\"6-852 Nubia a56.jpg\". Keep the WhatsApp window open while it works.",
    "Tip: tick \"Preview only\" the first time to see what would happen without renaming anything.",
]
LOG_COLORS = {"ok": "#15803d", "warn": "#b45309", "err": "#c0392b", "dim": theme.TEXT_2}


def shop_chat(group: str) -> str:
    """The chat the shop settings give a list ("6-"), or the orders chat for the codes without a
    prefix; "" when the shop settings have none (core/shop.py)."""
    s = shop.get().settings
    if not group:
        return (s.get("orders_chat") or "").strip()
    return (s.get("chats") or {}).get(group[:-1], "").strip()


def load_settings():
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


class RenamerPage(QWidget):
    busy_changed = Signal(bool)

    def __init__(self):
        super().__init__()
        self.setStyleSheet(look.STYLE)
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
        self.mode = "wide"       # "wide", "medium" or "narrow" (page width)

        self._build()
        self.txt_edit.setText(s.get("txt", ""))
        self.folder_edit.setText(s.get("folder", ""))
        self.rec_check.setChecked(s.get("recursive", False))
        self.threshold_spin.setValue(int(s.get("threshold", 10)))
        self.max_spin.setValue(int(s.get("max_messages", 3000)))
        self.neighbors_check.setChecked(s.get("neighbors", True))
        self.note_check.setChecked(s.get("add_note", False))
        self.dry_check.setChecked(s.get("dry_run", False))
        self.txt_edit.textChanged.connect(lambda _: self._show_codes())
        self.folder_edit.textChanged.connect(lambda _: self._show_codes())
        self._show_codes()
        self._show_help()

        self.poll_timer = QTimer(self, interval=80)
        self.poll_timer.timeout.connect(self._poll)
        self.poll_timer.start()

    # ---- layout: the look of core/look.py
    def _build(self):
        self.b_open = look.button("Open WhatsApp", "outline", "message-circle", theme.NAVY)
        self.b_open.clicked.connect(self._open_whatsapp)

        self.hero = look.Hero("done", hint_text="")
        self.pb = self.hero.progress
        self.lbl_status = self.hero.title  # the worker's status line is the control card's title
        self.b_start = look.button("Start", "heroStart", "play", theme.NAVY)
        self.b_start.clicked.connect(self._start)
        self.b_stop = look.button("Stop", "heroOutline", "square", "#ffffff")
        self.b_stop.setEnabled(False)
        self.b_stop.clicked.connect(self._stop)
        self.b_undo = look.button("Undo last rename", "heroGhost", "undo-2", "#ffffff")
        self.b_undo.clicked.connect(self._undo)
        self.b_reset = look.button("Reset", "heroGhost", "rotate-ccw", "#ffffff")
        self.b_reset.clicked.connect(self._reset)
        self.hero.add(self.b_start, self.b_stop, self.b_undo, self.b_reset)

        # 1, 2: the product list and the day folder
        files = look.Card("file-text", theme.ACCENT_SOFT, theme.ACCENT, "Product list and photos",
                          "Each list uses its own folder in the day folder: the 6- list the folder \"6-\" …")
        self.txt_edit = QLineEdit()
        self.btn_txt = look.button("Browse", "mini")
        self.btn_txt.clicked.connect(self._pick_txt)
        self.lbl_codes = QLabel(objectName="hint", wordWrap=True)
        self.folder_edit = QLineEdit()
        self.btn_folder = look.button("Browse", "mini")
        self.btn_folder.clicked.connect(self._pick_folder)
        self.rec_check = look.ToggleRow("folder", theme.NEUTRAL_SOFT, theme.NAVY, "Include all subfolders")
        files.lay.addLayout(look.column(look.field("Product code list (.txt)"),
                                        look.path_box(self.txt_edit, self.btn_txt, icon="file-text"), self.lbl_codes))
        files.lay.addLayout(look.column(look.field("Day folder with the photos"),
                                        look.path_box(self.folder_edit, self.btn_folder), self.rec_check))

        # 3: the chat of each list
        self.box_lists = look.Card("message-circle", theme.SUCCESS_SOFT, theme.SUCCESS, "WhatsApp chat for each list",
                                   "Done from the bottom list up")
        self.lists_frame = QWidget()
        self.lists_grid = QGridLayout(self.lists_frame)
        self.lists_grid.setContentsMargins(0, 0, 0, 0)
        self.lists_grid.setHorizontalSpacing(10)
        self.lists_grid.setVerticalSpacing(8)
        self.box_lists.lay.addWidget(self.lists_frame)
        self.box_lists.lay.addWidget(look.hint('"Open WhatsApp" fills these in (chat names with "(6-)", "(5a-)" …). '
                                               'You can also choose or type a chat.'))

        # options
        self.sec_opt = look.Card("sliders-horizontal", theme.WARNING_SOFT, theme.WARNING, "Options")
        self.threshold_spin = QSpinBox()
        self.threshold_spin.setRange(0, 30)
        self.threshold_spin.setToolTip("Lower = stricter")
        self.max_spin = QSpinBox()
        self.max_spin.setRange(100, 100000)
        self.max_spin.setSingleStep(500)
        self.max_spin.setSuffix(" messages")
        self.sec_opt.lay.addLayout(look.row(look.column(look.field("Match strictness (lower = stricter)"),
                                                        self.threshold_spin),
                                            look.column(look.field("Search at most, per chat"), self.max_spin),
                                            spacing=10))
        self.neighbors_check = look.ToggleRow("image", theme.ACCENT_SOFT, theme.ACCENT, "Also use caption-less photos",
                                              "Sent next to the code")
        self.note_check = look.ToggleRow("message-circle", theme.WARNING_SOFT, theme.WARNING, "Add the (note)",
                                         "From the WhatsApp caption")
        self.dry_check = look.ToggleRow("scan-search", theme.NEUTRAL_SOFT, theme.NAVY, "Preview only",
                                        "Change nothing")
        self.sec_opt.lay.addLayout(look.column(self.neighbors_check, self.note_check, self.dry_check, spacing=2))
        self.sec_opt.lay.addWidget(look.hint('One photo becomes "2-226 Realme P3 Ultra.jpg"; 2 or more go into a folder '
                                             '"2-221 Oppo a6 pro 5g" as img1.jpg, img2.jpg …'))

        # the log: takes all the height that is left
        self.log_view = QTextEdit(readOnly=True)
        self.log_view.setFont(QFont(["Segoe UI", "Nirmala UI"], 10))
        self.log_card = look.LogCard(self.log_view, height=200, grow=True)

        self.look = look.ToolLayout(
            look.Header("match_renamer", [self.b_open]), self.hero,
            [("files", files), ("lists", self.box_lists), ("options", self.sec_opt)], [(self.log_card, 1)],
            [("files", "Files"), ("lists", "Chats"), ("options", "Options"), ("log", "Log")], "log",
            left_width=(430, 380), on_mode=self._layout)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.look)
        self._show_status()

    def _layout(self, mode):
        self.mode = mode

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.look.resize_to(event.size().width())

    def _show_status(self):
        busy = self.busy
        self.hero.set_status("WORKING" if busy else "READY", theme.LIVE if busy else theme.SKY,
                             self.lbl_status.text() or "Ready.", self.lbl_codes.text())

    def _build_lists(self, groups):
        """One row per list in the file: tick box, its codes, the WhatsApp chat and its folder.
        The card is a column of the page, so the codes go on a second line."""
        while self.lists_grid.count():
            item = self.lists_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.list_boxes = []
        self.groups_data = groups
        self.groups = list(groups)
        narrow = True
        chat_col = 1 if narrow else 2
        for c in range(4):
            self.lists_grid.setColumnStretch(c, 1 if c == chat_col else 0)
        day = self.folder_edit.text().strip()
        if not groups:
            self.lists_grid.addWidget(QLabel("Choose the product list first.", objectName="hint"), 0, 0, 1, 4)
            return
        for i, (g, ps) in enumerate(groups.items()):
            if g not in self.list_vars:
                saved = self.saved_lists.get(g, {})
                self.list_vars[g] = {"on": saved.get("on", True), "chat": saved.get("chat", "") or shop_chat(g)}
            v = self.list_vars[g]
            row = i * 2 if narrow else i
            folder = list_folder(day, g) if day and os.path.isdir(day) else None
            check = QCheckBox(group_label(g))
            check.setChecked(v["on"])
            check.toggled.connect(lambda on, v=v: v.__setitem__("on", on))
            codes = QLabel(f"{len(ps)} codes, {ps[0].code} up to {ps[-1].code}", objectName="hint")
            box = QComboBox()
            box.setEditable(True)
            box.setInsertPolicy(QComboBox.NoInsert)
            box.addItems(self.chat_names)
            box.setCurrentText(v["chat"])
            box.setMinimumContentsLength(14)
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
        self._show_status()

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
        self.worker.submit(self.worker.job_open_whatsapp, list(self.groups), {g: shop_chat(g) for g in self.groups})

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
        no_chat = [group_label(l["group"]) for l in on if not l["chat"]]
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
        self.note_check.setChecked(False)
        self.dry_check.setChecked(False)
        self._show_codes()
        self.log_view.clear()
        self._show_help()
        self.pb.setRange(0, 1)
        self.pb.setValue(0)
        self.lbl_status.setText("Ready.")
        self._show_status()
        self._save_settings()

    def is_busy(self) -> bool:
        return self.busy

    def _set_busy(self, busy):
        self.busy = busy
        self.busy_changed.emit(busy)
        for w in (self.b_open, self.b_start, self.b_undo, self.b_reset):
            w.setEnabled(not busy)
        self.b_stop.setEnabled(busy)
        self._show_status()

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
                    self._show_status()
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
