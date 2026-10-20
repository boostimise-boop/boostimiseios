"""Product codes as people type them. Every tool reads codes through this file.

A shop writes its codes in one of three styles, its shop setting "code_style" (see clean_style):

- prefix (the default): a list number, a dash and a number, as in 2-558. All of these mean 2-152:

      2-152  2:152  2_152  2+152  2;152  2&152  2:152+  2:152-  2+152-

  and several codes of one list may be written together:

      2-151+152  2-151-152  2-151+2-152  2:152+2-153  2+152-2+153  2-151 & 2-152

  A code joined by + & : or ; must start its line ("redmi 12 8+128" is memory, "at 10:30" a time).
- letters: the shop's letters and a number, as in CC1023 or AB-12. Also typed cc1023, CC 1023,
  CC-1023, CC1023+1024, CC1023 & CC1024. The letters are the code's prefix (the chat it is in).
- keyword: a # or a word and a number, as in #1023 or Order 1023. Also typed Order: #1023, order-1023,
  #1023+1024. These codes have no prefix: their key is the number alone, and they are in one orders chat.

reader() reads codes in the shop's style and set_style() changes it; the functions below use it.
core/code_samples.json holds sample messages and the codes each style must find in them.
`python -m core.codes_check` checks this file and the website's lib/codes.ts against them; the ports
android/.../core/ProductCode.kt and ios/.../Core/ProductCode.swift check them in their own tests.
"""
import re
from functools import lru_cache

# ---- the prefix style (2-558) ------------------------------------------------------------------
PREFIX = r"\d{1,3}[A-Za-z]{0,2}"
DASH = r"[-‐-―_]"  # joins the two halves of a code anywhere
LOOSE = r"[+&:;]"            # joins them only in a code that starts a line
SEP = r"(?:" + DASH + "|" + LOOSE + ")"
_END = r"(?![0-9A-Za-z])"
_START = r"(?<![0-9A-Za-z])"
# A number after a code that adds one more code of the same list. A lone digit followed by a joiner
# starts a code of another list instead ("3-163+4-165" is 3-163 and 4-165).
_MORE_NUM = r"(?!\d[ \t]*" + SEP + r"[ \t]*\d)\d{1,6}" + _END


def _join(prefix):
    """What comes between two codes of a list whose prefix matches the regex `prefix`: "+164",
    "+2-164", " & 2-164", "-164", "-2+164" (with spaces around "-" only before a full code)."""
    head = prefix + r"\s*" + SEP + r"\s*"
    return (r"(?:[ \t]*[+&][ \t]*(?:" + head + r")?|[ \t]*" + DASH + r"[ \t]*" + head
            + "|" + DASH + "(?:" + head + ")?)")


# The prefix style's patterns. Tools read codes with reader(), which has these for the shop's style.
# One code alone, as in a code list or a result file: group 1 prefix, group 2 number.
CODE = _START + r"(" + PREFIX + r")\s*" + SEP + r"\s*(\d{1,6})" + _END
CODE_RE = re.compile(CODE)
# A code written in a chat with the codes that follow it: 1 prefix, 2 joiner, 3 number, 4 the rest of
# the list (read with more_numbers). Other patterns may be put in front of it if they capture nothing.
CHAT_CODE = (_START + r"(" + PREFIX + r")\s*(" + SEP + r")\s*(\d{1,6})" + _END
             + r"((?:" + _join(r"\1") + _MORE_NUM + r")*)")
CHAT_CODE_RE = re.compile(CHAT_CODE, re.I)
# CHAT_CODE in JavaScript (flags "gm"), with the line-start rule built in.
JS_CHAT_CODE = (_START + r"(" + PREFIX + r")\s*(?:" + DASH + r"|(?<=^[^0-9A-Za-z\n]*[0-9A-Za-z]+\s*)"
                + LOOSE + r")\s*(\d{1,6})" + _END + r"((?:" + _join(r"\1") + _MORE_NUM + r")*)")
# One item of group 4 in JavaScript; "__P__" is replaced by the code's prefix before use (flags "gi").
JS_MORE_ITEM = _join("__P__") + "(" + _MORE_NUM + ")"
BN_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")

# ---- the letters (CC1023) and keyword (#1023) styles --------------------------------------------
_LETTERS_GAP = r"[ \t]*" + DASH + r"?[ \t]*"            # CC1023, CC 1023, CC-1023, cc_1023
_KEYWORD_GAP = r"[ \t]*(?:[-‐-―_:.#][ \t]*){0,2}"   # #1023, Order 1023, Order: #1023, order-1023
_NUMBER = r"\d{1,6}" + _END


