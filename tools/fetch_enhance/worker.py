"""Fetch & Enhance: Fetch by Code's search, then for each code only the photos with people in them
are kept and enhanced (4x) on the enhancement server. They are saved under Fetch by Code's names
("2-221 Oppo a6 pro 5g 1.jpg" …) for the page to run Rename by Match on them afterwards, or straight
under the names Rename by Match gives: one photo "2-226 Realme P3 Ultra.jpg", two or more a folder
"2-221 Oppo a6 pro 5g" with img1.jpg …

Phones, screenshots and other photos without a person are never saved. Each enhancement is taken
from the account's daily quota exactly like in the Photo Enhancer (reserve, then finish).
"""
from __future__ import annotations

import io
import re
import shutil
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Signal

from core import net
from tools.match_renamer.engine import caption_note, safe_filename
from tools.photo_enhancer import api
from tools.photo_enhancer.api import DEFAULT_SETTINGS
from tools.whatsapp_fetch.helpers import IMAGE_EXTS, CodeItem, existing_output
from tools.whatsapp_fetch.worker import WhatsAppWorker

MIN_FACE_CONFIDENCE = 0.8
# The biggest face must be at least this part of the photo's short side. People photos sent for a
# cover measured 0.15 and up; a contact picture or wallpaper in a phone screenshot is smaller.
MIN_FACE_SHARE = 0.07
PARALLEL = 2      # photos checked / enhanced at once: the server's GPU works on one while the next uploads
POLL_S = 1.0


class Stopped(Exception):
    pass


class LimitHit(Exception):
    """The quota said no: code is limit | blocked | onboarding | update_required."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def error_text(e: BaseException) -> str:
    return getattr(e, "message", None) or str(e) or type(e).__name__


def is_named(name: str, base: str) -> bool:
    """"2-283 Infinix hot 50i", with only (bracketed) text after it: a note or "(2)"."""
    name, base = name.lower(), base.lower()
    return name.startswith(base) and re.fullmatch(r"(\s*\([^()]*\))*", name[len(base):]) is not None


def existing_named(out_dir: Path, base: str) -> Path | None:
    """The photo ("base.jpg", "base (note).jpg") or folder ("base (note)/img1.jpg") saved for a code before."""
    try:
        entries = sorted(out_dir.iterdir())
    except OSError:
        return None
    for p in entries:
        if p.is_dir():
            if is_named(p.name, base) and any(p.iterdir()):
                return p
        elif p.suffix.lower() in IMAGE_EXTS and is_named(p.stem, base):
            return p
    return None


def save_named(out_dir: Path, label: str, photos: list[tuple[bytes, str]]) -> Path:
    """One photo: "<label>.jpg". Several: the folder "<label>" with img1.jpg, img2.jpg … Returns the
    file or folder."""
    out_dir.mkdir(parents=True, exist_ok=True)
    if len(photos) == 1:
        data, ext = photos[0]
        path = out_dir / f"{label}{ext}"
        path.write_bytes(data)
        return path
    folder = out_dir / label
    folder.mkdir(exist_ok=True)
    for i, (data, ext) in enumerate(photos, 1):
        (folder / f"img{i}{ext}").write_bytes(data)
    return folder


def people_in(data: dict) -> int:
    """Faces big and sure enough to be a photo of people, from the server's analysis."""
    a = data.get("analysis") or {}
    short = min(a.get("width") or 0, a.get("height") or 0)
    return sum(1 for f in data.get("faces") or []
               if (f.get("confidence") or 0) >= MIN_FACE_CONFIDENCE and (f.get("size_px") or 0) >= MIN_FACE_SHARE * short)


def as_uploadable(data: bytes, ext: str) -> tuple[bytes, str]:
    """The server takes JPG, PNG and WEBP; anything else (rare on WhatsApp: GIF, HEIC) becomes a JPG."""
    if ext in api.ACCEPTED_EXT:
        return data, ext
    from PIL import Image, ImageOps
    try:
        from pi_heif import register_heif_opener
        register_heif_opener()
    except ImportError:
        pass
    with Image.open(io.BytesIO(data)) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=95)
    return buf.getvalue(), ".jpg"


def image_size(path: Path) -> tuple[int | None, int | None]:
    from PIL import Image
    try:
        with Image.open(path) as im:
            return im.size
    except Exception:  # noqa: BLE001 - only for the dashboard
        return None, None


