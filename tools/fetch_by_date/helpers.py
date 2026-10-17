"""Fetch by Date: file names, the record of saved images (dedup manifest), the CSV log, and reading
the dates WhatsApp shows and writes into its download file names."""
from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_filename(name: str, max_len: int = 120) -> str:
    """Strip characters that are illegal in Windows file/folder names."""
    cleaned = _INVALID_CHARS.sub("_", name).strip(" .")
    if not cleaned:
        cleaned = "unnamed"
    return cleaned[:max_len]


@dataclass
class DownloadRecord:
    message_id: str
    filename: str
    date: str
    time: str
    sender: str
    status: str  # downloaded | skipped_duplicate | failed


class Manifest:
    """Tracks which WhatsApp message ids have already been downloaded for a
    given chat, so re-running the tool on the same date range does not
    re-download the same images."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ids: set[str] = set()
        if self.path.exists():
            try:
                self._ids = set(json.loads(self.path.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                self._ids = set()

    def has(self, message_id: str) -> bool:
        return message_id in self._ids

    def add(self, message_id: str) -> None:
        self._ids.add(message_id)

    def save(self) -> None:
        self.path.write_text(json.dumps(sorted(self._ids)), encoding="utf-8")


class CsvLogger:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        is_new = not self.path.exists()
        self._file = open(self.path, "a", newline="", encoding="utf-8-sig")
        self._writer = csv.writer(self._file)
        if is_new:
            self._writer.writerow(["filename", "date", "time", "sender", "message_id", "status"])
            self._file.flush()

    def write(self, record: DownloadRecord) -> None:
        self._writer.writerow([
            record.filename,
            record.date,
            record.time,
            record.sender,
            record.message_id,
            record.status,
        ])
        self._file.flush()

    def close(self) -> None:
        self._file.close()


def forget_history(manifest_dir: Path) -> int:
    """Forget which images were already saved (every chat), so the next fetch saves them again.
    The image files themselves are kept. Returns how many chats were forgotten."""
    forgotten = 0
    for entry in manifest_dir.glob("*.json") if manifest_dir.exists() else ():
        try:
            entry.unlink()
            forgotten += 1
        except OSError:
            pass
    return forgotten


_VIEWER_WHEN = re.compile(
    r"^\s*(?P<day>.+?)\s+(?:at\s+|,\s*)?(?P<time>\d{1,2}[:.]\d{2}(?:\s*[AP]M)?)\s*$", re.IGNORECASE)
_NUMERIC_DAY = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{2,4})$")
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def parse_viewer_timestamp(text: str, today: date | None = None) -> dict:
    """Parse the media viewer's header time, e.g. ``9/7/2026 at 11:49 PM``.

    Returns candidates keyed "MDY", "DMY" (numeric dates are ambiguous
    between month-first and day-first locales) and "REL" (Today, Yesterday
    or a weekday name, which WhatsApp uses for the past week).
    Minute precision only; values are None when they don't apply.
    """
    result = {"MDY": None, "DMY": None, "REL": None}
    match = _VIEWER_WHEN.match(text or "")
    if not match:
        return result

    clock = None
    time_text = match.group("time").replace(".", ":").upper()
    for fmt in ("%I:%M %p", "%I:%M%p", "%H:%M"):
        try:
            clock = datetime.strptime(time_text, fmt).time()
            break
        except ValueError:
            continue
    if clock is None:
        return result

    day_text = match.group("day").strip().rstrip(",").lower()
    today = today or date.today()
    if day_text == "today":
        result["REL"] = datetime.combine(today, clock)
        return result
    if day_text == "yesterday":
        result["REL"] = datetime.combine(today - timedelta(days=1), clock)
        return result
    if day_text in _WEEKDAYS:
        days_back = (today.weekday() - _WEEKDAYS.index(day_text)) % 7 or 7
        result["REL"] = datetime.combine(today - timedelta(days=days_back), clock)
        return result

    numeric = _NUMERIC_DAY.match(day_text)
    if numeric:
        a, b, year = int(numeric.group(1)), int(numeric.group(2)), int(numeric.group(3))
        if year < 100:
            year += 2000
        for key, (month, day_num) in (("MDY", (a, b)), ("DMY", (b, a))):
            try:
                result[key] = datetime.combine(date(year, month, day_num), clock)
            except ValueError:
                pass
    return result


_WA_MEDIA_NAME = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})\s+at\s+(\d{1,2})\.(\d{2})\.(\d{2})\s*(AM|PM)?", re.IGNORECASE)


def parse_wa_media_filename(name: str) -> datetime | None:
    """Read the message timestamp out of a WhatsApp download filename.

    WhatsApp names downloads after the message they came from, e.g.
    ``WhatsApp Image 2026-09-07 at 11.49.33 PM.jpeg``. That is the most
    reliable date available -- image messages carry no date in the DOM, only
    a time, and the date separators above them are localized.
    """
    match = _WA_MEDIA_NAME.search(name)
    if not match:
        return None
    year, month, day, hour, minute, second, meridiem = match.groups()
    hour = int(hour)
    if meridiem:
        meridiem = meridiem.upper()
        if meridiem == "PM" and hour != 12:
            hour += 12
        elif meridiem == "AM" and hour == 12:
            hour = 0
    try:
        return datetime(int(year), int(month), int(day), hour, int(minute), int(second))
    except ValueError:
        return None
