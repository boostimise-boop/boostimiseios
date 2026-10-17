"""The Photo Enhancer's pieces, drawn like the website's components:

  Dropzone            ImageUploader.tsx (the big drop area, or the small "+ Add" tile)
  ThumbStrip          ImageQueue.tsx (thumbnails with progress rings and status badges)
  CompareView         BeforeAfterSlider.tsx (divider, zoom to 1:1, pan) + its toolbar
  PhotoBox            the original photo on the checkerboard, dimmed while it is enhanced
  ProcessingCard      ProcessingProgress.tsx
  AnalysisPanel       AnalysisPanel.tsx
  ResultDetails       ResultDetails.tsx
  EnhancementControls EnhancementControls.tsx
"""
from __future__ import annotations

import math
import time
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QGridLayout, QHBoxLayout, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from core import webui as W
from core.ui import FlowLayout

from . import api
from .api import EnhanceSettings, preset_for
from .queue import QueueItem


# ---- uploader --------------------------------------------------------------------------------
class Dropzone(QFrame):
    """Click, drop or (on the page) paste JPG / PNG / WEBP photos - several at once."""

    files = Signal(list)   # list[Path]

    def __init__(self, max_mb=lambda: 50, tile: bool = False, parent=None):
        super().__init__(parent)
        self.max_mb, self.tile = max_mb, tile
        self.dragging = False
        self.setAcceptDrops(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setObjectName("drop")
        self.setAttribute(Qt.WA_Hover)
        lay = QVBoxLayout(self)
        if tile:
            lay.setAlignment(Qt.AlignCenter)
            self.setFixedSize(80, 80)
            self.setToolTip("Add more images (or drop / paste them here)")
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(2)
            self.tile_icon = W.Icon("plus", W.MUTED_FG, 20)
            self.tile_text = W.label("Add", 12, 600, W.MUTED_FG, align=Qt.AlignCenter)
            lay.addWidget(self.tile_icon, 0, Qt.AlignHCenter)
            lay.addWidget(self.tile_text, 0, Qt.AlignHCenter)
        else:
            lay.setContentsMargins(24, 72, 24, 72)
            lay.setSpacing(16)
            badge = W.Icon("image-up", W.PRIMARY, 32)
            badge.setFixedSize(64, 64)
            badge.setAlignment(Qt.AlignCenter)
            badge.setStyleSheet(f"background: {W.tint(W.PRIMARY, .1)}; border-radius: 18px;")
            lay.addWidget(badge, 0, Qt.AlignHCenter)
            self.title = W.label("Drag & Drop Images Here", 18, 700, align=Qt.AlignCenter)
            lay.addLayout(W.v_col(self.title, W.label("or", 14, 400, W.MUTED_FG, align=Qt.AlignCenter), spacing=4))
            btn = W.Button("Upload Images", "upload", "default", "lg")
            btn.clicked.connect(self.pick)
            lay.addWidget(btn, 0, Qt.AlignHCenter)
            self.hint = W.label("", 12, 400, W.MUTED_FG, wrap=True, align=Qt.AlignCenter)
            lay.addWidget(self.hint)
            lay.insertStretch(0, 1)
            lay.addStretch(1)
            self.update_hint()
        self._style()

    def update_hint(self):
        if not self.tile:
            self.hint.setText(f"Supported: JPG / JPEG / PNG / WEBP · up to {self.max_mb():g} MB each · "
                              "select several at once · or paste with Ctrl+V")

    def _style(self):
        hover = self.underMouse() and not self.dragging
        border = W.PRIMARY if self.dragging else (W.tint(W.PRIMARY, .6) if hover else W.BORDER)
        bg = W.tint(W.PRIMARY, .05) if self.dragging else (W.tint(W.PRIMARY, .03) if hover else W.CARD)
        radius = 10 if self.tile else 18
        self.setStyleSheet(f"QFrame#drop {{ background: {bg}; border: 2px dashed {border}; border-radius: {radius}px; }}")
        if self.tile:
            color = W.PRIMARY if hover or self.dragging else W.MUTED_FG
            self.tile_icon.set("plus", color)
            W.set_color(self.tile_text, color)
        elif hasattr(self, "title"):
            self.title.setText("Drop your images" if self.dragging else "Drag & Drop Images Here")

    def event(self, e):
        if e.type() in (e.Type.HoverEnter, e.Type.HoverLeave):
            self._style()
        return super().event(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.pick()
        super().mouseReleaseEvent(e)

    def pick(self):
        paths, _ = QFileDialog.getOpenFileNames(self.window(), "Choose photos", "",
                                                "Images (*.jpg *.jpeg *.png *.webp);;All files (*)")
        if paths:
            self.files.emit([Path(p) for p in paths])

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self.dragging = True
            self._style()

    def dragLeaveEvent(self, e):
        self.dragging = False
        self._style()

    def dropEvent(self, e):
        self.dragging = False
        self._style()
        paths = [Path(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            e.acceptProposedAction()
            self.files.emit(paths)


# ---- thumbnails --------------------------------------------------------------------------------
def status_label(it: QueueItem) -> str:
    return {
        "idle": "Ready to enhance", "waiting": "Waiting", "uploading": f"Uploading {round(it.upload * 100)}%",
        "queued": "Queued", "done": "Enhanced",
        "processing": f"{(it.job or {}).get('stage_label') or 'Processing'} {round(it.progress)}%",
        "failed": f"Failed: {it.error or 'error'}",
    }.get(it.status, "Cancelled")


class Thumb(QWidget):
    selected = Signal(str)
    removed = Signal(str)

    TILE = 80

    def __init__(self, item: QueueItem, parent=None):
        super().__init__(parent)
        self.item = item
        self.is_selected = False
        self.setFixedSize(self.TILE + 12, self.TILE + 12)
        self.setCursor(Qt.PointingHandCursor)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_Hover)
        self.refresh()

    def tile_rect(self) -> QRectF:
        return QRectF(4, 8, self.TILE, self.TILE)

    def close_rect(self) -> QRectF:
        t = self.tile_rect()
        return QRectF(t.right() - 14, t.top() - 6, 20, 20)

    def refresh(self):
        self.setToolTip(f"{self.item.name} - {status_label(self.item)}")
        self.update()

    def event(self, e):
        if e.type() in (e.Type.HoverEnter, e.Type.HoverLeave, e.Type.HoverMove):
            self.update()
        return super().event(e)

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        if self.underMouse() and self.close_rect().contains(e.position()):
            self.removed.emit(self.item.id)
        elif self.tile_rect().contains(e.position()):
            self.selected.emit(self.item.id)

    def paintEvent(self, _):
        it = self.item
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        t = self.tile_rect()
        if self.is_selected:  # ring-2 ring-primary ring-offset-2
            p.setPen(QPen(QColor(W.PRIMARY), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(t.adjusted(-3, -3, 3, 3), 13, 13)
        clip = QPainterPath()
        clip.addRoundedRect(t, 10, 10)
        p.save()
        p.setClipPath(clip)
        p.fillRect(t, QColor(W.MUTED))
        if it.thumb is not None and not it.thumb.isNull():
            img = it.thumb
            k = max(t.width() / img.width(), t.height() / img.height())
            sw, sh = t.width() / k, t.height() / k
            p.drawImage(t, img, QRectF((img.width() - sw) / 2, (img.height() - sh) / 2, sw, sh))
        if not self.is_selected and self.underMouse():
            p.fillRect(t, QColor(255, 255, 255, 25))
        busy = it.status in ("uploading", "queued", "processing")
        if busy or it.status == "waiting":
            p.fillRect(t, QColor(0, 0, 0, 140))
            if it.status == "waiting":
                p.setOpacity(.8)
                p.drawPixmap(int(t.center().x() - 10), int(t.center().y() - 10),
                             W.icon_pixmap("clock", "#ffffff", 20, max(2.0, W.dpr())))
                p.setOpacity(1)
            else:
                frac = it.upload if it.status == "uploading" else it.progress / 100 if it.status == "processing" else .02
                r = QRectF(t.center().x() - 15, t.center().y() - 15, 30, 30)
                p.setBrush(Qt.NoBrush)
                p.setPen(QPen(QColor(255, 255, 255, 64), 3))
                p.drawEllipse(r)
                p.setPen(QPen(QColor("#ffffff"), 3, Qt.SolidLine, Qt.RoundCap))
                p.drawArc(r, 90 * 16, -int(360 * 16 * max(.03, min(1.0, frac))))
                p.setFont(W.font(9, 700))
                p.drawText(r, Qt.AlignCenter, "..." if it.status == "queued" else str(round(frac * 100)))
        if it.status in ("failed", "cancelled"):
            c = QColor(W.ROSE_950)
            c.setAlpha(153)
            p.fillRect(t, c)
            p.drawPixmap(int(t.center().x() - 10), int(t.center().y() - 10),
                         W.icon_pixmap("triangle-alert", W.ROSE_100, 20, max(2.0, W.dpr())))
        p.restore()
        p.setPen(QPen(QColor(W.BORDER), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(t.adjusted(.5, .5, -.5, -.5), 10, 10)
        if it.status == "done":
            b = QRectF(t.right() - 24, t.bottom() - 24, 20, 20)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(W.EMERALD))
            p.drawEllipse(b)
            p.drawPixmap(int(b.x() + 4), int(b.y() + 4), W.icon_pixmap("check", "#ffffff", 12, max(2.0, W.dpr()), stroke=3))
        if self.underMouse():  # the remove button
            c = self.close_rect()
            p.setPen(QPen(QColor(W.BORDER), 1))
            p.setBrush(QColor(W.BG))
            p.drawEllipse(c)
            over = c.contains(self.mapFromGlobal(W.cursor_pos()).toPointF())
            p.drawPixmap(int(c.x() + 4), int(c.y() + 4),
                         W.icon_pixmap("x", W.FG if over else W.MUTED_FG, 12, max(2.0, W.dpr())))
        p.end()


class ThumbStrip(QScrollArea):
    """The uploaded photos side by side, then the "+ Add" tile."""

    selected = Signal(str)
    removed = Signal(str)
    files = Signal(list)

    def __init__(self, max_mb, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.NoFrame)
        self.setWidgetResizable(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFixedHeight(Thumb.TILE + 12 + 14)
        self.setStyleSheet("QScrollArea { background: transparent; }")
        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        self.row = QHBoxLayout(inner)
        self.row.setContentsMargins(0, 0, 8, 0)
        self.row.setSpacing(2)
        self.add_tile = Dropzone(max_mb, tile=True)
        self.add_tile.files.connect(self.files)
        holder = QWidget()
        holder.setFixedSize(Thumb.TILE + 12, Thumb.TILE + 12)
        hl = QVBoxLayout(holder)
        hl.setContentsMargins(4, 8, 8, 4)
        hl.addWidget(self.add_tile)
        self.row.addWidget(holder)
        self.row.addStretch(1)
        self.setWidget(inner)
        self.viewport().setAutoFillBackground(False)
        self.thumbs: dict[str, Thumb] = {}

    def sync(self, items: list[QueueItem], selected_id: str | None):
        ids = [i.id for i in items]
        for k in list(self.thumbs):
            if k not in ids:
                w = self.thumbs.pop(k)
                self.row.removeWidget(w)
                w.deleteLater()
        for n, it in enumerate(items):
            th = self.thumbs.get(it.id)
            if th is None:
                th = Thumb(it)
                th.selected.connect(self.selected)
                th.removed.connect(self.removed)
                self.thumbs[it.id] = th
                self.row.insertWidget(n, th)
            th.item = it
            th.is_selected = it.id == selected_id
            th.refresh()

    def refresh_item(self, item_id: str):
        th = self.thumbs.get(item_id)
        if th is not None:
            th.refresh()

    def wheelEvent(self, e):  # a mouse wheel scrolls the strip sideways
        bar = self.horizontalScrollBar()
        bar.setValue(bar.value() - e.angleDelta().y())
        e.accept()


# ---- images: levels for sharp downscaling --------------------------------------------------
def mip_levels(img: QImage, smallest: int = 480) -> list[QImage]:
    """The image and halved copies (area-averaged), so a big photo stays sharp when shown small.
    Worker thread."""
    levels = [img]
    while levels[-1].width() > smallest * 2 and levels[-1].height() > 8:
        last = levels[-1]
        levels.append(last.scaled(max(1, last.width() // 2), max(1, last.height() // 2),
                                  Qt.IgnoreAspectRatio, Qt.SmoothTransformation))
    return levels


def pick_level(levels: list[QImage], want_width: float) -> QImage:
    best = levels[0]
    for lv in levels:
        if lv.width() >= want_width * .95:
            best = lv
    return best


def draw_contained(p: QPainter, levels: list[QImage], target: QRectF, clip: QRectF):
    """Draws the image object-contain inside ``target`` (which may be zoomed beyond ``clip``)."""
    full = levels[0]
    if full.isNull():
        return
    k = min(target.width() / full.width(), target.height() / full.height())
    w, h = full.width() * k, full.height() * k
    dest = QRectF(target.x() + (target.width() - w) / 2, target.y() + (target.height() - h) / 2, w, h)
    vis = dest.intersected(clip)
    if vis.isEmpty():
        return
    img = pick_level(levels, w * W.dpr())
    sx, sy = img.width() / dest.width(), img.height() / dest.height()
    src = QRectF((vis.x() - dest.x()) * sx, (vis.y() - dest.y()) * sy, vis.width() * sx, vis.height() * sy)
    p.drawImage(vis, img, src)


# ---- before / after ----------------------------------------------------------------------------
class CompareView(QWidget):
    """Drag the divider; scroll to zoom (up to 1:1 x 2), drag to pan when zoomed, double-click to
    inspect, arrow keys move the divider, +/- zoom, 0 fits."""

    zoom_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.before: list[QImage] = []
        self.after: list[QImage] = []
        self.res_w, self.res_h = 1, 1
        self.pos = 50.0
        self.zoom = 1.0
        self.off = QPointF(0, 0)
        self.drag: dict | None = None
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.setMinimumHeight(220)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setToolTip("Before and after comparison. Arrow keys move the divider, plus and minus zoom.")
        self._pulse = 0.0
        self._pulse_timer = QTimer(self, interval=60, timeout=self._tick)

    def _tick(self):
        self._pulse = (self._pulse + .06) % 1
        self.update()

    def set_result(self, width: int, height: int):
        self.res_w, self.res_h = max(1, width), max(1, height)
        self.pos, self.zoom, self.off = 50.0, 1.0, QPointF(0, 0)
        self.after = []
        self._pulse_timer.start()
        self.update()
        self.zoom_changed.emit()

    def set_before(self, levels: list[QImage]):
        self.before = levels
        self.update()

    def set_after(self, levels: list[QImage]):
        self.after = levels
        self._pulse_timer.stop()
        self.update()

    # geometry
    def box(self) -> QRectF:
        """The comparison box: the result's aspect ratio, as large as fits."""
        avail = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        k = min(avail.width() / self.res_w, avail.height() / self.res_h)
        w, h = self.res_w * k, self.res_h * k
        return QRectF(avail.x() + (avail.width() - w) / 2, avail.y(), w, h)

    def one_to_one(self) -> float:
        b = self.box()
        return max(1.0, self.res_w / b.width()) if b.width() > 0 else 1.0

    def max_zoom(self) -> float:
        return max(8.0, self.one_to_one() * 2)

    def _clamp(self, off: QPointF, z: float) -> QPointF:
        b = self.box()
        return QPointF(min(0.0, max(b.width() - b.width() * z, off.x())), min(0.0, max(b.height() - b.height() * z, off.y())))

    def zoom_at(self, z: float, px: float, py: float):
        nz = min(self.max_zoom(), max(1.0, z))
        k = nz / self.zoom
        self.off = self._clamp(QPointF(px - (px - self.off.x()) * k, py - (py - self.off.y()) * k), nz)
        self.zoom = nz
        self.update()
        self.zoom_changed.emit()

    def zoom_center(self, factor: float):
        b = self.box()
        self.zoom_at(self.zoom * factor, b.width() / 2, b.height() / 2)

    def fit(self):
        self.zoom_at(1.0, 0, 0)

    def actual_pixels(self):
        b = self.box()
        self.zoom_at(self.one_to_one(), b.width() / 2, b.height() / 2)

    def percent(self) -> int:
        return round(self.zoom / self.one_to_one() * 100)

    def _local(self, e) -> QPointF:
        b = self.box()
        pt = e.position()
        return QPointF(pt.x() - b.x(), pt.y() - b.y())

    # input
    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        b = self.box()
        p = self._local(e)
        div_x = self.pos / 100 * b.width()
        if abs(p.x() - div_x) < 24 or self.zoom <= 1.001:
            self.drag = {"kind": "divider"}
            self.pos = min(100.0, max(0.0, p.x() / b.width() * 100))
        else:
            self.drag = {"kind": "pan", "x": e.position().x(), "y": e.position().y(), "ox": self.off.x(), "oy": self.off.y()}
        self._cursor(p)
        self.update()

    def mouseMoveEvent(self, e):
        p = self._local(e)
        if not self.drag:
            self._cursor(p)
            return
        b = self.box()
        if self.drag["kind"] == "divider":
            self.pos = min(100.0, max(0.0, p.x() / b.width() * 100))
        else:
            self.off = self._clamp(QPointF(self.drag["ox"] + e.position().x() - self.drag["x"],
                                           self.drag["oy"] + e.position().y() - self.drag["y"]), self.zoom)
        self.update()

    def mouseReleaseEvent(self, e):
        self.drag = None
        self._cursor(self._local(e))

    def _cursor(self, p: QPointF):
        if self.drag and self.drag["kind"] == "pan":
            self.setCursor(Qt.ClosedHandCursor)
        elif self.zoom > 1.001 and abs(p.x() - self.pos / 100 * self.box().width()) >= 24:
            self.setCursor(Qt.OpenHandCursor)
        else:
            self.setCursor(Qt.SizeHorCursor)

    def mouseDoubleClickEvent(self, e):
        p = self._local(e)
        self.zoom_at(1.0 if self.zoom > 1.5 else 3.0, p.x(), p.y())

    def wheelEvent(self, e):
        p = self._local(e)
        dy = -e.angleDelta().y() * (100 / 120)
        self.zoom_at(self.zoom * math.exp(-dy * 0.0015), p.x(), p.y())
        e.accept()

    def keyPressEvent(self, e):
        step = 10 if e.modifiers() & Qt.ShiftModifier else 2
        k = e.key()
        if k == Qt.Key_Left:
            self.pos = max(0.0, self.pos - step)
        elif k == Qt.Key_Right:
            self.pos = min(100.0, self.pos + step)
        elif k in (Qt.Key_Plus, Qt.Key_Equal):
            self.zoom_center(1.5)
        elif k == Qt.Key_Minus:
            self.zoom_center(1 / 1.5)
        elif k == Qt.Key_0:
            self.fit()
        else:
            super().keyPressEvent(e)
            return
        self.update()

    def resizeEvent(self, e):
        self.off = self._clamp(self.off, self.zoom)
        super().resizeEvent(e)
        self.zoom_changed.emit()

    # drawing
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        b = self.box()
        clip = QPainterPath()
        clip.addRoundedRect(b, 14, 14)
        p.save()
        p.setClipPath(clip)
        W.checkerboard(p, b)
        layer = QRectF(b.x() + self.off.x(), b.y() + self.off.y(), b.width() * self.zoom, b.height() * self.zoom)
        if self.after:
            draw_contained(p, self.after, layer, b)
        before_clip = QRectF(b.x(), b.y(), b.width() * self.pos / 100, b.height())
        if self.before and before_clip.width() > 0:
            p.save()
            p.setClipRect(before_clip, Qt.IntersectClip)
            if not self.after:
                p.fillRect(before_clip, QColor(W.MUTED))
            draw_contained(p, self.before, layer, before_clip)
            p.restore()
        if not self.after:  # loading the result
            p.fillRect(b, QColor(255, 255, 255, 102))
            text = "Loading full-resolution result..."
            p.setFont(W.font(14))
            tw = p.fontMetrics().horizontalAdvance(text)
            pill = QRectF(b.center().x() - (tw + 44) / 2, b.center().y() - 18, tw + 44, 36)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 230))
            p.drawRoundedRect(pill, 18, 18)
            dot = QColor(W.PRIMARY)
            dot.setAlphaF(.5 + .5 * abs(math.sin(self._pulse * math.pi)))
            p.setBrush(dot)
            p.drawEllipse(QRectF(pill.x() + 16, pill.center().y() - 4, 8, 8))
            p.setPen(QColor(W.FG))
            p.drawText(pill.adjusted(32, 0, -12, 0), Qt.AlignVCenter | Qt.AlignLeft, text)
        # labels
        f = W.font(11, 700)
        f.setLetterSpacing(QFont.AbsoluteSpacing, 1.1)
        p.setFont(f)
        for text, left, bg, hide in (("BEFORE", True, QColor(0, 0, 0, 153), self.pos < 12),
                                     ("AFTER", False, QColor(0, 98, 233, 217), self.pos > 88)):
            if hide:
                continue
            tw = p.fontMetrics().horizontalAdvance(text) + 16
            r = QRectF(b.x() + 12 if left else b.right() - 12 - tw, b.y() + 12, tw, 22)
            p.setPen(Qt.NoPen)
            p.setBrush(bg)
            p.drawRoundedRect(r, 6, 6)
            p.setPen(QColor("#ffffff"))
            p.drawText(r, Qt.AlignCenter, text)
        # divider
        x = b.x() + b.width() * self.pos / 100
        p.setPen(QPen(QColor(0, 0, 0, 60), 4))
        p.drawLine(QPointF(x, b.top()), QPointF(x, b.bottom()))
        p.setPen(QPen(QColor("#ffffff"), 2))
        p.drawLine(QPointF(x, b.top()), QPointF(x, b.bottom()))
        h = QRectF(x - 20, b.center().y() - 20, 40, 40)
        p.setPen(QPen(QColor("#ffffff"), 2))
        p.setBrush(QColor(0, 0, 0, 128))
        p.drawEllipse(h.adjusted(1, 1, -1, -1))
        p.drawPixmap(int(h.x() + 10), int(h.y() + 10), W.icon_pixmap("chevrons-left-right", "#ffffff", 20, max(2.0, W.dpr())))
        p.restore()
        p.setPen(QPen(QColor(W.BORDER), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(b.adjusted(.5, .5, -.5, -.5), 14, 14)
        if self.hasFocus():
            p.setPen(QPen(QColor(77, 163, 255, 128), 3))
            p.drawRoundedRect(b.adjusted(-1, -1, 1, 1), 15, 15)
        p.end()


class CompareToolbar(QWidget):
    """−, zoom %, +, Fit, 1:1 and the hint, under the comparison."""

    def __init__(self, view: CompareView, parent=None):
        super().__init__(parent)
        self.view = view
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addStretch(1)
        self.minus = W.Button("", "minus", "outline", "icon")
        self.minus.setToolTip("Zoom out")
        self.minus.clicked.connect(lambda: view.zoom_center(1 / 1.5))
        self.pct = W.label("100%", 12, 400, W.MUTED_FG, mono=True, align=Qt.AlignCenter)
        self.pct.setFixedWidth(56)
        self.plus = W.Button("", "plus", "outline", "icon")
        self.plus.setToolTip("Zoom in")
        self.plus.clicked.connect(lambda: view.zoom_center(1.5))
        fit = W.Button("Fit", "maximize", "outline", "sm", height=32, weight=600)
        fit.setToolTip("Fit to view (0)")
        fit.clicked.connect(view.fit)
        one = W.Button("1:1", "scan-search", "outline", "sm", height=32, weight=600)
        one.setToolTip("Actual pixels of the enhanced image")
        one.clicked.connect(view.actual_pixels)
        self.hint = W.label("Scroll to zoom · drag to pan · double-click to inspect", 12, 400, W.MUTED_FG)
        for w in (self.minus, self.pct, self.plus, fit, one):
            lay.addWidget(w)
        lay.addSpacing(8)
        lay.addWidget(self.hint)
        lay.addStretch(1)
        view.zoom_changed.connect(self.refresh)
        self.refresh()

    def refresh(self):
        self.pct.setText(f"{self.view.percent()}%")
        self.minus.setEnabled(self.view.zoom > 1.001)
        self.plus.setEnabled(self.view.zoom < self.view.max_zoom())

    def resizeEvent(self, e):
        self.hint.setVisible(e.size().width() > 640)
        super().resizeEvent(e)


# ---- processing -----------------------------------------------------------------------------
STEPS = [("analyzing", "Analyzing image..."), ("detecting_faces", "Detecting faces..."),
         ("restoring", "Restoring image..."), ("upscaling", "Upscaling..."), ("finalizing", "Finalizing...")]
ORDER = {"waiting": -3, "uploading": -2, "queued": -1, "analyzing": 0, "detecting_faces": 1, "restoring": 2,
         "upscaling": 3, "finalizing": 4, "saving": 4, "done": 5}


class _StepDot(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(24, 24)
        self.state = "todo"
        self.angle = 0

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(.5, .5, 23, 23)
        if self.state == "done":
            p.setPen(QPen(QColor(W.PRIMARY), 1))
            p.setBrush(QColor(W.PRIMARY))
            p.drawEllipse(r)
            p.drawPixmap(5, 5, W.icon_pixmap("check", "#ffffff", 14, max(2.0, W.dpr())))
        elif self.state == "active":
            p.setPen(QPen(QColor(W.PRIMARY), 1))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(r)
            p.drawPixmap(5, 5, W.icon_pixmap("loader", W.PRIMARY, 14, max(2.0, W.dpr()), self.angle))
        else:
            c = QColor(W.MUTED_FG)
            c.setAlphaF(.5)
            p.setPen(QPen(QColor(W.BORDER), 1))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(r)
            p.setPen(Qt.NoPen)
            p.setBrush(c)
            p.drawEllipse(QRectF(9, 9, 6, 6))
        p.end()


class ProcessingCard(QFrame):
    """Headline + %, progress bar, elapsed seconds, the five steps and Cancel."""

    cancel = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("proc")
        self.setStyleSheet(f"QFrame#proc {{ background: rgba(255, 255, 255, 214); border: 1px solid {W.BORDER}; "
                           f"border-radius: 18px; }}")
        W.shadow(self, blur=40, dy=16, alpha=.14)
        self.setMaximumWidth(448)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 16)
        lay.setSpacing(24)
        self.headline = W.label("Working...", 18, 700)
        self.pct = W.label("0%", 14, 400, W.MUTED_FG, mono=True)
        self.bar = W.Bar(8)
        self.elapsed = W.label("0.0s elapsed", 12, 400, W.MUTED_FG, mono=True)
        lay.addLayout(W.v_col(W.h_row(self.headline, None, self.pct), self.bar, self.elapsed, spacing=12))
        steps = QVBoxLayout()
        steps.setSpacing(10)
        self.dots, self.step_labels = [], []
        for _, text in STEPS:
            dot = _StepDot()
            lab = W.label(text, 14)
            self.dots.append(dot)
            self.step_labels.append(lab)
            steps.addLayout(W.h_row(dot, lab, None, spacing=12))
        lay.addLayout(steps)
        btn = W.Button("Cancel", "x", "ghost-muted", "sm", full=True)
        btn.clicked.connect(self.cancel)
        lay.addWidget(btn)
        self.started_at = time.time()
        self.stage, self.progress, self.upload, self.queue_position = "waiting", 0.0, 0.0, None
        self.timer = QTimer(self, interval=250, timeout=self._tick)
        self.spin = QTimer(self, interval=40, timeout=self._spin)

    def set_state(self, stage: str, progress: float, upload: float, queue_position, started_at: float):
        self.stage, self.progress, self.upload, self.queue_position = stage, progress, upload, queue_position
        self.started_at = started_at
        headline = next((t for k, t in STEPS if k == stage), "Working...")
        if stage == "uploading":
            headline = f"Uploading... {round(upload * 100)}%"
        elif stage == "queued":
            headline = f"Waiting in queue (#{queue_position})..." if queue_position and queue_position > 1 else "Starting..."
        elif stage == "waiting":
            headline = (f"Waiting for {queue_position} photo{'' if queue_position == 1 else 's'} ahead..."
                        if queue_position and queue_position > 0 else "Waiting...")
        elif stage == "saving":
            headline = "Finalizing..."
        pct = upload * 100 if stage == "uploading" else progress
        self.headline.setText(headline)
        self.pct.setText(f"{round(pct)}%")
        self.bar.set(pct / 100)
        current = ORDER.get(stage, -1)
        for i, (dot, lab) in enumerate(zip(self.dots, self.step_labels)):
            dot.state = "done" if current > i else "active" if current == i else "todo"
            dot.update()
            lab.setFont(W.font(14, 600 if current == i else 400))
            W.set_color(lab, W.MUTED_FG if current > i else W.FG if current == i else W.tint(W.MUTED_FG, .6))
        self._tick()

    def _tick(self):
        self.elapsed.setText(f"{max(0.0, time.time() - self.started_at):.1f}s elapsed")

    def _spin(self):
        for d in self.dots:
            if d.state == "active":
                d.angle = (d.angle + 18) % 360
                d.update()

    def showEvent(self, e):
        self.timer.start()
        self.spin.start()
        super().showEvent(e)

    def hideEvent(self, e):
        self.timer.stop()
        self.spin.stop()
        super().hideEvent(e)


class FailedCard(QFrame):
    retry = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("failed")
        self.setStyleSheet(f"QFrame#failed {{ background: rgba(255, 255, 255, 230); border: 1px solid {W.BORDER}; "
                           f"border-radius: 18px; }}")
        W.shadow(self, blur=40, dy=16, alpha=.14)
        self.setMaximumWidth(448)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(12)
        lay.addWidget(W.Icon("triangle-alert", W.ROSE, 24), 0, Qt.AlignHCenter)
        self.text = W.label("", 14, 600, wrap=True, align=Qt.AlignCenter)
        lay.addWidget(self.text)
        btn = W.Button("Try again", "refresh-cw", "default", "sm")
        btn.clicked.connect(self.retry)
        lay.addWidget(btn, 0, Qt.AlignHCenter)


class PhotoBox(QWidget):
    """The original photo on the checkerboard (dimmed and softened while it is being enhanced),
    with the "not enhanced yet" pill, the progress card or the failure card over it."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.levels: list[QImage] = []
        self.soft: QImage | None = None
        self.busy = False
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumHeight(300)
        self.idle_pill = QFrame(self)
        self.idle_pill.setObjectName("pill")
        self.idle_pill.setStyleSheet(f"QFrame#pill {{ background: rgba(255, 255, 255, 230); border: 1px solid {W.BORDER}; "
                                     f"border-radius: 13px; }}")
        pl = QHBoxLayout(self.idle_pill)
        pl.setContentsMargins(12, 0, 12, 0)
        pl.setSpacing(6)
        pl.addWidget(W.Icon("wand-2", W.MUTED_FG, 14))
        pl.addWidget(W.label("Not enhanced yet - press Enhance photo", 12, 600, W.MUTED_FG))
        self.idle_pill.setFixedHeight(26)
        self.idle_pill.hide()
        self.progress = ProcessingCard(self)
        self.progress.hide()
        self.failed = FailedCard(self)
        self.failed.hide()

    def set_image(self, levels: list[QImage]):
        self.levels = levels
        self.soft = None
        if levels:
            small = pick_level(levels, 320)
            # blur-[2px]: down and up again
            tiny = small.scaled(max(1, small.width() // 6), max(1, small.height() // 6), Qt.IgnoreAspectRatio,
                                Qt.SmoothTransformation)
            self.soft = tiny.scaled(small.width(), small.height(), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        self.update()

    def set_mode(self, mode: str):
        """idle | busy | failed | none"""
        self.busy = mode == "busy"
        self.idle_pill.setVisible(mode == "idle")
        self.progress.setVisible(mode == "busy")
        self.failed.setVisible(mode == "failed")
        self.setMinimumHeight(460 if self.busy else 300)
        self._place()
        self.update()

    def resizeEvent(self, e):
        self._place()
        super().resizeEvent(e)

    def _place(self):
        r = self.rect()
        self.idle_pill.adjustSize()
        self.idle_pill.move((r.width() - self.idle_pill.width()) // 2, r.height() - 12 - self.idle_pill.height())
        for card in (self.progress, self.failed):
            w = min(448, r.width() - 32)
            card.setFixedWidth(max(240, w))
            card.adjustSize()
            card.move((r.width() - card.width()) // 2, max(8, (r.height() - card.height()) // 2))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        r = QRectF(self.rect()).adjusted(.5, .5, -.5, -.5)
        clip = QPainterPath()
        clip.addRoundedRect(r, 18, 18)
        p.save()
        p.setClipPath(clip)
        W.checkerboard(p, r)
        if self.levels:
            if self.busy and self.soft is not None:
                p.setOpacity(.4)
                pad = r.width() * .005
                draw_contained(p, [self.soft], r.adjusted(-pad, -pad, pad, pad), r)
                p.setOpacity(1)
            else:
                draw_contained(p, self.levels, r.adjusted(0, 0, 0, 0), r)
        p.restore()
        p.setPen(QPen(QColor(W.BORDER), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(r, 18, 18)
        p.end()


# ---- analysis & result ------------------------------------------------------------------------
class _Meter(QWidget):
    def __init__(self, label_text: str, value: float, invert: bool = False):
        super().__init__()
        v = max(0.0, min(1.0, value))
        bad = 1 - v if invert else v
        color = W.ROSE if bad > .6 else W.AMBER if bad > .3 else W.EMERALD
        bar = W.Bar(6)
        bar.set(v, color, minimum=.04)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        lay.addLayout(W.h_row(W.label(label_text, 12, 400, W.MUTED_FG), None,
                              W.label(str(round(v * 100)), 12, 400, W.FG, mono=True)))
        lay.addWidget(bar)


def _panel() -> W.Box:
    return W.Box(radius=14, bg=W.CARD, border=W.BORDER, padding=16, spacing=16)


class AnalysisPanel(QWidget):
    """"AI is analyzing your photo..." and then the analysis: quality, type, size, faces, the
    four problem meters and why the pipeline was chosen."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.content: QWidget | None = None
        self._shown = None

    def show_data(self, data: dict | None, loading: bool):
        key = (id(data) if data else None, loading)
        if key == self._shown:
            return
        self._shown = key
        if self.content is not None:
            self.content.hide()
            self.content.deleteLater()
            self.content = None
        if loading:
            box = W.Box(radius=14, bg=W.CARD, border=W.BORDER, padding=16, layout="h", spacing=8)
            box.add(W.Spinner(16, W.MUTED_FG))
            box.add(W.label("AI is analyzing your photo...", 14, 400, W.MUTED_FG))
            box.lay.addStretch(1)
            self.content = box
        elif data:
            self.content = self._build(data)
        if self.content is not None:
            self.lay.addWidget(self.content)
        self.setVisible(self.content is not None)

    def _build(self, data: dict) -> QWidget:
        a = data.get("analysis") or {}
        faces = data.get("faces") or []
        box = _panel()
        quality = W.Badge(f"quality {round(a.get('quality_score', 0))}/100", "outline", mono=True)
        box.add(W.h_row(W.Icon("brain", W.PRIMARY, 16), W.label("AI analysis", 14, 700), None, quality, spacing=8))
        badges = QWidget()
        flow = FlowLayout(badges, 6, 6)
        type_label = {"photo": "Photo", "illustration": "Illustration", "document": "Document"}.get(a.get("image_type"), "Photo")
        texts = [type_label, f"{a.get('width')}×{a.get('height')} · {a.get('megapixels', 0):.1f} MP",
                 f"{len(faces)} face{'' if len(faces) == 1 else 's'}"]
        if a.get("jpeg_quality") is not None:
            texts.append(f"JPEG q≈{a['jpeg_quality']}")
        if a.get("grayscale"):
            texts.append("B&W")
        if a.get("sepia"):
            texts.append("Sepia")
        for t in texts:
            flow.addWidget(W.Badge(t, "secondary"))
        box.add(badges)
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(10)
        meters = [("Blur", max(0.0, (a.get("blur", 0) - .3) / .45)), ("Noise", a.get("noise", 0)),
                  ("Compression", a.get("jpeg_artifacts", 0)), ("Low resolution", a.get("low_resolution", 0))]
        for i, (name, v) in enumerate(meters):
            grid.addWidget(_Meter(name, v), i // 2, i % 2)
        box.add(grid)
        reasons = (data.get("plan") or {}).get("reasons") or []
        if reasons:
            box.add(W.Separator())
            for r in reasons:
                row = W.h_row(W.Icon("circle-check", W.PRIMARY, 14), W.label(r, 12, 400, W.MUTED_FG, wrap=True),
                              spacing=8, stretch_at=1)
                row.setAlignment(Qt.AlignTop)
                box.add(row)
        return box


STAGE_LABELS = {"analysis": "Analysis", "face_detection": "Face detection", "face_restoration": "Face restoration",
                "upscaling": "Upscaling", "finalizing": "Color / sharpen", "saving": "Saving"}


def result_details(result: dict) -> QWidget:
    b = result.get("benchmark") or {}
    v = result.get("validation") or {}
    box = _panel()
    faces = (f"{b.get('faces_restored', 0)} of {b['faces_detected']} restored"
             + (f" ({b['face_model']})" if b.get("face_model") else "")) if b.get("faces_detected") else "none detected"
    rows = [("gauge", "Resolution", f"{b.get('input')} → {b.get('output')}"), ("scan-face", "Faces", faces),
            ("timer", "Time", f"{b.get('total_seconds', 0):.1f} s")]
    for icon, key, val in rows:
        k = W.label(key, 14, 400, W.MUTED_FG)
        k.setFixedWidth(80)
        box.add(W.h_row(W.Icon(icon, W.MUTED_FG, 16), k, W.label(val, 14, 600, wrap=True), spacing=10, stretch_at=2))
    total = b.get("total_seconds") or 1
    stages = [(k, s) for k, s in (b.get("seconds") or {}).items() if s > 0.01]
    if stages:
        box.add(W.Separator())
        for k, s in stages:
            name = W.label(STAGE_LABELS.get(k, k), 12, 400, W.MUTED_FG)
            name.setFixedWidth(112)
            bar = W.Bar(6, W.tint(W.PRIMARY, .7))
            bar.set(min(1.0, s / total))
            sec = W.label(f"{s:.1f}s", 12, 400, W.MUTED_FG, mono=True, align=Qt.AlignRight | Qt.AlignVCenter)
            sec.setFixedWidth(48)
            box.add(W.h_row(name, bar, sec, spacing=8, stretch_at=1))
    fr = result.get("face_restoration") or []
    if fr:
        box.add(W.Separator())
        for f in fr:
            fid = f", fidelity {f['fidelity']:.2f}" if f.get("fidelity") is not None else ""
            box.add(W.label(f"Face {f['index'] + 1}: {f['model']}{fid}, blend {round(f.get('blend', 0) * 100)}%",
                            12, 400, W.MUTED_FG))
    warnings = v.get("warnings") or []
    if warnings:
        box.add(W.Separator())
        for w in warnings:
            row = W.h_row(W.Icon("triangle-alert", W.AMBER_600, 14), W.label(w, 12, 400, W.AMBER_600, wrap=True),
                          spacing=8, stretch_at=1)
            box.add(row)
    if result.get("preview_downscaled"):
        box.add(W.Separator())
        box.add(W.label("The comparison view is downscaled for the screen - downloads are full resolution.",
                        12, 400, W.MUTED_FG, wrap=True))
    return box


# ---- controls -------------------------------------------------------------------------------
MODE_ITEMS = [("auto", "Auto", "sparkles", "Analyze the photo and choose the pipeline automatically"),
              ("face", "Face Enhance", "scan-face", "Prioritise AI face restoration"),
              ("restore", "Photo Restore", "history", "Old, blurry, noisy, scanned or heavily compressed photos"),
              ("upscale", "Upscale", "maximize-2", "Pure AI super-resolution")]


class AutoSlider(QWidget):
    """A 0-100 % slider that can be on "Auto" (value None): the AUTO badge, or a reset button."""

    changed = Signal(object)   # float | None

    def __init__(self, title: str, hint: str, parent=None):
        super().__init__(parent)
        self.value: float | None = None
        self.auto: float | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self.title = W.label(title, 14, 600)
        self.title.setToolTip(hint)
        self.title.setCursor(Qt.WhatsThisCursor)
        self.badge = W.Badge("Auto", "secondary", px=10, upper=True)
        self.reset = W.Button("", "rotate-ccw", "ghost-muted", "icon-sm", height=20, icon_size=12)
        self.reset.setToolTip(f"Reset {title} to automatic")
        self.reset.clicked.connect(lambda: self.changed.emit(None))
        self.pct = W.label("50%", 12, 400, W.MUTED_FG, mono=True, align=Qt.AlignRight | Qt.AlignVCenter)
        self.pct.setFixedWidth(36)
        lay.addLayout(W.h_row(self.title, None, self.badge, self.reset, self.pct, spacing=6))
        self.slider = W.Slider(0, 100, 50)
        self.slider.valueChanged.connect(self._moved)
        lay.addWidget(self.slider)
        self._updating = False

    def set(self, value: float | None, auto: float | None, enabled: bool = True):
        self.value, self.auto = value, auto
        shown = value if value is not None else auto if auto is not None else .5
        self._updating = True
        self.slider.setValue(round(shown * 100))
        self._updating = False
        self.slider.set_dim(value is None)
        self.badge.setVisible(value is None)
        self.reset.setVisible(value is not None)
        self.pct.setText(f"{round(shown * 100)}%")
        self.slider.setEnabled(enabled)
        self.reset.setEnabled(enabled)

    def _moved(self, v: int):
        if not self._updating:
            self.changed.emit(v / 100)


class EnhancementControls(QWidget):
    settings_changed = Signal(object)    # EnhanceSettings
    enhance = Signal()
    apply_all_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.s = api.DEFAULT_SETTINGS
        self.analysis: dict | None = None
        self.input_size: tuple[int, int] | None = None
        self.max_scale = 4
        self.disabled = False
        self.busy = False
        self.apply_all = False
        self.photo_count = 1
        self.enhance_count = 1
        self.advanced = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(24)

        def section_title(text):
            return W.label(text.upper(), 12, 700, W.MUTED_FG, upper_spacing=.6)

        # mode
        grid = QGridLayout()
        grid.setSpacing(8)
        self.mode_tiles = {}
        for n, (value, text, icon, hint) in enumerate(MODE_ITEMS):
            t = W.ToggleTile(text, icon, 44)
            t.setToolTip(hint)
            t.clicked.connect(lambda _=False, v=value: self._set_mode(v))
            grid.addWidget(t, n // 2, n % 2)
            self.mode_tiles[value] = t
        lay.addLayout(W.v_col(section_title("Enhancement"), grid, spacing=12))

        # scale
        self.dims = W.label("", 12, 400, W.MUTED_FG, mono=True)
        srow = QHBoxLayout()
        srow.setSpacing(8)
        self.scale_tiles = {}
        for k in (2, 4):
            t = W.ToggleTile(f"{k}×", None, 40, 16, 700, align_left=False)
            t.clicked.connect(lambda _=False, v=k: self._set(scale=v))
            srow.addWidget(t)
            self.scale_tiles[k] = t
        lay.addLayout(W.v_col(W.h_row(section_title("Upscale"), None, self.dims), srow, spacing=12))
        lay.addWidget(W.Separator())

        # strength
        self.strength_pct = W.label("80%", 12, 400, W.MUTED_FG, mono=True)
        st = W.label("Enhancement strength", 14, 600)
        st.setToolTip("How much of the AI result is used. Lower keeps more of the original look.")
        st.setCursor(Qt.WhatsThisCursor)
        self.strength = W.Slider(0, 100, 80)
        self.strength.valueChanged.connect(lambda v: self._set(strength=v / 100) if not self._updating else None)
        lay.addLayout(W.v_col(W.h_row(st, None, self.strength_pct), self.strength, spacing=10))

        # face
        self.faces_badge = W.Badge("0 faces", "secondary", px=10)
        self.face_switch = W.Switch(True)
        self.face_switch.toggled.connect(lambda v: self._set(face_enhance=v) if not self._updating else None)
        face = QVBoxLayout()
        face.setSpacing(12)
        face.addLayout(W.h_row(W.label("Face enhancement", 14, 600), self.faces_badge, None, self.face_switch, spacing=8))
        self.adv_btn = W.Button("Face model && fidelity", "chevron-down", "ghost-muted", "xs", font_px=12, weight=400)
        self.adv_btn.setStyleSheet(self.adv_btn.styleSheet() + "QPushButton { padding: 0; } QPushButton:hover { background: transparent; }")
        self.adv_btn.clicked.connect(self._toggle_advanced)
        self.adv = QWidget()
        adv = QVBoxLayout(self.adv)
        adv.setContentsMargins(0, 4, 0, 0)
        adv.setSpacing(16)
        self.face_model = W.Select([("auto", "Auto (recommended)"), ("codeformer", "Balanced"),
                                    ("gfpgan", "Sharp restore"), ("compare", "Compare both, keep most faithful")])
        self.face_model.currentIndexChanged.connect(
            lambda _: self._set(face_model=self.face_model.value()) if not self._updating else None)
        adv.addLayout(W.v_col(W.label("Model", 12, 400, W.MUTED_FG), self.face_model, spacing=8))
        self.fidelity = AutoSlider("Identity fidelity", "Higher preserves the person's identity and original features; "
                                   "lower restores more aggressively.")
        self.fidelity.changed.connect(lambda v: self._set(fidelity=v))
        adv.addWidget(self.fidelity)
        self.adv.hide()
        self.face_more = W.wrap(W.v_col(W.h_row(self.adv_btn, None), self.adv, spacing=0))
        face.addWidget(self.face_more)
        lay.addLayout(face)
        lay.addWidget(W.Separator())

        self.denoise = AutoSlider("Denoise", "Removes camera, low-light and compression noise. Auto uses the measured "
                                  "noise level of your photo.")
        self.denoise.changed.connect(lambda v: self._set(denoise=v))
        lay.addWidget(self.denoise)
        self.sharpen = AutoSlider("Sharpen", "Halo-free detail sharpening applied after upscaling. Restored faces are protected.")
        self.sharpen.changed.connect(lambda v: self._set(sharpen=v))
        lay.addWidget(self.sharpen)

        ct = W.label("Color enhancement", 14, 600)
        ct.setToolTip("Gentle exposure, contrast, white-balance and vibrance correction with a built-in limit on colour change.")
        ct.setCursor(Qt.WhatsThisCursor)
        self.color_switch = W.Switch(True)
        self.color_switch.toggled.connect(lambda v: self._set(color_enhance=v) if not self._updating else None)
        lay.addLayout(W.h_row(ct, None, self.color_switch))

        # apply all
        box = W.Box(radius=10, bg=W.tint(W.MUTED, .4), border=W.BORDER, padding=12, layout="h", spacing=12)
        self.apply_check = W.CheckBox("")
        self.apply_check.toggled.connect(self._apply_all)
        self.apply_text = W.label("Enhance photo runs on the selected photo only.", 12, 400, W.MUTED_FG, wrap=True)
        title = W.label("Enhance all photos at once", 14, 600)
        title.setCursor(Qt.PointingHandCursor)
        title.mousePressEvent = lambda e: self.apply_check.toggle()
        box.add(self.apply_check, 0, Qt.AlignTop)
        box.add(W.v_col(title, self.apply_text, spacing=4), 1)
        lay.addWidget(box)

        self.button = W.Button("ENHANCE PHOTO", "wand-2", "gradient", "2xl", full=True, weight=700)
        self.button.clicked.connect(self.enhance)
        W.shadow(self.button, blur=24, dy=8, alpha=.25, color=W.BLUE)
        lay.addWidget(self.button)
        self._updating = False
        self.refresh()

    # ---- state in -------------------------------------------------------------------------
    def set_state(self, settings: EnhanceSettings, analysis: dict | None, input_size, max_scale: int, disabled: bool,
                  busy: bool, apply_all: bool, photo_count: int, enhance_count: int):
        self.s, self.analysis, self.input_size, self.max_scale = settings, analysis, input_size, max_scale
        self.disabled, self.busy, self.apply_all = disabled, busy, apply_all
        self.photo_count, self.enhance_count = photo_count, enhance_count
        self.refresh()

    def refresh(self):
        s = self.s
        self._updating = True
        for v, t in self.mode_tiles.items():
            t.setChecked(v == s.mode)
            t.setEnabled(not self.disabled)
        for k, t in self.scale_tiles.items():
            t.setChecked(k == s.scale)
            t.setEnabled(not self.disabled and k <= self.max_scale)
            t.setToolTip("Output would exceed the server's resolution limit" if k > self.max_scale else "")
        if self.input_size:
            w, h = self.input_size
            self.dims.setText(f"{w}×{h} → {w * s.scale}×{h * s.scale}")
        self.dims.setVisible(bool(self.input_size))
        self.strength.setValue(round(s.strength * 100))
        self.strength.setEnabled(not self.disabled)
        self.strength_pct.setText(f"{round(s.strength * 100)}%")
        faces = len((self.analysis or {}).get("faces") or [])
        self.faces_badge.setVisible(self.analysis is not None)
        self.faces_badge.set_text(f"{faces} face{'' if faces == 1 else 's'}")
        self.faces_badge.set_variant("default" if faces else "secondary")
        self.face_switch.setChecked(s.face_enhance)
        self.face_switch.setEnabled(not self.disabled)
        self.face_more.setVisible(s.face_enhance)
        self.face_model.set_value(s.face_model)
        self.face_model.setEnabled(not self.disabled)
        self.fidelity.set(s.fidelity, .7, not self.disabled and s.face_model != "gfpgan")
        rec = (self.analysis or {}).get("recommended") or {}
        self.denoise.set(s.denoise, rec.get("denoise"), not self.disabled)
        self.sharpen.set(s.sharpen, rec.get("sharpen"), not self.disabled)
        self.color_switch.setChecked(s.color_enhance)
        self.color_switch.setEnabled(not self.disabled)
        self.apply_check.setChecked(self.apply_all)
        self.apply_text.setText(
            f"One click enhances every photo{f' ({self.photo_count})' if self.photo_count > 1 else ''} with these settings."
            if self.apply_all else "Enhance photo runs on the selected photo only.")
        self.button.setEnabled(not self.disabled and not self.busy)
        self.button.setText("Enhancing..." if self.busy else f"ENHANCE {self.enhance_count} PHOTOS"
                            if self.apply_all and self.enhance_count > 1 else "ENHANCE PHOTO")
        self._updating = False

    # ---- changes out ----------------------------------------------------------------------
    def _set(self, **patch):
        if self._updating:
            return
        self.settings_changed.emit(replace(self.s, **patch))

    def _set_mode(self, mode: str):
        if not self._updating:
            self.settings_changed.emit(preset_for(mode, self.s))

    def _apply_all(self, on: bool):
        if not self._updating:
            self.apply_all_changed.emit(on)

    def _toggle_advanced(self):
        self.advanced = not self.advanced
        self.adv.setVisible(self.advanced)
        self.adv_btn.set_icon("chevron-up" if self.advanced else "chevron-down")

