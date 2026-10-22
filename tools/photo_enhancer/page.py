"""AI Photo Enhancer - the website's home page (app/HomeView.tsx) as a tool: the landing view
until photos are added, then the workspace (queue, before / after viewer, AI analysis, settings).

Photos go to the enhancement server (bmapi.boostimise.com); each enhancement is first taken from
the account's daily quota in Supabase (reserve_my_enhancement) and its outcome recorded
(finish_my_enhancement), exactly like on the website, so the dashboard and limits stay in step.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QSettings, QStandardPaths, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QImage, QImageReader, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QScrollArea, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)

from core import account as acc
from core import net
from core import webui as W
from core.account_widgets import LimitDialog, QuotaPill
from core.paths import data_dir

from . import api
from .api import DEFAULT_SETTINGS, EnhanceSettings
from .queue import EnhanceQueue, QueueItem, is_active, is_finished, is_startable
from .widgets import (
    AnalysisPanel, CompareToolbar, CompareView, Dropzone, EnhancementControls, PhotoBox, ThumbStrip, mip_levels,
    result_details,
)

DISPLAY_MAX = 4096   # the original is shown from a copy at most this big
NARROW_BELOW = 900   # page width under which the settings go below the photo

FEATURES = [("maximize-2", "AI Upscale 2× / 4×", "AI super-resolution with seamless tiling for photos up to 6000×4000."),
            ("scan-face", "Face restoration", "AI face restoration for faces that need it - identity-preserving, never over-processed."),
            ("history", "Photo restoration", "Blur, noise, JPEG artifacts, faded colours and low resolution fixed automatically."),
            ("shield-check", "Private & local", "Runs on our own GPU servers. Uploads are deleted after processing.")]
MODES = [("sparkles", "Auto", "Analyze the photo and choose the pipeline automatically"),
         ("scan-face", "Face Enhance", "Prioritise AI face restoration"),
         ("history", "Photo Restore", "Old, blurry, noisy, scanned or heavily compressed photos"),
         ("maximize-2", "Upscale", "Pure AI super-resolution")]
STEPS = [("image-up", "Upload", "Drag & drop, pick or paste (Ctrl+V) JPG, PNG or WEBP photos - several at once."),
         ("sliders-horizontal", "Pick your settings", "AI analyzes each photo and recommends denoise, sharpen and face settings."),
         ("download", "Compare & download", "Drag the before/after slider, zoom to 1:1, then download PNG, JPG or all as ZIP.")]


def ahead_of(items: list[QueueItem], it: QueueItem) -> int:
    """Photos that must finish before ``it`` starts (for the "waiting" message)."""
    idx = items.index(it)
    return sum(1 for i, x in enumerate(items)
               if (i < idx and x.status == "waiting") or x.status in ("uploading", "queued", "processing"))


def load_display(path: Path) -> list[QImage]:
    """The original for the viewer (EXIF orientation applied, at most DISPLAY_MAX px). Worker thread."""
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    size = reader.size()
    longest = max(size.width(), size.height())
    if longest > DISPLAY_MAX:
        reader.setScaledSize(size * (DISPLAY_MAX / longest))
    img = reader.read()
    if img.isNull():
        raise ValueError(reader.errorString())
    return mip_levels(img)


def load_preview(job: dict) -> list[QImage]:
    img = QImage.fromData(api.fetch_bytes(api.result_url(job, "preview")))
    if img.isNull():
        raise ValueError("The enhanced preview could not be read")
    return mip_levels(img)


def reveal(path: Path):
    if sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", str(path)])
    else:
        os.startfile(path.parent)  # type: ignore[attr-defined]


def _centered(widget: QWidget, max_width: int, margins=(16, 0, 16, 0)) -> QHBoxLayout:
    widget.setMaximumWidth(max_width)
    row = QHBoxLayout()
    row.setContentsMargins(*margins)
    row.addStretch(1)
    row.addWidget(widget, 1000)
    row.addStretch(1)
    return row


def _feature_card(icon: str, title: str, text: str) -> QWidget:
    card = W.Box(radius=18, bg=W.CARD, border=W.BORDER, padding=20, spacing=6)
    tile = W.Icon(icon, W.PRIMARY, 20)
    tile.setFixedSize(40, 40)
    tile.setAlignment(Qt.AlignCenter)
    tile.setStyleSheet(f"background: {W.tint(W.PRIMARY, .1)}; border-radius: 14px;")
    card.add(tile)
    card.lay.addSpacing(10)
    card.add(W.label(title, 14, 700))
    card.add(W.label(text, 14, 400, W.MUTED_FG, wrap=True))
    card.lay.addStretch(1)
    return card


class PhotoEnhancerPage(QWidget):
    busy_changed = Signal(bool)

    def __init__(self):
        super().__init__()
        self.account = acc.get()
        self.prefs = QSettings(str(data_dir("photo_enhancer") / "settings.ini"), QSettings.IniFormat)
        self.q = EnhanceQueue(self)
        self.selected_id: str | None = None
        self.settings: EnhanceSettings = DEFAULT_SETTINGS
        self.apply_all = False
        self.analyses: dict[str, dict | None] = {}
        self.pending: set[str] = set()
        self.health: dict | None = None
        self.offline = False
        self.originals: OrderedDict[str, list[QImage]] = OrderedDict()
        self.previews: OrderedDict[str, list[QImage]] = OrderedDict()
        self._loading: set[str] = set()
        self._busy = False
        self._narrow: bool | None = None
        self._shown_view = None

        self.setFocusPolicy(Qt.ClickFocus)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.alert = W.Alert("Enhancement server is not reachable", "The photo enhancement server is offline right "
                             "now. Please try again in a few minutes.", "destructive", "server-crash")
        alert_holder = QWidget()
        alert_holder.setStyleSheet(f"background: {W.BG};")
        al = QVBoxLayout(alert_holder)
        al.setContentsMargins(0, 16, 0, 0)
        al.addLayout(_centered(self.alert, 768))
        self.alert_holder = alert_holder
        alert_holder.hide()
        root.addWidget(alert_holder)
        self.root = QStackedWidget()
        root.addWidget(self.root, 1)
        self.landing = self._build_landing()
        self.workspace = self._build_workspace()
        self.root.addWidget(self.landing)
        self.root.addWidget(self.workspace)

        self.q.changed.connect(self.refresh)
        self.q.item_changed.connect(self._on_item_changed)
        self.account.changed.connect(self._on_account)
        self.health_timer = QTimer(self, interval=10_000, timeout=self.check_health)
        self.refresh_soon_timer = QTimer(self, singleShot=True, interval=800, timeout=self.account.refresh)
        QShortcut(QKeySequence.Paste, self, self.paste, context=Qt.WidgetWithChildrenShortcut)
        self.check_health()
        self.health_timer.start()
        self._on_account()

    # ---- building ------------------------------------------------------------------------
    def _build_landing(self) -> QWidget:
        page = W.WebPage()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 56, 0, 48)
        lay.setSpacing(0)
        hero = QWidget()
        hl = QVBoxLayout(hero)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(0)
        hl.addWidget(W.HeroTitle("AI PHOTO ENHANCER"))
        hl.addSpacing(16)
        hl.addWidget(W.label("Enhance and upscale your photos with AI", 18, 400, W.MUTED_FG, align=Qt.AlignCenter))
        hl.addSpacing(36)
        upload_card = W.Box(radius=22, bg=W.tint(W.CARD, .4, W.BG), border=W.BORDER, padding=12)
        W.shadow(upload_card, blur=50, dy=20, alpha=.07, color="#2f0d68")
        self.dropzone = Dropzone(self.max_upload_mb)
        self.dropzone.files.connect(self.on_files)
        upload_card.add(self.dropzone)
        hl.addLayout(_centered(upload_card, 672, (0, 0, 0, 0)))
        hl.addSpacing(20)
        self.signed_in_as = W.label("", 14, 400, W.MUTED_FG)
        self.hero_pill = QuotaPill()
        hl.addLayout(W.h_row(None, W.Icon("check", W.EMERALD, 16), self.signed_in_as, self.hero_pill, None, spacing=6))
        lay.addLayout(_centered(hero, 896))
        lay.addSpacing(64)

        feats = W.ResponsiveGrid(220, 4, 16, breakpoints=(1, 2, 4))
        for f in FEATURES:
            feats.add(_feature_card(*f))
        lay.addLayout(_centered(feats, 1152, (24, 0, 24, 0)))
        lay.addSpacing(64)

        band = QWidget()
        band.setObjectName("band")
        band.setStyleSheet(f"QWidget#band {{ background: {W.tint(W.MUTED, .3)}; border-top: 1px solid {W.BORDER}; "
                           f"border-bottom: 1px solid {W.BORDER}; }}")
        bl = QVBoxLayout(band)
        bl.setContentsMargins(0, 64, 0, 64)
        bl.setSpacing(8)
        bl.addWidget(W.label("How it works", 28, 700, align=Qt.AlignCenter))
        bl.addWidget(W.label("Pick your settings, then press Enhance photo. Change them and press it again to re-run.",
                             16, 400, W.MUTED_FG, wrap=True, align=Qt.AlignCenter))
        bl.addSpacing(32)
        steps = W.ResponsiveGrid(240, 3, 24, breakpoints=(1, 3, 3))
        for n, (icon, title, text) in enumerate(STEPS, 1):
            card = W.Box(radius=18, bg=W.BG, border=W.BORDER, padding=24, spacing=6)
            card.add(W.h_row(W.Icon(icon, W.PRIMARY, 24), None, W.label(f"0{n}", 12, 400, W.MUTED_FG, mono=True)))
            card.lay.addSpacing(10)
            card.add(W.label(title, 16, 700))
            card.add(W.label(text, 14, 400, W.MUTED_FG, wrap=True))
            card.lay.addStretch(1)
            steps.add(card)
        bl.addLayout(_centered(steps, 1152, (24, 0, 24, 0)))
        lay.addWidget(band)
        lay.addSpacing(64)

        lay.addWidget(W.label("Four ways to enhance", 28, 700, align=Qt.AlignCenter))
        lay.addSpacing(8)
        lay.addWidget(W.label("Every mode can be fine-tuned: strength, denoise, sharpen, face model and fidelity, colour.",
                              16, 400, W.MUTED_FG, wrap=True, align=Qt.AlignCenter))
        lay.addSpacing(32)
        modes = W.ResponsiveGrid(220, 4, 16, breakpoints=(1, 2, 4))
        for icon, title, text in MODES:
            card = W.Box(radius=18, bg=W.BG, border=W.BORDER, padding=20, layout="h", spacing=12)
            card.add(W.Icon(icon, W.PRIMARY, 20), 0, Qt.AlignTop)
            card.add(W.v_col(W.label(title, 14, 700), W.label(text, 14, 400, W.MUTED_FG, wrap=True), None, spacing=4), 1)
            modes.add(card)
        lay.addLayout(_centered(modes, 1152, (24, 0, 24, 0)))
        lay.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(page)
        scroll.setStyleSheet(f"QScrollArea {{ background: {W.BG}; }}")
        return scroll

    def _build_workspace(self) -> QWidget:
        # viewer column
        self.viewer_col = W.WebPage()
        vl = QVBoxLayout(self.viewer_col)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(12)
        self.back_btn = W.Button("New image", "arrow-left", "ghost", "sm", height=32)
        self.back_btn.clicked.connect(self.reset)
        self.counts = W.label("", 12, 400, W.MUTED_FG)
        self.zip_jpg = W.Button("All as JPG (.zip)", "file-archive", "outline", "sm", height=32)
        self.zip_png = W.Button("All as PNG (.zip)", "file-archive", "outline", "sm", height=32)
        self.zip_jpg.clicked.connect(lambda: self.download_zip("jpg"))
        self.zip_png.clicked.connect(lambda: self.download_zip("png"))
        self.ws_pill = QuotaPill()
        head = W.h_row(self.back_btn, None, self.ws_pill, self.counts, self.zip_jpg, self.zip_png, spacing=8)
        vl.addLayout(head)
        self.strip = ThumbStrip(self.max_upload_mb)
        self.strip.selected.connect(self.select)
        self.strip.removed.connect(self.remove)
        self.strip.files.connect(self.on_files)
        vl.addWidget(self.strip)
        self.info = W.label("", 12, 400, W.MUTED_FG, mono=True)
        self.info.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        vl.addWidget(self.info)

        self.view_stack = QStackedWidget()
        # done: comparison + toolbar + downloads
        done = QWidget()
        dl = QVBoxLayout(done)
        dl.setContentsMargins(0, 0, 0, 0)
        dl.setSpacing(12)
        self.compare = CompareView()
        dl.addWidget(self.compare, 1)
        dl.addWidget(CompareToolbar(self.compare))
        self.dl_png = W.Button("Download PNG", "download", "default", "xl", full=True, font_px=14)
        self.dl_jpg = W.Button("Download JPG", "download", "outline", "xl", full=True, font_px=14)
        self.dl_png.clicked.connect(lambda: self.download("png"))
        self.dl_jpg.clicked.connect(lambda: self.download("jpg"))
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(self.dl_png, 1)
        row.addWidget(self.dl_jpg, 1)
        buttons = W.wrap(row)
        buttons.setMaximumWidth(640)
        dl.addLayout(W.h_row(None, buttons, None))
        self.save_to = W.label("", 12, 400, W.MUTED_FG, align=Qt.AlignCenter)
        self.save_to.setTextFormat(Qt.RichText)
        self.save_to.linkActivated.connect(lambda _: self.change_folder())
        dl.addWidget(self.save_to)
        self.view_stack.addWidget(done)
        # not done: the original with overlays
        self.photo = PhotoBox()
        self.photo.progress.cancel.connect(lambda: self.selected and self.remove(self.selected.id))
        self.photo.failed.retry.connect(lambda: self.selected and self.on_enhance([self.selected.id]))
        self.view_stack.addWidget(self.photo)
        vl.addWidget(self.view_stack, 1)

        # controls column
        self.aside_inner = W.WebPage()
        al = QVBoxLayout(self.aside_inner)
        al.setContentsMargins(0, 0, 4, 0)
        al.setSpacing(16)
        self.analysis_panel = AnalysisPanel()
        al.addWidget(self.analysis_panel)
        self.result_holder = QVBoxLayout()
        al.addLayout(self.result_holder)
        self._result_widget: QWidget | None = None
        self._result_key: str | None = None
        card = W.Box(radius=18, bg=W.CARD, border=W.BORDER, padding=20)
        self.controls = EnhancementControls()
        self.controls.settings_changed.connect(self.on_settings)
        self.controls.enhance.connect(lambda: self.on_enhance())
        self.controls.apply_all_changed.connect(self._set_apply_all)
        card.add(self.controls)
        al.addWidget(card)
        al.addWidget(W.label("Pick your settings, then press <b>Enhance photo</b>. You can change them and press it "
                             "again to re-run.", 12, 400, W.MUTED_FG, wrap=True))
        al.addStretch(1)

        # wide: side by side (settings scroll on their own); narrow: one column that scrolls
        self.ws_layouts = QStackedWidget()
        self.wide = W.WebPage()
        wl = QHBoxLayout(self.wide)
        wl.setContentsMargins(24, 20, 16, 20)
        wl.setSpacing(24)
        self.aside_scroll = QScrollArea()
        self.aside_scroll.setWidgetResizable(True)
        self.aside_scroll.setFrameShape(QScrollArea.NoFrame)
        self.aside_scroll.setFixedWidth(396)
        self.aside_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.wide_lay = wl
        self.narrow = QScrollArea()
        self.narrow.setWidgetResizable(True)
        self.narrow.setFrameShape(QScrollArea.NoFrame)
        narrow_inner = W.WebPage()
        self.narrow_lay = QVBoxLayout(narrow_inner)
        self.narrow_lay.setContentsMargins(16, 16, 16, 24)
        self.narrow_lay.setSpacing(24)
        self.narrow.setWidget(narrow_inner)
        self.ws_layouts.addWidget(self.wide)
        self.ws_layouts.addWidget(self.narrow)
        page = W.WebPage()
        pl = QVBoxLayout(page)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.addWidget(self.ws_layouts)
        for w in (self.aside_scroll, self.narrow, self.strip):
            w.setStyleSheet("QScrollArea { background: transparent; }")
            w.viewport().setAutoFillBackground(False)
        self._apply_layout(False)
        return page

    def _apply_layout(self, narrow: bool):
        if narrow == self._narrow:
            return
        self._narrow = narrow
        if narrow:
            if self.aside_scroll.widget() is self.aside_inner:
                self.aside_scroll.takeWidget()
            self.wide_lay.removeWidget(self.viewer_col)
            self.narrow_lay.addWidget(self.viewer_col)
            self.narrow_lay.addWidget(self.aside_inner)
            self.photo.setMinimumHeight(420)
            self.compare.setMinimumHeight(360)
            self.ws_layouts.setCurrentWidget(self.narrow)
        else:
            self.narrow_lay.removeWidget(self.viewer_col)
            self.narrow_lay.removeWidget(self.aside_inner)
            self.wide_lay.addWidget(self.viewer_col, 1)
            self.wide_lay.addWidget(self.aside_scroll)
            self.aside_scroll.setWidget(self.aside_inner)
            self.compare.setMinimumHeight(220)
            self.ws_layouts.setCurrentWidget(self.wide)
        self.viewer_col.show()
        self.aside_inner.show()

    def resizeEvent(self, e):
        self._apply_layout(e.size().width() < NARROW_BELOW)
        super().resizeEvent(e)

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(W.BG))
        p.end()

    # ---- sidebar hooks -------------------------------------------------------------------
    def is_busy(self) -> bool:
        return any(is_active(i.status) for i in self.q.items)

    def page_shown(self):
        self.setFocus()
        self.check_health()
        self.health_timer.start()

    def page_hidden(self):
        if not self.is_busy():
            self.health_timer.stop()

    def closeEvent(self, e):
        self.health_timer.stop()
        self.q.shutdown()
        super().closeEvent(e)

    # ---- quota gate (EnhanceQueue calls these) -------------------------------------------
    def reserve(self, it: QueueItem) -> dict:
        w, h = it.dims or (None, None)
        res = self.account.reserve_enhancement(it.name, it.size, it.settings.mode, it.settings.scale, w, h)
        quota = res.get("quota")
        net.ui(lambda: self.account.set_quota(quota))
        return res

    def finish(self, record_id: str, **patch):
        status = patch.get("status")

        def done(_):
            if status and status != "completed":
                self.refresh_soon_timer.start()  # failed / cancelled ones are refunded
        net.run_bg(lambda: self.account.finish_enhancement(record_id, **patch), done, lambda e: None)

    def on_denied(self, code: str, _message: str):
        if code == "onboarding":
            self.account.refresh()  # opens the mobile-number step
        elif code == "update_required":
            from core import guard
            guard.get().check()  # the server refused this version: the update screen takes over
        else:
            self.show_limit(code)

    def show_limit(self, reason: str):
        LimitDialog(self, reason, on_plans=self._open_plans).exec()

    def _open_plans(self):
        from core.shell import find_shell
        shell = find_shell(self)
        if shell is not None:
            shell.open_tool("plans")

    # ---- server health -------------------------------------------------------------------
    def max_upload_mb(self) -> float:
        return float(((self.health or {}).get("limits") or {}).get("max_upload_mb") or 50)

    def check_health(self):
        def ok(h):
            self.health, self.offline = h, False
            self.dropzone.update_hint()
            self._update_offline()

        def bad(_e):
            self.offline = True
            self._update_offline()
        net.run_bg(api.get_health, ok, bad)

    def _update_offline(self):
        self.alert_holder.setVisible(self.offline)
        self.refresh()

    # ---- photos in -----------------------------------------------------------------------
    def on_files(self, paths: list[Path], temp: bool = False, names: list[str] | None = None):
        max_mb = self.max_upload_mb()
        ok, bad = [], []
        for n, p in enumerate(paths):
            err = api.validate_file(p, max_mb)
            if err:
                bad.append(f"{p.name}: {err}")
            else:
                ok.append((p, names[n] if names else p.name))
        if bad:
            W.toast.error(bad[0] if len(bad) == 1 else f"{len(bad)} files skipped - {bad[0]}")
        if not ok:
            return
        # don't accept more photos than the daily quota can still enhance: photos already in the
        # queue that haven't used a credit yet (idle / waiting / failed / cancelled) count against it
        q = self.account.quota
        remaining = q.remaining if q else None
        if remaining is not None:
            unreserved = sum(i.status in ("idle", "waiting", "failed", "cancelled") for i in self.q.items)
            slots = max(0, remaining - unreserved)
            if slots == 0:
                self.show_limit("limit")
                return
            if len(ok) > slots:
                skipped = len(ok) - slots
                ok = ok[:slots]
                W.toast.warning(f"Only {slots} more photo{'' if slots == 1 else 's'} can be enhanced today - {skipped} skipped")
        had_selection = self.selected is not None  # before adding: the first photo becomes the selected one
        ids = self.q.add([p for p, _ in ok], self.settings, temp=temp, names=[n for _, n in ok])
        if not had_selection or len(ok) == 1:
            self.select(ids[0])
        if len(ok) > 1:
            W.toast(f"{len(ok)} photos added - press Enhance photo when ready")

    def paste(self):
        mime = QGuiApplication.clipboard().mimeData()
        if mime is None:
            return
        if mime.hasUrls():
            paths = [Path(u.toLocalFile()) for u in mime.urls() if u.isLocalFile()]
            if paths:
                self.on_files(paths)
                return
        if mime.hasImage():
            img = QImage(mime.imageData())
            if img.isNull():
                return
            folder = data_dir("photo_enhancer") / "pasted"
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"pasted-{int(time.time() * 1000)}.png"
            if img.save(str(path), "PNG"):
                self.on_files([path], temp=True, names=["image.png"])

    # ---- selection / settings ------------------------------------------------------------
    @property
    def selected(self) -> QueueItem | None:
        items = self.q.items
        return self.q.get(self.selected_id) if self.selected_id and self.q.get(self.selected_id) else (items[0] if items else None)

    def select(self, item_id: str):
        self.selected_id = item_id
        it = self.q.get(item_id)
        if it is not None and not is_finished(it.status):
            self.ensure_analysis(it, self.settings.scale)
        self.refresh()

    def ensure_analysis(self, it: QueueItem, scale: int):
        """Fetch (once) the AI analysis shown for a photo."""
        key = f"{it.id}:{scale}"
        if key in self.analyses or key in self.pending or self.offline:
            return
        self.pending.add(key)

        def done(res):
            self.analyses[key] = res
            self.pending.discard(key)
            self.refresh()

        def failed(_e):
            self.analyses[key] = None
            self.pending.discard(key)
            self.refresh()
        net.run_bg(lambda: api.analyze_image(it.path, it.name, scale), done, failed)
        self.refresh()

    def on_settings(self, s: EnhanceSettings):
        scale_changed = s.scale != self.settings.scale
        self.settings = s
        sel = self.selected
        if scale_changed and sel is not None:
            self.ensure_analysis(sel, s.scale)  # face decisions depend on output size
        self.refresh()

    def _set_apply_all(self, on: bool):
        self.apply_all = on
        self.refresh()

    def remove(self, item_id: str):
        sel = self.selected
        self.q.remove(item_id)
        self.originals.pop(item_id, None)
        if sel is not None and item_id == sel.id:
            rest = self.q.items
            self.selected_id = rest[0].id if rest else None
        self.refresh()

    def reset(self):
        self.q.clear()
        self.selected_id = None
        self.analyses.clear()
        self.originals.clear()
        self.previews.clear()
        self.refresh()

    def on_enhance(self, ids: list[str] | None = None):
        sel = self.selected
        if sel is None:
            return
        p = self.account.profile
        if self.account.user and p and not p.get("onboarded"):
            self.account.refresh()  # the mobile-number step comes first
            return
        if p and p.get("blocked"):
            self.show_limit("blocked")
            return
        q = self.account.quota
        if q is not None and q.remaining == 0:
            self.show_limit("limit")
            return
        self.q.enhance(self.settings, ids if ids is not None else (None if self.apply_all else [sel.id]))

    # ---- images for the viewer -----------------------------------------------------------
    def _ensure_original(self, it: QueueItem):
        key = f"orig:{it.id}"
        if it.id in self.originals or key in self._loading:
            return
        self._loading.add(key)

        def done(levels):
            self._loading.discard(key)
            if self.q.get(it.id) is None:
                return
            self.originals[it.id] = levels
            while len(self.originals) > 4:
                self.originals.popitem(last=False)
            self._show_images()

        def failed(e):
            self._loading.discard(key)
            W.toast.error(f"{it.name}: the photo can't be opened ({e})")
        net.run_bg(lambda: load_display(it.path), done, failed)

    def _ensure_preview(self, job: dict):
        job_id = job["job_id"]
        key = f"prev:{job_id}"
        if job_id in self.previews or key in self._loading:
            return
        self._loading.add(key)

        def done(levels):
            self._loading.discard(key)
            self.previews[job_id] = levels
            while len(self.previews) > 4:
                self.previews.popitem(last=False)
            self._show_images()

        def failed(e):
            self._loading.discard(key)
            W.toast.error(f"The enhanced photo could not be loaded ({getattr(e, 'message', e)})")
        net.run_bg(lambda: load_preview(job), done, failed)

    def _show_images(self):
        sel = self.selected
        if sel is None:
            return
        original = self.originals.get(sel.id, [])
        done_job = sel.job if sel.status == "done" and sel.job and sel.job.get("result") else None
        if done_job:
            self.compare.set_before(original)
            preview = self.previews.get(done_job["job_id"])
            if preview:
                self.compare.set_after(preview)
        else:
            self.photo.set_image(original)

    # ---- rendering -----------------------------------------------------------------------
    def _on_account(self):
        u = self.account.display_user() or {}
        self.signed_in_as.setText(f"Signed in as {u.get('name') or u.get('email') or ''}")
        self.refresh()

    def _on_item_changed(self, item_id: str):
        self.strip.refresh_item(item_id)
        sel = self.selected
        if sel is not None and sel.id == item_id and is_active(sel.status):
            self._update_progress(sel)
        busy = self.is_busy()
        if busy != self._busy:
            self._busy = busy
            self.busy_changed.emit(busy)

    def _update_progress(self, sel: QueueItem):
        queue_position = ahead_of(self.q.items, sel) if sel.status == "waiting" else (sel.job or {}).get("queue_position")
        self.photo.progress.set_state(sel.stage, sel.progress, sel.upload, queue_position, sel.started_at)

    def refresh(self):
        items = self.q.items
        sel = self.selected
        busy = self.is_busy()
        if busy != self._busy:
            self._busy = busy
            self.busy_changed.emit(busy)
        if sel is None:
            self.root.setCurrentWidget(self.landing)
            self._shown_view = None
            return
        self.root.setCurrentWidget(self.workspace)
        stats = self.q.stats()
        multi = len(items) > 1
        self.back_btn.setText("Clear all" if multi else "New image")
        done_ids = [i.job_id for i in items if i.status == "done" and i.job_id]
        self.counts.setText(f"{stats['done']} of {len(items)} enhanced"
                            + (f" · {stats['failed']} failed" if stats["failed"] else "") if multi else "")
        self.counts.setVisible(multi)
        for b in (self.zip_jpg, self.zip_png):
            b.setVisible(multi and bool(done_ids))
        self.strip.sync(items, sel.id)
        dims = f"{sel.dims[0]}×{sel.dims[1]} · " if sel.dims else ""
        self.info.setText(f"{sel.name} · {dims}{api.format_bytes(sel.size)}")

        sel_busy = is_active(sel.status)
        done = sel.job if sel.status == "done" and sel.job and sel.job.get("result") else None
        a_key = f"{sel.id}:{self.settings.scale}"
        analysis = self.analyses.get(a_key)
        max_scale = ((analysis or {}).get("input") or {}).get("max_scale", 4)
        self._ensure_original(sel)

        if done:
            result = done["result"]
            if self._shown_view != ("done", done["job_id"]):
                self._shown_view = ("done", done["job_id"])
                self.compare.set_result(result["width"], result["height"])
                self._ensure_preview(done)
            self.view_stack.setCurrentIndex(0)
            self._update_downloads(done)
        else:
            self._shown_view = ("photo", sel.id, sel.status)
            self.view_stack.setCurrentIndex(1)
            mode = "busy" if sel_busy else "failed" if sel.status in ("failed", "cancelled") else (
                "idle" if sel.status == "idle" else "none")
            self.photo.failed.text.setText(sel.error or "This photo was not enhanced.")
            self.photo.set_mode(mode)
            if sel_busy:
                self._update_progress(sel)
        self._show_images()

        # aside: the result's details, or the AI analysis
        result_key = done["job_id"] if done else None
        if result_key != self._result_key:
            self._result_key = result_key
            if self._result_widget is not None:
                self._result_widget.hide()
                self._result_widget.deleteLater()
                self._result_widget = None
            if done:
                self._result_widget = result_details(done["result"])
                self.result_holder.addWidget(self._result_widget)
        if done:
            self.analysis_panel.show_data(None, False)
        else:
            self.analysis_panel.show_data(analysis, a_key in self.pending)

        startable = sum(is_startable(i.status) for i in items)
        self.controls.set_state(self.settings, analysis, sel.dims, max_scale, self.offline,
                                startable == 0 if self.apply_all else sel_busy, self.apply_all, len(items),
                                startable if self.apply_all else 1)

    # ---- downloads -----------------------------------------------------------------------
    def save_folder(self) -> Path:
        saved = self.prefs.value("save_folder", "", type=str)
        if saved and Path(saved).is_dir():
            return Path(saved)
        return Path(QStandardPaths.writableLocation(QStandardPaths.DownloadLocation) or Path.home() / "Downloads")

    def change_folder(self):
        folder = QFileDialog.getExistingDirectory(self.window(), "Save enhanced photos to", str(self.save_folder()))
        if folder:
            self.prefs.setValue("save_folder", folder)
            self.refresh()

    def _update_downloads(self, job: dict):
        sizes = (job.get("result") or {}).get("file_sizes") or {}
        for btn, kind in ((self.dl_png, "png"), (self.dl_jpg, "jpg")):
            if getattr(btn, "_downloading", False):
                continue
            size = sizes.get(kind)
            btn.setText(f"Download {kind.upper()}" + (f"  ({api.format_bytes(size)})" if size else ""))
            btn.set_loading(kind == "png" and job.get("png_ready") is False)
        folder = self.save_folder()
        self.save_to.setText(f'Saves to {folder.name or folder} · <a href="#" style="color:{W.PRIMARY};">Change</a>')
        self.save_to.setToolTip(str(folder))

    def download(self, kind: str):
        sel = self.selected
        job = sel.job if sel and sel.status == "done" else None
        if not job:
            return
        btn = self.dl_png if kind == "png" else self.dl_jpg
        self._run_download(btn, lambda prog: api.download_result(job, kind, self.save_folder(), prog),
                           f"Download {kind.upper()}")

    def download_zip(self, fmt: str):
        ids = [i.job_id for i in self.q.items if i.status == "done" and i.job_id]
        if not ids:
            return
        btn = self.zip_png if fmt == "png" else self.zip_jpg
        self._run_download(btn, lambda prog: api.download_zip(ids, fmt, self.save_folder(), prog),
                           f"All as {fmt.upper()} (.zip)")

    def _run_download(self, btn: W.Button, work, label: str):
        if getattr(btn, "_downloading", False):
            return
        btn._downloading = True
        btn.set_loading(True)
        btn.setText("Downloading…")

        def progress(done: int, total: int):
            if total:
                net.ui(lambda: btn.setText(f"Downloading… {round(done / total * 100)}%"))

        def finish():
            btn._downloading = False
            btn.set_loading(False)
            btn.setText(label)
            self.refresh()

        def ok(path: Path):
            finish()
            W.toast.success(f"Saved {path.name}", action=("Show", lambda: reveal(path)))

        def failed(e):
            finish()
            W.toast.error(f"Download failed: {getattr(e, 'message', None) or e}")
        net.run_bg(lambda: work(progress), ok, failed)
