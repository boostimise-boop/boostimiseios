"""Pure helpers: the reaction buttons, the codes a message is about, and the list that is
copied or saved."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from core import codes as product_codes
from tools.pathao_autofill.order_parser import find_codes

# The buttons come from the shop settings (core/shop.py): one per reaction, "All" and "Custom".
# 😢 is WhatsApp's built-in "sad" reaction, so it counts as 😥. The "custom" choice looks for the
# emojis typed next to it.
CUSTOM = "custom"
ALL = "all"
DEFAULT_CHOICE = ALL
OLD_KEYS = {"x": "❌", "nophone": "📵", "sad": "😥"}  # the saved choice up to 4.4
ALIASES = {"😥": ("😢",)}


def with_aliases(emojis) -> tuple[str, ...]:
    """The emojis looked for: each one, and right after it the reactions that count as it (😥 → 😢)."""
    out: list[str] = []
    for e in emojis:
        for x in (e, *ALIASES.get(e, ())):
            if x not in out:
                out.append(x)
    return tuple(out)


def make_choices(reactions: list[dict]) -> list[tuple]:
    """(settings key, button text, emojis looked for, tooltip) for the shop's reactions, then All and Custom."""
    out = []
    for r in reactions:
        e, label = r["emoji"], r.get("label") or ""
        tip = f"Codes reacted with {e}" + (f" - {label}" if label else "")
        if e in ALIASES:
            tip += f" (or {' '.join(ALIASES[e])}, WhatsApp's built-in reaction)"
        out.append((e, e, with_aliases([e]), tip))
    if len(reactions) > 1:
        es = [r["emoji"] for r in reactions]
        out.append((ALL, " ".join(es), with_aliases(es), "Codes reacted with any of " + " ".join(es)))
    out.append((CUSTOM, "Custom", (), "Codes reacted with the emojis you type in the box next to it"))
    return out


def choice_key(saved: str) -> str:
    return OLD_KEYS.get(saved, saved)


def reaction_labels(reactions: list[dict]) -> dict[str, str]:
    """{"❌": "Cancelled", ...}, also for the reactions that count as one of them (😢 as 😥)."""
    labels = {}
    for r in reactions:
        if r.get("label"):
            for e in with_aliases([r["emoji"]]):
                labels.setdefault(e, r["label"])
    return labels

# As wa_bridge_reactions.js reads reactions: '❤️' and '❤', '👍🏻' and '👍' are the same reaction.
_EMOJI_MARKS = re.compile("[︎️\U0001F3FB-\U0001F3FF]")
_ZWJ, _KEYCAP = "‍", "⃣"

_BN_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
# A date such as 16-10-2026 or 2026-10-16 is not a code.
_DATE = re.compile(r"(?<![0-9A-Za-z])(?:(?:[12]?\d|3[01])[-/.](?:0?[1-9]|1[0-2])[-/.](?:\d{4}|\d{2})"
                   r"|\d{4}[-/.]\d{1,2}[-/.]\d{1,2})(?![0-9A-Za-z])")
# A message naming more codes is a list or summary: its reactions are not about one code.
MAX_CODES = 3


def _is_flag_half(ch: str) -> bool:
    return "\U0001F1E6" <= ch <= "\U0001F1FF"


def _joins(cur: str, ch: str) -> bool:
    """Whether `ch` is part of the emoji `cur` (a 👨‍👩‍👧 family, a 🇧🇩 flag, 1️⃣, a tag flag)."""
    return (cur[-1] == _ZWJ or ch in (_ZWJ, _KEYCAP) or bool(unicodedata.combining(ch))
            or "\U000E0020" <= ch <= "\U000E007F"
            or (_is_flag_half(ch) and len(cur) == 1 and _is_flag_half(cur)))


def split_emojis(text: str) -> list[str]:
    """'👍 ❤️,🔥' -> ['👍', '❤', '🔥']: one entry per emoji, without skin tones and variation
    marks, each once. Spaces and , ; . only separate."""
    out: list[str] = []
    cur = ""
    for ch in _EMOJI_MARKS.sub("", text or ""):
        if ch.isspace() or ch in ",;.":
            if cur:
                out.append(cur)
            cur = ""
        elif cur and _joins(cur, ch):
            cur += ch
        else:
            if cur:
                out.append(cur)
            cur = ch
    if cur:
        out.append(cur)
    return list(dict.fromkeys(out))


def custom_reactions(text: str) -> tuple[str, ...]:
    """The emojis typed for the custom choice; 😥 also brings 😢, as the 😥 button does."""
    return with_aliases(split_emojis(text))


def normalize(code: str) -> str:
    """'2-558', '2 - 558', '2:558+', '5A-380' -> '2-558', '5a-380'; 'CC1023' -> 'cc-1023'; '#1023' -> '1023'
    (the shop's code style, as order_parser writes codes)."""
    return product_codes.normalize(code)


def message_codes(text: str) -> list[str]:
    """The codes a reacted message is about: the code(s) an order message starts with ("6-872",
    "3-163+164", "2-151-152") and the codes written anywhere in it, if that is no more than MAX_CODES."""
    written = product_codes.codes_in_text(_DATE.sub(" ", (text or "").translate(_BN_DIGITS)))
    codes = list(dict.fromkeys([*find_codes(text), *written]))
    return codes if len(codes) <= MAX_CODES else []


def code_sort_key(code: str):
    """2-558 < 2-560 < 3-12 < 5a-380 (in the shop's code style)."""
    return product_codes.reader().sort_key(code)


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
    photos: str = ""  # with "List + photos": what was saved ("3 photos", "No photos" …)
    photos_path: str = ""  # the saved photo, or the folder of a code's photos

    def add(self, hit: dict):
        self.emojis.update(split_emojis(hit.get("react") or ""))
        for phone in hit.get("phones") or []:
            if phone not in self.phones:
                self.phones.append(phone)
        chat = hit.get("chat") or ""
        if chat and chat not in self.chats:
            self.chats.append(chat)
        self.t = max(self.t, int(hit.get("t") or 0))

    def react(self, wanted: tuple[str, ...], labels: dict[str, str] | None = None) -> str:
        """The reactions found, in the order of the choice: '❌📵', or with what they mean in the
        shop settings '❌ Cancelled, 📵 Phone off'."""
        found = [e for e in wanted if e in self.emojis]
        if not labels or not any(labels.get(e) for e in found):
            return "".join(found)
        return ", ".join(f"{e} {labels[e]}" if labels.get(e) else e for e in found)


def format_list(rows: list[Found], wanted: tuple[str, ...], labels: dict[str, str] | None = None) -> str:
    """'1. 2-558 Realme C85 ❌ 01734484040' (or '❌ Cancelled' with a label), one code per line."""
    lines = []
    for n, f in enumerate(rows, 1):
        parts = [f.code, f.name, f.react(wanted, labels), ", ".join(f.phones)]
        lines.append(f"{n}. " + " ".join(p for p in parts if p))
    return "\n".join(lines) + ("\n" if lines else "")
