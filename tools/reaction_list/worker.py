"""Background thread: reads the chats in the shared WhatsApp window and reports the codes whose
messages got one of the chosen reactions, and can save their reacted photos. Connecting and waiting
for WhatsApp is Fetch by Code's worker; only the job is different."""
from __future__ import annotations

import base64
import hashlib
import time
import traceback
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Signal

from tools.pathao_autofill.order_parser import find_phones
from tools.whatsapp_fetch.helpers import (CodeItem, code_pattern, existing_output, ext_for, save_images,
                                          scan_patterns, scan_since)
from tools.whatsapp_fetch.worker import WhatsAppWorker

from .helpers import code_sort_key, message_codes, normalize


class ReactionListWorker(WhatsAppWorker):
    found = Signal(list)  # [{code, react, phones, chat, t}] from one chat; code as normalize() writes it
    photos = Signal(str, str, str)  # code (as normalize() writes it), status text, saved path

    def __init__(self):
        super().__init__("reactions")  # the bridge with the reaction reader

    def _run_job(self, rows: list[tuple[int, CodeItem]], opt: dict):
        """rows: the pasted codes to look for, or none for every code.
        opt: reactions, lookback_days, chat, photos_dir ('' = list only)."""
        started = time.time()
        items = [item for _, item in rows]
        listed = {normalize(item.code): item for item in reversed(items)}  # the first line of a code wins
        wanted = set(listed)
        reactions = opt["reactions"]
        codes: set[str] = set()
        chats_of: dict[str, list[str]] = {}  # code -> ids of the chats it was reacted in
        no_code = orphans = 0
        saved = files = 0
        try:
            self._ensure_ready()
            since = scan_since(items, opt.get("lookback_days", 10))
            chat = opt.get("chat") or {}
            targets = self._eval(f"o => {self.api}.scanTargets(o)", {
                "since": int(since.timestamp()),
                "chatId": chat.get("id") or "",
                "chatTitle": chat.get("title") or "",
                "groupsOnly": bool(chat.get("groups_only")),
            }) or []
            self.progress.emit(0, max(len(targets), 1))
            what = f"{len(items)} code(s)" if items else "every code"
            self.log.emit(f"Reading {len(targets)} chat(s) active since {since:%d/%m/%Y}: {what} reacted with {reactions}…")
            scan_opts = {"since": int(since.timestamp()), "reactions": reactions, "gapMinutes": 5, "maxLoads": 300}
            short: list[tuple[str, int]] = []
            for n, target in enumerate(targets, 1):
                self._check_stop()
                title = target.get("title") or target["id"]
                self.activity.emit(f"Reading chat {n}/{len(targets)}: {title}")
                res = self._eval(f"([id, o]) => {self.api}.reactedMessages(id, o)", [target["id"], scan_opts]) or {}
                if res.get("error"):
                    self.log.emit(f"{title}: {res['error']}")
                    self.progress.emit(n, len(targets))
                    continue
                for w in res.get("warnings") or []:
                    self.log.emit(f"{title}: {w}")
                orphans += res.get("orphans") or 0
                entries = []
                for hit in res.get("hits") or []:
                    text = hit.get("text") or ""
                    in_text = message_codes(text)
                    if not in_text:
                        no_code += 1
                    phones = find_phones(text)
                    for code in in_text:
                        if wanted and code not in wanted:
                            continue
                        entries.append({"code": code, "react": hit.get("react") or "", "phones": phones,
                                        "chat": hit.get("chat") or title, "t": hit.get("t") or 0})
                        ids = chats_of.setdefault(code, [])
                        if target["id"] not in ids:
                            ids.append(target["id"])
                if entries:
                    new = sorted({e["code"] for e in entries}, key=code_sort_key)
                    codes.update(new)
                    self.found.emit(entries)
                    self.log.emit(f"{title}: {', '.join(new)}")
                if res.get("loaded") and not res.get("reached") and res.get("exhausted"):
                    short.append((title, res.get("oldest") or 0))
                self.progress.emit(n, len(targets))
            if short:
                sample = "; ".join(f"{t} (from {datetime.fromtimestamp(o):%d/%m})" for t, o in short[:6] if o)
                self.log.emit(f"Note: {len(short)} chat(s) have no older messages on WhatsApp Web "
                              f"(before {since:%d/%m}), e.g. {sample}")
            if opt.get("photos_dir") and codes:
                saved, files = self._save_photos(sorted(codes, key=code_sort_key), listed, chats_of, since, opt)
        except Exception as e:
            if self._is_closed_error(e):
                raise
            if str(e) == "Stopped":
                self.log.emit("Stopped by user.")
            else:
                self.log.emit(traceback.format_exc())
                self.log.emit(f"Error: {e}")

        if no_code:
            self.log.emit(f"{no_code} reacted message(s) name no code (left out).")
        if orphans:
            self.log.emit(f"{orphans} reacted photo(s) have no code written before them (left out).")
        secs = int(time.time() - started)
        if items:
            summary = f"Done in {secs}s: {len(codes)} of {len(wanted)} code(s) reacted with {reactions}."
        else:
            summary = f"Done in {secs}s: {len(codes)} code(s) reacted with {reactions}."
        if opt.get("photos_dir") and codes:
            summary = summary[:-1] + f", photos saved for {saved} ({files} images)."
        self.log.emit(summary)
        self.activity.emit("")
        self.job_finished.emit(summary)

    def _check_stop(self):
        if self._stop_job.is_set() or self._quit.is_set():
            raise RuntimeError("Stopped")

    def _save_photos(self, codes: list[str], listed: dict[str, CodeItem], chats_of: dict[str, list[str]],
                     since: datetime, opt: dict) -> tuple[int, int]:
        """Save the reacted photos of each listed code as '2-558 Realme C85.jpg' (as Fetch by Code names
        them): the photos with the reaction, or all photos of a code whose message has it.
        Returns (codes saved, images saved)."""
        out_dir = Path(opt["photos_dir"])
        out_dir.mkdir(parents=True, exist_ok=True)
        key = {code: str(i) for i, code in enumerate(codes)}
        by_chat: dict[str, list[str]] = {}
        for code in codes:
            for cid in chats_of.get(code, []):
                by_chat.setdefault(cid, []).append(code)
        todo = [c for c in codes if not existing_output(out_dir, (listed.get(c) or CodeItem(c)).base_name())]
        for code in codes:
            if code not in todo:
                prev = existing_output(out_dir, (listed.get(code) or CodeItem(code)).base_name())
                self.photos.emit(code, "Saved earlier", str(prev))
        total = len(by_chat) + len(todo)
        self.log.emit(f"Finding the photos of {len(todo)} code(s) in {len(by_chat)} chat(s)…")
        found: dict[str, dict[str, dict]] = {}
        for n, (cid, chat_codes) in enumerate(by_chat.items(), 1):
            self._check_stop()
            chat_codes = [c for c in chat_codes if c in todo]
            self.progress.emit(n, total)
            if not chat_codes:
                continue
            self.activity.emit(f"Finding photos: chat {n}/{len(by_chat)}")
            res = self._eval(f"([id, o]) => {self.api}.scanChat(id, o)", [cid, {
                "codes": [{"key": key[c], "pattern": code_pattern(c)} for c in chat_codes],
                "since": int(since.timestamp()), "neighbors": "after", "gapMinutes": 5, "maxNeighbors": 30,
                "maxLoads": 300, "reactions": opt["reactions"], **scan_patterns(),
            }]) or {}
            if res.get("error"):
                self.log.emit(f"Photos: {res['error']}")
            for k, imgs in (res.get("hits") or {}).items():
                bucket = found.setdefault(k, {})
                for img in imgs:
                    bucket.setdefault(img["id"], img)

        saved = files = 0
        for i, code in enumerate(todo, 1):
            self._check_stop()
            self.progress.emit(len(by_chat) + i, total)
            images = sorted(found.get(key[code], {}).values(), key=lambda x: x.get("t", 0))
            if not images:
                self.photos.emit(code, "No photos", "")
                self.log.emit(f"[{code}] no reacted photos.")
                continue
            data_files: list[tuple[bytes, str]] = []
            hashes: set[str] = set()
            for j, img in enumerate(images, 1):
                self._check_stop()
                self.activity.emit(f"[{code}] downloading {j}/{len(images)}")
                self.photos.emit(code, f"Downloading {j}/{len(images)}…", "")
                res = self._eval(f"id => {self.api}.download(id)", img["id"]) or {}
                if res.get("error") or not res.get("data"):
                    self.log.emit(f"[{code}] photo {j} failed: {res.get('error') or 'no data'}")
                    continue
                data = base64.b64decode(res["data"])
                digest = hashlib.sha256(data).hexdigest()
                if digest not in hashes:  # the same picture posted / forwarded twice
                    hashes.add(digest)
                    data_files.append((data, ext_for(res.get("mimetype"), data, res.get("filename"))))
            if not data_files:
                self.photos.emit(code, f"Found {len(images)}, download failed", "")
                continue
            path = save_images(out_dir, (listed.get(code) or CodeItem(code)).base_name(), data_files)
            saved += 1
            files += len(data_files)
            self.photos.emit(code, f"{len(data_files)} photo" + ("s" if len(data_files) > 1 else ""), str(path))
            self.log.emit(f"[{code}] saved {len(data_files)} photo(s) → {path}")
        return saved, files
