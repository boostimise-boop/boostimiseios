"""PhotoMatch engine: scan two folders, fingerprint the images and find which
images in the left folder have no counterpart in the right folder.

Three matching modes:
  exact   - identical file content (any file name). Fast, no decoding.
  visual  - 256-bit perceptual hash. Survives resizing, recompression,
            format conversion (PNG <-> JPG), small tone changes.
  ai      - DINOv2-small image embeddings run through OpenVINO on the Intel
            NPU / Arc GPU / CPU. Survives edits, filters, crops, watermarks.

No GUI code lives here so the engine can be tested and reused on its own.
"""
from __future__ import annotations

import hashlib
import io
import os
import sqlite3
import sys
import threading
import time
import urllib.request
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

import numpy as np
from PIL import Image, ImageFile, ImageOps

ImageFile.LOAD_TRUNCATED_IMAGES = True
Image.MAX_IMAGE_PIXELS = None  # local photos only; allow huge panoramas

try:  # iPhone photos (.heic). pi-heif = decode-only build of pillow-heif (LGPL, no GPL x265 encoder)
    from pi_heif import register_heif_opener
except ImportError:
    try:
        from pillow_heif import register_heif_opener
    except ImportError:
        register_heif_opener = None
if register_heif_opener:
    register_heif_opener()

IMAGE_EXTS = {
    ".jpg", ".jpeg", ".jpe", ".jfif", ".png", ".bmp", ".gif", ".tif", ".tiff",
    ".webp", ".heic", ".heif", ".avif", ".ico",
    # Camera RAW: listed and compared by content; decoded only when Pillow can.
    ".cr2", ".cr3", ".nef", ".arw", ".dng", ".orf", ".rw2", ".raf", ".srw", ".pef",
}
SKIP_DIRS = {"$recycle.bin", "system volume information", ".thumbnails"}

APP_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "PhotoMatch"
# Read-only files shipped with the app (inside the installed exe, or next to this script).
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))

MODES = ("exact", "visual", "ai")

# Similarity (0..1) at or above which two images count as "the same".
DEFAULT_THRESHOLD = {"visual": 0.90, "ai": 0.90}

# DINOv2-small (Meta, Apache-2.0) exported to ONNX by the onnx-community.
AI_MODEL_URL = "https://huggingface.co/onnx-community/dinov2-small/resolve/main/onnx/model.onnx"
AI_MODEL_FILE = "dinov2-small.onnx"
AI_MODEL_SIZE = 88_532_934
AI_MODEL_SHA256 = "f22797eabf810a75e41de68d378541ebea372122b25c4ce3ef25ff618250c20a"
BUNDLED_AI_MODEL = RESOURCE_DIR / "models" / "dinov2-small.xml"  # OpenVINO IR (FP16), made by packaging/prepare_model.py
AI_KIND = "ai-dinov2s-v1"  # bump when preprocessing/pooling changes (invalidates cache)
AI_INPUT = 224
AI_BATCH = 8

ProgressFn = Callable[[str, int, int], None]  # (message, done, total)


class Cancelled(Exception):
    pass


# --------------------------------------------------------------------------
# Scanning
# --------------------------------------------------------------------------

@dataclass
class ImgFile:
    path: str      # absolute path
    rel: str       # path relative to the scanned root
    size: int
    mtime_ns: int

    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    @property
    def subfolder(self) -> str:
        return os.path.dirname(self.rel)


def _norm(p: str) -> str:
    return os.path.normcase(os.path.abspath(p))


def is_inside(child: str, parent: str) -> bool:
    child, parent = _norm(child), _norm(parent)
    return child != parent and child.startswith(parent.rstrip("\\/") + os.sep)


def scan_folder(root: str, recursive: bool, exclude: Iterable[str] = (),
                cancel: Optional[threading.Event] = None) -> list[ImgFile]:
    """List image files under root. Folders in `exclude` are skipped."""
    root = os.path.abspath(root)
    excluded = {_norm(e) for e in exclude}
    out: list[ImgFile] = []
    stack = [root]
    while stack:
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        folder = stack.pop()
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    if (recursive and entry.name.lower() not in SKIP_DIRS
                            and _norm(entry.path) not in excluded):
                        stack.append(entry.path)
                elif os.path.splitext(entry.name)[1].lower() in IMAGE_EXTS:
                    st = entry.stat()
                    out.append(ImgFile(entry.path, os.path.relpath(entry.path, root),
                                       st.st_size, st.st_mtime_ns))
            except OSError:
                continue
    out.sort(key=lambda f: f.rel.lower())
    return out


