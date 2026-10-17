"""The multi-image queue - the website's lib/useEnhanceQueue.ts.

Added photos stay "idle" until the user presses Enhance; then uploads are paced a few jobs ahead
of the GPU (the server processes one image at a time per GPU), all active jobs are polled with a
single request, and every photo can be removed or re-enhanced independently. The gate reserves
each enhancement from the daily quota before the upload starts and reports how it ended.
"""
from __future__ import annotations

import itertools
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Protocol

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QImage, QImageReader

from core import net
from core import webui as W

from . import api
from .api import EnhanceSettings

MAX_ACTIVE = 2          # jobs kept on the server at once (1 processing + 1 queued keeps the GPU busy)
POLL_MS = 600
ACTIVE = ("uploading", "queued", "processing")
THUMB = 160


def is_active(s: str) -> bool:
    return s in ACTIVE or s == "waiting"


def is_finished(s: str) -> bool:
    return s in ("done", "failed", "cancelled")


def is_startable(s: str) -> bool:
    """Not running right now, so Enhance can (re)start it."""
    return s == "idle" or is_finished(s)


@dataclass
class QueueItem:
    id: str
    path: Path
    name: str
    size: int
    settings: EnhanceSettings
    thumb: QImage | None = None
    dims: tuple[int, int] | None = None
    status: str = "idle"       # idle waiting uploading queued processing done failed cancelled
    stage: str = "idle"
    progress: float = 0.0      # 0..100 (server progress)
    upload: float = 0.0        # 0..1
    job_id: str | None = None
    job: dict | None = None
    error: str | None = None
    started_at: float = field(default_factory=time.time)
    record_id: str | None = None   # quota record of the current run (None when not tracked)
    temp: bool = False             # a pasted image saved by us: deleted with the item


class Gate(Protocol):
    def reserve(self, it: QueueItem) -> dict: ...                     # worker thread
    def finish(self, record_id: str, **patch) -> None: ...            # UI thread, fire and forget
    def on_denied(self, code: str, message: str) -> None: ...         # UI thread


_ids = itertools.count()


def _new_id() -> str:
    return f"{int(time.time() * 1000):x}-{next(_ids):x}"


def load_thumb(path: Path) -> tuple[QImage, tuple[int, int]]:
    """A small copy for the strip (cover-cropped when drawn) and the photo's size as shown (EXIF
    orientation applied, like a browser's naturalWidth / naturalHeight). Worker thread."""
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    size = reader.size()
    w, h = size.width(), size.height()
    if w > 0 and h > 0:
        k = THUMB / min(w, h)
        if k < 1:
            reader.setScaledSize(size * k)
    img = reader.read()
    if img.isNull():
        raise ValueError(reader.errorString())
    transposed = img.width() > img.height() and w < h or img.width() < img.height() and w > h
    dims = (h, w) if transposed else (w, h)
    if w <= 0 or h <= 0:
        dims = (img.width(), img.height())
    return img, dims


