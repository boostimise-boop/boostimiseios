"""SnapRename - browse a folder's photos and sub-folders, rename them, and tidy photos away.

Big folders stay fast because of three things:
  * only the cards you can actually see exist as widgets (they are recycled as you scroll),
  * thumbnails are decoded on background threads, newest-on-screen first,
  * decoded thumbnails are cached on disk, so opening the same folder again is instant.
"""
from __future__ import annotations

import os
import queue
import shutil
import stat
import tempfile
import threading
import time
import zlib
from collections import OrderedDict

from PIL import Image, ImageOps, PngImagePlugin
from PySide6.QtCore import QMimeData, QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QDrag, QFont, QFontMetrics, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QAbstractScrollArea, QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMessageBox, QPushButton,
    QSizePolicy, QVBoxLayout, QWidget, QFileDialog,
)

from core import theme
from core.images import pil_to_qimage

APP_NAME = "SnapRename"

THUMB_SIZE = 160  # thumbnail box (logical pixels) - change this to make previews bigger/smaller
CARD_WIDTH = 226  # narrowest a card may be - controls how many fit per row (cards widen to fill it)
GRID_GAP = 10     # space between cards
IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".jfif", ".png", ".gif", ".bmp",
    ".webp", ".tif", ".tiff", ".ico",
}
INVALID_CHARS = '\\/:*?"<>|'
DRAG_THRESHOLD = 5      # pixels the mouse must travel before a click becomes a drag
DRAG_MIME = "application/x-snaprename-path"
WORKER_THREADS = 4      # background threads decoding thumbnails
RESULTS_PER_TICK = 12   # thumbnails handed to the window per tick
MEMORY_CACHE = 300      # thumbnails kept in memory
MAX_CARDS = 120         # ceiling on live cards
WHEEL_STEP = 100        # pixels moved per wheel notch
CACHE_DAYS = 30         # disk cache entries older than this are swept away

# --- Colour palette: the app's (core/theme.py) -------------------------------------------
BG = theme.BG                      # page background
SURFACE = theme.SURFACE            # cards, toolbar, inputs
BORDER = theme.BORDER              # default hairline border
ACCENT = theme.ACCENT              # primary brand colour
ACCENT_HOVER = theme.ACCENT_HOVER
ACCENT_SOFT = theme.ACCENT_SOFT    # tint used behind chips, badges and folder tiles
TEXT_PRIMARY = theme.TEXT
TEXT_SECONDARY = theme.TEXT_2
TEXT_MUTED = theme.MUTED
SUCCESS = "#1a9c5c"
WARNING = "#c67c17"
ERROR = "#dd3d4a"
THUMB_BG = "#eef0f6"

STYLE = f"""
QWidget#snaprename {{ background: {BG}; }}
QWidget#toolbar {{ background: {SURFACE}; border-bottom: 1px solid {BORDER}; }}
QLabel {{ background: transparent; color: {TEXT_PRIMARY}; }}
QLabel#brand {{ font-size: 13pt; font-weight: 700; }}
QLabel#folder {{ color: {TEXT_SECONDARY}; }}
QLabel#loading {{ color: {ACCENT}; }}
QLabel#count {{ background: {ACCENT_SOFT}; color: {ACCENT}; font-weight: 700; font-size: 9pt;
               border-radius: 6px; padding: 4px 10px; }}
QLabel#message {{ color: {TEXT_MUTED}; font-size: 11pt; }}
QPushButton {{ background: {SURFACE}; color: {TEXT_PRIMARY}; border: 1px solid {theme.BORDER_STRONG}; border-radius: 8px;
              padding: 7px 12px; font-size: 9pt; }}
QPushButton:hover {{ background: {ACCENT_SOFT}; border-color: #9cc5fb; color: {theme.ACCENT_DARK}; }}
QPushButton:disabled {{ color: {TEXT_MUTED}; }}
QPushButton#accent {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {ACCENT}, stop:1 {theme.ACCENT_2});
              color: #ffffff; border: none; font-weight: 700; padding: 8px 14px; }}
QPushButton#accent:hover {{ background: {ACCENT_HOVER}; color: #ffffff; }}
QFrame#card {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px; }}
QFrame#card:hover {{ border-color: #b3d1fa; }}
QFrame#card[drop="true"] {{ border: 2px solid {ACCENT}; }}
QFrame#card QLabel#tile {{ background: {THUMB_BG}; border-radius: 6px; color: {TEXT_MUTED}; }}
QFrame#card QLabel#tile[folder="true"] {{ background: {ACCENT_SOFT}; }}
QFrame#card QLabel#name {{ font-weight: 700; font-size: 9pt; }}
QFrame#card QLabel#details {{ color: {TEXT_SECONDARY}; font-size: 8pt; }}
QFrame#card QLabel#ext {{ color: {TEXT_MUTED}; font-size: 9pt; }}
QFrame#card QLabel#status {{ font-size: 8pt; }}
QFrame#card QLineEdit {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 5px; padding: 4px 6px;
                        color: {TEXT_PRIMARY}; }}
QFrame#card QLineEdit:focus {{ border-color: {ACCENT}; }}
QAbstractScrollArea#grid {{ background: {BG}; border: none; }}
QScrollBar:vertical {{ background: {BG}; width: 12px; margin: 0; }}
QScrollBar::handle:vertical {{ background: #d3d6e0; border-radius: 5px; min-height: 30px; margin: 2px; }}
QScrollBar::handle:vertical:hover {{ background: #bfc3d0; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
"""


