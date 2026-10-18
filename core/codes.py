"""Product codes as people type them. Every tool reads codes through this file.

All of these mean the code 2-152:

    2-152  2:152  2_152  2+152  2;152  2&152  2:152+  2:152-  2+152-

and several codes of one list may be written together:

    2-151+152  2-151-152  2-151+2-152  2:152+2-153  2+152-2+153  2-151 & 2-152

A code joined by + & : or ; must start its line ("redmi 12 8+128" is memory, "at 10:30" a time).
android/.../core/ProductCode.kt and ios/.../Core/ProductCode.swift are ports of this file.
"""
import re
from functools import lru_cache

PREFIX = r"\d{1,3}[A-Za-z]{0,2}"
DASH = r"[-‐-―_]"  # joins the two halves of a code anywhere
LOOSE = r"[+&:;]"            # joins them only in a code that starts a line
SEP = r"(?:" + DASH + "|" + LOOSE + ")"
_END = r"(?![0-9A-Za-z])"
# A number after a code that adds one more code of the same list. A lone digit followed by a joiner
# starts a code of another list instead ("3-163+4-165" is 3-163 and 4-165).
_MORE_NUM = r"(?!\d[ \t]*" + SEP + r"[ \t]*\d)\d{1,6}" + _END


def _join(prefix):
    """What comes between two codes of a list whose prefix matches the regex `prefix`: "+164",
    "+2-164", " & 2-164", "-164", "-2+164" (with spaces around "-" only before a full code)."""
    head = prefix + r"\s*" + SEP + r"\s*"
    return (r"(?:[ \t]*[+&][ \t]*(?:" + head + r")?|[ \t]*" + DASH + r"[ \t]*" + head
            + "|" + DASH + "(?:" + head + ")?)")


# One code alone, as in a code list or a result file: group 1 prefix, group 2 number.
CODE = r"(?<![0-9A-Za-z])(" + PREFIX + r")\s*" + SEP + r"\s*(\d{1,6})" + _END
CODE_RE = re.compile(CODE)
# A code written in a chat with the codes that follow it: 1 prefix, 2 joiner, 3 number, 4 the rest of
# the list (read with more_numbers). Other patterns may be put in front of it if they capture nothing.
CHAT_CODE = (r"(?<![0-9A-Za-z])(" + PREFIX + r")\s*(" + SEP + r")\s*(\d{1,6})" + _END
             + r"((?:" + _join(r"\1") + _MORE_NUM + r")*)")
CHAT_CODE_RE = re.compile(CHAT_CODE, re.I)
# CHAT_CODE in JavaScript (flags "gm"), with the line-start rule built in.
JS_CHAT_CODE = (r"(?<![0-9A-Za-z])(" + PREFIX + r")\s*(?:" + DASH + r"|(?<=^[^0-9A-Za-z\n]*[0-9A-Za-z]+\s*)"
                + LOOSE + r")\s*(\d{1,6})" + _END + r"((?:" + _join(r"\1") + _MORE_NUM + r")*)")
# One item of group 4 in JavaScript; "__P__" is replaced by the code's prefix before use (flags "gi").
JS_MORE_ITEM = _join("__P__") + "(" + _MORE_NUM + ")"
BN_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")


def norm(prefix, number):
    return f"{prefix.lower()}-{int(number)}"


def split_code(code):
    prefix, number = code.rsplit("-", 1)
    return prefix, int(number)


@lru_cache(maxsize=256)
def _more_re(prefix):
    return re.compile(_join(re.escape(prefix)) + "(" + _MORE_NUM + ")", re.I)


def more_numbers(prefix, more):
    """The numbers in group 4 of CHAT_CODE: "+2-153-154" -> ["153", "154"]."""
    return _more_re(prefix).findall(more or "")


def match_codes(m):
    """The codes of one CHAT_CODE match: "2-151-152" -> ["2-151", "2-152"]."""
    numbers = [m.group(3)] + more_numbers(m.group(1), m.group(4))
    return list(dict.fromkeys(norm(m.group(1), n) for n in numbers))


def code_matches(text):
    """The codes written in a chat message, as CHAT_CODE_RE matches."""
    text = text or ""
    pos = 0
    while True:
        m = CHAT_CODE_RE.search(text, pos)
        if not m:
            return
        line = text[text.rfind("\n", 0, m.start()) + 1:m.start()]
        if m.group(2) in "+&:;" and re.search(r"[0-9A-Za-z]", line):
            pos = m.end(1)  # "redmi 12 8+128", "at 10:30": not a code
            continue
        yield m
        pos = m.end()


def codes_in_text(text):
    out = []
    for m in code_matches((text or "").translate(BN_DIGITS)):
        out += match_codes(m)
    return list(dict.fromkeys(out))


def list_code(token):
    """A code typed in a code list, written the usual way: "2:152+" -> "2-152", "5A_380" -> "5A-380"
    (the prefix keeps its spelling). Anything else comes back as it is."""
    m = re.fullmatch(r"\s*(" + PREFIX + r")\s*" + SEP + r"\s*(\d{1,6})[\s+\-]*", (token or "").translate(BN_DIGITS))
    return f"{m.group(1)}-{m.group(2)}" if m else (token or "").strip()


def _js_escape(s):
    return re.sub(r"([.*+?^${}()|\[\]\\/])", r"\\\1", s)


def code_pattern(code):
    """Regex (valid in both Python and JavaScript) matching a message that is about `code`.

    '2-152' matches 'Realme 2-152', '2 - 152', '#2-152,', '2:152+', '2_152', '2-151-152', '2+151-2+152'
    and '2-151+152', but not '12-152' or '2-1520'.
    """
    code = list_code(code)
    parts = [_js_escape(p) for p in re.split(r"\s*" + SEP + r"\s*", code) if p]
    sep = r"\s*" + SEP + r"\s*"
    if len(parts) != 2 or not parts[1].isdigit():
        return r"(?<![0-9A-Za-z])(?:" + sep.join(parts) + r")" + _END
    prefix, number = parts
    last = "0*" + str(int(number))
    one = prefix + sep + last
    if int(number) < 10:  # "3-163+4-165" is not about 3-4
        last += r"(?![ \t]*" + SEP + r"[ \t]*\d)"
    in_list = (prefix + sep + r"\d{1,6}" + _END + "(?:" + _join(prefix) + _MORE_NUM + ")*?"
               + _join(prefix) + last)
    return r"(?<![0-9A-Za-z])(?:" + one + "|" + in_list + r")" + _END
