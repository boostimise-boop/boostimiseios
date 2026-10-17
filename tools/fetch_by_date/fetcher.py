"""Fetch by Date: the WhatsApp Web automation.

Works in the WhatsApp tab of the shared browser window (core/whatsapp.py), logged into the
user's own WhatsApp Web session. It never touches WhatsApp's servers or protocol directly --
every action (opening a chat, clicking an image, clicking the Download button) is the same
action a human would perform in the browser.

This module has no Qt dependency; it reports progress through a plain
``on_event(event_type, payload)`` callback (see worker.py).

NOTE ON FRAGILITY: WhatsApp Web's DOM is not a public API. All selectors
here were verified against the live site (September 2026); see SELECTORS.md
for the full list and how to repair them if WhatsApp changes its markup.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from .helpers import (CsvLogger, DownloadRecord, Manifest, parse_viewer_timestamp,
                      parse_wa_media_filename, sanitize_filename)

EventCallback = Callable[[str, dict], None]

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}

# The walk runs newest -> oldest, so the first item older than the start of
# the range normally ends it. Requiring a few in a row means one oddly-dated
# item can't cut the run short and leave in-range images behind.
_OLDER_TOLERANCE = 3

# A long date range can mean thousands of images, and older ones have to be
# fetched from WhatsApp's servers before they can be saved. Every wait is
# therefore generous and retried; no single slow step may end the run.
_DOWNLOAD_ATTEMPTS = 3
_DOWNLOAD_BUTTON_TIMEOUT_MS = 15_000
_MENU_ITEM_TIMEOUT_MS = 5_000
_DOWNLOAD_START_TIMEOUT_MS = 180_000
_FETCH_TIMEOUT_S = 180
_FETCH_CLICKS = 3
_ADVANCE_WAIT_S = 60
_ADVANCE_ATTEMPTS = 2
# Stepping to newer items only happens to find the starting point, and at the
# newest image a Next click may simply do nothing, so it gives up sooner.
_FORWARD_WAIT_S = 20
_MAX_RECOVERIES = 5
_MOVES_BEFORE_RECOVERY_RESET = 50
_MAX_CONSECUTIVE_ERRORS = 5

# The viewer header shows time to the minute only. With this slack an item is
# only skipped without downloading when it is certainly outside the range.
_SLACK_AFTER = timedelta(minutes=2)
_SLACK_BEFORE = timedelta(minutes=1)

# Downloads land in a boostimise-fbd-* folder in the temp dir under random
# names, and are deleted as soon as each image is handled. A run that was
# killed leaves its folder behind; the next run removes it.
_TEMP_PREFIX = "boostimise-fbd-"
_STALE_TEMP_AGE_S = 3600


def _image_extension(path: Path, suggested: str) -> Optional[str]:
    """Extension to save an image under: the download's own if it is an image
    type, otherwise sniffed from the file's first bytes. Anything the viewer
    shows that isn't a video is an image, so a download name without a proper
    extension must not cost the image. None if it isn't an image after all."""
    ext = Path(suggested).suffix.lower()
    if ext in _IMAGE_EXTS:
        return ext
    try:
        with open(path, "rb") as handle:
            head = handle.read(12)
    except OSError:
        return None
    if head.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if head.startswith(b"\x89PNG"):
        return ".png"
    if head.startswith(b"GIF8"):
        return ".gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    if head.startswith(b"BM"):
        return ".bmp"
    return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# delta > 0 scrolls toward older messages; delta === null jumps to the newest.
_SCROLL_JS = r"""
(delta) => {
  const panel = document.querySelector('[data-testid="conversation-panel-messages"]')
             || document.querySelector('#main');
  if (!panel) return null;
  let el = null;
  for (let node = panel; node; node = node.parentElement) {
    if (node.scrollHeight > node.clientHeight + 50 && node.clientHeight > 200) { el = node; break; }
    if (node.id === 'main') break;
  }
  if (!el) {
    el = Array.from(panel.querySelectorAll('div'))
      .find(d => d.scrollHeight > d.clientHeight + 50 && d.clientHeight > 200);
  }
  if (!el) return null;
  const before = el.scrollTop;
  el.scrollTop = delta === null ? el.scrollHeight : Math.max(0, before - delta);
  el.dispatchEvent(new Event('scroll', {bubbles: true}));
  return {before, after: el.scrollTop, scrollHeight: el.scrollHeight, clientHeight: el.clientHeight};
}
"""

