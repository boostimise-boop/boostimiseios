"""PhotoMatch - find the photos in one folder that are missing from another
folder, then copy them somewhere else. The matching itself is in matcher.py.
"""
from __future__ import annotations

import csv
import json
import os
import queue
import subprocess
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor

from PIL import Image
from PySide6.QtCore import QAbstractTableModel, QEvent, QItemSelection, QItemSelectionModel, QModelIndex, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QBoxLayout, QButtonGroup, QCheckBox, QComboBox, QFileDialog, QGridLayout, QGroupBox, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMenu, QMessageBox, QProgressBar, QPushButton, QRadioButton, QSizePolicy,
    QSlider, QSplitter, QTableView, QVBoxLayout, QWidget,
)

from core import theme
from core.images import pil_to_qimage
from core.ui import FlowLayout

from . import matcher as M

__version__ = "1.0.0"
SETTINGS_FILE = M.APP_DIR / "settings.json"

MODE_TEXT = {
    "exact": ("Exact copies", "Identical files, even if renamed. Fastest."),
    "visual": ("Visual match", "Also finds resized, re-compressed or converted copies (JPG ↔ PNG)."),
    "ai": ("AI match", "Also finds edited copies: colour graded, cropped, filtered, watermarked."),
}
FILTERS = ("Missing from right folder", "Found in right folder", "All images")
COLUMNS = ("chk", "name", "folder", "size", "status", "closest")
HEADINGS = {"chk": "", "name": "File name", "folder": "Subfolder", "size": "Size", "status": "Status",
            "closest": "Closest image in right folder"}
WIDTHS = {"chk": 38, "name": 220, "folder": 130, "size": 80, "status": 150, "closest": 260}
TAG_COLORS = {"missing": "#c42b1c", "found": "#15803d", "warn": "#9d5d00"}
ACCENT = theme.ACCENT
STACK_BELOW = 820  # page width (px) under which "How to match" goes below "Folders"

# The look comes from core/theme.py; only what is special to this page is here.
STYLE = f"""
QWidget#photomatch {{ background: {theme.BG}; }}
QProgressBar {{ min-height: 8px; max-height: 8px; border-radius: 4px; }}
QProgressBar::chunk {{ border-radius: 4px; }}
QLabel#title {{ font-size: 17pt; font-weight: 700; color: {theme.TEXT}; }}
QLabel#dim {{ color: {theme.TEXT_2}; }}
QLabel#small {{ color: {theme.TEXT_2}; font-size: 9pt; }}
QLabel#bold {{ font-weight: 600; }}
QLabel#preview {{ background: #eceef5; border: 1px dashed {theme.BORDER_STRONG}; border-radius: 10px; color: {theme.TEXT_2}; }}
QLabel#empty {{ color: {theme.TEXT_2}; background: transparent; }}
"""

HELP = (
    "PhotoMatch looks at every image in the LEFT folder and searches the RIGHT folder for the same "
    "photo. Left images with no match are listed as Missing and are ticked for copying.\n\n"
    "• Exact copies – byte-for-byte identical files (file names don't matter).\n\n"
    "• Visual match – a 256-bit visual fingerprint. Finds copies that were resized, re-saved with "
    "different quality or converted between formats.\n\n"
    "• AI match – the DINOv2 vision model running on your Intel NPU (or Arc GPU / CPU) through "
    "OpenVINO. Also finds colour-graded, cropped, filtered or watermarked edits. Very similar shots "
    "(e.g. burst photos) may count as the same photo – raise the slider if that happens.\n\n"
    "Fingerprints are cached, so comparing the same folders again is fast. Copying never overwrites "
    "anything: identical files are skipped and name clashes get “(1)”, “(2)”… added.\n\n"
    f"Data folder: {M.APP_DIR}")


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return str(n)


def open_path(path: str, parent=None):
    try:
        os.startfile(path)
    except OSError as e:
        QMessageBox.critical(parent, "PhotoMatch", f"Could not open:\n{path}\n\n{e}")


def show_in_explorer(path: str):
    subprocess.Popen(f'explorer /select,"{os.path.normpath(path)}"')


class ClickLabel(QLabel):
    """Preview area: opens the image when clicked, re-renders it when resized."""
    clicked = Signal()
    resized = Signal()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.resized.emit()


class ResultsModel(QAbstractTableModel):
    """The rows shown in the table: indexes into the comparison result."""

    def __init__(self, page: "PhotoMatchPage"):
        super().__init__()
        self.page = page
        self.rows: list[int] = []

    def set_rows(self, rows: list[int]):
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def refresh(self, indexes):
        pos = {i: r for r, i in enumerate(self.rows)}
        rows = [pos[i] for i in indexes if i in pos]
        if rows:
            self.dataChanged.emit(self.index(min(rows), 0), self.index(max(rows), len(COLUMNS) - 1))

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return HEADINGS[COLUMNS[section]]
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        i = self.rows[index.row()]
        col = COLUMNS[index.column()]
        if col == "chk":
            if role == Qt.CheckStateRole:
                return Qt.Checked if self.page._is_checked(i) else Qt.Unchecked
            return None
        if role == Qt.DisplayRole:
            values, _ = self.page._row(i)
            return values[index.column()]
        if role == Qt.ForegroundRole:
            return QColor(TAG_COLORS[self.page._status(i)[1]])
        if role == Qt.TextAlignmentRole:
            return int((Qt.AlignRight if col == "size" else Qt.AlignLeft) | Qt.AlignVCenter)
        if role == Qt.ToolTipRole:
            m = self.page.result.matches[i]
            if col == "closest":
                return m.right.path if m.right else None
            return m.left.path
        return None