def is_listed(filename):
    """True for image files, skipping anything with an .after extension."""
    if "after" in filename.lower().split(".")[1:]:
        return False
    return os.path.splitext(filename)[1].lower() in IMAGE_EXTENSIONS


def is_hidden(path):
    """True for entries Windows keeps out of sight, e.g. 'System Volume Information'."""
    if os.path.basename(path).startswith("."):
        return True
    try:
        attributes = os.stat(path).st_file_attributes
    except (OSError, AttributeError):
        return False
    return bool(attributes & (stat.FILE_ATTRIBUTE_HIDDEN | stat.FILE_ATTRIBUTE_SYSTEM))


def name_problem(name):
    """Return why this name can't be used on Windows, or None when it's fine."""
    if not name:
        return "Name can't be empty"
    if any(c in INVALID_CHARS for c in name) or name.endswith("."):
        return 'Not allowed: \\ / : * ? " < > |  or a trailing dot'
    return None


def format_size(num_bytes):
    if num_bytes < 1024:
        return f"{num_bytes} B"
    for unit in ("KB", "MB", "GB"):
        num_bytes /= 1024
        if num_bytes < 1024 or unit == "GB":
            return f"{num_bytes:.1f} {unit}"


class Item:
    """One thing in the current folder - a sub-folder or a photo."""

    __slots__ = ("kind", "path", "name")

    def __init__(self, kind, path):
        self.kind = kind          # "folder" or "image"
        self.path = path
        self.name = os.path.basename(path)

    def split_name(self):
        """Folders rename whole; photos keep their extension."""
        return os.path.splitext(self.name) if self.kind == "image" else (self.name, "")


