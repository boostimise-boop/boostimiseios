"""The seller's shop settings, shared by every tool and every Boostimise app (website, Windows,
Android, iPhone) through the account: the Reaction List reactions and what each one means, and the
WhatsApp chat of each code prefix (Pathao Auto-fill), and the code style with its one orders chat
("code_style" and "orders_chat": kept as they come, core/codes.py checks them).

A copy is kept in <data>/shop/shop.json so the tools work signed out and offline. Signed in, the
account's row in shop_profiles (boostimise/supabase/schema.sql) is the one that counts:

- No row yet: this PC's settings are uploaded.
- The first time this PC syncs with the account: the row is kept, and chats it doesn't have are
  added from this PC (so a phone that synced first with no chats doesn't wipe this PC's).
- After that the row wins, except over a change made here that could not be uploaded yet.

android/.../core/Shop.kt, ios/.../Core/Shop.swift and the website's lib/shop.ts work the same way.
"""
from __future__ import annotations

import json
import logging
import os

from PySide6.QtCore import QObject, Signal

from . import account as acc
from . import net
from .paths import data_dir

log = logging.getLogger("allinone.shop")

# 😢 is WhatsApp's built-in "sad" reaction: a 😥 reaction also counts it (see reaction_list).
DEFAULT_REACTIONS = [{"emoji": "❌", "label": ""}, {"emoji": "📵", "label": ""}, {"emoji": "😥", "label": ""}]
MAX_REACTIONS = 12
FILE = data_dir("shop") / "shop.json"


def clean_reactions(value) -> list[dict]:
    """[{"emoji", "label"}] with each emoji once, without empty ones."""
    out, seen = [], set()
    for r in value if isinstance(value, list) else []:
        if not isinstance(r, dict):
            continue
        emoji = str(r.get("emoji") or "").strip()
        if emoji and emoji not in seen:
            seen.add(emoji)
            out.append({"emoji": emoji, "label": str(r.get("label") or "").strip()[:40]})
    return out[:MAX_REACTIONS]


def clean_chats(value) -> dict[str, str]:
    """{"6": "Chat name"}: lower-case prefixes, without the "<prefix>-" names that are the default."""
    out = {}
    for p, name in (value.items() if isinstance(value, dict) else []):
        p, name = str(p).strip().lower(), str(name or "").strip()
        if p and name and name != f"{p}-":
            out[p] = name[:120]
    return out


def clean(settings) -> dict:
    s = dict(settings) if isinstance(settings, dict) else {}
    s["reactions"] = clean_reactions(s.get("reactions")) if "reactions" in s else list(DEFAULT_REACTIONS)
    s["chats"] = clean_chats(s.get("chats"))
    return s


def chat_name(chats: dict, orders_chat: str, prefix: str) -> str:
    """The WhatsApp chat of a code list: its chat in the shop settings, the orders chat for codes
    without a prefix (#1023), or else the chat named like the list ("6-")."""
    prefix = (prefix or "").lower()
    if not prefix:
        return (orders_chat or "").strip()
    return (chats.get(prefix) or f"{prefix}-").strip()


def _old_chat_names() -> dict:
    """Pathao Auto-fill's chat names from before the shop settings, to start this PC's copy with."""
    try:
        with open(data_dir("pathao_autofill") / "settings.json", encoding="utf-8") as f:
            return clean_chats(json.load(f).get("chat_names"))
    except (OSError, ValueError, AttributeError):
        return {}