# --------------------------------------------------------------------------
# Decoding and fingerprints
# --------------------------------------------------------------------------

def digest_bytes(data: bytes) -> bytes:
    return hashlib.blake2b(data, digest_size=16).digest()


def digest_file(path: str) -> bytes:
    h = hashlib.blake2b(digest_size=16)
    with open(path, "rb") as fh:
        while chunk := fh.read(1 << 20):
            h.update(chunk)
    return h.digest()


def open_image(src, min_side: int) -> Image.Image:
    """Decode an image (path or bytes) upright and in RGB. JPEGs are decoded
    at reduced resolution (DCT scaling) when only a small version is needed,
    which is several times faster than a full decode."""
    im = Image.open(io.BytesIO(src) if isinstance(src, (bytes, bytearray)) else src)
    if im.format == "JPEG":
        im.draft("RGB", (min_side, min_side))
    im = ImageOps.exif_transpose(im)
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        bg.alpha_composite(im)
        im = bg
    return im.convert("RGB")


def _dct_rows(n: int, k: int) -> np.ndarray:
    x = np.arange(n)
    u = np.arange(k)[:, None]
    return np.cos(np.pi * (2 * x + 1) * u / (2 * n)).astype(np.float32)


_DCT = _dct_rows(64, 16)


def phash256(im: Image.Image) -> np.ndarray:
    """256-bit DCT perceptual hash, returned as 32 packed bytes."""
    g = np.asarray(im.convert("L").resize((64, 64), Image.Resampling.BOX), dtype=np.float32)
    d = (_DCT @ g @ _DCT.T).ravel()
    return np.packbits(d > np.median(d[1:]))


def ai_input(im: Image.Image) -> np.ndarray:
    """Whole image squashed to the model input (keeps everything in frame)."""
    return np.asarray(im.resize((AI_INPUT, AI_INPUT), Image.Resampling.BICUBIC), dtype=np.uint8)


# --------------------------------------------------------------------------
# Persistent fingerprint cache (so re-runs on big folders are instant)
# --------------------------------------------------------------------------

class FeatureCache:
    def __init__(self, path: Path = APP_DIR / "cache.sqlite"):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS digest (path TEXT PRIMARY KEY, size INT, mtime INT, digest BLOB)")
        self.db.execute("CREATE TABLE IF NOT EXISTS feat (path TEXT, kind TEXT, size INT, mtime INT, feat BLOB, "
                        "PRIMARY KEY (path, kind))")
        self.db.commit()
        self.lock = threading.Lock()

    def get_digest(self, f: ImgFile) -> Optional[bytes]:
        with self.lock:
            row = self.db.execute("SELECT size, mtime, digest FROM digest WHERE path=?", (_norm(f.path),)).fetchone()
        return row[2] if row and row[0] == f.size and row[1] == f.mtime_ns else None

    def get_feat(self, f: ImgFile, kind: str):
        """Returns (found, feature_bytes_or_None). None feature = undecodable."""
        with self.lock:
            row = self.db.execute("SELECT size, mtime, feat FROM feat WHERE path=? AND kind=?",
                                  (_norm(f.path), kind)).fetchone()
        if row and row[0] == f.size and row[1] == f.mtime_ns:
            return True, row[2]
        return False, None

    def put(self, digests: list, feats: list, kind: Optional[str]):
        with self.lock:
            if digests:
                self.db.executemany("INSERT OR REPLACE INTO digest VALUES (?,?,?,?)",
                                    [(_norm(f.path), f.size, f.mtime_ns, d) for f, d in digests])
            if feats and kind:
                self.db.executemany("INSERT OR REPLACE INTO feat VALUES (?,?,?,?,?)",
                                    [(_norm(f.path), kind, f.size, f.mtime_ns, b) for f, b in feats])
            self.db.commit()

    def clear(self):
        with self.lock:
            self.db.execute("DELETE FROM digest")
            self.db.execute("DELETE FROM feat")
            self.db.commit()
            self.db.execute("VACUUM")

    def close(self):
        with self.lock:
            self.db.close()


