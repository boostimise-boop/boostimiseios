"""Product-code handling: parse the codes .txt file, find codes inside chat text,
and read/write the result file ("5a-403 samsung a70")."""
import os

# 5a-403, 5A-403, 5a - 403, 5a_403, 5a:403 ... and the codes typed in a chat ("2-151-152"): core/codes.py
from core.codes import CODE_RE, code_matches, codes_in_text, norm, split_code  # noqa: F401

# Results that mean "not identified yet" (these codes are searched again on the next run)
NOT_DONE = {"", "not found", "no image", "unknown"}


def strip_codes(text):
    """The text with the codes in it replaced by spaces."""
    text = text or ""
    for m in reversed(list(code_matches(text))):
        text = text[:m.start()] + " " + text[m.end():]
    return text


def is_done(model):
    """A model counts as finished unless it is empty, a status word, or only a guess ("redmi ?")."""
    model = (model or "").strip().lower()
    return model not in NOT_DONE and not model.endswith("?")


class CodeList:
    """The codes file, kept line by line so the output file has the same layout."""

    def __init__(self, path):
        self.path = path
        self.lines = []   # (raw line, code or None)
        self.blocks = {}  # prefix -> [codes in file order]
        self.label = {}   # code -> spelling used in the file
        with open(path, encoding="utf-8-sig", errors="replace") as f:
            for raw in f.read().splitlines():
                s = raw.strip()
                m = CODE_RE.match(s) if s else None
                if not m or m.start() != 0:
                    self.lines.append((raw.rstrip(), None))
                    continue
                code = norm(m.group(1), m.group(2))
                self.lines.append((raw.rstrip(), code))
                if code not in self.label:
                    self.label[code] = s[: m.end()]
                    self.blocks.setdefault(m.group(1).lower(), []).append(code)

    @property
    def codes(self):
        return [c for p in self.blocks for c in self.blocks[p]]

    def prefix_range(self, prefix):
        nums = [split_code(c)[1] for c in self.blocks[prefix]]
        return min(nums), max(nums)


def read_results(path):
    """Read an existing result file -> {code: model}."""
    res = {}
    if not path or not os.path.exists(path):
        return res
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        for raw in f.read().splitlines():
            s = raw.strip()
            m = CODE_RE.match(s)
            if m and m.start() == 0:
                res[norm(m.group(1), m.group(2))] = s[m.end():].strip()
    return res


def write_results(path, codelist, models):
    """Write "<code> <model>" for every code, keeping the codes file's order and blank lines."""
    out = []
    for raw, code in codelist.lines:
        if code is None:
            out.append(raw)
        else:
            out.append(f"{codelist.label[code]} {models.get(code, '')}".rstrip())
    text = "\n".join(out) + "\n"
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except PermissionError:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