# One cheap, non-waiting read of everything the walk needs to know about the
# item the media viewer is currently showing.
_VIEWER_STATE_JS = r"""
() => {
  const m = document.querySelector('[data-testid="media-viewer-modal"]');
  if (!m) return {open: false};
  const video = m.querySelector('video');
  let src = '';
  if (video) {
    const source = video.querySelector('source');
    src = video.getAttribute('src') || (source ? source.getAttribute('src') : '') || '';
  } else {
    const imgs = m.querySelectorAll('[data-testid="media-canvas-img"], [data-testid="media-image"] img, img[src^="blob:"]');
    for (const img of imgs) { const s = img.getAttribute('src'); if (s) { src = s; break; } }
  }
  if (!src) {
    // Not fetched yet: only a blurred inline preview exists. Fingerprint it
    // so moving between several unfetched images is still detectable.
    const preview = Array.from(m.querySelectorAll('img[src^="data:image"]'))
      .find(i => !i.closest('[data-testid="cell-frame-container"]'));
    if (preview) {
      const s = preview.getAttribute('src');
      src = 'preview:' + s.length + ':' + s.slice(-48);
    }
  }
  const title = m.querySelector('[data-testid="cell-frame-title"]');
  const secondary = m.querySelector('[data-testid="cell-frame-secondary"]');
  const prev = document.querySelector('[aria-label="Previous"]');
  const next = document.querySelector('[aria-label="Next"]');
  return {
    open: true,
    isVideo: !!video,
    src: src,
    sender: title ? (title.innerText || '').trim() : '',
    when: secondary ? (secondary.innerText || '').trim() : '',
    prevAvailable: !!prev && prev.getAttribute('aria-disabled') !== 'true' && !prev.hasAttribute('disabled'),
    nextAvailable: !!next && next.getAttribute('aria-disabled') !== 'true' && !next.hasAttribute('disabled'),
    // Older media is often not on this device yet: the viewer then shows a
    // "tap to download" overlay and no Download button until it is fetched.
    needsFetch: !!m.querySelector('[data-testid="media-state-download"]'),
    fetching: !!m.querySelector('[data-testid="media-state-pending"], [data-testid="loading-spinner"]'),
    canDownload: !!m.querySelector('[data-icon="ic-download"], [aria-label="Download"], [data-icon="download"]'),
    // In group chats the toolbar gains Reply privately / Report, which pushes
    // Download into the overflow menu instead.
    hasMenu: !!m.querySelector('[aria-label="Menu"], [data-icon="menu"], [data-icon="more-refreshed"]')
  };
}
"""


@dataclass
class FetchCounts:
    found: int = 0
    downloaded: int = 0
    skipped: int = 0
    failed: int = 0

    def as_dict(self) -> dict:
        return {"found": self.found, "downloaded": self.downloaded,
                "skipped": self.skipped, "failed": self.failed}


@dataclass
class _Walk:
    """State of one backwards walk through a chat's media."""
    chat_name: str
    to_dt: Optional[datetime] = None
    older_streak: int = 0
    # Minute of the item most recently reached; used to resume after the
    # viewer closes and to report where the walk stopped.
    last_dt: Optional[datetime] = None
    # (hash, timestamp) of items seen in this run, so items revisited after a
    # resume are ignored instead of being counted twice.
    run_hashes: set = field(default_factory=set)
    # How to read the viewer header's numeric date: learned by comparing it
    # with the first downloaded file's exact timestamp. None = not yet known,
    # "BAD" = header disagreed with a file, so it is never trusted again.
    viewer_order: Optional[str] = None
    # Items stepped over between the chat's newest image and the 'To' time,
    # and whether an image inside the range has been reached yet.
    newer_passed: int = 0
    in_range: bool = False
    # Items downloaded only to read their exact date, then not kept.
    checked_outside: int = 0
    header_warned: bool = False