# --------------------------------------------------------------------------
# AI embedder (OpenVINO: NPU / GPU / CPU)
# --------------------------------------------------------------------------

def list_ai_devices() -> list[tuple[str, str]]:
    """[(device_id, friendly_name)] ordered NPU, GPU, CPU. Empty if OpenVINO missing."""
    try:
        import openvino as ov
    except ImportError:
        return []
    core = ov.Core()
    devs = []
    for d in core.available_devices:
        try:
            name = core.get_property(d, "FULL_DEVICE_NAME")
        except Exception:
            name = d
        devs.append((d, str(name)))
    order = {"NPU": 0, "GPU": 1, "CPU": 2}
    devs.sort(key=lambda t: (order.get(t[0].split(".")[0], 3), t[0]))
    return devs


def ensure_ai_model(progress: ProgressFn, cancel: threading.Event) -> Path:
    """Download the model once (~88 MB) into %LOCALAPPDATA%\\PhotoMatch\\models."""
    target = APP_DIR / "models" / AI_MODEL_FILE
    if target.exists() and target.stat().st_size == AI_MODEL_SIZE:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_suffix(".part")
    sha = hashlib.sha256()
    req = urllib.request.Request(AI_MODEL_URL, headers={"User-Agent": "PhotoMatch/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp, open(part, "wb") as out:
        total = int(resp.headers.get("Content-Length") or AI_MODEL_SIZE)
        done = 0
        while chunk := resp.read(1 << 20):
            if cancel.is_set():
                out.close()
                part.unlink(missing_ok=True)
                raise Cancelled()
            out.write(chunk)
            sha.update(chunk)
            done += len(chunk)
            progress(f"Downloading AI model (first time only) … {done / 1e6:.0f} / {total / 1e6:.0f} MB", done, total)
    if sha.hexdigest() != AI_MODEL_SHA256:
        part.unlink(missing_ok=True)
        raise RuntimeError("The downloaded AI model is corrupted (checksum mismatch). Please try again.")
    os.replace(part, target)
    return target


_compiled_models: dict[str, object] = {}


def get_ai_model(device: str, progress: ProgressFn, cancel: threading.Event):
    """Compiled OpenVINO model taking a uint8 NHWC RGB batch. Compiled blobs are
    cached on disk, so only the very first NPU compile is slow (~30 s)."""
    if device in _compiled_models:
        return _compiled_models[device]
    import openvino as ov
    from openvino.preprocess import ColorFormat, PrePostProcessor

    model_path = BUNDLED_AI_MODEL if BUNDLED_AI_MODEL.exists() else ensure_ai_model(progress, cancel)
    core = ov.Core()
    cache_dir = APP_DIR / "ov_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    core.set_property({"CACHE_DIR": str(cache_dir)})

    model = core.read_model(str(model_path))
    model.reshape({model.inputs[0].any_name: [AI_BATCH, 3, AI_INPUT, AI_INPUT]})  # NPU needs static shapes
    ppp = PrePostProcessor(model)
    ppp.input().tensor().set_element_type(ov.Type.u8).set_layout(ov.Layout("NHWC")).set_color_format(ColorFormat.RGB)
    ppp.input().model().set_layout(ov.Layout("NCHW"))
    ppp.input().preprocess().convert_element_type(ov.Type.f32) \
        .mean([123.675, 116.28, 103.53]).scale([58.395, 57.12, 57.375])
    model = ppp.build()

    first = not any(cache_dir.iterdir())
    hint = " (first time can take ~30 s)" if first or device.startswith("NPU") else ""
    progress(f"Loading AI model on {device}{hint} …", 0, 0)
    compiled = core.compile_model(model, device, {"PERFORMANCE_HINT": "THROUGHPUT"})
    _compiled_models[device] = compiled
    return compiled


def pool_embedding(hidden: np.ndarray) -> np.ndarray:
    """DINOv2 tokens (B, 1+patches, 384) -> L2-normalised global descriptor.
    CLS token concatenated with the mean patch token."""
    cls = hidden[:, 0, :]
    patch = hidden[:, 1:, :].mean(axis=1)
    cls = cls / (np.linalg.norm(cls, axis=1, keepdims=True) + 1e-8)
    patch = patch / (np.linalg.norm(patch, axis=1, keepdims=True) + 1e-8)
    emb = np.concatenate([cls, patch], axis=1)
    return (emb / (np.linalg.norm(emb, axis=1, keepdims=True) + 1e-8)).astype(np.float32)


# --------------------------------------------------------------------------
# Fingerprinting a list of files
# --------------------------------------------------------------------------

@dataclass
class Fingerprints:
    digests: list            # bytes | None per file
    feats: Optional[np.ndarray]  # (n, dim) or None for exact mode
    has_feat: np.ndarray     # bool per file


def _bounded_map(pool: ThreadPoolExecutor, fn, items, window: int, cancel: threading.Event):
    """Ordered parallel map that never keeps more than `window` results in memory."""
    pending: deque = deque()
    try:
        for item in items:
            pending.append(pool.submit(fn, item))
            if len(pending) >= window:
                yield pending.popleft().result()
            if cancel.is_set():
                raise Cancelled()
        while pending:
            yield pending.popleft().result()
            if cancel.is_set():
                raise Cancelled()
    finally:
        for fut in pending:
            fut.cancel()


def fingerprint(files: list[ImgFile], mode: str, label: str, progress: ProgressFn,
                cancel: threading.Event, cache: Optional[FeatureCache], device: str = "NPU",
                only_digest: Optional[set] = None) -> Fingerprints:
    n = len(files)
    workers = min(16, (os.cpu_count() or 4) + 2)

    # ---- exact: hash only the files whose size could possibly match -------
    if mode == "exact":
        digests: list = [None] * n
        todo = [i for i in range(n) if only_digest is None or i in only_digest]
        new = []

        def work(i):
            f = files[i]
            d = cache.get_digest(f) if cache else None
            if d is None:
                try:
                    d = digest_file(f.path)
                    new.append((f, d))
                except OSError:
                    d = None
            return i, d

        with ThreadPoolExecutor(workers) as pool:
            for k, (i, d) in enumerate(_bounded_map(pool, work, todo, workers * 4, cancel), 1):
                digests[i] = d
                if k % 25 == 0 or k == len(todo):
                    progress(f"{label}: comparing file contents {k:,} / {len(todo):,}", k, len(todo))
        if cache:
            cache.put(new, [], None)
        return Fingerprints(digests, None, np.zeros(n, bool))

    # ---- visual / ai -------------------------------------------------------
    kind = "visual" if mode == "visual" else AI_KIND
    dim_bytes = 32 if mode == "visual" else 768 * 4
    digests = [None] * n
    feats = np.zeros((n, 32), np.uint8) if mode == "visual" else np.zeros((n, 768), np.float32)
    has_feat = np.zeros(n, bool)
    final = np.zeros(n, bool)  # feature attempt finished (success or undecodable) -> safe to cache
    new_digests = []

    todo = []
    for i, f in enumerate(files):
        d = cache.get_digest(f) if cache else None
        found, blob = cache.get_feat(f, kind) if cache else (False, None)
        if d is not None and found:
            digests[i] = d
            if blob is not None and len(blob) == dim_bytes:
                feats[i] = np.frombuffer(blob, feats.dtype)
                has_feat[i] = True
        else:
            todo.append(i)
    cached = n - len(todo)
    if cached:
        progress(f"{label}: {cached:,} images already known from previous runs", cached, n)

    compiled = get_ai_model(device, progress, cancel) if (mode == "ai" and todo) else None

    def work(i):
        f = files[i]
        try:
            data = Path(f.path).read_bytes()
        except OSError:
            return i, None, None
        d = digest_bytes(data)
        try:
            im = open_image(data, 128 if mode == "visual" else 256)
            x = phash256(im) if mode == "visual" else ai_input(im)
        except Exception:
            x = None
        return i, d, x

    infer_queue = None
    if compiled is not None:
        import openvino as ov

        infer_queue = ov.AsyncInferQueue(compiled)

        def on_done(request, idxs):
            emb = pool_embedding(request.get_output_tensor(0).data)
            for row, i in enumerate(idxs):
                feats[i] = emb[row]
                has_feat[i] = True
                final[i] = True

        infer_queue.set_callback(on_done)

    batch_x, batch_i = [], []

    def flush():
        if not batch_i:
            return
        x = np.zeros((AI_BATCH, AI_INPUT, AI_INPUT, 3), np.uint8)
        x[:len(batch_x)] = np.stack(batch_x)
        infer_queue.start_async({0: x}, list(batch_i))
        batch_x.clear()
        batch_i.clear()

    t0 = time.time()
    try:
        with ThreadPoolExecutor(workers) as pool:
            for k, (i, d, x) in enumerate(_bounded_map(pool, work, todo, workers * 4, cancel), 1):
                digests[i] = d
                if d is not None:
                    new_digests.append((files[i], d))
                if x is None:
                    final[i] = d is not None
                else:
                    if mode == "visual":
                        feats[i] = x
                        has_feat[i] = final[i] = True
                    else:
                        batch_x.append(x)
                        batch_i.append(i)
                        if len(batch_i) == AI_BATCH:
                            flush()
                if k % 10 == 0 or k == len(todo):
                    rate = k / max(time.time() - t0, 1e-6)
                    eta = (len(todo) - k) / rate if rate else 0
                    where = f" on {device}" if mode == "ai" else ""
                    progress(f"{label}: analysing images{where} {k:,} / {len(todo):,}"
                             f"  ({rate:.0f}/s, ~{eta:.0f}s left)", k, len(todo))
            if infer_queue is not None:
                flush()
                infer_queue.wait_all()
    finally:
        if infer_queue is not None:
            infer_queue.wait_all()
        if cache:
            cache.put(new_digests,
                      [(files[i], feats[i].tobytes() if has_feat[i] else None) for i in todo if final[i]],
                      kind)
    return Fingerprints(digests, feats, has_feat)


# --------------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------------

def nearest(left: np.ndarray, right: np.ndarray, mode: str) -> tuple[np.ndarray, np.ndarray]:
    """For every left row: index of the most similar right row and its similarity (0..1)."""
    if len(left) == 0 or len(right) == 0:
        return np.full(len(left), -1), np.zeros(len(left), np.float32)
    idx = np.empty(len(left), np.int64)
    sim = np.empty(len(left), np.float32)
    if mode == "visual":
        chunk = max(1, 4_000_000 // (len(right) * 32))
        for s in range(0, len(left), chunk):
            dist = np.bitwise_count(left[s:s + chunk, None, :] ^ right[None, :, :]).sum(axis=2, dtype=np.int32)
            idx[s:s + chunk] = dist.argmin(axis=1)
            sim[s:s + chunk] = 1.0 - dist.min(axis=1) / 256.0
    else:
        chunk = max(1, 8_000_000 // len(right))
        for s in range(0, len(left), chunk):
            cos = left[s:s + chunk] @ right.T
            idx[s:s + chunk] = cos.argmax(axis=1)
            sim[s:s + chunk] = np.clip(cos.max(axis=1), 0, 1)
    return idx, sim


@dataclass
class Match:
    left: ImgFile
    right: Optional[ImgFile]   # closest image in the right folder (None = nothing comparable)
    score: float               # similarity 0..1 (1.0 for identical files)
    exact: bool                # byte-identical copy exists in the right folder
    readable: bool             # image content could be decoded (visual/ai modes)


@dataclass
class CompareResult:
    left_root: str
    right_root: str
    mode: str
    device: str
    left: list[ImgFile]
    right: list[ImgFile]
    matches: list[Match]
    seconds: float
    cached_note: str = ""
    errors: list[str] = field(default_factory=list)

    def is_matched(self, m: Match, threshold: float) -> bool:
        return m.exact or (self.mode != "exact" and m.readable and m.right is not None and m.score >= threshold)


def compare_folders(left_root: str, right_root: str, recursive: bool, mode: str,
                    progress: ProgressFn, cancel: threading.Event, device: str = "NPU",
                    use_cache: bool = True) -> CompareResult:
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}")
    if _norm(left_root) == _norm(right_root):
        raise ValueError("The left and right folders are the same folder.")
    cache = FeatureCache() if use_cache else None
    try:
        return _compare(left_root, right_root, recursive, mode, progress, cancel, device, cache)
    finally:
        if cache:
            cache.close()


def _compare(left_root, right_root, recursive, mode, progress, cancel, device, cache) -> CompareResult:
    t0 = time.time()
    progress("Scanning left folder …", 0, 0)
    left = scan_folder(left_root, recursive, [right_root], cancel)
    progress(f"Scanning right folder … (left has {len(left):,} images)", 0, 0)
    right = scan_folder(right_root, recursive, [left_root], cancel)
    if not left:
        raise ValueError("No images were found in the left folder.")

    if mode == "exact":
        right_sizes = {f.size for f in right}
        left_sizes = {f.size for f in left}
        lf = fingerprint(left, mode, "Left folder", progress, cancel, cache,
                         only_digest={i for i, f in enumerate(left) if f.size in right_sizes})
        rf = fingerprint(right, mode, "Right folder", progress, cancel, cache,
                         only_digest={i for i, f in enumerate(right) if f.size in left_sizes})
    else:
        lf = fingerprint(left, mode, "Left folder", progress, cancel, cache, device)
        rf = fingerprint(right, mode, "Right folder", progress, cancel, cache, device)

    progress("Matching …", 0, 0)
    by_digest: dict[bytes, int] = {}
    for j, d in enumerate(rf.digests):
        if d is not None:
            by_digest.setdefault(d, j)

    best_idx = np.full(len(left), -1)
    best_sim = np.zeros(len(left), np.float32)
    if mode != "exact":
        r_rows = np.flatnonzero(rf.has_feat)
        l_rows = np.flatnonzero(lf.has_feat)
        idx, sim = nearest(lf.feats[l_rows], rf.feats[r_rows], mode)
        best_idx[l_rows] = np.where(idx >= 0, r_rows[np.maximum(idx, 0)], -1)
        best_sim[l_rows] = sim

    matches = []
    for i, f in enumerate(left):
        d = lf.digests[i]
        j = by_digest.get(d) if d is not None else None
        if j is not None:
            matches.append(Match(f, right[j], 1.0, True, bool(lf.has_feat[i]) or mode == "exact"))
        else:
            k = int(best_idx[i])
            matches.append(Match(f, right[k] if k >= 0 else None, float(best_sim[i]), False,
                                 bool(lf.has_feat[i])))
    return CompareResult(os.path.abspath(left_root), os.path.abspath(right_root), mode,
                         device if mode == "ai" else "CPU", left, right, matches, time.time() - t0)


# --------------------------------------------------------------------------
# Copying
# --------------------------------------------------------------------------

def _same_file(a: str, b: str) -> bool:
    try:
        return os.path.getsize(a) == os.path.getsize(b) and digest_file(a) == digest_file(b)
    except OSError:
        return False


def copy_files(files: list[ImgFile], dest_root: str, keep_structure: bool,
               progress: ProgressFn, cancel: threading.Event) -> dict:
    """Copy files into dest_root. Never overwrites: identical files already there
    are skipped, name clashes get ' (1)', ' (2)' … appended."""
    import shutil

    copied = skipped = 0
    failed: list[str] = []
    for k, f in enumerate(files, 1):
        if cancel.is_set():
            break
        rel = f.rel if keep_structure else f.name
        dest = os.path.join(dest_root, rel)
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            if os.path.exists(dest):
                if _same_file(f.path, dest):
                    skipped += 1
                    continue
                stem, ext = os.path.splitext(dest)
                n = 1
                while os.path.exists(f"{stem} ({n}){ext}"):
                    n += 1
                dest = f"{stem} ({n}){ext}"
            shutil.copy2(f.path, dest)
            copied += 1
        except OSError as e:
            failed.append(f"{f.rel}: {e.strerror or e}")
        finally:
            progress(f"Copying {k:,} / {len(files):,} …", k, len(files))
    return {"copied": copied, "skipped": skipped, "failed": failed, "cancelled": cancel.is_set()}
