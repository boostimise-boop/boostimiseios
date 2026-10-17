"""Client for the AI enhancement server (bmapi.boostimise.com) - the website's lib/api.ts.
Blocking calls: run them with core.net.run_bg()."""
from __future__ import annotations

import threading
import urllib.parse
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from core import net
from core.config import API_BASE

MODES = ("auto", "face", "restore", "upscale")
FACE_MODELS = ("auto", "codeformer", "gfpgan", "compare")
ACCEPTED_EXT = (".jpg", ".jpeg", ".png", ".webp")
CONTENT_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


@dataclass(frozen=True)
class EnhanceSettings:
    mode: str = "auto"
    scale: int = 2                     # 2 | 4
    strength: float = 0.8              # 0..1
    face_enhance: bool = True
    face_model: str = "auto"
    fidelity: float | None = None      # None = adaptive
    denoise: float | None = None       # None = automatic
    sharpen: float | None = None       # None = automatic
    color_enhance: bool = True
    quality: int = 95


DEFAULT_SETTINGS = EnhanceSettings()


def preset_for(mode: str, s: EnhanceSettings) -> EnhanceSettings:
    """Settings each mode starts from (the user can still change everything)."""
    if mode == "upscale":
        return replace(s, mode=mode, face_enhance=False, color_enhance=False, strength=1.0, sharpen=None, denoise=None)
    if mode == "face":
        return replace(s, mode=mode, face_enhance=True, color_enhance=False, strength=0.8)
    if mode == "restore":
        return replace(s, mode=mode, face_enhance=True, color_enhance=True, strength=0.9)
    return replace(s, mode=mode, face_enhance=True, color_enhance=True, strength=0.8)


def _js_bool(v: bool) -> str:
    return "true" if v else "false"


def get_health() -> dict:
    return net.get_json(f"{API_BASE}/api/health", timeout=10)


def get_gpu() -> dict:
    return net.get_json(f"{API_BASE}/api/gpu", timeout=10)


def analyze_image(path: Path, name: str, scale: int) -> dict:
    """AI analysis + recommended settings for one photo (synchronous on the server)."""
    body = net.Multipart([("scale", str(scale))], "image", name, path, content_type(path))
    return net.post_multipart(f"{API_BASE}/api/analyze", body, timeout=120).json()


def get_jobs(ids: list[str]) -> dict:
    """Status of many jobs in one request: {"jobs": [...], "missing": [...]}."""
    q = urllib.parse.quote(",".join(ids), safe="")
    return net.get_json(f"{API_BASE}/api/jobs?ids={q}", timeout=15)


def cancel_job(job_id: str):
    """Cancels a running job, or deletes a finished one's results (best effort, in the background)."""
    net.run_bg(lambda: net.request("DELETE", f"{API_BASE}/api/job/{job_id}", timeout=15), None, lambda e: None)


def result_url(job: dict, kind: str) -> str:
    return f"{API_BASE}{(job.get('urls') or {}).get(kind, '')}"


def content_type(path: Path) -> str:
    return CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")


def submit_enhance(path: Path, name: str, s: EnhanceSettings, on_upload: Callable[[float], None] | None,
                   cancel: threading.Event) -> str:
    """Uploads the photo with its settings; returns the job id (raises net.Cancelled when aborted)."""
    fields = [("mode", s.mode), ("scale", str(s.scale)), ("strength", f"{s.strength:.2f}"),
              ("face_enhance", _js_bool(s.face_enhance)), ("face_model", s.face_model),
              ("color_enhance", _js_bool(s.color_enhance)), ("quality", str(s.quality))]
    # omitted fields = "automatic" on the server
    if s.fidelity is not None:
        fields.append(("fidelity", f"{s.fidelity:.2f}"))
    if s.denoise is not None:
        fields.append(("denoise", f"{s.denoise:.2f}"))
    if s.sharpen is not None:
        fields.append(("sharpen", f"{s.sharpen:.2f}"))
    body = net.Multipart(fields, "image", name, path, content_type(path), on_upload, cancel)
    resp = net.post_multipart(f"{API_BASE}/api/enhance", body, timeout=120)
    data = resp.json() or {}
    if resp.status != 202 or not data.get("job_id"):
        raise net.HttpError(resp.status, "http_error", f"Upload failed ({resp.status})", data)
    return data["job_id"]


def fetch_bytes(url: str, timeout: float = 180) -> bytes:
    return net.request("GET", url, timeout=timeout).data


def download_result(job: dict, kind: str, folder: Path, on_progress=None) -> Path:
    """Saves the PNG / JPG under the server's name (e.g. photo_enhanced_4x.png) in ``folder``."""
    return net.download(result_url(job, kind), folder, f"{job.get('original_name') or 'photo'}_enhanced.{kind}",
                        on_progress=on_progress, timeout=240)


def download_zip(job_ids: list[str], fmt: str, folder: Path, on_progress=None) -> Path:
    """One ZIP with all finished results (ids in a form body, so batches of hundreds fit)."""
    body = net.form_body([("ids", ",".join(job_ids)), ("format", fmt)])
    return net.download(f"{API_BASE}/api/download-zip", folder, f"enhanced_photos_{len(job_ids)}_{fmt}.zip",
                        method="POST", body=body, headers={"Content-Type": "application/x-www-form-urlencoded"},
                        on_progress=on_progress, timeout=600)


def format_bytes(n: int | float) -> str:
    if n < 1024:
        return f"{int(n)} B"
    if n < 1024 ** 2:
        return f"{n / 1024:.0f} KB"
    return f"{n / 1024 ** 2:.1f} MB"


def validate_file(path: Path, max_upload_mb: float) -> str | None:
    if path.suffix.lower() not in ACCEPTED_EXT:
        return "Unsupported file type. Please use JPG, JPEG, PNG or WEBP."
    try:
        size = path.stat().st_size
    except OSError:
        return "The file can't be read."
    if size > max_upload_mb * 1024 * 1024:
        return f"File is too large (max {max_upload_mb:g} MB)."
    if size == 0:
        return "The file is empty."
    return None