class Shop(QObject):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.shop_name = ""
        self.settings = clean({"chats": _old_chat_names()})
        self.synced_user: str | None = None  # the account this PC synced with last
        self.dirty = False  # changed here and not uploaded yet
        self._syncing = False
        self._load()
        self.account = acc.get()
        self.account.state_changed.connect(self._on_account_state)
        self.account.table_changed.connect(lambda t: t == "shop_profiles" and self.sync())
        self._on_account_state(self.account.state)

    # ---- reading ---------------------------------------------------------------------------
    def reactions(self) -> list[dict]:
        return [dict(r) for r in self.settings["reactions"]]

    def chats(self) -> dict[str, str]:
        return dict(self.settings["chats"])

    def chat_for(self, prefix: str) -> str:
        return chat_name(self.settings["chats"], self.settings.get("orders_chat") or "", prefix)

    def label_of(self, emoji: str) -> str:
        return next((r["label"] for r in self.settings["reactions"] if r["emoji"] == emoji), "")

    # ---- changing --------------------------------------------------------------------------
    def save(self, reactions: list[dict] | None = None, chats: dict | None = None, shop_name: str | None = None,
             **other):
        """Keeps the change on this PC and uploads it when signed in. `other` sets more keys of the
        settings (code_style=..., orders_chat=...); None removes one."""
        new = dict(self.settings)
        if reactions is not None:
            new["reactions"] = reactions
        if chats is not None:
            new["chats"] = chats
        for key, value in other.items():
            if value is None:
                new.pop(key, None)
            else:
                new[key] = value
        new = clean(new)
        name = self.shop_name if shop_name is None else shop_name.strip()[:80]
        if new == self.settings and name == self.shop_name:
            return
        self.settings, self.shop_name, self.dirty = new, name, True
        self._write()
        self.changed.emit()
        self._upload()

    def set_chats(self, chats: dict):
        self.save(chats=chats)

    def set(self, key: str, value):
        """One key of the settings (None removes it), e.g. set("code_style", {"kind": "prefix"})."""
        self.save(**{key: value})

    # ---- the account's copy ----------------------------------------------------------------
    def _uid(self) -> str | None:
        if self.account.state != acc.READY or self.account.offline:
            return None
        return (self.account.user or {}).get("id")

    def _on_account_state(self, state: str):
        if state == acc.READY:
            self.sync()

    def sync(self):
        """Reads the account's shop settings (and uploads this PC's when it has none)."""
        uid = self._uid()
        if not uid or self._syncing:
            return
        self._syncing = True
        a = self.account

        def work():
            rows = a.select("shop_profiles", f"select=shop_name,settings,updated_at&user_id=eq.{uid}")
            return rows[0] if rows else None

        def done(row):
            self._syncing = False
            if self._uid() != uid:
                return
            if row is None or self.dirty:
                if row is None and self.synced_user not in (None, uid):
                    # another account's settings were on this PC: start this one fresh
                    self.settings, self.shop_name = clean({}), ""
                    self._write()
                    self.changed.emit()
                self._upload()
                return
            server = clean(row.get("settings"))
            if self.synced_user is None:  # this PC's first sync: keep its chats the row doesn't have
                merged = clean({**server, "chats": {**self.settings["chats"], **server["chats"]}})
                if merged != server:
                    self.settings, self.shop_name = merged, row.get("shop_name") or self.shop_name
                    self.dirty = True
                    self._write()
                    self.changed.emit()
                    self._upload()
                    return
            self._adopt(row, uid)

        def failed(e):
            self._syncing = False
            log.info("Could not read the shop settings: %s", e)
        net.run_bg(work, done, failed)

    def _upload(self):
        uid = self._uid()
        if not uid:
            return
        settings, name = dict(self.settings), self.shop_name or None
        net.run_bg(lambda: self.account.rpc("save_my_shop", {"p_settings": settings, "p_shop_name": name}),
                   lambda row: self._uploaded(row, uid, settings),
                   lambda e: log.info("Could not save the shop settings: %s", e))

    def _uploaded(self, row, uid: str, sent: dict):
        if self._uid() != uid:
            return
        if sent == self.settings:
            self.dirty = False
        if isinstance(row, dict) and not self.dirty:
            self._adopt(row, uid)
        else:
            self.synced_user = uid
            self._write()

    def _adopt(self, row: dict, uid: str):
        new, name = clean(row.get("settings")), row.get("shop_name") or ""
        changed = new != self.settings or name != self.shop_name
        self.settings, self.shop_name, self.synced_user, self.dirty = new, name, uid, False
        self._write()
        if changed:
            self.changed.emit()

    # ---- this PC's copy --------------------------------------------------------------------
    def _load(self):
        try:
            with open(FILE, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            return
        if isinstance(d, dict):
            self.settings = clean(d.get("settings"))
            self.shop_name = str(d.get("shop_name") or "")
            self.synced_user = d.get("synced_user") or None
            self.dirty = bool(d.get("dirty"))

    def _write(self):
        tmp = str(FILE) + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"shop_name": self.shop_name, "settings": self.settings,
                           "synced_user": self.synced_user, "dirty": self.dirty}, f, ensure_ascii=False, indent=2)
            os.replace(tmp, FILE)
        except OSError as e:
            log.warning("Could not save %s: %s", FILE, e)


_shop: Shop | None = None


def get() -> Shop:
    """The app's one Shop (made on first use, on the UI thread)."""
    global _shop
    if _shop is None:
        _shop = Shop()
    return _shop