class FetchEnhanceWorker(WhatsAppWorker):
    limit_hit = Signal(str)  # why the quota said no (see LimitHit)
    code_saved = Signal(int)  # row whose photos were saved

    def __init__(self, account):
        super().__init__("basic")
        self.account = account
        self.opt: dict = {}

    def _existing(self, out_dir: Path, base: str) -> Path | None:
        return existing_named(out_dir, base) or existing_output(out_dir, base)

    def _run_job(self, rows, opt):
        self.opt = opt
        if opt.get("people_only", True) or opt.get("scale"):
            try:
                api.get_health()
            except Exception as e:  # noqa: BLE001
                self.log.emit(f"Cannot reach the enhancement server ({error_text(e)}). Check the internet and try again.")
                self.activity.emit("")
                self.job_finished.emit("Failed: the enhancement server is not reachable.")
                return
        super()._run_job(rows, opt)

    def _check_stop(self, abort: threading.Event):
        if self._stop_job.is_set() or self._quit.is_set():
            abort.set()
        if abort.is_set():
            raise Stopped()

    # ---- one code ------------------------------------------------------------------
    def _save(self, row: int, item: CodeItem, base: str, out_dir: Path, files: list[tuple[bytes, str]],
              failed: int, images: list[dict]) -> tuple[str, int]:
        opt = self.opt
        work = Path(tempfile.mkdtemp(prefix="boostimise-fe-"))
        abort = threading.Event()
        try:
            paths = []
            for i, (data, ext) in enumerate(files, 1):
                data, ext = as_uploadable(data, ext)
                path = work / f"{item.code} {i}{ext}"
                path.write_bytes(data)
                paths.append(path)

            if opt.get("people_only", True):
                kept = self._people(row, item, paths, abort)
                if not kept:
                    text = f"No photos of people ({len(paths)} skipped: phones, screenshots …)"
                    self.row_status.emit(row, "none", text, "")
                    self.log.emit(f"[{item.code}] {text}")
                    return "none", 0
            else:
                kept = paths

            scale = opt.get("scale", 4)
            if scale:
                photos, plain = self._enhance_all(row, item, kept, scale, abort)
            else:
                photos, plain = [(p.read_bytes(), p.suffix.lower()) for p in kept], 0

            if opt.get("rename_after"):
                # "2-283 Infinix hot 50i 1.jpg", also a single photo: Rename by Match names them afterwards
                for i, (data, ext) in enumerate(photos, 1):
                    (out_dir / f"{base} {i}{ext}").write_bytes(data)
                path = out_dir / f"{base} 1{photos[0][1]}"
            else:
                captions = [img.get("caption") or "" for img in images]
                label = safe_filename(base + (caption_note(captions, base) if opt.get("add_note") else ""))
                path = save_named(out_dir, label, photos)
        except Stopped:
            self.row_status.emit(row, "", "Stopped", "")
            return "error", 0
        except LimitHit as e:
            self._stop_job.set()
            self.limit_hit.emit(e.code)
            self.row_status.emit(row, "error", f"Not saved: {e}", "")
            self.log.emit(f"[{item.code}] not saved: {e} Stopping.")
            return "error", 0
        finally:
            shutil.rmtree(work, ignore_errors=True)

        what = f"enhanced {scale}×" if scale else "saved"
        text = f"{len(photos)} photo{'s' if len(photos) > 1 else ''} {what}"
        if len(kept) < len(paths):
            text += f", {len(paths) - len(kept)} without people skipped"
        if plain:
            text += f" ({plain} not enhanced, saved as received)"
        if failed:
            text += f" ({failed} download(s) failed)"
        self.row_status.emit(row, "error" if plain else "ok", text, str(path))
        self.code_saved.emit(row)
        self.log.emit(f"[{item.code}] {text} → {path}")
        return "ok", len(photos)

    def _people(self, row: int, item: CodeItem, paths: list[Path], abort: threading.Event) -> list[Path]:
        """The photos with people in them (a photo that can't be checked is kept)."""
        self.row_status.emit(row, "working", f"Looking for people in {len(paths)} photo(s)…", "")
        self.activity.emit(f"[{item.code}] looking for people in {len(paths)} photo(s)")

        def check(path: Path) -> int:
            self._check_stop(abort)
            return people_in(api.analyze_image(path, path.name, 4))

        with ThreadPoolExecutor(PARALLEL) as pool:
            futures = [pool.submit(check, p) for p in paths]
        kept = []
        for path, fut in zip(paths, futures):
            try:
                if fut.result():
                    kept.append(path)
            except Stopped:
                raise
            except Exception as e:  # noqa: BLE001
                self.log.emit(f"[{item.code}] photo {path.stem.rsplit(' ', 1)[-1]} could not be checked for "
                              f"people ({error_text(e)}), so it is kept")
                kept.append(path)
        self.log.emit(f"[{item.code}] {len(kept)} of {len(paths)} photo(s) with people")
        return kept

    def _enhance_all(self, row: int, item: CodeItem, paths: list[Path], scale: int,
                     abort: threading.Event) -> tuple[list[tuple[bytes, str]], int]:
        """The enhanced photos (a photo the server fails on is kept as received), and how many failed."""
        q = self.account.quota
        if q is not None and q.remaining is not None and q.remaining < len(paths):
            raise LimitHit("limit", f"{q.remaining} enhancement(s) left today, {item.code} needs {len(paths)}.")
        settings = replace(DEFAULT_SETTINGS, scale=scale)
        done = [0]
        lock = threading.Lock()
        self.row_status.emit(row, "working", f"Enhancing {scale}× 0/{len(paths)}…", "")
        self.activity.emit(f"[{item.code}] enhancing {len(paths)} photo(s) {scale}×")

        def one(path: Path) -> bytes:
            data = self._enhance_one(path, settings, abort)
            with lock:
                done[0] += 1
                self.row_status.emit(row, "working", f"Enhancing {scale}× {done[0]}/{len(paths)}…", "")
            return data

        with ThreadPoolExecutor(PARALLEL) as pool:
            futures = [pool.submit(one, p) for p in paths]
            errors = [f.exception() for f in futures]
        for e in errors:
            if isinstance(e, LimitHit):
                raise e
        if any(isinstance(e, Stopped) for e in errors):
            raise Stopped()
        photos, plain = [], 0
        for path, fut, e in zip(paths, futures, errors):
            if e is None:
                photos.append((fut.result(), ".jpg"))
            else:
                plain += 1
                self.log.emit(f"[{item.code}] photo {path.stem.rsplit(' ', 1)[-1]} not enhanced "
                              f"({error_text(e)}), saved as received")
                photos.append((path.read_bytes(), path.suffix.lower()))
        return photos, plain

    # ---- one photo on the enhancement server (Photo Enhancer's queue, one at a time) ------
    def _settle(self, record_id: str | None, **patch):
        if record_id:
            self.account.finish_enhancement(record_id, **patch)
            if patch.get("status") not in (None, "completed"):
                net.ui(self.account.refresh)  # failed / cancelled ones are refunded

    def _enhance_one(self, path: Path, settings, abort: threading.Event) -> bytes:
        width, height = image_size(path)
        while True:
            self._check_stop(abort)
            res = self.account.reserve_enhancement(path.name, path.stat().st_size, settings.mode, settings.scale,
                                                   width, height)
            quota = res.get("quota")
            net.ui(lambda: self.account.set_quota(quota))
            if not res.get("ok"):
                abort.set()  # nothing more can start today
                raise LimitHit(res.get("code", "limit"), res.get("message") or "Today's limit is reached.")
            record_id = res.get("id")
            try:
                job_id = api.submit_enhance(path, path.name, settings, None, abort)
                break
            except net.Cancelled:
                self._settle(record_id, status="cancelled")
                raise Stopped() from None
            except net.HttpError as e:
                if e.status != 503:
                    self._settle(record_id, status="failed", error=error_text(e))
                    raise
                self._settle(record_id, status="cancelled")  # server queue full (other users): try again
                time.sleep(3)
            except Exception as e:
                self._settle(record_id, status="failed", error=error_text(e))
                raise
        if record_id:
            self.account.finish_enhancement(record_id, job_id=job_id)
        try:
            job = self._wait_job(job_id, abort)
        except Stopped:
            api.cancel_job(job_id)
            self._settle(record_id, status="cancelled")
            raise
        except Exception as e:
            self._settle(record_id, status="failed", error=error_text(e))
            raise
        result = job.get("result") or {}
        inp = job.get("input") or {}
        self._settle(record_id, status="completed", job_id=job_id,
                     output_width=result.get("width") or inp.get("output_width"),
                     output_height=result.get("height") or inp.get("output_height"),
                     seconds=(result.get("benchmark") or {}).get("total_seconds"))
        try:
            return api.fetch_bytes(api.result_url(job, "jpg"))
        finally:
            api.cancel_job(job_id)  # saved here: delete it from the server

    def _wait_job(self, job_id: str, abort: threading.Event) -> dict:
        misses = 0
        while True:
            self._check_stop(abort)
            try:
                res = api.get_jobs([job_id])
                misses = 0
            except Exception:  # noqa: BLE001
                misses += 1
                if misses >= 20:
                    raise RuntimeError("Lost contact with the enhancement server") from None
                time.sleep(POLL_S)
                continue
            job = next((j for j in res.get("jobs") or [] if j.get("job_id") == job_id), None)
            if job is None:
                if job_id in (res.get("missing") or []):
                    raise RuntimeError("Job expired or the server restarted")
            elif job.get("status") == "completed":
                return job
            elif job.get("status") in ("failed", "cancelled"):
                raise RuntimeError((job.get("error") or {}).get("message") or f"processing {job['status']}")
            time.sleep(POLL_S)