class PhotoMatchPage(QWidget):
    busy_changed = Signal(bool)

    def __init__(self):
        super().__init__(objectName="photomatch")
        self.setAttribute(Qt.WA_StyledBackground)
        self.setStyleSheet(STYLE)
        self.settings = self._load_settings()

        self.events: queue.Queue = queue.Queue()
        self.cancel = threading.Event()
        self.busy = False
        self.result: M.CompareResult | None = None
        self.matched: list[bool] = []
        self.unchecked: set[int] = set()      # missing images the user un-ticked
        self.extra_checked: set[int] = set()  # found images the user ticked
        self.devices: list[tuple[str, str]] = []
        self.thresholds = {m: int(round(self.settings.get(f"threshold_{m}", M.DEFAULT_THRESHOLD[m] * 100)))
                           for m in ("visual", "ai")}
        self.thumbs: dict[str, tuple] = {}
        self.thumb_pool = ThreadPoolExecutor(2)
        self.thumb_size = (560, 420)  # decoded size; shown scaled to fit the panel
        self.preview_token = 0
        self.sort_col: str | None = None
        self.sort_rev = False
        self._timers: dict[str, QTimer] = {}
        self._pending: dict[str, object] = {}

        self._build_ui()
        self._apply_settings()
        threading.Thread(target=self._detect_devices, daemon=True).start()
        self.poll_timer = QTimer(self, interval=100)
        self.poll_timer.timeout.connect(self._poll_events)
        self.poll_timer.start()

    # --------------------------------------------------------------- building
    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 10, 16, 12)
        outer.setSpacing(6)

        head = QHBoxLayout()
        head.addWidget(QLabel("PhotoMatch", objectName="title"))
        sub = QLabel("Finds the photos in the left folder that are NOT in the right folder, "
                     "so you can copy them somewhere.", objectName="dim")
        sub.setWordWrap(True)
        head.addWidget(sub, 1)
        help_btn = QPushButton("?  Help")
        help_btn.clicked.connect(self._show_help)
        head.addWidget(help_btn)
        more = QPushButton("⋯  More")
        menu = QMenu(more)
        menu.addAction("How matching works", self._show_help)
        menu.addSeparator()
        menu.addAction("Export results to CSV…", self._export_csv)
        menu.addAction("Clear fingerprint cache", self._clear_cache)
        menu.addAction("Open PhotoMatch data folder", lambda: open_path(str(M.APP_DIR), self))
        more.setMenu(menu)
        head.addWidget(more)
        outer.addLayout(head)

        self.top = QBoxLayout(QBoxLayout.LeftToRight)  # turns into a column in a narrow window
        self.top.addWidget(self._build_folders(), 5)
        self.top.addWidget(self._build_mode(), 4)
        outer.addLayout(self.top)

        run = QHBoxLayout()
        self.compare_btn = QPushButton("▶  Compare folders", objectName="accent")
        self.compare_btn.clicked.connect(self._start_compare)
        run.addWidget(self.compare_btn)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop)
        run.addWidget(self.stop_btn)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        run.addWidget(self.progress, 1)
        outer.addLayout(run)
        self.status_label = QLabel("Choose the two folders, then press Compare.", objectName="dim")
        outer.addWidget(self.status_label)

        body = QSplitter(Qt.Horizontal, childrenCollapsible=False)
        body.addWidget(self._build_table())
        body.addWidget(self._build_preview())
        body.setStretchFactor(0, 1)
        body.setSizes([900, 360])
        outer.addWidget(body, 1)
        outer.addLayout(self._build_bottom())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        direction = QBoxLayout.TopToBottom if event.size().width() < STACK_BELOW else QBoxLayout.LeftToRight
        if self.top.direction() != direction:
            self.top.setDirection(direction)

    def _build_folders(self):
        box = QGroupBox("Folders")
        grid = QGridLayout(box)
        grid.setColumnStretch(0, 1)
        self.left_edit, self.right_edit = QLineEdit(), QLineEdit()
        rows = (("Left folder  –  the photos you want to check", self.left_edit),
                ("Right folder  –  where they should already be", self.right_edit))
        for n, (label, edit) in enumerate(rows):
            grid.addWidget(QLabel(label, objectName="bold"), n * 2, 0, 1, 2)
            grid.addWidget(edit, n * 2 + 1, 0)
            browse = QPushButton("Browse…")
            browse.clicked.connect(lambda _=False, e=edit: self._browse(e))
            grid.addWidget(browse, n * 2 + 1, 1)
        opts = QHBoxLayout()
        self.recursive_check = QCheckBox("Include subfolders")
        self.recursive_check.setChecked(True)
        opts.addWidget(self.recursive_check)
        opts.addStretch(1)
        swap = QPushButton("⇅  Swap left / right")
        swap.clicked.connect(self._swap)
        opts.addWidget(swap)
        grid.addLayout(opts, 4, 0, 1, 2)
        return box

    def _build_mode(self):
        box = QGroupBox("How to match")
        grid = QGridLayout(box)
        grid.setColumnStretch(1, 1)
        self.mode_group = QButtonGroup(self)
        self.mode_radios = {}
        for n, mode in enumerate(M.MODES):
            title, desc = MODE_TEXT[mode]
            rb = QRadioButton(title)
            self.mode_group.addButton(rb)
            grid.addWidget(rb, n, 0)
            self.mode_radios[mode] = rb
            label = QLabel(desc, objectName="small")
            label.setWordWrap(True)
            grid.addWidget(label, n, 1)
        self.mode_radios["visual"].setChecked(True)

        dev = QHBoxLayout()
        dev.addWidget(QLabel("Run AI on"))
        self.device_box = QComboBox()
        self.device_box.addItem("Detecting NPU / GPU …")
        self.device_box.setEnabled(False)
        self.device_box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        dev.addWidget(self.device_box, 1)
        grid.addLayout(dev, 3, 0, 1, 2)

        thr = QHBoxLayout()
        thr.addWidget(QLabel("Same photo if similarity ≥"))
        self.threshold_slider = QSlider(Qt.Horizontal)
        self.threshold_slider.setRange(50, 100)
        self.threshold_slider.setValue(90)
        self.threshold_slider.valueChanged.connect(self._on_threshold_drag)
        thr.addWidget(self.threshold_slider, 1)
        self.threshold_label = QLabel("90 %", objectName="bold")
        self.threshold_label.setMinimumWidth(44)
        thr.addWidget(self.threshold_label)
        grid.addLayout(thr, 4, 0, 1, 2)
        self.threshold_hint = QLabel(objectName="small")
        self.threshold_hint.setWordWrap(True)
        grid.addWidget(self.threshold_hint, 5, 0, 1, 2)
        for rb in self.mode_radios.values():  # connected last: _on_mode needs the widgets above
            rb.toggled.connect(lambda on: on and self._on_mode())
        return box

    def _build_table(self):
        frame = QWidget()
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        bar.addWidget(QLabel("Show"))
        self.filter_box = QComboBox()
        self.filter_box.addItems(FILTERS)
        self.filter_box.currentIndexChanged.connect(lambda _: self._refresh_table())
        bar.addWidget(self.filter_box)
        bar.addSpacing(16)
        bar.addWidget(QLabel("Search"))
        self.search_edit = QLineEdit()
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setMaximumWidth(260)
        self.search_edit.textChanged.connect(lambda _: self._debounce("search", 250, self._refresh_table))
        bar.addWidget(self.search_edit)
        bar.addStretch(1)
        lay.addLayout(bar)

        self.model = ResultsModel(self)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(30)
        hdr = self.table.horizontalHeader()
        hdr.setSectionsClickable(True)
        hdr.setHighlightSections(False)
        hdr.sectionClicked.connect(lambda section: self._sort_by(COLUMNS[section]))
        for n, col in enumerate(COLUMNS):
            self.table.setColumnWidth(n, WIDTHS[col])
        hdr.setSectionResizeMode(0, QHeaderView.Fixed)
        hdr.setStretchLastSection(True)
        self.table.selectionModel().selectionChanged.connect(
            lambda *_: self._debounce("preview", 80, self._update_preview))
        self.table.doubleClicked.connect(self._on_double_click)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_right_click)
        self.table.installEventFilter(self)
        self.table.viewport().installEventFilter(self)
        lay.addWidget(self.table, 1)

        self.empty_label = QLabel("No results yet.\n\nPick a left and a right folder, choose how to match,\n"
                                  "then press  ▶ Compare folders.", self.table.viewport(), objectName="empty")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        return frame

    def _build_preview(self):
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(12, 0, 0, 0)
        self.preview = {}
        for slot, title in (("left", "Selected image (left folder)"), ("right", "Closest image in right folder")):
            if slot == "right":
                lay.addSpacing(8)
            lay.addWidget(QLabel(title, objectName="bold"))
            img = ClickLabel(objectName="preview")
            img.setAlignment(Qt.AlignCenter)
            img.setMinimumSize(170, 96)
            img.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
            img.setCursor(Qt.PointingHandCursor)
            img.clicked.connect(lambda s=slot: self._open_preview(s))
            img.resized.connect(lambda s=slot: self._debounce("render_" + s, 60, lambda: self._render_preview(s)))
            lay.addWidget(img, 1)
            cap = QLabel(objectName="small")
            cap.setWordWrap(True)
            cap.setTextInteractionFlags(Qt.TextSelectableByMouse)
            lay.addWidget(cap)
            self.preview[slot] = {"img": img, "cap": cap, "path": None, "image": None}
        self._clear_preview()
        return box

    def _build_bottom(self):
        bar = FlowLayout()  # wraps onto more lines in a narrow window
        self.summary_label = QLabel(objectName="bold")
        bar.addWidget(self.summary_label)
        bar.addStretch()
        self.tick_btn = QPushButton("Tick all shown")
        self.tick_btn.clicked.connect(lambda: self._set_all_checked(True))
        bar.addWidget(self.tick_btn)
        self.untick_btn = QPushButton("Untick all")
        self.untick_btn.clicked.connect(lambda: self._set_all_checked(False))
        bar.addWidget(self.untick_btn)
        self.keep_structure_check = QCheckBox("Keep subfolder structure")
        self.keep_structure_check.setChecked(True)
        bar.addWidget(self.keep_structure_check)
        self.export_btn = QPushButton("Export list…")
        self.export_btn.clicked.connect(self._export_csv)
        bar.addWidget(self.export_btn)
        self.copy_btn = QPushButton("Copy ticked images to…", objectName="accent")
        self.copy_btn.clicked.connect(self._copy_checked)
        bar.addWidget(self.copy_btn)
        for b in (self.copy_btn, self.export_btn, self.tick_btn, self.untick_btn):
            b.setEnabled(False)
        return bar

    # --------------------------------------------------------------- settings
    def _load_settings(self) -> dict:
        try:
            return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _apply_settings(self):
        s = self.settings
        self.left_edit.setText(s.get("left", ""))
        self.right_edit.setText(s.get("right", ""))
        self.recursive_check.setChecked(s.get("recursive", True))
        self.keep_structure_check.setChecked(s.get("keep_structure", True))
        self.mode_radios[s.get("mode") if s.get("mode") in M.MODES else "visual"].setChecked(True)
        self._on_mode()

    def _save_settings(self):
        data = dict(self.settings, left=self.left_edit.text(), right=self.right_edit.text(),
                    recursive=self.recursive_check.isChecked(), keep_structure=self.keep_structure_check.isChecked(),
                    mode=self._mode(), device=self._device_id() or self.settings.get("device", ""),
                    threshold_visual=self.thresholds["visual"], threshold_ai=self.thresholds["ai"])
        try:
            SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
            SETTINGS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError:
            pass

    # ------------------------------------------------------------ small bits
    def _debounce(self, key: str, ms: int, fn):
        """Run fn once, ms after the last call with this key."""
        self._pending[key] = fn
        timer = self._timers.get(key)
        if timer is None:
            timer = self._timers[key] = QTimer(self, singleShot=True)
            timer.timeout.connect(lambda k=key: self._pending.pop(k, lambda: None)())
        timer.start(ms)

    def _browse(self, edit: QLineEdit):
        start = edit.text() if os.path.isdir(edit.text()) else os.path.expanduser("~\\Pictures")
        path = QFileDialog.getExistingDirectory(self, "Choose a folder", start)
        if path:
            edit.setText(os.path.normpath(path))

    def _swap(self):
        a, b = self.left_edit.text(), self.right_edit.text()
        self.left_edit.setText(b)
        self.right_edit.setText(a)

    def _mode(self) -> str:
        return next((m for m, rb in self.mode_radios.items() if rb.isChecked()), "visual")

    @staticmethod
    def _device_label(dev_id: str, name: str) -> str:
        hint = {"NPU": "low power", "GPU": "fastest", "CPU": "slowest"}.get(dev_id.split(".")[0], "")
        return f"{dev_id}  –  {name}" + (f"   ({hint})" if hint else "")

    def _device_id(self) -> str:
        return self.device_box.currentData() or "" if self.devices else ""

    def _detect_devices(self):
        try:
            devs = M.list_ai_devices()
        except Exception:
            devs = []
        self.events.put(("devices", devs))

    def _on_mode(self):
        mode = self._mode()
        self.device_box.setEnabled(mode == "ai" and bool(self.devices) and not self.busy)
        if mode == "exact":
            self.threshold_slider.setEnabled(False)
            self.threshold_label.setText("—")
            self.threshold_hint.setText("Exact mode has no threshold: files are either identical or not.")
        else:
            self.threshold_slider.setEnabled(True)
            if self.threshold_slider.value() != self.thresholds[mode]:
                self.threshold_slider.setValue(self.thresholds[mode])  # also fires _on_threshold_drag
            self._show_threshold(self.thresholds[mode])

    def _show_threshold(self, value: int):
        self.threshold_label.setText(f"{value} %")
        default = int(M.DEFAULT_THRESHOLD[self._mode()] * 100)
        self.threshold_hint.setText(
            f"Recommended {default} %.  Lower = more lenient (fewer photos reported missing).  "
            f"You can move this after comparing – the list updates instantly.")

    def _on_threshold_drag(self, value: int):
        mode = self._mode()
        if mode == "exact":
            return
        self.thresholds[mode] = value
        self._show_threshold(value)
        if self.result and self.result.mode == mode:
            self._debounce("threshold", 200, self._reclassify)

    # --------------------------------------------------------------- compare
    def _start_compare(self):
        left, right = self.left_edit.text().strip().strip('"'), self.right_edit.text().strip().strip('"')
        if not left or not os.path.isdir(left):
            QMessageBox.warning(self, "PhotoMatch", "Please choose an existing LEFT folder.")
            return
        if not right or not os.path.isdir(right):
            QMessageBox.warning(self, "PhotoMatch", "Please choose an existing RIGHT folder.")
            return
        if os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right)):
            QMessageBox.warning(self, "PhotoMatch", "The left and right folders are the same folder.")
            return
        mode = self._mode()
        device = self._device_id() if mode == "ai" else ""
        if mode == "ai" and not device:
            QMessageBox.warning(self, "PhotoMatch", "No AI device is available yet.\n\nWait a moment for "
                                                    "NPU/GPU detection or choose Visual match.")
            return
        recursive = self.recursive_check.isChecked()
        self.cancel = threading.Event()
        cancel = self.cancel
        self._set_busy(True)
        self.status_label.setText("Starting …")

        def job():
            try:
                res = M.compare_folders(left, right, recursive, mode, self._progress, cancel, device)
                self.events.put(("result", res))
            except M.Cancelled:
                self.events.put(("cancelled", "Comparison stopped."))
            except Exception as e:
                detail = "".join(traceback.format_exception_only(type(e), e)).strip()
                if mode == "ai":
                    detail += f"\n\nIf the {device} keeps failing, pick another device under “Run AI on”."
                self.events.put(("error", detail))

        threading.Thread(target=job, daemon=True).start()

    def _progress(self, message: str, done: int, total: int):
        self.events.put(("progress", (message, done, total)))

    def _stop(self):
        self.cancel.set()
        self.status_label.setText("Stopping …")

    def is_busy(self) -> bool:
        return self.busy

    def _set_busy(self, busy: bool):
        if busy != self.busy:
            self.busy_changed.emit(busy)
        self.busy = busy
        self.compare_btn.setEnabled(not busy)
        self.stop_btn.setEnabled(busy)
        for rb in self.mode_radios.values():
            rb.setEnabled(not busy)
        if not self.devices:
            self.mode_radios["ai"].setEnabled(False)
        has = self.result is not None and not busy
        for b in (self.copy_btn, self.export_btn, self.tick_btn, self.untick_btn):
            b.setEnabled(has)
        self._on_mode()
        if not busy:
            self.progress.setRange(0, 1)
            self.progress.setValue(0)

    def _poll_events(self):
        last_progress = None
        try:
            while True:
                kind, data = self.events.get_nowait()
                if kind == "progress":
                    last_progress = data
                else:
                    self._handle_event(kind, data)
        except queue.Empty:
            pass
        if last_progress and self.busy:
            message, done, total = last_progress
            self.status_label.setText(message)
            if total > 0:
                self.progress.setRange(0, total)
                self.progress.setValue(done)
            else:
                self.progress.setRange(0, 0)  # moving bar: amount of work not known yet

    def _handle_event(self, kind: str, data):
        if kind == "devices":
            self.devices = data
            self.device_box.clear()
            if data:
                for dev_id, name in data:
                    self.device_box.addItem(self._device_label(dev_id, name), dev_id)
                saved = self.settings.get("device")
                self.device_box.setCurrentIndex(max(0, next((n for n, (d, _) in enumerate(data) if d == saved), 0)))
            else:
                self.device_box.addItem("OpenVINO not installed – AI match unavailable")
                if self._mode() == "ai":
                    self.mode_radios["visual"].setChecked(True)
            self._set_busy(self.busy)
        elif kind == "result":
            self._set_result(data)
        elif kind in ("cancelled", "error"):
            self._set_busy(False)
            self.status_label.setText(data.splitlines()[0] if kind == "cancelled" else "Something went wrong.")
            if kind == "error":
                QMessageBox.critical(self, "PhotoMatch", data)
        elif kind == "copied":
            self._copy_done(*data)
        elif kind == "thumb":
            self._thumb_ready(*data)

    def _set_result(self, res: M.CompareResult):
        self.result = res
        self.unchecked.clear()
        self.extra_checked.clear()
        self.sort_col, self.sort_rev = None, False
        self.table.horizontalHeader().setSortIndicatorShown(False)
        self.thumbs.clear()
        self._set_busy(False)
        self._reclassify()
        where = f"AI on {res.device}" if res.mode == "ai" else MODE_TEXT[res.mode][0].lower()
        took = f"{res.seconds:.1f} s" if res.seconds >= 1 else "under a second"
        self.status_label.setText(f"Done in {took} ({where}).  Tick the photos you want, then press "
                                  f"“Copy ticked images to…”.  Move the similarity slider to fine-tune.")
        if self.model.rows:
            self.table.selectRow(0)

    def _reclassify(self):
        if not self.result:
            return
        thr = self.thresholds.get(self.result.mode, 100) / 100.0
        self.matched = [self.result.is_matched(m, thr) for m in self.result.matches]
        self._refresh_table()

    # ----------------------------------------------------------------- table
    def _is_checked(self, i: int) -> bool:
        return i in self.extra_checked if self.matched[i] else i not in self.unchecked

    def _set_checked(self, i: int, on: bool):
        if self.matched[i]:
            (self.extra_checked.add if on else self.extra_checked.discard)(i)
        else:
            (self.unchecked.discard if on else self.unchecked.add)(i)

    def _status(self, i: int) -> tuple[str, str]:
        m = self.result.matches[i]
        if m.exact:
            return "✓  Exact copy", "found"
        if self.matched[i]:
            return f"✓  Similar  {m.score:.0%}", "found"
        if self.result.mode != "exact" and not m.readable:
            return "✗  Missing (unreadable)", "warn"
        return "✗  Missing", "missing"

    def _row(self, i: int):
        m = self.result.matches[i]
        status, tag = self._status(i)
        closest = ""
        if m.right is not None:
            show_score = not self.matched[i] and self.result.mode != "exact"  # found rows show it in Status
            closest = f"{m.score:.0%}   {m.right.rel}" if show_score else m.right.rel
        values = ("", m.left.name, m.left.subfolder, human_size(m.left.size), status, closest)
        return values, tag

    def _visible_rows(self) -> list[int]:
        flt = self.filter_box.currentText()
        q = self.search_edit.text().strip().lower()
        rows = []
        for i, m in enumerate(self.result.matches):
            if flt == FILTERS[0] and self.matched[i] or flt == FILTERS[1] and not self.matched[i]:
                continue
            if q and q not in m.left.rel.lower():
                continue
            rows.append(i)
        if self.sort_col:
            ms = self.result.matches
            key = {"chk": lambda i: self._is_checked(i),
                   "name": lambda i: ms[i].left.name.lower(),
                   "folder": lambda i: ms[i].left.subfolder.lower(),
                   "size": lambda i: ms[i].left.size,
                   "status": lambda i: (self.matched[i], ms[i].exact, ms[i].score),
                   "closest": lambda i: ms[i].score}[self.sort_col]
            rows.sort(key=key, reverse=self.sort_rev)
        return rows

    def _selected_indexes(self) -> list[int]:
        return [self.model.rows[ix.row()] for ix in self.table.selectionModel().selectedRows()]

    def _refresh_table(self):
        if not self.result:
            self.model.set_rows([])
            return
        selected = set(self._selected_indexes())
        self.model.set_rows(self._visible_rows())
        keep = QItemSelection()
        for r, i in enumerate(self.model.rows):
            if i in selected:
                keep.select(self.model.index(r, 0), self.model.index(r, len(COLUMNS) - 1))
        if not keep.isEmpty():
            self.table.selectionModel().select(keep, QItemSelectionModel.ClearAndSelect)
        if self.model.rows:
            self.empty_label.hide()
        else:
            self.empty_label.setText("Nothing to show here.\n\n" + (
                "Every photo in the left folder was found in the right folder. 🎉"
                if self.filter_box.currentText() == FILTERS[0] and not self.search_edit.text()
                else "Try another filter or search."))
            self.empty_label.show()
        self._update_summary()

    def _refresh_rows(self, rows):
        self.model.refresh(rows)
        self._update_summary()

    def _update_summary(self):
        if not self.result:
            self.summary_label.setText("")
            return
        res = self.result
        found = sum(self.matched)
        missing = len(self.matched) - found
        ticked = sum(1 for i in range(len(self.matched)) if self._is_checked(i))
        self.summary_label.setText(f"Left: {len(res.left):,} images   ·   Right: {len(res.right):,}   ·   "
                                   f"Found: {found:,}   ·   Missing: {missing:,}")
        self.copy_btn.setText(f"Copy {ticked:,} ticked image{'s' if ticked != 1 else ''} to…")

    def _sort_by(self, col: str):
        if not self.result:
            return
        self.sort_rev = not self.sort_rev if self.sort_col == col else False
        self.sort_col = col
        hdr = self.table.horizontalHeader()
        hdr.setSortIndicatorShown(col != "chk")
        hdr.setSortIndicator(COLUMNS.index(col), Qt.DescendingOrder if self.sort_rev else Qt.AscendingOrder)
        self._refresh_table()

    def eventFilter(self, obj, event):
        if obj is self.table.viewport():
            if event.type() == QEvent.Resize:
                self.empty_label.setGeometry(obj.rect())
            elif event.type() in (QEvent.MouseButtonPress, QEvent.MouseButtonDblClick) \
                    and event.button() == Qt.LeftButton:
                idx = self.table.indexAt(event.position().toPoint())
                if idx.isValid() and idx.column() == 0 and self.result:  # the tick box column
                    if event.type() == QEvent.MouseButtonPress:
                        i = self.model.rows[idx.row()]
                        self._set_checked(i, not self._is_checked(i))
                        self._refresh_rows([i])
                    return True
        elif obj is self.table and event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_Space:
                self._toggle_selected()
                return True
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                self._open_selected()
                return True
        return super().eventFilter(obj, event)

    def _toggle_selected(self):
        rows = self._selected_indexes()
        if rows:
            on = not all(self._is_checked(i) for i in rows)
            for i in rows:
                self._set_checked(i, on)
            self._refresh_rows(rows)

    def _set_checked_selected(self, on: bool):
        rows = self._selected_indexes()
        for i in rows:
            self._set_checked(i, on)
        self._refresh_rows(rows)

    def _set_all_checked(self, on: bool):
        if not self.result:
            return
        rows = range(len(self.matched)) if not on else list(self.model.rows)
        for i in rows:
            self._set_checked(i, on)
        self._refresh_rows(list(self.model.rows))

    def _selected_match(self):
        if not self.result:
            return None
        rows = self._selected_indexes()
        if not rows:
            current = self.table.currentIndex()
            rows = [self.model.rows[current.row()]] if current.isValid() else []
        return self.result.matches[rows[0]] if rows else None

    def _open_selected(self, closest=False):
        m = self._selected_match()
        f = (m.right if closest else m.left) if m else None
        if f:
            open_path(f.path, self)

    def _explore_selected(self, closest=False):
        m = self._selected_match()
        f = (m.right if closest else m.left) if m else None
        if f:
            show_in_explorer(f.path)

    def _on_double_click(self, index):
        if index.isValid() and index.column() != 0:
            self._open_selected(closest=COLUMNS[index.column()] == "closest")

    def _on_right_click(self, pos):
        idx = self.table.indexAt(pos)
        if not idx.isValid() or not self.result:
            return
        if not self.table.selectionModel().isRowSelected(idx.row(), QModelIndex()):
            self.table.selectRow(idx.row())
        self.table.setCurrentIndex(idx)
        m = self.result.matches[self.model.rows[idx.row()]]
        menu = QMenu(self)
        menu.addAction("Open image", self._open_selected)
        menu.addAction("Show in Explorer", lambda: self._explore_selected(False))
        menu.addSeparator()
        for text, fn in (("Open closest match", lambda: self._open_selected(True)),
                         ("Show closest match in Explorer", lambda: self._explore_selected(True))):
            menu.addAction(text, fn).setEnabled(m.right is not None)
        menu.addSeparator()
        menu.addAction("Tick selected", lambda: self._set_checked_selected(True))
        menu.addAction("Untick selected", lambda: self._set_checked_selected(False))
        menu.exec(self.table.viewport().mapToGlobal(pos))

    # --------------------------------------------------------------- preview
    def _clear_preview(self):
        for p in self.preview.values():
            p["img"].clear()
            p["cap"].setText("")
            p["path"] = p["image"] = None

    def _render_preview(self, slot: str):
        p = self.preview[slot]
        if p["image"] is None:
            return
        w, h = p["img"].width() - 4, p["img"].height() - 4
        if w < 20 or h < 20:
            return
        ratio = self.devicePixelRatioF()
        pm = QPixmap.fromImage(p["image"].scaled(int(w * ratio), int(h * ratio), Qt.KeepAspectRatio,
                                                 Qt.SmoothTransformation))
        pm.setDevicePixelRatio(ratio)
        p["img"].setPixmap(pm)

    def _update_preview(self):
        m = self._selected_match()
        if not m:
            return
        self.preview_token += 1
        token = self.preview_token
        for slot, f in (("left", m.left), ("right", m.right)):
            p = self.preview[slot]
            p["path"] = f.path if f else None
            p["image"] = None
            if f is None:
                p["img"].clear()
                p["img"].setText("Nothing comparable\nin the right folder")
                p["cap"].setText("")
                continue
            if f.path in self.thumbs:
                self._thumb_ready(token, slot, f.path, *self.thumbs[f.path])
            else:
                p["img"].clear()
                p["img"].setText("Loading …")
                p["cap"].setText(self._caption(slot, m, None))
                self.thumb_pool.submit(self._load_thumb, token, slot, f.path)

    def _caption(self, slot: str, m: M.Match, dims) -> str:
        f = m.left if slot == "left" else m.right
        dim = f"{dims[0]} × {dims[1]}   ·   " if dims else ""
        text = f"{f.rel}\n{dim}{human_size(f.size)}"
        if slot == "right":
            if m.exact:
                text += "   ·   identical file"
            elif self.result and self.result.mode != "exact":
                text += f"   ·   similarity {m.score:.0%}"
        return text

    def _load_thumb(self, token, slot, path):
        dims = None
        try:
            with Image.open(path) as im0:
                dims = im0.size
                if im0.getexif().get(0x0112) in (5, 6, 7, 8):
                    dims = dims[::-1]
            im = M.open_image(path, max(self.thumb_size) * 2)
            im.thumbnail(self.thumb_size, Image.Resampling.LANCZOS)
            image = pil_to_qimage(im)
        except Exception:
            image = None
        self.events.put(("thumb", (token, slot, path, image, dims)))

    def _thumb_ready(self, token, slot, path, image, dims):
        if len(self.thumbs) > 400:
            self.thumbs.clear()
        self.thumbs[path] = (image, dims)
        if token != self.preview_token:
            return
        m = self._selected_match()
        p = self.preview[slot]
        if not m or p["path"] != path:
            return
        p["image"] = image
        if image is None:
            p["img"].clear()
            p["img"].setText("No preview available\n(format not supported)")
        else:
            self._render_preview(slot)
        p["cap"].setText(self._caption(slot, m, dims))

    def _open_preview(self, slot):
        path = self.preview[slot]["path"]
        if path:
            open_path(path, self)

    # ------------------------------------------------------------------ copy
    def _copy_checked(self):
        if not self.result or self.busy:
            return
        rows = [i for i in range(len(self.matched)) if self._is_checked(i)]
        if not rows:
            QMessageBox.information(self, "PhotoMatch", "No images are ticked.")
            return
        start = self.settings.get("last_dest") or os.path.dirname(self.result.left_root)
        dest = QFileDialog.getExistingDirectory(self, f"Copy {len(rows):,} images to which folder?", start)
        if not dest:
            return
        dest = os.path.normpath(dest)
        res = self.result
        if os.path.normcase(dest) == os.path.normcase(res.left_root):
            QMessageBox.warning(self, "PhotoMatch", "That is the left folder itself – choose another folder.")
            return
        if M.is_inside(dest, res.left_root) and self.recursive_check.isChecked():
            if QMessageBox.question(self, "PhotoMatch", "This folder is inside the left folder, so the copies will "
                                                        "show up the next time you compare.\n\nCopy there anyway?"
                                    ) != QMessageBox.Yes:
                return
        self.settings["last_dest"] = dest
        files = [res.matches[i].left for i in rows]
        keep = self.keep_structure_check.isChecked()
        self.cancel = threading.Event()
        cancel = self.cancel
        self._set_busy(True)

        def job():
            try:
                summary = M.copy_files(files, dest, keep, self._progress, cancel)
                self.events.put(("copied", (summary, dest)))
            except Exception as e:
                self.events.put(("error", f"Copy failed: {e}"))

        threading.Thread(target=job, daemon=True).start()

    def _copy_done(self, summary: dict, dest: str):
        self._set_busy(False)
        lines = [f"Copied {summary['copied']:,} image{'s' if summary['copied'] != 1 else ''} to\n{dest}"]
        if summary["skipped"]:
            lines.append(f"{summary['skipped']:,} were already there (identical) and were skipped.")
        if summary["cancelled"]:
            lines.append("Copying was stopped before it finished.")
        if summary["failed"]:
            lines.append(f"{len(summary['failed'])} could not be copied:\n  " + "\n  ".join(summary["failed"][:8]))
        self.status_label.setText(lines[0].replace("\n", " "))
        if QMessageBox.question(self, "PhotoMatch", "\n\n".join(lines) + "\n\nOpen the folder now?") == QMessageBox.Yes:
            open_path(dest, self)

    # ---------------------------------------------------------------- misc
    def _export_csv(self):
        if not self.result:
            QMessageBox.information(self, "PhotoMatch", "Compare two folders first.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export list", "photomatch-results.csv", "CSV (Excel) (*.csv)")
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(["Status", "Ticked", "Left image", "Size (bytes)", "Similarity %", "Closest right image"])
            for i, m in enumerate(self.result.matches):
                w.writerow([self._status(i)[0].split(maxsplit=1)[1], "yes" if self._is_checked(i) else "no",
                            m.left.path, m.left.size, f"{m.score * 100:.1f}" if m.right else "",
                            m.right.path if m.right else ""])
        self.status_label.setText(f"Saved {path}")

    def _clear_cache(self):
        if self.busy:
            return
        if QMessageBox.question(self, "PhotoMatch", "Forget all stored image fingerprints?\n\nThe next comparison "
                                                    "will re-read every image (slower), nothing else changes."
                                ) == QMessageBox.Yes:
            M.FeatureCache().clear()
            QMessageBox.information(self, "PhotoMatch", "Fingerprint cache cleared.")

    def _show_help(self):
        QMessageBox.information(self, f"PhotoMatch {__version__} – how matching works", HELP)

    def closeEvent(self, event):
        self.cancel.set()
        self._save_settings()
        self.thumb_pool.shutdown(wait=False, cancel_futures=True)
        super().closeEvent(event)