def _list_join(head):
    """What comes between two codes of a list in these styles: "+1024", " & CC1024", "+#1024"."""
    return r"[ \t]*[+&][ \t]*(?:" + head + r")?"


# The words an order message may put before its code: "Order: 2-558", "কোড 2-558".
_ORDER_START = r"^\W*(?:(?:order|code|id|অর্ডার|কোড)\W*)?"
_WORD = re.compile(r"[A-Za-z]{1,12}")  # a keyword (or "#")


def _js_escape(s):
    return re.sub(r"([.*+?^${}()|\[\]\\/])", r"\\\1", s)


def _clean_line(line):
    line = line.translate(BN_DIGITS).replace(" ", " ")
    line = re.sub(r"[*~`]|(?<!\d)_|_(?!\d)", "", line)  # WhatsApp *bold* / _italic_ markers, not "2_152"
    return re.sub(r"\s+", " ", line).strip()


# ---- the style setting ----------------------------------------------------------------------------
DEFAULT_STYLE = {"kind": "prefix"}
MAX_LETTERS = 12
MAX_KEYWORDS = 6


def clean_style(value) -> dict:
    """A valid style from the shop setting; the prefix style for anything else.

    {"kind": "prefix"}
    {"kind": "letters", "letters": ["CC", "AB"], "sep": ""}   sep is how a code is written: "", "-" or " "
    {"kind": "keyword", "keywords": ["#", "Order"]}           # or words of A-Z; the first one is how a code is written
    """
    s = value if isinstance(value, dict) else {}
    if s.get("kind") == "letters":
        letters = []
        for item in s.get("letters") if isinstance(s.get("letters"), list) else []:
            item = str(item).strip().upper()
            if re.fullmatch(r"[A-Z]{1,4}", item) and item not in letters:
                letters.append(item)
        if letters:
            sep = s.get("sep") if s.get("sep") in ("", "-", " ") else ""
            return {"kind": "letters", "letters": letters[:MAX_LETTERS], "sep": sep}
    elif s.get("kind") == "keyword":
        words = []
        for item in s.get("keywords") if isinstance(s.get("keywords"), list) else []:
            item = str(item).strip()
            if (item == "#" or _WORD.fullmatch(item)) and item.lower() not in (w.lower() for w in words):
                words.append(item)
        if words:
            return {"kind": "keyword", "keywords": words[:MAX_KEYWORDS]}
    return dict(DEFAULT_STYLE)


def example(style) -> str:
    """How a code of the style looks: "2-558", "CC1023", "#1023"."""
    r = Reader(style)
    if r.kind == "prefix":
        return r.written("2", "558")
    return r.written(r.prefixes()[0] if r.prefixes() else "", "1023")


def describe(style) -> str:
    """"Prefix and number, like 2-558", "Letters and number (CC, AB), like CC1023", "# and number, like #1023"."""
    s = clean_style(style)
    if s["kind"] == "letters":
        return f"Letters and number ({', '.join(s['letters'])}), like {example(s)}"
    if s["kind"] == "keyword":
        return f"{' or '.join(s['keywords'])} and number, like {example(s)}"
    return f"Prefix and number, like {example(s)}"


_GUESS_PREFIX = re.compile(r"(" + PREFIX + r")\s*" + SEP + r"\s*(\d{1,6})")
_GUESS = re.compile(r"(#|[A-Za-z]{1,12})(" + _KEYWORD_GAP + r")(\d{1,6})")


def guess_style(text):
    """The style of a few codes typed one per line or with commas: (style, None), or (None, what is wrong).

    "2-558, 3-12" is the prefix style, "CC1023, AB-12" letters, "#1023" or "Order 1023" keyword.
    """
    examples = [e.strip() for e in re.split(r"[\n,]", (text or "").translate(BN_DIGITS)) if e.strip()]
    if not examples:
        return None, "Type two or three of your codes."
    kinds, letters, seps, keywords = {}, [], [], []
    for ex in examples:
        if _GUESS_PREFIX.fullmatch(ex):
            kinds.setdefault("prefix", ex)
            continue
        m = _GUESS.fullmatch(ex)
        if not m:
            return None, f"“{ex}” is not a code this app can read: a code is letters, a prefix, # or a word, then a number."
        word, gap, _ = m.groups()
        mark = re.sub(r"[ \t]", "", gap)
        if (word != "#" and len(word) <= 4 and (mark == "" or re.fullmatch(DASH, mark))
                and (" " not in gap or word.isupper())):
            kinds.setdefault("letters", ex)
            if word.upper() not in letters:
                letters.append(word.upper())
            seps.append("-" if mark else " " if gap else "")
        else:
            kinds.setdefault("keyword", ex)
            if word.lower() not in (k.lower() for k in keywords):
                keywords.append(word)
    if len(kinds) > 1:
        shown = ", ".join(f"{ex} is {_KIND_NAMES[k]}" for k, ex in kinds.items())
        return None, f"These codes are written in different styles ({shown}). Type codes of one style."
    kind = next(iter(kinds))
    if kind == "letters":
        sep = max(("", "-", " "), key=seps.count)
        return clean_style({"kind": "letters", "letters": letters, "sep": sep}), None
    if kind == "keyword":
        return clean_style({"kind": "keyword", "keywords": keywords}), None
    return dict(DEFAULT_STYLE), None