class ThumbnailLoader:
    """Decodes thumbnails on background threads and caches them on disk.

    Requests are served newest-first, and anything that has scrolled out of view is dropped
    instead of decoded, so flicking through a folder of 8 MB photos stays responsive.
    """

    def __init__(self, size, workers=WORKER_THREADS):
        self.size = size  # pixels on the screen (twice THUMB_SIZE on a 200 % display)
        self.requests = queue.LifoQueue()
        self.results = queue.Queue()
        self._lock = threading.Lock()
        self._queued = set()   # paths waiting for a worker
        self._wanted = set()   # paths still on screen
        self.outstanding = 0
        self.cache_dir = os.path.join(
            os.environ.get("LOCALAPPDATA") or tempfile.gettempdir(), APP_NAME, "thumbnails")
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
        except OSError:
            self.cache_dir = None
        for _ in range(workers):
            threading.Thread(target=self._worker, daemon=True).start()
        threading.Thread(target=self._sweep_old_cache, daemon=True).start()

    # --- main thread ------------------------------------------------------------------
    def request(self, path):
        with self._lock:
            if path in self._queued:
                return
            self._queued.add(path)
            self._wanted.add(path)
            self.outstanding += 1
        self.requests.put(path)

    def set_wanted(self, paths):
        """Tell the workers which paths are still on screen; the rest are skipped."""
        with self._lock:
            self._wanted = set(paths)

    # --- worker threads ---------------------------------------------------------------
    def _worker(self):
        while True:
            path = self.requests.get()
            with self._lock:
                self._queued.discard(path)
                still_wanted = path in self._wanted
            if not still_wanted:
                self._deliver(path, None, None, "skip")
                continue
            try:
                image, size = self._build(path)
                self._deliver(path, pil_to_qimage(image), size, "ok")
            except Exception:  # corrupt, unreadable or unsupported file
                self._deliver(path, None, None, "fail")

    def _deliver(self, path, image, size, status):
        with self._lock:
            self.outstanding = max(0, self.outstanding - 1)
        self.results.put((path, image, size, status))

    def _build(self, path):
        st = os.stat(path)
        cache_file = self._cache_file(path, st)
        if cache_file and os.path.exists(cache_file):
            try:
                with Image.open(cache_file) as cached:
                    cached.load()
                    dims = cached.info.get("dims", "")
                    width, height = ((int(v) for v in dims.split("x")) if "x" in dims
                                     else cached.size)
                    return cached.copy(), (width, height)
            except Exception:
                pass  # unreadable cache entry - fall through and build it again

        with Image.open(path) as img:
            width, height = img.size
            if img.getexif().get(0x0112) in (5, 6, 7, 8):  # rotated 90 degrees via EXIF
                width, height = height, width
            img.draft("RGB", (self.size * 2, self.size * 2))  # much faster decode for big JPEGs
            img = ImageOps.exif_transpose(img)
            img.thumbnail((self.size, self.size), Image.LANCZOS, reducing_gap=2.0)
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGBA")
            thumb = img.copy()
        self._write_cache(cache_file, thumb, (width, height))
        return thumb, (width, height)

    def _cache_file(self, path, st):
        if not self.cache_dir:
            return None
        raw = (f"{os.path.abspath(path).lower()}|{st.st_size}|{st.st_mtime_ns}|{self.size}"
               .encode("utf-8", "replace"))
        key = f"{zlib.crc32(raw):08x}{zlib.crc32(raw[::-1]):08x}"
        return os.path.join(self.cache_dir, key[:2], key + ".png")

    def _write_cache(self, cache_file, thumb, size):
        if not cache_file:
            return
        try:
            os.makedirs(os.path.dirname(cache_file), exist_ok=True)
            meta = PngImagePlugin.PngInfo()
            meta.add_text("dims", f"{size[0]}x{size[1]}")
            partial = f"{cache_file}.{os.getpid()}.{threading.get_ident()}.part"
            thumb.save(partial, "PNG", pnginfo=meta, compress_level=1)
            os.replace(partial, cache_file)  # never leave a half-written thumbnail behind
        except Exception:
            pass  # a cache miss next time is harmless

    def _sweep_old_cache(self):
        if not self.cache_dir:
            return
        cutoff = time.time() - CACHE_DAYS * 86400
        try:
            for folder, _, files in os.walk(self.cache_dir):
                for name in files:
                    full = os.path.join(folder, name)
                    try:
                        if os.path.getmtime(full) < cutoff:
                            os.remove(full)
                    except OSError:
                        pass
        except OSError:
            pass


