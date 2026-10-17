"""Background thread: reads the chats in the shared WhatsApp window and reports the codes whose
messages got one of the chosen reactions. Connecting and waiting for WhatsApp is Fetch by Code's
worker; only the job is different."""
from __future__ import annotations

import time
import traceback
from datetime import datetime

from PySide6.QtCore import Signal

from tools.pathao_autofill.order_parser import find_phones
from tools.whatsapp_fetch.helpers import CodeItem, scan_since
from tools.whatsapp_fetch.worker import WhatsAppWorker

from .helpers import code_sort_key, message_codes, normalize


class ReactionListWorker(WhatsAppWorker):
    found = Signal(list)  # [{code, react, phones, chat, t}] from one chat; code as normalize() writes it

    def __init__(self):
        super().__init__("reactions")  # the bridge with the reaction reader (shared with Fetch by Reaction)

    def _run_job(self, rows: list[tuple[int, CodeItem]], opt: dict):
        """rows: the pasted codes to look for, or none for every code. opt: reactions, lookback_days, chat."""
        started = time.time()
        items = [item for _, item in rows]
        wanted = {normalize(item.code) for item in items}
        reactions = opt["reactions"]
        codes: set[str] = set()
        no_code = orphans = 0
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
                if self._stop_job.is_set() or self._quit.is_set():
                    raise RuntimeError("Stopped")
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
        self.log.emit(summary)
        self.activity.emit("")
        self.job_finished.emit(summary)