_KIND_NAMES = {"prefix": "prefix and number", "letters": "letters and number", "keyword": "# or a word and number"}


# ---- the reader -----------------------------------------------------------------------------------
class Reader:
    """Reads the codes of one style. Its patterns are valid in Python, JavaScript, Java and ICU.

    code / code_re      one code alone, as in a code list or a result file: group 1 prefix, 2 number
    chat_code / _re     a code in a chat with the codes after it: 1 prefix, 2 joiner, 3 number, 4 the rest
    js_chat_code        a code in a chat for JavaScript (flags "gim"): 1 prefix, 2 number, 3 the rest of
                        the list, read item by item with js_more_item ("__P__" in it replaced by the prefix)
    js_any_code         any code in a message (the WhatsApp script tells lists from single codes with it)
    js_code_parts       codes the WhatsApp script counts per prefix: group 1 prefix, 2 number

    A keyword-style code has no prefix: its group 1 is always empty.
    """

    def __init__(self, style=None):
        self.style = clean_style(style)
        self.kind = self.style["kind"]
        if self.kind == "prefix":
            core = r"(" + PREFIX + r")\s*" + SEP + r"\s*(\d{1,6})"
            self.code, self.chat_code = CODE, CHAT_CODE
            self.js_chat_code, self.js_more_item = JS_CHAT_CODE, JS_MORE_ITEM
            self.js_any_code = _START + r"(\d{1,3}[A-Za-z]?)\s*-\s*(\d{2,4})" + _END
            self.js_code_parts = _START + r"(" + PREFIX + r")\s*" + DASH + r"\s*(\d{1,6})" + _END
        else:
            if self.kind == "letters":
                letters = "(?:" + "|".join(sorted(self.style["letters"], key=len, reverse=True)) + ")"
                core = r"(" + letters + r")" + _LETTERS_GAP + r"(\d{1,6})"
                self.chat_code = (_START + r"(" + letters + r")(" + _LETTERS_GAP + r")(\d{1,6})" + _END
                                  + r"((?:" + _list_join(r"\1" + _LETTERS_GAP) + _NUMBER + r")*)")
                self.js_chat_code = (_START + core + _END
                                     + r"((?:" + _list_join(r"" + _LETTERS_GAP) + _NUMBER + r")*)")
                self.js_more_item = _list_join("__P__" + _LETTERS_GAP) + "(" + _NUMBER + ")"
                self.js_any_code = _START + core + _END
            else:
                words = "(?:" + "|".join(_js_escape(k) for k in sorted(self.style["keywords"], key=len, reverse=True)) + ")"
                self._head = words + _KEYWORD_GAP
                core = r"(?:" + self._head + r")?()(\d{1,6})"  # in a code list the keyword may be left out
                self.chat_code = (_START + words + r"()(" + _KEYWORD_GAP + r")(\d{1,6})" + _END
                                  + r"((?:" + _list_join(self._head) + _NUMBER + r")*)")
                self.js_more_item = _list_join(self._head) + "(" + _NUMBER + ")"
                self.js_any_code = _START + words + "()" + _KEYWORD_GAP + r"(\d{1,6})" + _END
                self.js_chat_code = self.js_any_code + r"((?:" + _list_join(self._head) + _NUMBER + r")*)"
            self.code = _START + core + _END
            self.js_code_parts = self.js_any_code
        self.code_re = re.compile(self.code, re.I)
        self.chat_code_re = re.compile(self.chat_code, re.I)
        self._list_re = re.compile(r"\s*" + core + r"[\s+\-]*", re.I)            # fullmatch: a code in a list
        self._line_re = re.compile(r"\s*" + core + r"[+\-]*(?=\s|$)", re.I)      # match: a list line's code
        self._order_re = re.compile(_ORDER_START + self.chat_code, re.I)
        self._more = {}

    def __repr__(self):
        return f"Reader({self.style})"

    # ---- one code ------------------------------------------------------------------------------
    def prefixes(self):
        """The letters of the letters style; [] for the other styles."""
        return list(self.style.get("letters", []))

    def written(self, prefix, number):
        """How a code is written in lists and file names: "2-558", "CC1023", "AB-12", "#1023", "Order 1023"."""
        if self.kind == "letters":
            letters = next((l for l in self.style["letters"] if l.lower() == prefix.lower()), prefix.upper())
            return f"{letters}{self.style['sep']}{number}"
        if self.kind == "keyword":
            word = self.style["keywords"][0]
            return f"{word}{'' if word == '#' else ' '}{number}"
        return f"{prefix}-{number}"

    def list_code(self, token):
        """A code typed in a code list, written the usual way: "2:152+" -> "2-152", "5A_380" -> "5A-380"
        (the prefix keeps its spelling), "cc 1023" -> "CC1023", "1023" -> "#1023". Anything else comes
        back as it is."""
        m = self._list_re.fullmatch((token or "").translate(BN_DIGITS))
        return self.written(m.group(1), m.group(2)) if m else (token or "").strip()

    def normalize(self, code):
        """The key of a code: "2 - 558", "2:558+" -> "2-558", "5A-380" -> "5a-380", "CC1023" -> "cc-1023",
        "#1023" -> "1023". Anything else comes back stripped and in lower case."""
        m = self._list_re.fullmatch((code or "").translate(BN_DIGITS))
        return norm(m.group(1), m.group(2)) if m else (code or "").strip().lower()

    def code_parts(self, code):
        """{"prefix": "4a", "number": 205} for "4a-205" (the WhatsApp script reads a chat further back
        when its codes start after this one), {"prefix": "", ...} for "#1023"; {} for anything else."""
        m = self._list_re.fullmatch((code or "").translate(BN_DIGITS))
        return {"prefix": m.group(1).lower(), "number": int(m.group(2))} if m else {}

    def sort_key(self, code):
        """2-558 < 2-560 < 3-12 < 5a-380; cc-12 < cc-1023; 12 < 1023."""
        m = self._list_re.fullmatch((code or "").translate(BN_DIGITS))
        if not m:
            return (10**6, code or "", 0)
        p = re.match(r"(\d*)(.*)", m.group(1))
        return (int(p.group(1) or 0), p.group(2).lower(), int(m.group(2)))

    def split_list_line(self, line):
        """A code-list line -> (code, the rest): "2-558<TAB>Realme C85" -> ("2-558", "Realme C85"),
        "Order 1023 Realme" -> ("Order 1023", "Realme"). A line that does not start with a code gives
        its first word as the code, as written."""
        line = (line or "").strip()
        m = self._line_re.match(line.translate(BN_DIGITS))
        if m:
            return self.written(m.group(1), m.group(2)), line[m.end():].strip()
        code, _, rest = line.replace("\t", " ", 1).partition(" ")
        return self.list_code(code), rest.strip()

    # ---- codes in a message -----------------------------------------------------------------------
    def more_numbers(self, prefix, more):
        """The numbers in group 4 of chat_code: "+2-153-154" -> ["153", "154"]."""
        if not more:
            return []
        if self.kind == "prefix":
            return _more_re(prefix).findall(more)
        key = prefix.lower()
        r = self._more.get(key)
        if r is None:
            head = _js_escape(prefix) + _LETTERS_GAP if self.kind == "letters" else self._head
            r = self._more[key] = re.compile(_list_join(head) + "(" + _NUMBER + ")", re.I)
        return r.findall(more)

    def match_codes(self, m):
        """The codes of one chat_code match: "2-151-152" -> ["2-151", "2-152"]."""
        numbers = [m.group(3)] + self.more_numbers(m.group(1), m.group(4))
        return list(dict.fromkeys(norm(m.group(1), n) for n in numbers))

    def code_matches(self, text):
        """The codes written in a chat message, as chat_code_re matches."""
        text = text or ""
        if self.kind != "prefix":
            yield from self.chat_code_re.finditer(text)
            return
        pos = 0
        while True:
            m = self.chat_code_re.search(text, pos)
            if not m:
                return
            line = text[text.rfind("\n", 0, m.start()) + 1:m.start()]
            if m.group(2) in "+&:;" and re.search(r"[0-9A-Za-z]", line):
                pos = m.end(1)  # "redmi 12 8+128", "at 10:30": not a code
                continue
            yield m
            pos = m.end()

    def codes_in_text(self, text):
        """The keys of the codes written in a message: "2-151+152 and 3-12" -> ["2-151", "2-152", "3-12"]."""
        out = []
        for m in self.code_matches((text or "").translate(BN_DIGITS)):
            out += self.match_codes(m)
        return list(dict.fromkeys(out))

    def order_codes(self, text):
        """The codes an order message starts with (one of its first three lines): ["6-872"],
        ["3-163", "3-164"] for "3-163+164", ["1023"] for "Order: #1023", or []."""
        lines = [l for l in (_clean_line(l) for l in (text or "").splitlines()) if l]
        for line in lines[:3]:
            m = self._order_re.match(line)
            if m:
                return self.match_codes(m)
        return []

    def code_pattern(self, code):
        """Regex (valid in both Python and JavaScript) matching a message that is about `code`.

        '2-152' matches 'Realme 2-152', '2 - 152', '#2-152,', '2:152+', '2_152', '2-151-152', '2+151-2+152'
        and '2-151+152', but not '12-152' or '2-1520'. 'CC1023' matches 'cc 1023' and 'CC1022+1023';
        '#1023' matches 'Order #1023' and '#1022+1023' but not '#10234'.
        """
        if self.kind == "prefix":
            return _prefix_code_pattern(code)
        m = self._list_re.fullmatch((code or "").translate(BN_DIGITS))
        if not m:
            return _START + "(?:" + _js_escape((code or "").strip()) + ")" + _END
        head = _js_escape(m.group(1)) + _LETTERS_GAP if self.kind == "letters" else self._head
        last = "0*" + str(int(m.group(2)))
        join = _list_join(head)
        in_list = head + r"\d{1,6}" + _END + "(?:" + join + _NUMBER + ")*?" + join + last
        return _START + "(?:" + head + last + "|" + in_list + ")" + _END