class WhatsAppFetcher:
    def __init__(self, browser, page, manifest_dir: Path,
                 on_event: Optional[EventCallback] = None):
        """browser, page: the shared window (Playwright's connect_over_cdp) and its WhatsApp tab."""
        self.browser = browser
        self.page = page
        self.manifest_dir = manifest_dir
        self.download_root = Path()  # set per fetch
        self.on_event = on_event or (lambda event, payload: None)
        # Safety net for any Playwright call without an explicit timeout.
        self.page.set_default_timeout(60_000)

        # Downloads not yet deleted, and the folder they land in.
        self._temp_downloads: list[Path] = []
        self._temp_dir = Path(tempfile.mkdtemp(prefix=_TEMP_PREFIX))
        # Every tool that connects to the shared window makes the browser save
        # downloads into its own folder, so this one claims the downloads back
        # before each one. The setting only lasts while its session is open.
        self._download_session = None

    def _emit(self, event: str, payload: Optional[dict] = None) -> None:
        self.on_event(event, payload or {})

    def _trace(self, text: str) -> None:
        """Verbose step logging, off unless WA_FETCH_TRACE is set."""
        if os.environ.get("WA_FETCH_TRACE"):
            self._emit("log", {"text": f"[trace] {text}"})

    # ------------------------------------------------------------------ #
    # Session lifecycle
    # ------------------------------------------------------------------ #
    def is_logged_in(self) -> bool:
        try:
            return self.page.locator("#pane-side").is_visible()
        except Exception:
            return False

    def shows_qr(self) -> bool:
        try:
            return self.page.locator("canvas[aria-label], [data-ref] canvas").count() > 0
        except Exception:
            return False

    def shutdown(self) -> None:
        """Let go of the shared window without closing it: give downloads back
        to the browser (the user's own downloads in that window go to their
        Downloads folder again) and delete the temporary copies."""
        try:
            if self._download_session is not None:
                self._download_session.send("Browser.setDownloadBehavior", {"behavior": "default"})
                self._download_session.detach()
        except Exception:
            pass
        self._download_session = None
        shutil.rmtree(self._temp_dir, ignore_errors=True)

    def clear_temp(self) -> int:
        """Delete temporary download copies this run still holds and the folders
        left by runs that were killed. Returns how many files were removed."""
        temp_files = 0
        for path in self._temp_downloads:
            if path.exists():
                self._discard(path)
                temp_files += 1
        self._temp_downloads.clear()
        cutoff = time.time() - _STALE_TEMP_AGE_S
        for folder in Path(tempfile.gettempdir()).glob(_TEMP_PREFIX + "*"):
            try:
                if folder == self._temp_dir or folder.stat().st_mtime > cutoff:
                    continue
                temp_files += sum(1 for entry in folder.iterdir() if entry.is_file())
                shutil.rmtree(folder, ignore_errors=True)
            except OSError:
                continue
        return temp_files

    def _claim_downloads(self) -> None:
        if self._download_session is None:
            self._download_session = self.browser.new_browser_cdp_session()
        self._temp_dir.mkdir(parents=True, exist_ok=True)
        self._download_session.send("Browser.setDownloadBehavior", {
            "behavior": "allowAndName", "downloadPath": str(self._temp_dir), "eventsEnabled": True})

    # ------------------------------------------------------------------ #
    # Chat selection
    # ------------------------------------------------------------------ #
    def _park_mouse(self) -> None:
        """WhatsApp's left nav rail shows a tooltip when the pointer rests at
        (0, 0) -- Playwright's default -- and that tooltip swallows clicks on
        the search box. Park the pointer over the middle of the page first."""
        try:
            self.page.mouse.move(640, 450)
            self.page.wait_for_timeout(250)
        except Exception:
            pass

    _SEARCH_BOX_SELECTORS = (
        "input[data-tab='3']",
        "[data-testid='chat-list-search-container'] input",
        "div[contenteditable='true'][data-tab='3']",
    )
    _ROW_SELECTORS = (
        "[data-testid='cell-frame-container']",
        "div[role='listitem']",
        "div[role='row']",
    )

    def _find_search_box(self):
        for selector in self._SEARCH_BOX_SELECTORS:
            candidate = self.page.locator(selector).first
            try:
                if candidate.count() > 0 and candidate.is_visible():
                    return candidate
            except Exception:
                continue
        return None

    def _find_rows(self):
        for selector in self._ROW_SELECTORS:
            candidate = self.page.locator(f"#pane-side {selector}")
            try:
                if candidate.count() > 0:
                    return candidate
            except Exception:
                continue
        return None

    def list_chats(self, scroll_rounds: int = 6) -> list[str]:
        page = self.page
        pane = page.locator("#pane-side")
        pane.wait_for(state="visible", timeout=15000)
        titles: list[str] = []
        seen: set[str] = set()
        box = pane.bounding_box()
        for _ in range(scroll_rounds):
            # One snapshot per round: the sidebar re-renders while it scrolls,
            # so reading row by row could wait on a row that no longer exists.
            for title in page.locator("#pane-side span[title]").evaluate_all(
                    "els => els.map(e => e.getAttribute('title'))"):
                if title and title not in seen:
                    seen.add(title)
                    titles.append(title)
            if box:
                page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                page.mouse.wheel(0, 1500)
                page.wait_for_timeout(350)
        return titles

    def open_chat(self, chat_name: str) -> bool:
        page = self.page
        try:
            self._park_mouse()
            search_box = self._find_search_box()
            if search_box is None:
                self._emit("log", {"text": "Could not locate the WhatsApp Web search box "
                                            "(its DOM may have changed - see SELECTORS.md)."})
                return False

            search_box.click(timeout=10_000, force=True)
            page.keyboard.press("Control+A")
            page.keyboard.press("Delete")
            page.keyboard.type(chat_name, delay=25)
            page.wait_for_timeout(1500)

            results = self._find_rows()
            if results is None:
                self._emit("log", {"text": f"No search results appeared for '{chat_name}'."})
                return False
            try:
                results.first.wait_for(state="visible", timeout=15_000)
            except PlaywrightTimeoutError:
                self._emit("log", {"text": f"No search results appeared for '{chat_name}'."})
                return False

            target = None
            for el in page.locator("#pane-side span[title]").all():
                if el.get_attribute("title") == chat_name:
                    target = el
                    break

            if target is not None:
                row = target.locator(
                    "xpath=ancestor::*[@data-testid='cell-frame-container' "
                    "or @role='listitem' or @role='row'][1]").first
                (row if row.count() > 0 else target).click(timeout=10_000, force=True)
            else:
                self._emit("log", {"text": f"No exact title match for '{chat_name}'; "
                                            f"opening the top search result instead."})
                results.first.click(timeout=10_000, force=True)

            # NOTE: do NOT press Escape here. In current WhatsApp Web, Escape
            # closes the conversation that was just opened, which used to make
            # every fetch report "no message rows found".
            page.locator("#main").wait_for(state="visible", timeout=30_000)
            page.wait_for_timeout(1500)
            return True
        except PlaywrightTimeoutError as exc:
            self._emit("log", {"text": f"open_chat timed out: {str(exc).splitlines()[0]}"})
            return False

    # ------------------------------------------------------------------ #
    # Fetching
    # ------------------------------------------------------------------ #
    def fetch_images(self, chat_name: str, from_dt: datetime, to_dt: datetime,
                      download_root: Path, stop_event: threading.Event) -> None:
        """Walk the chat's media backwards (newest -> oldest) via WhatsApp's
        own image viewer, saving every image inside [from_dt, to_dt] into
        download_root/<chat>/<date>/.

        The message list cannot be used to enumerate images: WhatsApp
        virtualizes off-screen rows, stripping their thumbnails. The viewer's
        Previous button walks the chat's entire media collection regardless
        of what the list has loaded.
        """
        self.download_root = download_root
        self._emit("log", {"text": "Working in the WhatsApp window - please don't click inside "
                                    "it until the fetch finishes."})
        self._emit("status", {"text": f"Opening chat '{chat_name}'..."})
        if not self.open_chat(chat_name):
            self._emit("finished", {"success": False,
                                     "message": f"Could not find a chat named '{chat_name}'."})
            return

        safe_chat = sanitize_filename(chat_name)
        chat_dir_root = self.download_root / safe_chat
        manifest = Manifest(self.manifest_dir / f"{safe_chat}.json")
        csv_logger = CsvLogger(chat_dir_root / "download_log.csv")
        counts = FetchCounts()
        walk = _Walk(chat_name=chat_name, to_dt=to_dt)

        self._emit("status", {"text": "Looking for the most recent image..."})
        # The chat may be left scrolled up, or open at its unread messages;
        # start from the bottom so the newest image is the one opened.
        self._scroll_to_newest()
        if not self._open_newest_image(stop_event):
            csv_logger.close()
            self._emit("finished", {"success": False,
                                     "message": "Found no images in this chat to start from."})
            return
        self._seek_forward(walk, to_dt, stop_event)

        stop_reason = ""
        recoveries = 0
        moves_since_recovery = 0
        consecutive_errors = 0
        try:
            while not stop_event.is_set():
                try:
                    outcome = self._process_current_item(walk, counts, from_dt, to_dt, manifest,
                                                          csv_logger, chat_dir_root, stop_event)
                    consecutive_errors = 0
                except Exception as exc:  # noqa: BLE001 - keep a long run alive
                    consecutive_errors += 1
                    self._emit("log", {"text": f"Unexpected error on this image "
                                                f"({consecutive_errors}/{_MAX_CONSECUTIVE_ERRORS}): "
                                                f"{str(exc).splitlines()[0] if str(exc) else exc!r}"})
                    if consecutive_errors >= _MAX_CONSECUTIVE_ERRORS:
                        stop_reason = "Too many errors in a row; stopping."
                        break
                    outcome = "next"
                self._emit("counts", counts.as_dict())

                if outcome == "stop":
                    stop_reason = (f"Reached {walk.last_dt:%Y-%m-%d %H:%M}, before the start "
                                   f"of the range.")
                    break

                step = self._advance(stop_event)
                if step == "moved":
                    moves_since_recovery += 1
                    if moves_since_recovery >= _MOVES_BEFORE_RECOVERY_RESET:
                        recoveries = 0
                    continue
                if step == "stopped":
                    break
                if step == "end":
                    stop_reason = "Reached the oldest image in this chat."
                    break

                if self._browser_gone():
                    stop_reason = ("The WhatsApp window was closed, so the fetch stopped. Run "
                                   "it again to continue - images already saved are skipped.")
                    break

                # "closed" or "stuck": reopen the viewer and resume in place.
                if recoveries >= _MAX_RECOVERIES:
                    stop_reason = ("The image viewer kept failing; stopping. Run the fetch "
                                   "again to continue - images already saved are skipped.")
                    break
                recoveries += 1
                moves_since_recovery = 0
                what = "closed unexpectedly" if step == "closed" else "stopped responding"
                self._emit("log", {"text": f"The image viewer {what}; reopening and resuming "
                                            f"(attempt {recoveries}/{_MAX_RECOVERIES})..."})
                if not self._resume_walk(stop_event, walk):
                    stop_reason = ("Could not resume the image walk. Run the fetch again to "
                                   "continue - images already saved are skipped.")
                    break
        finally:
            csv_logger.close()
            self._close_viewer()

        if stop_reason:
            self._emit("log", {"text": stop_reason})
        if walk.checked_outside:
            self._emit("log", {"text": f"{walk.checked_outside} image(s) were downloaded only to "
                                        f"read their exact date and were outside your range, so "
                                        f"they were not saved (the browser's download list may "
                                        f"still show them)."})
        self._emit("log", {"text": f"Images are saved in: {chat_dir_root}"})
        verb = "Stopped" if stop_event.is_set() else "Done"
        message = (f"{verb}. Found {counts.found}, downloaded {counts.downloaded}, "
                   f"skipped {counts.skipped}, failed {counts.failed}.")
        self._emit("finished", {"success": True, "message": message})

    def _process_current_item(self, walk: _Walk, counts: FetchCounts, from_dt: datetime,
                               to_dt: datetime, manifest: Manifest, csv_logger: CsvLogger,
                               chat_dir_root: Path, stop_event: threading.Event) -> str:
        """Handle the item the viewer shows. Returns "next" or "stop"."""
        state = self._viewer_state()
        if not state.get("open"):
            return "next"  # _advance reports the closed viewer and triggers recovery

        when = state.get("when", "")
        sender = state.get("sender", "")
        bounds = self._header_bounds(walk, when)
        if bounds is not None:
            walk.last_dt = bounds[1]
        elif when and walk.viewer_order != "BAD" and not walk.header_warned:
            walk.header_warned = True
            self._emit("log", {"text": f"Couldn't read the date shown in the image viewer "
                                        f"('{when}'), so images are downloaded to check their "
                                        f"date (slower, still exact)."})

        # Pre-filter on the header: items certainly outside the range (and all
        # videos) are stepped over without downloading anything. This is what
        # carries the walk from the chat's newest image back to the 'To' time.
        certainly_older = bounds is not None and bounds[1] + _SLACK_AFTER <= from_dt
        certainly_newer = bounds is not None and bounds[0] - _SLACK_BEFORE > to_dt
        if certainly_older:
            return self._note_older(walk)
        if certainly_newer:
            self._note_newer(walk, when, to_dt)
            return "next"
        if state.get("isVideo"):
            return "next"
        self._emit("status", {"text": f"Checking {when or 'image'}... "
                                       f"({counts.downloaded} saved)"})

        result = self._download_current_media(stop_event)
        if result is None:
            if stop_event.is_set():
                return "next"  # interrupted by Stop, not a failure
            counts.failed += 1
            exact = bounds[0] if bounds is not None and bounds[0] == bounds[1] else None
            csv_logger.write(DownloadRecord(
                "", "", exact.date().isoformat() if exact else "",
                exact.strftime("%H:%M") if exact else when, sender, "failed"))
            return "next"

        temp_path, suggested, media_dt = result
        if media_dt is not None:
            self._calibrate_header(walk, when, media_dt)
            walk.last_dt = media_dt.replace(second=0, microsecond=0)

        digest = _sha256(temp_path)
        # A message is its content *and* its time: the same picture sent again
        # later is another message, and may be the only copy inside the range.
        message_key = (digest, media_dt)
        if message_key in walk.run_hashes:
            self._discard(temp_path)  # revisited after a resume; already handled
            return "next"
        walk.run_hashes.add(message_key)

        if media_dt is None:
            self._discard(temp_path)
            self._emit("log", {"text": f"Skipped '{suggested}' - no readable date in its "
                                        f"filename, so it can't be matched against the range."})
            return "next"

        if media_dt < from_dt:
            self._discard(temp_path)
            walk.checked_outside += 1
            return self._note_older(walk)
        walk.older_streak = 0

        if media_dt > to_dt:
            self._discard(temp_path)
            walk.checked_outside += 1
            walk.newer_passed += 1
            self._emit("log", {"text": f"Checked an image from {media_dt:%d/%m/%Y %H:%M:%S}: "
                                        f"after your 'To' time, so it was not saved."})
            return "next"

        ext = _image_extension(temp_path, suggested)
        if ext is None:
            self._discard(temp_path)
            self._emit("log", {"text": f"Skipped '{suggested}' - it isn't a recognised image "
                                        f"file."})
            return "next"

        counts.found += 1
        if not walk.in_range:
            walk.in_range = True
            if walk.newer_passed:
                self._emit("log", {"text": f"Reached your 'To' time after passing "
                                            f"{walk.newer_passed} newer item(s); saving images "
                                            f"from here back to {from_dt:%d/%m/%Y %H:%M:%S}."})
        day, clock = media_dt.date().isoformat(), media_dt.strftime("%H:%M:%S")
        dest = self._media_destination(chat_dir_root, media_dt, digest, ext)
        # The manifest alone isn't proof: files deleted or moved out of the
        # folder since an earlier run must be saved again, not skipped.
        if manifest.has(digest) and dest.exists():
            counts.skipped += 1
            self._discard(temp_path)
            csv_logger.write(DownloadRecord(digest[:16], dest.name, day, clock, sender,
                                             "skipped_duplicate"))
            self._emit("log", {"text": f"Already in the folder: {dest.name}"})
            return "next"
        # The manifest holds contents, not messages: if a copy of this picture
        # is still in the folder, this is the same picture sent another time.
        if manifest.has(digest) and not any(chat_dir_root.glob(f"*/*_{digest[:8]}.*")):
            self._emit("log", {"text": f"{dest.name} was saved by an earlier run but is no "
                                        f"longer in the folder; saving it again."})

        self._save_media(temp_path, dest)
        self._discard(temp_path)
        counts.downloaded += 1
        manifest.add(digest)
        manifest.save()
        csv_logger.write(DownloadRecord(digest[:16], dest.name, day, clock, sender, "downloaded"))
        self._emit("log", {"text": f"Downloaded {dest.name}"})
        return "next"

    @staticmethod
    def _note_older(walk: _Walk) -> str:
        walk.older_streak += 1
        return "stop" if walk.older_streak >= _OLDER_TOLERANCE else "next"

    def _note_newer(self, walk: _Walk, when: str, to_dt: datetime) -> None:
        walk.newer_passed += 1
        if walk.newer_passed == 1:
            self._emit("log", {"text": f"Skipping images newer than your 'To' time "
                                        f"({to_dt:%d/%m/%Y %H:%M:%S}) without downloading "
                                        f"them..."})
        self._emit("status", {"text": f"Skipping to your 'To' time... now at {when} "
                                       f"({walk.newer_passed} newer item(s) passed)"})

    # -- viewer header timestamps --------------------------------------- #
    @staticmethod
    def _header_bounds(walk: _Walk, when: str) -> Optional[tuple[datetime, datetime]]:
        """Earliest and latest moment the viewer header can mean, read without
        downloading anything; None if it can't be read or isn't trusted.

        Today/Yesterday/weekday headers are exact. A numeric date is ambiguous
        (9/7 is 7 Sep or 9 Jul) until its order is confirmed against a real
        file, so until then both readings are kept -- minus any that lie in
        the future -- and an item is only skipped when every reading puts it
        outside the range. That lets the walk pass everything newer than the
        'To' time without downloading it first."""
        if walk.viewer_order == "BAD":
            return None
        candidates = parse_viewer_timestamp(when)
        if candidates["REL"] is not None:
            return candidates["REL"], candidates["REL"]
        if walk.viewer_order in ("MDY", "DMY"):
            exact = candidates[walk.viewer_order]
            return (exact, exact) if exact is not None else None
        latest_possible = datetime.now() + timedelta(days=1)
        readings = [dt for dt in (candidates["MDY"], candidates["DMY"])
                    if dt is not None and dt <= latest_possible]
        if not readings:
            return None
        return min(readings), max(readings)

    def _calibrate_header(self, walk: _Walk, when: str, media_dt: datetime) -> None:
        """Check the header against a downloaded file's exact timestamp,
        learning its date order on the way. After any disagreement the header
        is never trusted again, since skipping relies on it."""
        if walk.viewer_order == "BAD":
            return
        candidates = parse_viewer_timestamp(when)
        target = media_dt.replace(second=0, microsecond=0)

        def matches(candidate: Optional[datetime]) -> bool:
            return candidate is not None and abs(candidate - target) <= timedelta(minutes=1)

        if candidates["REL"] is not None:
            agrees = matches(candidates["REL"])
        elif walk.viewer_order in ("MDY", "DMY"):
            trusted = candidates[walk.viewer_order]
            agrees = trusted is None or matches(trusted)
        else:
            month_first, day_first = matches(candidates["MDY"]), matches(candidates["DMY"])
            if month_first and not day_first:
                walk.viewer_order = "MDY"
            elif day_first and not month_first:
                walk.viewer_order = "DMY"
            agrees = (month_first or day_first
                      or (candidates["MDY"] is None and candidates["DMY"] is None))
        if not agrees:
            walk.viewer_order = "BAD"
            self._emit("log", {"text": "The viewer's displayed time disagreed with an "
                                        "image's real timestamp; checking every image "
                                        "individually from now on (slower, still exact)."})

    # -- viewer-driven media walk --------------------------------------- #
    def _browser_gone(self) -> bool:
        try:
            return self.page is None or self.page.is_closed()
        except Exception:
            return True

    def _viewer_state(self) -> dict:
        try:
            return self.page.evaluate(_VIEWER_STATE_JS) or {"open": False}
        except Exception:
            return {"open": False}

    def _open_newest_image(self, stop_event: threading.Event, max_scrolls: int = 60) -> bool:
        """Open the most recent image of the open chat in the media viewer,
        scrolling back through history until a thumbnail turns up."""
        page = self.page
        thumbs = page.locator("#main [data-testid='image-thumb']")
        for _ in range(max_scrolls):
            if stop_event.is_set():
                return False
            try:
                if thumbs.count() > 0:
                    newest = thumbs.last
                    newest.scroll_into_view_if_needed(timeout=10_000)
                    page.wait_for_timeout(400)
                    newest.click(timeout=10_000, force=True)
                    page.locator("[data-testid='media-viewer-modal']").first.wait_for(
                        state="visible", timeout=30_000)
                    page.wait_for_timeout(1200)
                    return True
            except Exception:
                # The message list re-renders constantly, so a thumbnail can
                # detach between locating and clicking it. Scroll and retry.
                page.wait_for_timeout(500)
            scroll = self._scroll(3000)
            page.wait_for_timeout(1200)
            if scroll.get("before") == 0 and scroll.get("after") == 0:
                break
        return False

    def _advance(self, stop_event: threading.Event, forward: bool = False) -> str:
        """Step the viewer to the next-older item (next-newer with forward)
        and wait until it has really changed. Returns "moved", "end",
        "closed", "stuck" or "stopped".

        Waiting for the change (instead of sleeping a fixed time) matters on
        slow loads: acting on a viewer that hasn't moved yet would download
        the same image twice or skip one."""
        page = self.page
        button, available = ("Next", "nextAvailable") if forward else ("Previous", "prevAvailable")
        wait_s = _FORWARD_WAIT_S if forward else _ADVANCE_WAIT_S
        try:
            before = self._viewer_state()
            if not before.get("open"):
                return "closed"
            if not before.get(available):
                return "end"
            before_src = before.get("src")
            before_header = (before.get("sender"), before.get("when"))

            for attempt in range(1, _ADVANCE_ATTEMPTS + 1):
                try:
                    page.locator(f"[aria-label='{button}']").first.click(timeout=10_000, force=True)
                except Exception:
                    pass
                deadline = time.monotonic() + wait_s
                header_changed_at = None
                while time.monotonic() < deadline:
                    if stop_event.is_set():
                        return "stopped"
                    page.wait_for_timeout(200)
                    now = self._viewer_state()
                    if not now.get("open"):
                        return "closed"
                    src = now.get("src")
                    if src and src != before_src:
                        # The picture can switch a moment before the header,
                        # and a stale header would misdate this item -- at the
                        # 'To' boundary, enough to skip it. Items from the same
                        # minute (albums) keep the header, so the wait is short.
                        settle_until = time.monotonic() + 0.6
                        while (time.monotonic() < settle_until
                               and (now.get("sender"), now.get("when")) == before_header):
                            page.wait_for_timeout(150)
                            now = self._viewer_state()
                        page.wait_for_timeout(200)
                        return "moved"
                    if (now.get("sender"), now.get("when")) != before_header:
                        # Header moved on but the picture didn't: either the
                        # same media was sent twice in a row, or it is still
                        # loading. Give it a moment, then accept the move.
                        header_changed_at = header_changed_at or time.monotonic()
                        if time.monotonic() - header_changed_at >= 5:
                            return "moved"
                if attempt < _ADVANCE_ATTEMPTS:
                    self._emit("log", {"text": f"The viewer hadn't moved to the {button.lower()} "
                                                f"image after {wait_s}s; clicking again..."})
            return "stuck"
        except Exception:
            return "closed"

    def _seek_forward(self, walk: _Walk, target: Optional[datetime],
                      stop_event: threading.Event) -> None:
        """Step the viewer to newer items until one is certainly after
        ``target`` or the chat's newest image is reached.

        The viewer opens on whichever thumbnail happens to be rendered, which
        is not always the newest image -- e.g. when the chat was left scrolled
        up. Walking backwards from there would silently miss everything newer,
        including images inside the range."""
        stepped = 0
        while not stop_event.is_set():
            state = self._viewer_state()
            if not state.get("open") or not state.get("nextAvailable"):
                break
            when = state.get("when", "")
            bounds = self._header_bounds(walk, when)
            if target is not None and bounds is not None and bounds[0] - _SLACK_BEFORE > target:
                break
            if stepped == 0:
                self._emit("log", {"text": f"The viewer opened at {when or 'an older image'}, "
                                            f"not the newest image; stepping forward to newer "
                                            f"images first..."})
            step = self._advance(stop_event, forward=True)
            if step == "stuck":
                self._emit("log", {"text": f"Couldn't step past {when or 'this image'} to newer "
                                            f"images; images after it may be missed."})
            if step != "moved":
                break
            stepped += 1
            self._emit("status", {"text": f"Stepping forward to newer images... now at "
                                           f"{self._viewer_state().get('when', '')}"})
        if stepped:
            self._emit("log", {"text": f"Moved forward {stepped} image(s); walking back from "
                                        f"there."})

    def _resume_walk(self, stop_event: threading.Event, walk: _Walk) -> bool:
        """Reopen the viewer at the newest image and fast-forward back to
        where the walk was, using the header time (no downloads)."""
        page = self.page
        try:
            self._close_viewer()
            if page.locator("#main").count() == 0 and not self.open_chat(walk.chat_name):
                return False
            self._scroll_to_newest()
            if not self._open_newest_image(stop_event):
                return False
        except Exception:
            return False
        self._seek_forward(walk, walk.last_dt or walk.to_dt, stop_event)

        if walk.last_dt is None:
            return True

        first = self._header_bounds(walk, self._viewer_state().get("when", ""))
        if first is None:
            self._emit("log", {"text": "Can't fast-forward: the viewer's displayed times "
                                        "couldn't be read."})
            return False
        if first[1] < walk.last_dt:
            # Reopened somewhere older than we already were; resuming from
            # here could silently skip images, so refuse.
            return False

        self._emit("log", {"text": f"Fast-forwarding back to {walk.last_dt:%Y-%m-%d %H:%M}..."})
        while not stop_event.is_set():
            # Stop at the first item that might be at or before where the walk
            # was: re-checking a few is harmless, overshooting would skip some.
            bounds = self._header_bounds(walk, self._viewer_state().get("when", ""))
            if bounds is None or bounds[0] <= walk.last_dt:
                return True
            if self._advance(stop_event) != "moved":
                return False
        return False

    def _ensure_media_loaded(self, stop_event: threading.Event) -> bool:
        """Make sure the viewer's current item is fully on this device, so its
        Download button exists.

        Older images usually are not: the viewer shows a blurred preview with
        a "tap to download" overlay and *no* Download button. Clicking the
        overlay makes WhatsApp fetch it; while that runs the overlay turns into
        a spinner, and clicking again then would cancel it -- so the overlay
        is only re-clicked if WhatsApp has fallen back to the initial state
        (i.e. the fetch failed)."""
        page = self.page
        overlay = "[data-testid='media-viewer-modal'] [data-testid='media-state-download']"
        deadline = time.monotonic() + _FETCH_TIMEOUT_S
        clicks, last_click, announced = 0, 0.0, False
        while time.monotonic() < deadline:
            if stop_event.is_set():
                return False
            state = self._viewer_state()
            if not state.get("open"):
                return False
            if (state.get("canDownload") or state.get("hasMenu")) and not state.get("needsFetch"):
                return True
            if state.get("needsFetch") and not state.get("fetching"):
                if clicks >= _FETCH_CLICKS:
                    self._emit("log", {"text": f"WhatsApp couldn't fetch the image from "
                                                f"{state.get('when') or 'this message'} after "
                                                f"{_FETCH_CLICKS} tries - it may no longer be "
                                                f"available on the phone."})
                    return False
                if time.monotonic() - last_click >= 10:
                    if not announced:
                        self._emit("status", {"text": f"Fetching image from "
                                                       f"{state.get('when') or 'WhatsApp'}..."})
                        announced = True
                    try:
                        page.locator(overlay).first.click(timeout=10_000, force=True)
                    except Exception:
                        pass
                    clicks += 1
                    last_click = time.monotonic()
            page.wait_for_timeout(500)
        self._emit("log", {"text": f"Timed out after {_FETCH_TIMEOUT_S}s waiting for WhatsApp "
                                    f"to fetch an image."})
        return False

    def _download_current_media(self, stop_event: threading.Event):
        """Download whatever the viewer is currently showing, retrying slow
        or failed attempts. Returns (temp_path, suggested_filename,
        datetime|None) or None."""
        page = self.page
        last_error = ""
        for attempt in range(1, _DOWNLOAD_ATTEMPTS + 1):
            if not self._ensure_media_loaded(stop_event):
                return None
            try:
                self._trace(f"download attempt {attempt}: waiting for the download event")
                self._claim_downloads()
                before = set(self._temp_dir.iterdir())
                with page.expect_download(timeout=_DOWNLOAD_START_TIMEOUT_MS) as download_info:
                    self._trigger_download()
                download = download_info.value
                self._trace(f"download event received: {download.suggested_filename}")
                failure = download.failure()  # waits until the download has finished
                if failure:
                    raise RuntimeError(f"the download failed ({failure})")
                arrived = sorted(set(self._temp_dir.iterdir()) - before, key=lambda p: p.stat().st_mtime)
                if not arrived:
                    # Another tool connected to the WhatsApp window just then and
                    # took the downloads over; the retry claims them back.
                    raise RuntimeError("the download was saved somewhere else")
                temp_path = arrived[-1]
                self._temp_downloads.append(temp_path)
                suggested = download.suggested_filename or "whatsapp-media"
                self._dismiss_menu()
                return temp_path, suggested, parse_wa_media_filename(suggested)
            except Exception as exc:  # noqa: BLE001 - retried, then reported
                last_error = str(exc).splitlines()[0] if str(exc) else repr(exc)
                self._dismiss_menu()
                if not self._viewer_state().get("open"):
                    break
                if attempt < _DOWNLOAD_ATTEMPTS:
                    self._emit("log", {"text": f"Download attempt {attempt} failed "
                                                f"({last_error}); retrying..."})
                    page.wait_for_timeout(3000 * attempt)
        self._emit("log", {"text": f"Could not download an image after {_DOWNLOAD_ATTEMPTS} "
                                    f"attempts: {last_error}"})
        return None

    @staticmethod
    def _media_destination(chat_dir_root: Path, media_dt: datetime, digest: str,
                            ext: str) -> Path:
        """Where a kept download goes: Downloads/WhatsApp Images/<chat>/<date>/.

        WhatsApp reuses filenames for images sent in the same second, so the
        content hash is appended to keep every file distinct.
        """
        day = media_dt.date().isoformat()
        stamp = media_dt.strftime("%H%M%S")
        return chat_dir_root / day / sanitize_filename(f"{day}_{stamp}_{digest[:8]}{ext}")

    @staticmethod
    def _save_media(temp_path: Path, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(temp_path, dest)

    def _discard(self, temp_path: Path) -> None:
        try:
            temp_path.unlink(missing_ok=True)
        except Exception:
            pass

    def _scroll(self, delta: Optional[int]) -> dict:
        """Scroll the message list from JavaScript (mouse-wheel events stop
        reaching it after the media viewer closes). Positive delta scrolls
        toward older messages; None jumps to the newest."""
        try:
            return self.page.evaluate(_SCROLL_JS, delta) or {}
        except Exception:
            return {}

    def _scroll_to_newest(self, max_rounds: int = 40) -> None:
        last_height = -1
        for _ in range(max_rounds):
            result = self._scroll(None)
            self.page.wait_for_timeout(500)
            height = result.get("scrollHeight", 0)
            at_bottom = result.get("after", 0) + result.get("clientHeight", 0) >= height - 5
            if at_bottom and height == last_height:
                return
            last_height = height

    _DOWNLOAD_BTN_SELECTOR = ("[data-testid='media-viewer-modal'] [data-icon='ic-download'], "
                               "[data-testid='media-viewer-modal'] [aria-label='Download'], "
                               "[data-testid='media-viewer-modal'] [data-icon='download']")
    _MENU_BTN_SELECTOR = ("[data-testid='media-viewer-modal'] [aria-label='Menu'], "
                           "[data-testid='media-viewer-modal'] [data-icon='menu'], "
                           "[data-testid='media-viewer-modal'] [data-icon='more-refreshed']")
    _MENU_DOWNLOAD_SELECTOR = ("[role='menu'] [data-icon='ic-download'], "
                                "[role='menu'] [aria-label='Download'], "
                                "[role='menuitem'][aria-label='Download']")

    def _trigger_download(self) -> None:
        """Click the viewer's Download control.

        In one-to-one chats it sits in the toolbar. In group chats the toolbar
        also carries Reply privately / Report, which pushes Download into the
        overflow ("three dots") menu, so it has to be opened first."""
        page = self.page
        toolbar_button = page.locator(self._DOWNLOAD_BTN_SELECTOR).first
        if toolbar_button.count() > 0:
            toolbar_button.click(timeout=_DOWNLOAD_BUTTON_TIMEOUT_MS, force=True)
            return

        item = page.locator(self._MENU_DOWNLOAD_SELECTOR).first
        self._trace(f"menu path: download items already present = {item.count()}")
        if item.count() == 0:
            menu_button = page.locator(self._MENU_BTN_SELECTOR).first
            if menu_button.count() == 0:
                raise RuntimeError("the viewer has neither a Download button nor a menu")
            menu_button.click(timeout=_DOWNLOAD_BUTTON_TIMEOUT_MS, force=True)
            item = page.locator(self._MENU_DOWNLOAD_SELECTOR).first
            try:
                item.wait_for(state="visible", timeout=_MENU_ITEM_TIMEOUT_MS)
            except PlaywrightTimeoutError:
                # The menu was probably already open (someone clicked in the
                # browser window), so that click closed it. Open it again.
                self._trace("menu path: no Download item; reopening the menu")
                menu_button.click(timeout=_DOWNLOAD_BUTTON_TIMEOUT_MS, force=True)
                item = page.locator(self._MENU_DOWNLOAD_SELECTOR).first
                item.wait_for(state="visible", timeout=_MENU_ITEM_TIMEOUT_MS)
        self._trace(f"menu path: clicking Download (matches={item.count()})")
        item.click(timeout=_DOWNLOAD_BUTTON_TIMEOUT_MS, force=True)
        self._trace("menu path: click returned")

    def _dismiss_menu(self) -> None:
        """Close the overflow menu if it is open, so Escape later reaches the
        viewer rather than the menu."""
        try:
            if self.page.locator("[role='menu']").count() > 0:
                self.page.keyboard.press("Escape")
                self.page.wait_for_timeout(300)
        except Exception:
            pass

    def _close_viewer(self) -> None:
        """Escape closes the media viewer (and only the viewer -- it does not
        close the conversation while a modal is up)."""
        self._dismiss_menu()
        try:
            if self.page.locator("[data-testid='media-viewer-modal']").count() > 0:
                self.page.keyboard.press("Escape")
                self.page.wait_for_timeout(400)
        except Exception:
            pass
