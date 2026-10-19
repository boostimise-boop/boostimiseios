"""Pure helpers: code-list parsing, file naming, match patterns and saving."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from core.codes import code_pattern, list_code, split_code  # noqa: F401  (code_pattern is used by the workers)

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".bmp")
_DATE_RE = re.compile(r"(?:^|\s)(\d{1,2}/\d{1,2}(?:/\d{2,4})?)\s*$")
_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


@dataclass
class CodeItem:
    code: str
    name: str = ""
    date: str = ""

    def base_name(self, include_date: bool = False) -> str:
        """'2-558 Realme C85' (optionally '2-558 Realme C85 26-9')."""
        parts = [self.code, self.name]
        if include_date and self.date:
            parts.append(self.date.replace("/", "-"))
        return safe_filename(" ".join(p for p in parts if p)) or safe_filename(self.code) or "item"


def parse_code_list(text: str) -> list[CodeItem]:
    """Parse lines like '2-558<TAB>Realme C85<TAB>26/9' (tabs or spaces). A code typed '2:558' or
    '2_558+' is written '2-558'."""
    items = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        code, _, rest = line.replace("\t", " ", 1).partition(" ")
        code = list_code(code)
        if not any(ch.isdigit() for ch in code):
            continue  # header / junk line
        rest = re.sub(r"\s+", " ", rest.replace("\t", " ")).strip()
        date = ""
        m = _DATE_RE.search(rest)
        if m:
            date = m.group(1)
            rest = rest[: m.start()].strip()
        items.append(CodeItem(code=code, name=rest, date=date))
    return items


def code_parts(code: str) -> dict:
    """{'prefix': '4a', 'number': 205} for '4a-205' (the bridge reads a chat further back when its
    codes start after this one); {} for anything that is not a code."""
    try:
        prefix, number = split_code(list_code(code))
    except ValueError:
        return {}
    return {"prefix": prefix, "number": number} if re.fullmatch(r"\d{1,3}[A-Za-z]{0,2}", prefix) else {}


def safe_filename(name: str) -> str:
    name = _BAD_CHARS.sub("-", name)
    name = re.sub(r"\s+", " ", name).strip().rstrip(". ")
    if name.split(".")[0].upper() in _RESERVED:
        name += "_"
    return name[:150].rstrip(". ")


def list_date(text: str, now: datetime | None = None) -> datetime | None:
    """'26/9' -> 26 Sep of this year (last year if that is in the future); '26/9/26' and '26/9/2026' too."""
    m = re.fullmatch(r"\s*(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\s*", text or "")
    if not m:
        return None
    now = now or datetime.now()
    day, month = int(m.group(1)), int(m.group(2))
    year = int(m.group(3)) if m.group(3) else now.year
    if year < 100:
        year += 2000
    try:
        d = datetime(year, month, day)
    except ValueError:
        return None
    if not m.group(3) and d > now + timedelta(days=1):
        d = d.replace(year=year - 1)
    return d


def scan_since(items: list[CodeItem], lookback_days: int, now: datetime | None = None) -> datetime:
    """Oldest message time to read: `lookback_days` ago, extended to a week before the oldest list date."""
    now = now or datetime.now()
    since = now - timedelta(days=lookback_days)
    for it in items:
        d = list_date(it.date, now)
        if d:
            since = min(since, d - timedelta(days=7))
    return since


def ext_for(mimetype: str | None, data: bytes, filename: str | None = None) -> str:
    mt = (mimetype or "").split(";")[0].strip().lower()
    by_mime = {
        "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png", "image/webp": ".webp",
        "image/gif": ".gif", "image/heic": ".heic", "image/heif": ".heic", "image/bmp": ".bmp",
    }
    if mt in by_mime:
        return by_mime[mt]
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[:4] == b"GIF8":
        return ".gif"
    if filename:
        suffix = Path(filename).suffix.lower()
        if suffix in IMAGE_EXTS:
            return suffix
    return ".jpg"


def existing_output(out_dir: Path, base: str) -> Path | None:
    """Return the file (or old-style folder) saved for this item by an earlier run, if any."""
    folder = out_dir / base
    if folder.is_dir() and any(folder.iterdir()):
        return folder
    for name in (base, f"{base} 1"):
        for ext in IMAGE_EXTS:
            f = out_dir / (name + ext)
            if f.is_file():
                return f
    return None


def save_images(out_dir: Path, base: str, images: list[tuple[bytes, str]]) -> Path:
    """One image -> '<base>.jpg'. Several -> '<base> 1.jpg', '<base> 2.jpg', ... (no subfolder).

    Returns the (first) file written.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    if len(images) == 1:
        data, ext = images[0]
        path = out_dir / (base + ext)
        path.write_bytes(data)
        return path
    paths = []
    for i, (data, ext) in enumerate(images, 1):
        path = out_dir / f"{base} {i}{ext}"
        path.write_bytes(data)
        paths.append(path)
    return paths[0]