# ---- helpers that do not depend on the style -------------------------------------------------
def norm(prefix, number):
    """The key of a code: ("2", "0558") -> "2-558", ("5A", "380") -> "5a-380", ("", "1023") -> "1023"."""
    return f"{prefix.lower()}-{int(number)}" if prefix else str(int(number))


def split_code(code):
    """A key back to its parts: "2-558" -> ("2", 558), "cc-1023" -> ("cc", 1023), "1023" -> ("", 1023).
    ValueError for anything else."""
    prefix, _, number = code.rpartition("-")
    return prefix, int(number)


@lru_cache(maxsize=256)
def _more_re(prefix):
    return re.compile(_join(re.escape(prefix)) + "(" + _MORE_NUM + ")", re.I)


def _prefix_list_code(token):
    m = re.fullmatch(r"\s*(" + PREFIX + r")\s*" + SEP + r"\s*(\d{1,6})[\s+\-]*", (token or "").translate(BN_DIGITS))
    return f"{m.group(1)}-{m.group(2)}" if m else (token or "").strip()


def _prefix_code_pattern(code):
    code = _prefix_list_code(code)
    parts = [_js_escape(p) for p in re.split(r"\s*" + SEP + r"\s*", code) if p]
    sep = r"\s*" + SEP + r"\s*"
    if len(parts) != 2 or not parts[1].isdigit():
        return _START + r"(?:" + sep.join(parts) + r")" + _END
    prefix, number = parts
    last = "0*" + str(int(number))
    one = prefix + sep + last
    if int(number) < 10:  # "3-163+4-165" is not about 3-4
        last += r"(?![ \t]*" + SEP + r"[ \t]*\d)"
    in_list = (prefix + sep + r"\d{1,6}" + _END + "(?:" + _join(prefix) + _MORE_NUM + ")*?"
               + _join(prefix) + last)
    return _START + r"(?:" + one + "|" + in_list + r")" + _END


# ---- the shop's style -------------------------------------------------------------------------------
_reader = Reader(DEFAULT_STYLE)


def reader() -> Reader:
    """The reader of the shop's code style. A job that runs for a while keeps the one it started with."""
    return _reader


def style() -> dict:
    return dict(_reader.style)


def set_style(value) -> Reader:
    """Reads codes in this style from now on (the shop setting "code_style"; None is the prefix style)."""
    global _reader
    new = clean_style(value)
    if new != _reader.style:
        _reader = Reader(new)
    return _reader


# The shop's style, for the tools that read codes without holding a reader.
def more_numbers(prefix, more):
    return _reader.more_numbers(prefix, more)


def match_codes(m):
    return _reader.match_codes(m)


def code_matches(text):
    return _reader.code_matches(text)


def codes_in_text(text):
    return _reader.codes_in_text(text)


def order_codes(text):
    return _reader.order_codes(text)


def list_code(token):
    return _reader.list_code(token)


def normalize(code):
    return _reader.normalize(code)


def code_pattern(code):
    return _reader.code_pattern(code)