class Tile(QLabel):
    """The preview square: double-click opens, dragging a photo onto a folder card moves it."""

    def __init__(self, card):
        super().__init__(objectName="tile")
        self.card = card
        self._press = None
        self.setFixedSize(THUMB_SIZE, THUMB_SIZE)
        self.setAlignment(Qt.AlignCenter)
        self.setCursor(Qt.PointingHandCursor)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.card.open()

    def mousePressEvent(self, event):
        self._press = event.position().toPoint() if event.button() == Qt.LeftButton else None
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        self._press = None
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event):
        item = self.card.item
        if self._press is None or item is None or item.kind != "image":
            return
        if (event.position().toPoint() - self._press).manhattanLength() < DRAG_THRESHOLD:
            return  # still just a click
        self._press = None
        mime = QMimeData()
        mime.setData(DRAG_MIME, item.path.encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        pm = self.pixmap()
        if pm is not None and not pm.isNull():
            ghost = pm.scaled(QSize(96, 96) * pm.devicePixelRatio(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            ghost.setDevicePixelRatio(pm.devicePixelRatio())
            drag.setPixmap(ghost)
            drag.setHotSpot(QPoint(-14, -14))
        drag.exec(Qt.MoveAction)


class Card(QFrame):
    """One recycled card: preview tile on top, then name, rename field and Rename button.

    Cards are created once and re-pointed at different items as the grid scrolls, so a
    folder with thousands of photos still only costs a couple of dozen widgets.
    """

    def __init__(self, page, parent):
        super().__init__(parent, objectName="card")
        self.page = page
        self.item = None
        self.index = -1    # which grid slot this card currently holds
        self.photo = None  # the thumbnail shown, None while it is loading
        self._binding = False
        self.setAcceptDrops(True)
        self.setFixedWidth(CARD_WIDTH)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(0)
        self.tile = Tile(self)
        lay.addWidget(self.tile, 0, Qt.AlignHCenter)

        # Fixed line counts keep every card exactly the same height, which is what lets the
        # grid work out which rows are on screen without measuring each card.
        self.name_label = QLabel(objectName="name")
        self.name_label.setWordWrap(True)
        self.name_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.name_label.setFixedHeight(self._lines(self.name_label, 2, bold=True))
        lay.addSpacing(8)
        lay.addWidget(self.name_label)
        self.details_label = QLabel(objectName="details")
        self.details_label.setFixedHeight(self._lines(self.details_label, 1, size=8))
        lay.addWidget(self.details_label)

        row = QHBoxLayout()
        row.setContentsMargins(0, 6, 0, 0)
        row.setSpacing(4)
        self.entry = QLineEdit()
        self.entry.returnPressed.connect(self.rename)
        self.entry.textEdited.connect(self._on_edit)
        QShortcut(QKeySequence(Qt.Key_Escape), self.entry, activated=self.reset, context=Qt.WidgetShortcut)
        row.addWidget(self.entry, 1)
        self.ext_label = QLabel(objectName="ext")
        row.addWidget(self.ext_label)
        lay.addLayout(row)

        self.rename_btn = QPushButton("Rename", objectName="accent")
        self.rename_btn.setFocusPolicy(Qt.NoFocus)
        self.rename_btn.clicked.connect(self.rename)
        lay.addSpacing(6)
        lay.addWidget(self.rename_btn)
        self.status_label = QLabel(objectName="status")
        self.status_label.setWordWrap(True)
        self.status_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.status_label.setFixedHeight(self._lines(self.status_label, 2, size=8))
        lay.addSpacing(2)
        lay.addWidget(self.status_label)
        self.hide()

    @staticmethod
    def _lines(label, n, bold=False, size=9):
        font = QFont(label.font())
        font.setPointSize(size)
        font.setBold(bold)
        return QFontMetrics(font).lineSpacing() * n + 2

    # --- recycling --------------------------------------------------------------------
    def bind(self, item):
        """Point this card at a different folder/photo."""
        if self.item is item:
            return
        self.item = item
        base, ext = item.split_name()
        self._binding = True
        self.entry.setText(self.page.pending_edits.get(item.path, base))
        self.entry.setCursorPosition(0)
        self._binding = False
        self.ext_label.setText(ext)
        self.ext_label.setVisible(bool(ext))
        self.name_label.setText(item.name)
        self.name_label.setToolTip(item.name)
        self.photo = None

        is_folder = item.kind == "folder"
        if self.tile.property("folder") != is_folder:
            self.tile.setProperty("folder", is_folder)
            self.tile.style().unpolish(self.tile)
            self.tile.style().polish(self.tile)
        if is_folder:
            self.tile.setPixmap(self.page.folder_pixmap)
            self.details_label.setText(self.page.folder_details(item.path))
        else:
            self.tile.clear()
            self.details_label.setText(self.page.image_details(item.path))
            self.show_thumbnail()
        self._show_edit_status()

    def show_thumbnail(self):
        """Use the cached thumbnail if we have one, otherwise show the loading tile."""
        item = self.item
        if item is None or item.kind != "image":
            return
        cached = self.page.thumb_cache.get(item.path)
        if cached:
            self.photo, dimensions = cached
            self.tile.setPixmap(self.photo)
            self.details_label.setText(self.page.image_details(item.path, dimensions))
        elif item.path in self.page.failed_thumbs:
            self.tile.setText("No preview")
            self.tile.setStyleSheet("font-size: 8pt;")
        else:
            self.tile.setText("◌")
            self.tile.setStyleSheet("font-size: 20pt;")
            self.page.loader.request(item.path)

    def place(self, x, y):
        self.move(x, y)
        if not self.isVisible():
            self.show()

    def hide_card(self):
        self.hide()
        self.item = None
        self.index = -1

    # --- renaming ---------------------------------------------------------------------
    def set_status(self, text, color=TEXT_SECONDARY):
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {color};")

    def _on_edit(self, *_):
        if self._binding or self.item is None:
            return
        typed = self.entry.text()
        base = self.item.split_name()[0]
        if typed.strip() != base:
            self.page.pending_edits[self.item.path] = typed
        else:
            self.page.pending_edits.pop(self.item.path, None)
        self._show_edit_status()

    def _show_edit_status(self):
        if self.item is not None and self.item.path in self.page.pending_edits:
            self.set_status("↵ Enter or Rename to save · Esc to undo", WARNING)
        else:
            self.set_status("")

    def reset(self):
        if self.item is None:
            return
        self.page.pending_edits.pop(self.item.path, None)
        self._binding = True
        self.entry.setText(self.item.split_name()[0])
        self._binding = False
        self.set_status("")

    def rename(self):
        item = self.item
        if item is None:
            return
        parent_dir, old_name = os.path.split(item.path)
        new_base = self.entry.text().strip()
        new_name = new_base + item.split_name()[1]

        problem = name_problem(new_base)
        if problem:
            return self.set_status(f"✕ {problem}", ERROR)
        if new_name == old_name:
            return self.set_status("No change")

        new_path = os.path.join(parent_dir, new_name)
        # A case-only change (photo -> Photo) points at the same item on Windows, so allow it.
        if os.path.exists(new_path) and new_name.lower() != old_name.lower():
            return self.set_status(f'✕ "{new_name}" already exists', ERROR)
        try:
            os.rename(item.path, new_path)
        except OSError as err:
            return self.set_status(f"✕ Rename failed: {err.strerror or err}", ERROR)

        self.page.note_move(item.path, new_path)
        item.path, item.name = new_path, new_name
        self._binding = True
        self.entry.setText(new_base)
        self._binding = False
        self.name_label.setText(new_name)
        self.name_label.setToolTip(new_name)
        self.set_status("✓ Renamed successfully", SUCCESS)

    def open(self):
        if self.item is None:
            return
        if self.item.kind == "folder":
            self.page.load_folder(self.item.path)
        elif hasattr(os, "startfile"):
            os.startfile(self.item.path)

    # --- dropping a photo onto a folder -----------------------------------------------
    def set_drop_active(self, active):
        """Highlight the card while a photo is dragged over it."""
        self.setProperty("drop", active)
        self.style().unpolish(self)
        self.style().polish(self)

    def _accepts(self, event):
        return (self.item is not None and self.item.kind == "folder"
                and event.mimeData().hasFormat(DRAG_MIME))

    def dragEnterEvent(self, event):
        if self._accepts(event):
            self.set_drop_active(True)
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if self._accepts(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self.set_drop_active(False)

    def dropEvent(self, event):
        self.set_drop_active(False)
        if not self._accepts(event):
            event.ignore()
            return
        event.acceptProposedAction()
        path = bytes(event.mimeData().data(DRAG_MIME)).decode("utf-8")
        QTimer.singleShot(0, lambda: self.page.move_into_folder(path, self))


class CardGrid(QAbstractScrollArea):
    """The scrolling area; the page places the pooled cards on its viewport."""
    geometry_changed = Signal()
    scrolled = Signal()

    def __init__(self):
        super().__init__(objectName="grid")
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.verticalScrollBar().setSingleStep(20)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.geometry_changed.emit()

    def scrollContentsBy(self, dx, dy):
        self.scrolled.emit()

    def wheelEvent(self, event):
        bar = self.verticalScrollBar()
        pixels = event.pixelDelta().y()
        step = pixels if pixels else round(event.angleDelta().y() / 120 * WHEEL_STEP)
        bar.setValue(bar.value() - step)
        event.accept()


class SnapRenamePage(QWidget):
    def __init__(self):
        super().__init__(objectName="snaprename")
        self.setAttribute(Qt.WA_StyledBackground)
        self.setStyleSheet(STYLE)
        self.folder = None
        self.items = []
        self.pool = []
        self.pending_edits = {}                 # path -> half-typed name, kept while scrolling
        self.thumb_cache = OrderedDict()        # path -> (QPixmap, (w, h))
        self.failed_thumbs = set()
        self.folder_counts = {}
        self.columns = 1
        self.card_width = CARD_WIDTH  # stretched so each row fills the whole width
        self.card_height = None  # both set once the first card has been laid out
        self.row_height = None
        self._refreshing = False

        self.ratio = max(1.0, self.devicePixelRatioF(), self.screen().devicePixelRatio() if self.screen() else 1.0)
        self.loader = ThumbnailLoader(int(THUMB_SIZE * self.ratio))
        self.folder_pixmap = theme.icon_pixmap("folder", ACCENT, 64, self.ratio)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self._build_toolbar())
        lay.addWidget(self._build_grid(), 1)
        self.collect_timer = QTimer(self, interval=40)
        self.collect_timer.timeout.connect(self._collect_thumbnails)
        self.collect_timer.start()

    def _build_toolbar(self):
        bar = QWidget(objectName="toolbar")
        bar.setAttribute(Qt.WA_StyledBackground)
        row = QHBoxLayout(bar)
        row.setContentsMargins(16, 10, 16, 10)
        row.setSpacing(8)
        chip = QLabel()
        chip.setFixedSize(34, 30)
        chip.setAlignment(Qt.AlignCenter)
        chip.setStyleSheet(f"background: {ACCENT_SOFT}; border-radius: 6px;")
        chip.setPixmap(theme.icon_pixmap("edit", ACCENT, 18, self.devicePixelRatioF()))
        row.addWidget(chip)
        row.addWidget(QLabel(APP_NAME, objectName="brand"))
        row.addSpacing(12)
        select = QPushButton("📁  Select Folder", objectName="accent")
        select.clicked.connect(self.choose_folder)
        row.addWidget(select)
        self.up_button = QPushButton("↑  Up")
        self.up_button.setEnabled(False)
        self.up_button.clicked.connect(self.go_up)
        row.addWidget(self.up_button)
        new = QPushButton("＋  New Folder")
        new.clicked.connect(self.new_folder)
        row.addWidget(new)
        refresh = QPushButton("↻  Refresh")
        refresh.clicked.connect(self.refresh)
        row.addWidget(refresh)
        self.folder_label = QLabel("No folder selected yet", objectName="folder")
        self.folder_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        row.addSpacing(8)
        row.addWidget(self.folder_label, 1)
        self.loading_label = QLabel(objectName="loading")
        row.addWidget(self.loading_label)
        self.count_label = QLabel(objectName="count")
        self.count_label.hide()
        row.addWidget(self.count_label)
        return bar

    def _build_grid(self):
        self.grid = CardGrid()
        self.grid.geometry_changed.connect(self.relayout)
        self.grid.scrolled.connect(self.refresh_view)
        # One card up front so we know how tall a row is before anything is loaded.
        self._grow_pool(1)
        self.pool[0].ensurePolished()  # measured with the style sheet's paddings
        self.card_height = self.pool[0].sizeHint().height()
        self.row_height = self.card_height + GRID_GAP
        for card in self.pool:
            card.setFixedHeight(self.card_height)
        self.message = QLabel("🖼\n\nChoose a folder to see its photos here", self.grid.viewport(),
                              objectName="message")
        self.message.setAlignment(Qt.AlignCenter)
        return self.grid

    def _grow_pool(self, count):
        while len(self.pool) < count:
            card = Card(self, self.grid.viewport())
            card.setFixedWidth(self.card_width)
            if self.card_height:
                card.setFixedHeight(self.card_height)
            self.pool.append(card)

    def _show_message(self, text):
        self.message.setText(text)
        self._place_message()
        self.message.show()
        self.message.raise_()

    def _place_message(self):
        # Always cover the whole viewport, visible or not: the first resize arrives before the
        # label counts as visible, and skipping it left the label at its default top-left size.
        self.message.setGeometry(self.grid.viewport().rect())

    # --- scrolling and the visible window -------------------------------------------------
    def relayout(self):
        """Work out the column count and how many cards need to exist, then redraw."""
        if self.row_height is None:
            return
        vp = self.grid.viewport()
        width, height = vp.width(), vp.height()
        span = CARD_WIDTH + GRID_GAP
        self.columns = max(1, (width - GRID_GAP) // span)
        # Share the leftover width out between the cards, or it all ends up as a gap on the right.
        card_width = max(CARD_WIDTH, (width - GRID_GAP) // self.columns - GRID_GAP)
        if card_width != self.card_width:
            self.card_width = card_width
            for card in self.pool:
                card.setFixedWidth(card_width)
        rows = max(1, -(-len(self.items) // self.columns))
        total = rows * self.row_height + GRID_GAP if self.items else 0
        bar = self.grid.verticalScrollBar()
        bar.setRange(0, max(0, total - height))
        bar.setPageStep(height)
        visible_rows = max(1, -(-height // self.row_height) + 1)
        self._grow_pool(min(len(self.items), self.columns * visible_rows, MAX_CARDS) or 1)
        self._place_message()
        self.refresh_view(force=True)

    def refresh_view(self, force=False):
        """Point the pooled cards at whichever items are scrolled into view."""
        if self._refreshing or self.row_height is None:
            return
        self._refreshing = True
        try:
            top = self.grid.verticalScrollBar().value()
            start = max(0, top // self.row_height) * self.columns
            span = self.card_width + GRID_GAP
            on_screen = []
            for offset, card in enumerate(self.pool):
                index = start + offset
                if index >= len(self.items):
                    card.hide_card()
                    continue
                item = self.items[index]
                row, column = divmod(index, self.columns)
                card.place(GRID_GAP + column * span, GRID_GAP + row * self.row_height - top)
                if card.index != index or card.item is not item or force:
                    card.index = index
                    if force:
                        card.item = None  # rebuild after a rename, move or column change
                    card.bind(item)
                if item.kind == "image":
                    on_screen.append(item.path)
                    if card.photo is None:
                        card.show_thumbnail()  # re-ask for anything that was skipped earlier
            self.loader.set_wanted(on_screen)
        finally:
            self._refreshing = False

    # --- thumbnails arriving from the workers ---------------------------------------------
    def _collect_thumbnails(self):
        arrived = []
        for _ in range(RESULTS_PER_TICK):
            try:
                path, image, dimensions, status = self.loader.results.get_nowait()
            except queue.Empty:
                break
            if status == "ok":
                pm = QPixmap.fromImage(image)
                pm.setDevicePixelRatio(self.ratio)
                self.thumb_cache[path] = (pm, dimensions)
                self.thumb_cache.move_to_end(path)
                while len(self.thumb_cache) > MEMORY_CACHE:
                    self.thumb_cache.popitem(last=False)
                arrived.append(path)
            elif status == "fail":
                self.failed_thumbs.add(path)
                arrived.append(path)

        if arrived:
            wanted = set(arrived)
            for card in self.pool:
                if card.item is not None and card.item.path in wanted:
                    card.tile.setStyleSheet("")
                    card.show_thumbnail()
        left = self.loader.outstanding
        self.loading_label.setText(f"◌  Loading {left}…" if left else "")

    # --- folder navigation ---------------------------------------------------------------
    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select a folder of images",
                                                  self.folder or os.path.expanduser("~"))
        if folder:
            self.load_folder(folder)

    def refresh(self):
        if self.folder:
            self.folder_counts.clear()
            self.load_folder(self.folder)

    def go_up(self):
        parent = os.path.dirname(self.folder or "")
        if parent and parent != self.folder:
            self.load_folder(parent)

    def load_folder(self, folder):
        folder = os.path.normpath(folder)
        self._show_message("Reading folder…")
        self.message.repaint()
        try:
            entries = sorted(os.scandir(folder), key=lambda e: e.name.lower())
            folders = [e.path for e in entries if e.is_dir() and not is_hidden(e.path)]
            images = [e.path for e in entries if e.is_file() and is_listed(e.name)]
        except OSError as err:
            self._show_message("🖼\n\nChoose a folder to see its photos here" if not self.items else "")
            if self.items:
                self.message.hide()
            QMessageBox.critical(self, "Can't open folder", str(err))
            return

        self.folder = folder
        self.items = [Item("folder", p) for p in folders] + [Item("image", p) for p in images]
        self.pending_edits.clear()
        self.folder_label.setText(folder)
        self.folder_label.setToolTip(folder)
        parent = os.path.dirname(folder)
        self.up_button.setEnabled(bool(parent and parent != folder))
        self._update_count()

        for card in self.pool:
            card.hide_card()
        if self.items:
            self.message.hide()
        else:
            self._show_message("📭\n\nThis folder is empty")
        self.grid.verticalScrollBar().setValue(0)
        self.relayout()

    def _update_count(self):
        folders = sum(1 for i in self.items if i.kind == "folder")
        images = len(self.items) - folders
        parts = []
        if folders:
            parts.append(f"{folders} folder{'' if folders == 1 else 's'}")
        parts.append(f"{images} image{'' if images == 1 else 's'}")
        self.count_label.setText("  ·  ".join(parts))
        self.count_label.show()

    def folder_details(self, path):
        count = self.folder_counts.get(path)
        if count is None:
            try:
                count = sum(1 for _ in os.scandir(path))
            except OSError:
                count = -1
            self.folder_counts[path] = count
        if count < 0:
            return "double-click to open"
        return f"{count} item{'' if count == 1 else 's'}  ·  double-click to open"

    def image_details(self, path, dimensions=None):
        try:
            size = format_size(os.path.getsize(path))
        except OSError:
            size = ""
        pixels = f"{dimensions[0]} x {dimensions[1]}" if dimensions else ""
        return "  ·  ".join(part for part in (pixels, size) if part)

    # --- creating folders ----------------------------------------------------------------
    def new_folder(self):
        if not self.folder:
            QMessageBox.information(self, APP_NAME, "Choose a folder first.")
            return
        dialog = QInputDialog(self)
        dialog.setWindowTitle("New folder")
        dialog.setLabelText("Name for the new folder")
        dialog.setTextValue("New folder")
        dialog.setOkButtonText("Create")
        if dialog.exec() != QInputDialog.Accepted or not dialog.textValue().strip():
            return
        problem = self.create_folder(dialog.textValue())
        if problem:
            QMessageBox.critical(self, APP_NAME, problem)

    def create_folder(self, name):
        """Make a sub-folder in the current folder. Returns an error message, or None on success."""
        problem = name_problem(name.strip())
        if problem:
            return problem
        path = os.path.join(self.folder, name.strip())
        try:
            os.mkdir(path)
        except FileExistsError:
            return f'"{name.strip()}" already exists here.'
        except OSError as err:
            return f"Could not create the folder: {err.strerror or err}"
        self.load_folder(self.folder)
        return None

    # --- moving a photo into a folder ----------------------------------------------------
    def move_into_folder(self, path, folder_card):
        item = next((i for i in self.items if i.kind == "image" and i.path == path), None)
        folder_item = folder_card.item
        if item is None or folder_item is None:
            return
        card = next((c for c in self.pool if c.item is item), None)
        destination = os.path.join(folder_item.path, item.name)
        if os.path.exists(destination):
            if card:
                card.set_status(f'✕ "{item.name}" is already in that folder', ERROR)
            return
        try:
            shutil.move(item.path, destination)
        except (OSError, shutil.Error) as err:
            if card:
                card.set_status(f"✕ Move failed: {getattr(err, 'strerror', None) or err}", ERROR)
            return

        moved_name = item.name
        self.note_move(item.path, destination)
        self.items.remove(item)
        self.folder_counts.pop(folder_item.path, None)
        self._update_count()
        self.relayout()
        target = next((c for c in self.pool if c.item is folder_item), None)
        if target:
            target.set_status(f"✓ Moved {moved_name} here", SUCCESS)

    def note_move(self, old_path, new_path):
        """Keep the caches pointing at the right file after a rename or a move."""
        cached = self.thumb_cache.pop(old_path, None)
        if cached:
            self.thumb_cache[new_path] = cached
        if old_path in self.failed_thumbs:
            self.failed_thumbs.discard(old_path)
            self.failed_thumbs.add(new_path)
        self.pending_edits.pop(old_path, None)
        self.folder_counts.pop(old_path, None)
