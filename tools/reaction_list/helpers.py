"""Pure helpers: the reaction choices, the codes a message is about, and the list that is
copied or saved."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from core import codes as product_codes
from tools.pathao_autofill.order_parser import find_codes, normalize_code

# (settings key, button text, emojis looked for, tooltip). 😢 is WhatsApp's built-in "sad"
# reaction, so it counts as 😥 (as in Fetch by Reaction).
CHOICES = [
    ("x", "❌", ("❌",), "Codes reacted with ❌"),
    ("nophone", "📵", ("📵",), "Codes reacted with 📵"),
    ("sad", "😥", ("😥", "😢"), "Codes reacted with 😥 (or 😢, WhatsApp's built-in sad reaction)"),
    ("all", "❌ 📵 😥", ("❌", "📵", "😥", "😢"), "Codes reacted with any of ❌ 📵 😥 (or 😢)"),
]

_BN_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
# A date such as 16-10-2026 or 2026-10-16 is not a code.
_DATE = re.compile(r"(?<![0-9A-Za-z])(?:(?:[12]?\d|3[01])[-/.](?:0?[1-9]|1[0-2])[-/.](?:\d{4}|\d{2})"
                   r"|\d{4}[-/.]\d{1,2}[-/.]\d{1,2})(?![0-9A-Za-z])")
# One code of a list: "2-558", "2:558+", "5A_380" ...
_CODE = re.compile(r"\s*(" + product_codes.PREFIX + r")\s*" + product_codes.SEP + r"\s*(\d{1,6})[\s+\-]*", re.I)
# A message naming more codes is a list or summary: its reactions are not about one code.
MAX_CODES = 3


def normalize(code: str) -> str:
    """'2-558', '2 - 558', '2:558+', '5A-380' -> '2-558', '5a-380', as order_parser writes codes."""
    m = _CODE.fullmatch(code or "")
    return normalize_code(*m.groups()) if m else (code or "").strip().lower()


def message_codes(text: str) -> list[str]:
    """The codes a reacted message is about: the code(s) an order message starts with ("6-872",
    "3-163+164", "2-151-152") and the codes written anywhere in it, if that is no more than MAX_CODES."""
    written = product_codes.codes_in_text(_DATE.sub(" ", (text or "").translate(_BN_DIGITS)))
    codes = list(dict.fromkeys([*find_codes(text), *written]))
    return codes if len(codes) <= MAX_CODES else []


def code_sort_key(code: str):
    """2-558 < 2-560 < 3-12 < 5a-380."""
    m = _CODE.fullmatch(code or "")
    if not m:
        return (10**6, code, 0)
    prefix = re.match(r"(\d+)(.*)", m.group(1))
    return (int(prefix.group(1)), prefix.group(2).lower(), int(m.group(2)))


@dataclass
class Found:
    """One code of the list, with what all its reacted messages say."""

    code: str
    name: str = ""
    order: int = 0  # place in the pasted list (or 0)
    emojis: set[str] = field(default_factory=set)
    phones: list[str] = field(default_factory=list)
    chats: list[str] = field(default_factory=list)
    t: int = 0  # newest reacted message, unix seconds

    def add(self, hit: dict):
        self.emojis.update(hit.get("react") or "")
        for phone in hit.get("phones") or []:
            if phone not in self.phones:
                self.phones.append(phone)
        chat = hit.get("chat") or ""
        if chat and chat not in self.chats:
            self.chats.append(chat)
        self.t = max(self.t, int(hit.get("t") or 0))

    def react(self, wanted: tuple[str, ...]) -> str:
        """The reactions found, in the order of the choice ('❌📵')."""
        return "".join(e for e in wanted if e in self.emojis)


def format_list(rows: list[Found], wanted: tuple[str, ...]) -> str:
    """'1. 2-558 Realme C85 ❌ 01734484040', one code per line."""
    lines = []
    for n, f in enumerate(rows, 1):
        parts = [f.code, f.name, f.react(wanted), ", ".join(f.phones)]
        lines.append(f"{n}. " + " ".join(p for p in parts if p))
    return "\n".join(lines) + ("\n" if lines else "")