class EnhanceQueue(QObject):
    changed = Signal()           # items added / removed / a status changed
    item_changed = Signal(str)   # progress of one item

    def __init__(self, gate: Gate | None = None):
        super().__init__()
        self.items: list[QueueItem] = []
        self.gate = gate
        self.aborts: dict[str, threading.Event] = {}
        self.polling = False
        self.failures = 0
        self.batch_announced = True
        self.cooldown_until = 0.0   # after "server queue full": pause new uploads briefly
        self._pump_timer = QTimer(self, singleShot=True, timeout=self.pump)

    # ---- lookup ----------------------------------------------------------------------------
    def get(self, item_id: str) -> QueueItem | None:
        return next((i for i in self.items if i.id == item_id), None)

    def stats(self) -> dict:
        s = [i.status for i in self.items]
        return {"total": len(s), "done": s.count("done"), "failed": s.count("failed"), "idle": s.count("idle"),
                "active": sum(is_active(x) for x in s)}

    def _patch(self, item_id: str, structural: bool = True, **changes):
        it = self.get(item_id)
        if it is None:
            return
        for k, v in changes.items():
            setattr(it, k, v)
        self.item_changed.emit(item_id)
        if structural:
            self.changed.emit()

    # ---- quota gate ------------------------------------------------------------------------
    def _settle(self, record_id: str | None, **patch):
        """Report the end of a tracked run (failed / cancelled ones are refunded to the quota)."""
        if record_id and self.gate is not None:
            self.gate.finish(record_id, **patch)

    # ---- running ---------------------------------------------------------------------------
    def _start(self, it: QueueItem):
        """Reserve quota (if gated), then upload one photo."""
        if self.gate is None:
            self._upload(it, None)
            return

        def reserved(res: dict):
            cur = self.get(it.id)
            if cur is None or cur.status != "uploading":  # removed / cancelled while reserving
                if res.get("ok") and res.get("id"):
                    self.gate.finish(res["id"], status="cancelled")
                self.pump()
                return
            if not res.get("ok"):
                # nothing more can start today: park this photo and everything still waiting
                for i in self.items:
                    if i.id == it.id or i.status == "waiting":
                        i.status, i.stage, i.upload = "idle", "idle", 0.0
                self.changed.emit()
                self.gate.on_denied(res.get("code", "limit"), res.get("message", ""))
                return
            self._patch(it.id, record_id=res.get("id"))
            self._upload(cur, res.get("id"))

        gate = self.gate
        net.run_bg(lambda: gate.reserve(it), reserved,
                   lambda e: reserved({"ok": True, "id": None, "quota": None}))

    def _upload(self, it: QueueItem, record_id: str | None):
        cancel = threading.Event()
        self.aborts[it.id] = cancel
        item_id = it.id

        def progress(f: float):
            net.ui(lambda: self._patch(item_id, structural=False, upload=f))

        def uploaded(job_id: str):
            self.aborts.pop(item_id, None)
            if self.get(item_id) is None:
                api.cancel_job(job_id)  # removed while uploading
                self._settle(record_id, status="cancelled")
            else:
                if record_id and self.gate is not None:
                    self.gate.finish(record_id, job_id=job_id)
                self._patch(item_id, status="queued", stage="queued", job_id=job_id)
                self.poll()
            self.pump()

        def failed(e: BaseException):
            self.aborts.pop(item_id, None)
            if isinstance(e, net.Cancelled):
                self._settle(record_id, status="cancelled")
            elif isinstance(e, net.HttpError) and e.status == 503:
                # server queue full (other users): try again shortly (with a fresh reservation)
                self._settle(record_id, status="cancelled")
                self._patch(item_id, status="waiting", stage="waiting", record_id=None)
                self.cooldown_until = time.monotonic() + 3
            else:
                msg = (e.message if isinstance(e, net.HttpError) else str(e)) or "Upload failed"
                if isinstance(e, net.HttpError) and e.status == 0:
                    msg = "Cannot reach the enhancement server."
                self._settle(record_id, status="failed", error=msg)
                self._patch(item_id, status="failed", stage="failed", error=msg)
                if self.get(item_id) is not None:
                    W.toast.error(f"{it.name}: {msg}")
            self.pump()

        net.run_bg(lambda: api.submit_enhance(it.path, it.name, it.settings, progress, cancel), uploaded, failed)

    def pump(self):
        """Start uploads while fewer than MAX_ACTIVE jobs are in flight."""
        wait = self.cooldown_until - time.monotonic()
        if wait > 0:
            self._pump_timer.start(int(wait * 1000) + 50)
            return
        while True:
            active = sum(i.status in ACTIVE for i in self.items)
            nxt = next((i for i in self.items if i.status == "waiting"), None)
            if nxt is None or active >= MAX_ACTIVE:
                break
            self._patch(nxt.id, status="uploading", stage="uploading", upload=0.0, started_at=time.time(),
                        record_id=None)
            self._start(nxt)
        # whole batch finished? (photos never sent for enhancement don't count)
        sent = [i for i in self.items if i.status != "idle"]
        if len(sent) > 1 and not self.batch_announced and all(is_finished(i.status) for i in sent):
            self.batch_announced = True
            ok = sum(i.status == "done" for i in sent)
            if ok:
                W.toast.success(f"{ok} of {len(sent)} photos enhanced")

    # ---- polling ---------------------------------------------------------------------------
    def _tracked(self) -> list[QueueItem]:
        return [i for i in self.items if i.job_id and (i.status in ("queued", "processing")
                                                       or (i.status == "done" and (i.job or {}).get("png_ready") is False))]

    def poll(self):
        """One request for every in-flight job; runs while anything is pending."""
        if self.polling:
            return
        self.polling = True
        self._poll_once()

    def _poll_once(self):
        tracked = self._tracked()
        if not tracked:
            self.polling = False
            return
        ids = [i.job_id for i in tracked]
        net.run_bg(lambda: api.get_jobs(ids), lambda res: self._on_jobs(tracked, res), lambda e: self._on_poll_error(tracked))

    def _on_jobs(self, tracked: list[QueueItem], res: dict):
        self.failures = 0
        by_id = {j["job_id"]: j for j in res.get("jobs") or []}
        missing = set(res.get("missing") or [])
        finished = False
        for it in tracked:
            j = by_id.get(it.job_id)
            cur = self.get(it.id)
            if j is None:
                if it.job_id in missing and cur is not None and cur.job_id == it.job_id:
                    if cur.status != "done":
                        self._settle(cur.record_id, status="failed", error="Job expired or the server restarted")
                    self._patch(it.id, status="failed", stage="failed", error="Job expired or the server restarted")
                    finished = True
                continue
            if cur is None or cur.job_id != j["job_id"]:
                continue  # removed / re-run meanwhile
            status = j.get("status")
            if status == "completed":
                if cur.status != "done":
                    finished = True
                    result = j.get("result") or {}
                    bench = result.get("benchmark") or {}
                    inp = j.get("input") or {}
                    self._settle(cur.record_id, status="completed", job_id=j["job_id"],
                                 output_width=result.get("width") or inp.get("output_width"),
                                 output_height=result.get("height") or inp.get("output_height"),
                                 seconds=bench.get("total_seconds"))
                    if self.batch_announced and result:
                        W.toast.success(f"Enhanced to {result['width']}×{result['height']} in "
                                        f"{bench.get('total_seconds', 0):.1f}s")
                self._patch(it.id, status="done", stage="done", progress=100.0, job=j)
            elif status in ("failed", "cancelled"):
                err = (j.get("error") or {}).get("message")
                self._settle(cur.record_id, status=status, error=err)
                self._patch(it.id, status=status, stage=status, job=j, error=err)
                if status == "failed":
                    W.toast.error(f"{it.name}: {err or 'processing failed'}")
                finished = True
            else:
                queued = status == "queued"
                changed = cur.status != ("queued" if queued else "processing") or cur.stage != (
                    "queued" if queued else j.get("stage"))
                self._patch(it.id, structural=changed, status="queued" if queued else "processing",
                            stage="queued" if queued else j.get("stage") or "processing",
                            progress=float(j.get("progress") or 0), job=j)
        if finished:
            self.pump()
        QTimer.singleShot(POLL_MS, self._poll_once)

    def _on_poll_error(self, tracked: list[QueueItem]):
        self.failures += 1
        if self.failures >= 20:
            # ~12 s without a valid answer: stop instead of spinning forever
            for it in tracked:
                cur = self.get(it.id)
                if cur is not None and cur.status != "done":
                    self._settle(cur.record_id, status="failed", error="Lost contact with the enhancement server")
                    self._patch(it.id, status="failed", stage="failed", error="Lost contact with the enhancement server")
            W.toast.error("Lost contact with the enhancement server")
            self.failures = 0
            self.polling = False
            return
        QTimer.singleShot(POLL_MS, self._poll_once)

    # ---- editing the list --------------------------------------------------------------------
    def add(self, paths: list[Path], settings: EnhanceSettings, temp: bool = False,
            names: list[str] | None = None) -> list[str]:
        added = []
        for n, path in enumerate(paths):
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            name = names[n] if names else path.name
            added.append(QueueItem(_new_id(), path, name, size, settings, temp=temp))
        self.items.extend(added)
        for it in added:
            item_id = it.id

            def done(res, item_id=item_id):
                img, dims = res
                self._patch(item_id, thumb=img, dims=dims)
            net.run_bg(lambda p=it.path: load_thumb(p), done, lambda e: None)
        self.changed.emit()
        return [i.id for i in added]

    def _stop(self, it: QueueItem):
        ev = self.aborts.pop(it.id, None)
        if ev is not None:
            ev.set()
        if it.job_id:
            api.cancel_job(it.job_id)  # cancels if running, deletes results if finished
        if it.status in ("queued", "processing"):
            self._settle(it.record_id, status="cancelled")

    def remove(self, item_id: str):
        it = self.get(item_id)
        if it is None:
            return
        self._stop(it)
        self.items = [i for i in self.items if i.id != item_id]
        self._drop_temp(it)
        self.changed.emit()
        self.pump()

    def clear(self):
        for it in self.items:
            self._stop(it)
            self._drop_temp(it)
        self.items = []
        self.changed.emit()

    @staticmethod
    def _drop_temp(it: QueueItem):
        if it.temp:
            try:
                it.path.unlink(missing_ok=True)
            except OSError:
                pass

    def enhance(self, settings: EnhanceSettings, ids: list[str] | None = None):
        """Enhance idle photos, or re-enhance finished ones, with these settings (all if ids is None)."""
        targets = [i for i in self.items if is_startable(i.status) and (ids is None or i.id in ids)]
        if not targets:
            return
        for it in targets:
            if it.job_id:
                api.cancel_job(it.job_id)  # drop the old result on the server
        if len(targets) > 1:
            self.batch_announced = False  # one summary toast instead of one per photo
        for it in targets:
            it.settings = replace(settings)
            it.status, it.stage, it.progress, it.upload = "waiting", "waiting", 0.0, 0.0
            it.job_id, it.job, it.error, it.record_id = None, None, None, None
        self.changed.emit()
        self.pump()

    def shutdown(self):
        """The app is closing: stop uploads and running jobs (their quota is refunded)."""
        for it in self.items:
            if is_active(it.status):
                self._stop(it)
            self._drop_temp(it)

