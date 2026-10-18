"""Background thread that reads WhatsApp Web in the shared browser window (Playwright)."""
from __future__ import annotations

import base64
import hashlib
import queue
import threading
import time
import traceback
from collections import Counter
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from core import whatsapp

from .helpers import CodeItem, code_pattern, existing_output, ext_for, save_images, scan_since

HERE = Path(__file__).resolve().parent
# wa_bridge.js: photos of each code (Fetch by Code). wa_bridge_reactions.js: the same, and reads
# reactions (the Reaction List tool uses it).
BRIDGES = {"basic": HERE / "wa_bridge.js", "reactions": HERE / "wa_bridge_reactions.js"}


class WhatsAppWorker(QThread):
    log = Signal(str)
    state_changed = Signal(str, str)  # state, message: starting | loading | qr | ready | error | closed
    chats_loaded = Signal(list)  # [{id, title}]
    row_status = Signal(int, str, str, str)  # row, kind (working|ok|none|error|skipped), text, saved path
    progress = Signal(int, int)  # done, total
    activity = Signal(str)  # what is happening right now (status bar)
    job_finished = Signal(str)  # summary

    def __init__(self, bridge: str = "reactions"):
        super().__init__()
        # Both fetch tools can be in the one WhatsApp tab at once, so each bridge gets its own name there.
        self.api = f"window.__waf_{bridge}"
        self.bridge_js = BRIDGES[bridge].read_text(encoding="utf-8").replace("window.__waf", self.api)
        self.page = None
        self._commands: queue.Queue = queue.Queue()
        self._stop_job = threading.Event()
        self._quit = threading.Event()
        self._failed = False

    # ---- called from the GUI thread -------------------------------------------------
    def submit_job(self, rows: list[tuple[int, CodeItem]], options: dict):
        self._stop_job.clear()
        self._commands.put(("job", rows, options))

    def refresh_chats(self):
        self._commands.put(("chats",))

    def stop_job(self):
        self._stop_job.set()

    def shutdown(self):
        self._quit.set()
        self._stop_job.set()
        self._commands.put(("quit",))

    # ---- thread body ---------------------------------------------------------------
    def run(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self._fail("Playwright is not installed. Run:  pip install playwright")
            return
        try:
            with sync_playwright() as pw:  # leaving it only disconnects: the window stays for other tools
                self.state_changed.emit("starting", "Starting browser…")
                self._connect(pw)
                self._main_loop()
        except Exception as e:  # noqa: BLE001 - report anything to the GUI
            if not self._quit.is_set() and not self._is_closed_error(e):
                self.log.emit(traceback.format_exc())
                self._fail(self._friendly_error(e))
        finally:
            if not self._failed:
                self.state_changed.emit("closed", "Disconnected from WhatsApp")

    def _connect(self, pw):
        _, ctx = whatsapp.connect(pw, self.log.emit)
        self.page = whatsapp.whatsapp_page(ctx)
        self.page.add_init_script(self.bridge_js)  # for WhatsApp's reloads; _status() injects it now
        self.log.emit(f"Connected to the WhatsApp window ({whatsapp.WA_URL}).")

    def _main_loop(self):
        if not self._wait_until_ready():
            return
        self._send_chats()
        while not self._quit.is_set():
            try:
                cmd = self._commands.get_nowait()
            except queue.Empty:
                self.page.wait_for_timeout(400)  # also keeps Playwright's event pipe drained
                continue
            if cmd[0] == "quit":
                break
            if cmd[0] == "chats":
                self._send_chats()
            elif cmd[0] == "job":
                self._run_job(cmd[1], cmd[2])

    # ---- WhatsApp state ------------------------------------------------------------
    def _status(self) -> dict | None:
        probe = f"() => {self.api} ? {self.api}.status() : null"
        try:
            st = self.page.evaluate(probe)
            if st is None:
                self.page.evaluate(self.bridge_js)  # init script missed this document; inject now
                st = self.page.evaluate(probe)
            return st
        except Exception as e:
            if self._is_closed_error(e):
                raise
            return None  # page is navigating/reloading

    def _wait_until_ready(self) -> bool:
        last = None
        first_ready_at = None
        watch = whatsapp.LoadWatch(self.page, self.log.emit)
        while not self._quit.is_set():
            st = self._status()
            watch.tick(not (st and (st.get("ready") or st.get("qr"))))
            if st and st.get("ready"):
                if first_ready_at is None:
                    first_ready_at = time.time()
                    last = "syncing"
                    self.state_changed.emit("loading", "Logged in, loading chats…")
                # give WhatsApp a moment to finish its initial sync
                waited = time.time() - first_ready_at
                if (st.get("chats", 0) > 0 and waited > 3) or waited > 20:
                    ver = st.get("version") or "?"
                    self.state_changed.emit("ready", "Connected")
                    self.log.emit(f"Connected to WhatsApp Web {ver} ({st.get('chats', 0)} chats loaded).")
                    return True
            else:
                first_ready_at = None
                state = "qr" if st and st.get("qr") else "loading"
                if state != last:
                    last = state
                    if state == "qr":
                        self.state_changed.emit("qr", "Scan the QR code in the browser window")
                        self.log.emit("Not logged in: on your phone open WhatsApp > Linked devices > Link a device, "
                                      "then scan the QR code shown in the browser window.")
                    else:
                        self.state_changed.emit("loading", "Loading WhatsApp Web…")
            self.page.wait_for_timeout(1000)
        return False

    def _ensure_ready(self):
        st = self._status()
        if not st or not st.get("ready"):
            self.log.emit("WhatsApp Web is not ready, waiting…")
            if not self._wait_until_ready():
                raise RuntimeError("Stopped")

    def _eval(self, expression: str, arg=None):
        for attempt in range(3):
            try:
                return self.page.evaluate(expression, arg)
            except Exception as e:
                if self._is_closed_error(e) or attempt == 2:
                    raise
                msg = str(e)
                if "Execution context was destroyed" in msg or "navigat" in msg:
                    self.page.wait_for_timeout(2000)
                    self._ensure_ready()
                else:
                    raise

    def _send_chats(self):
        try:
            chats = self._eval(f"() => {self.api}.listChats()") or []
            self.chats_loaded.emit(chats)
        except Exception as e:
            if self._is_closed_error(e):
                raise
            self.log.emit(f"Could not read chat list: {e}")

    # ---- the actual work -----------------------------------------------------------
    def _run_job(self, rows: list[tuple[int, CodeItem]], opt: dict):
        out_dir = Path(opt["output_dir"])
        started = time.time()
        stats = Counter()
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self.log.emit(f"Cannot create output folder: {e}")
            self.job_finished.emit("Failed: cannot create output folder")
            return

        todo: list[tuple[int, CodeItem, str]] = []
        for row, item in rows:
            base = item.base_name(opt.get("include_date", False))
            prev = existing_output(out_dir, base) if opt.get("skip_existing", True) else None
            if prev:
                self.row_status.emit(row, "skipped", "Already saved", str(prev))
                stats["skipped"] += 1
            else:
                todo.append((row, item, base))

        finished: set[int] = set()
        if todo:
            try:
                found, mentions, unreacted, since, n_chats = self._scan(todo, opt)
                for i, (row, item, base) in enumerate(todo):
                    if self._stop_job.is_set() or self._quit.is_set():
                        raise RuntimeError("Stopped")
                    key = str(i)
                    images = sorted(found.get(key, {}).values(), key=lambda x: x.get("t", 0))
                    try:
                        kind, n = self._download_and_save(row, item, base, out_dir, images, mentions.get(key, 0),
                                                          unreacted.get(key, 0), since, opt.get("reactions", ""))
                    except Exception as e:
                        if self._is_closed_error(e):
                            raise
                        kind, n = "error", 0
                        self.row_status.emit(row, "error", f"Error: {e}", "")
                        self.log.emit(f"[{item.code}] error: {e}")
                    finished.add(row)
                    stats[kind] += 1
                    stats["files"] += n
                    self.progress.emit(n_chats + i + 1, n_chats + len(todo))
            except Exception as e:
                if self._is_closed_error(e):
                    raise
                if str(e) == "Stopped":
                    self.log.emit("Stopped by user.")
                else:
                    self.log.emit(traceback.format_exc())
                    self.log.emit(f"Error: {e}")
            for row, _, _ in todo:
                if row not in finished:
                    self.row_status.emit(row, "", "Stopped" if self._stop_job.is_set() else "Not done", "")

        secs = int(time.time() - started)
        summary = (f"Done in {secs}s: {stats['ok']} code(s) saved ({stats['files']} images), "
                   f"{stats['none']} not found, {stats['skipped']} skipped, {stats['error']} error(s).")
        self.log.emit(summary)
        self.activity.emit("")
        self.job_finished.emit(summary)

    def _scan(self, todo: list[tuple[int, CodeItem, str]], opt: dict):
        """Read every relevant chat back to the cut-off date, matching all codes in one pass.

        Returns ({code key: {msg id: image info}}, {code key: mention count},
                 {code key: photos left out for lacking a wanted reaction}, since, chats read).
        """
        self._ensure_ready()
        since = scan_since([item for _, item, _ in todo], opt.get("lookback_days", 10))
        since_ts = int(since.timestamp())
        chat = opt.get("chat") or {}
        targets = self._eval(f"o => {self.api}.scanTargets(o)", {
            "since": since_ts,
            "chatId": chat.get("id") or "",
            "chatTitle": chat.get("title") or "",
            "groupsOnly": bool(chat.get("groups_only")),
        }) or []
        total = len(targets) + len(todo)
        self.progress.emit(0, total)
        self.log.emit(f"Reading {len(targets)} chat(s) active since {since:%d/%m/%Y} for {len(todo)} code(s)…")
        if opt.get("reactions"):
            self.log.emit(f"Only photos reacted with {opt['reactions']}.")
        scan_opts = {
            "codes": [{"key": str(i), "pattern": code_pattern(item.code)} for i, (_, item, _) in enumerate(todo)],
            "since": since_ts,
            "neighbors": opt.get("neighbors", "none"),
            "gapMinutes": opt.get("gap_minutes", 5),
            "maxNeighbors": 30,
            "maxLoads": 300,
            "reactions": opt.get("reactions", ""),
        }
        for row, _, _ in todo:
            self.row_status.emit(row, "working", "Reading chats…", "")

        found: dict[str, dict[str, dict]] = {}
        mentions: Counter = Counter()
        unreacted: Counter = Counter()
        short: list[tuple[str, int]] = []
        for n, target in enumerate(targets, 1):
            if self._stop_job.is_set() or self._quit.is_set():
                raise RuntimeError("Stopped")
            title = target.get("title") or target["id"]
            self.activity.emit(f"Reading chat {n}/{len(targets)}: {title}")
            res = self._eval(f"([id, o]) => {self.api}.scanChat(id, o)", [target["id"], scan_opts]) or {}
            if res.get("error"):
                self.log.emit(f"{title}: {res['error']}")
                continue
            for w in res.get("warnings") or []:
                self.log.emit(f"{title}: {w}")
            hits = res.get("hits") or {}
            for key, imgs in hits.items():
                bucket = found.setdefault(key, {})
                for img in imgs:
                    bucket.setdefault(img["id"], img)
                row = todo[int(key)][0]
                self.row_status.emit(row, "working", f"Reading chats… {len(bucket)} photo(s) found", "")
            for key, count in (res.get("mentions") or {}).items():
                mentions[key] += count
            for key, count in (res.get("unreacted") or {}).items():
                unreacted[key] += count
            if hits:
                codes = ", ".join(todo[int(k)][1].code for k in hits)
                self.log.emit(f"{title}: photos for {codes}")
            if res.get("loaded") and not res.get("reached") and res.get("exhausted"):
                short.append((title, res.get("oldest") or 0))
            self.progress.emit(n, total)

        if short:
            sample = "; ".join(f"{t} (from {datetime.fromtimestamp(o):%d/%m})" for t, o in short[:6] if o)
            self.log.emit(f"Note: {len(short)} chat(s) have no older messages on WhatsApp Web "
                          f"(before {since:%d/%m}), e.g. {sample}")
        return found, mentions, unreacted, since, len(targets)

    def _download_and_save(self, row: int, item: CodeItem, base: str, out_dir: Path, images: list[dict],
                           mentions: int, unreacted: int, since: datetime, reactions: str) -> tuple[str, int]:
        if not images:
            if unreacted:
                detail = f"{unreacted} photo(s) found, none reacted with {reactions}"
            elif mentions:
                detail = f"code is in {mentions} message(s), none with a photo"
            else:
                detail = f"not in any chat since {since:%d/%m}"
            self.row_status.emit(row, "none", f"No photos ({detail})", "")
            self.log.emit(f"[{item.code}] no photos: {detail}.")
            return "none", 0

        chats = sorted({img.get("chat") or "?" for img in images})
        marks = "".join(sorted({img["react"] for img in images if img.get("react")}))
        self.log.emit(f"[{item.code}] {len(images)} photo(s) in: {', '.join(chats)}" + (f"  (reacted {marks})" if marks else ""))
        files: list[tuple[bytes, str]] = []
        hashes: set[str] = set()
        failed = 0
        for i, img in enumerate(images, 1):
            if self._stop_job.is_set() or self._quit.is_set():
                break
            self.row_status.emit(row, "working", f"Downloading {i}/{len(images)}…", "")
            self.activity.emit(f"[{item.code}] downloading {i}/{len(images)}")
            res = self._eval(f"id => {self.api}.download(id)", img["id"]) or {}
            if res.get("error") or not res.get("data"):
                failed += 1
                self.log.emit(f"[{item.code}] photo {i} failed: {res.get('error') or 'no data'}")
                continue
            data = base64.b64decode(res["data"])
            digest = hashlib.sha256(data).hexdigest()
            if digest in hashes:
                continue  # same picture posted/forwarded twice
            hashes.add(digest)
            files.append((data, ext_for(res.get("mimetype"), data, res.get("filename"))))

        if not files:
            self.row_status.emit(row, "error", f"Found {len(images)}, download failed", "")
            return "error", 0
        path = save_images(out_dir, base, files)
        text = f"Saved {len(files)} image" + ("s" if len(files) > 1 else "")
        if failed:
            text += f" ({failed} failed)"
        self.row_status.emit(row, "ok", text, str(path))
        self.log.emit(f"[{item.code}] {text} → {path}")
        return "ok", len(files)

    # ---- errors --------------------------------------------------------------------
    def _fail(self, message: str):
        self._failed = True
        self.state_changed.emit("error", message)
        self.log.emit(message)

    @staticmethod
    def _is_closed_error(e: Exception) -> bool:
        msg = str(e)
        return any(s in msg for s in ("Target page, context or browser has been closed",
                                      "Target closed", "Browser has been closed", "Connection closed"))

    def _friendly_error(self, e: Exception) -> str:
        if isinstance(e, whatsapp.BrowserError):
            return str(e)
        msg = str(e).strip().splitlines()[0] if str(e).strip() else repr(e)
        return f"Browser error: {msg}"
