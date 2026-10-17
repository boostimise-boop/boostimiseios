"""Free, offline model detection: read the phone model name from the chat text posted with the
photos (order message, caption, messages below) and from text printed inside the photos (OCR):
names ("Galaxy A06"), model numbers ("SM-A065F", "V2111" - see model_codes.py) and brand logos."""
import io
import re
import threading

from PIL import Image

from .codes import strip_codes
from .identifier import Identification, clean_model_name
from .model_codes import names_from_codes, resolve

# brand words -> the name written in the result ("samsung galaxy" -> "samsung")
_BRANDS = [
    (("apple", "iphone"), "iphone"), (("iphone",), "iphone"), (("i", "phone"), "iphone"),
    (("samsung", "galaxy"), "samsung"), (("samsung",), "samsung"), (("galaxy",), "samsung"),
    (("xiaomi", "redmi"), "redmi"), (("xiaomi", "poco"), "poco"), (("xiaomi", "mi"), "mi"),
    (("xiaomi",), "xiaomi"), (("redmi",), "redmi"), (("poco",), "poco"), (("mi",), "mi"),
    (("realme",), "realme"), (("narzo",), "realme narzo"), (("oppo",), "oppo"), (("vivo",), "vivo"),
    (("iqoo",), "iqoo"), (("infinix",), "infinix"), (("tecno",), "tecno"), (("itel",), "itel"),
    (("one", "plus"), "oneplus"), (("oneplus",), "oneplus"), (("nokia",), "nokia"),
    (("motorola",), "motorola"), (("moto",), "moto"), (("huawei",), "huawei"), (("honor",), "honor"),
    (("google", "pixel"), "google pixel"), (("pixel",), "google pixel"), (("symphony",), "symphony"),
    (("walton",), "walton"), (("lava",), "lava"), (("nothing", "phone"), "nothing phone"),
    (("asus",), "asus"), (("zte",), "zte"), (("nord",), "oneplus nord"),
    (("walton", "nexg"), "nexg"), (("nexg",), "nexg"), (("walton", "xanon"), "xanon"), (("xanon",), "xanon"),
    # series names that give the brand away on their own: "Spark 20 pro", "Reno 10", "Innova 40"
    (("spark",), "tecno spark"), (("camon",), "tecno camon"), (("pova",), "tecno pova"),
    (("reno",), "oppo reno"), (("innova",), "symphony innova"), (("helio",), "symphony helio"),
]
_BRANDS.sort(key=lambda b: -len(b[0]))  # longest match first

# words that can follow a brand inside a model name
_WORDS = {
    "pro", "max", "plus", "ultra", "lite", "mini", "prime", "neo", "power", "play", "note", "hot", "spark",
    "camon", "pova", "smart", "zero", "fold", "flip", "edge", "fe", "se", "xr", "xs", "x", "5g", "4g",
    "nord", "ce", "go", "core", "narzo", "reno", "find", "nova", "mate", "turbo", "star", "gt", "active",
    "air", "vip", "pop", "phantom", "innova", "helio", "xanon", "nexg",
    "e", "s", "t", "r", "i", "+", "pro+", "a", "c", "y", "v", "k", "f", "m", "p", "g", "z",
}
_MODEL_TOKEN = re.compile(r"^(?:[a-z]{0,6}\d{1,4}[a-z]{0,4}\+?|[a-z]\d{0,4})$")
_NUM_FIRST = re.compile(r"^\d{1,4}[a-z]?\+?$")                   # "13", "60i", "9+" - not OCR noise like "4cb"
_LETTER_FIRST = re.compile(r"^[a-z]{1,4}\d{1,5}[a-z]{0,4}\+?$")  # "a70", "v30e", "x6725b" - not "rrcep17"
_NET = ("5g", "4g")
_YEAR = re.compile(r"^20[0-3]\d$")                                # "huawei y7 2019", "moto g play 2024"
_SERIES_START = {"note", "hot", "spark", "camon", "pova", "narzo", "nord", "reno", "find", "nova", "mate",
                 "galaxy", "redmi", "poco", "iphone", "pixel", "smart", "zero", "edge"}
# brands whose models are never a bare number: "tecno 3" is OCR noise, "redmi 10" is a phone
_NEEDS_SERIES = {"tecno", "infinix", "itel", "samsung", "vivo", "oppo", "poco", "symphony", "walton", "nexg",
                 "motorola", "moto", "huawei", "lava", "zte", "asus"}
# words people write around a model ("A70 cover", "y21 er case", "model no a70")
_FILLER = {"cover", "covers", "case", "back", "er", "model", "mobile", "set", "phone", "for", "r",
           "no", "name", "number"}
_OS_WORD = re.compile(r"^(ui|os)\d*$")   # "realme UI 6.0", "Funtouch OS 14" are software, not models

# OCR often drops spaces: "RedmiNote4", "iPhone13ProMax", "SamsungA70", "MiA2", "GTNeo2", "NEXGN28"
_PEEL = sorted({
    "samsung", "galaxy", "apple", "iphone", "xiaomi", "redmi", "poco", "realme", "narzo", "oppo", "vivo",
    "infinix", "tecno", "itel", "oneplus", "nokia", "motorola", "huawei", "honor", "google", "pixel",
    "note", "hot", "spark", "camon", "pova", "nord", "reno", "nova", "mate", "pro", "max", "plus", "ultra",
    "lite", "mini", "mi", "smart", "zero", "walton", "symphony", "lava",
    "gt", "neo", "go", "pop", "nexg", "xanon", "innova", "helio",
    # words that often follow a model in shop listings ("MiA2LiteDualSim")
    "dual", "sim", "global", "version", "snapdragon", "smartphone", "mobile", "price",
}, key=len, reverse=True)
# "13promax", "x8dpi", "v20se", "s20fe", "6air"
_DIGIT_SUFFIX = re.compile(r"(\d)(pro|max|plus|ultra|lite|mini|dpi|prime|power|play|edge|(?:se|fe|neo|air|vip)(?![a-z]))")
_GLUED_5G = re.compile(r"([a-z\d])([45]g)(?![0-9b])")               # "note405g", "curve5g" (not "64gb")
# label/listing noise: "S/N:61...", "IMEI1:86...", "8/256GB", "256+8R", "64GB", "50MP", "5000mAh"
_NOISE = re.compile(r"\bs\s*/\s*n\b|\bimei\s*\d?|\b\d{1,2}\s*[/+]\s*\d{2,4}\s*(gb|ge|g|r)?\b"
                    r"|\b\d{2,4}\s*[/+]\s*\d{1,2}\s*(gb|g|r)?\b|\b\d+\s*(gb|tb|mb|mp|mah|hz|mm|w)\b")
_O_FOR_ZERO = re.compile(r"^([a-z]{0,2})(\d[\do]*)([a-z]?)$")        # OCR "g5o" -> "g50", "6oi" -> "60i"


def _split_glued(tok):
    out = []
    while True:
        for w in _PEEL:
            rest = tok[len(w):]
            if tok.startswith(w) and rest and (rest[0].isdigit() or rest.startswith(tuple(_PEEL))
                                                or _MODEL_TOKEN.match(rest)):
                out.append(w)
                tok = rest
                break
        else:
            out.append(tok)
            return out


def _fix_ocr_digits(tok):
    m = _O_FOR_ZERO.match(tok)
    return m.group(1) + m.group(2).replace("o", "0") + m.group(3) if m else tok


def _tokens(text):
    text = strip_codes(text).lower()            # never read the product code as a model
    text = _DIGIT_SUFFIX.sub(r"\1 \2", text)
    text = _NOISE.sub(" ", text)
    text = _GLUED_5G.sub(r"\1 \2 ", text)
    return [_fix_ocr_digits(p) for t in re.sub(r"[^a-z0-9+]+", " ", text).split() for p in _split_glued(t)]


_SPEC = re.compile(r"^\d+(gb|tb|mb|mp|mah|w|hz|mm)$")   # "64gb", "50mp", "5000mah" are specs, not models


def _is_model_token(t):
    if t in _WORDS:
        return True
    return not _SPEC.match(t) and bool(_NUM_FIRST.match(t) or _LETTER_FIRST.match(t) or re.fullmatch(r"[a-z]", t))


def _has_number(tokens):
    return any(ch.isdigit() for t in tokens for ch in t)


def _brand_at(toks, i):
    for words, name in _BRANDS:
        if tuple(toks[i:i + len(words)]) == words:
            return name, len(words)
    return None


def models_with_brand(text):
    """'Samsung Galaxy A70 5G' -> ['samsung a70 5g'] ; 'I phone 17pro max cover' -> ['iphone 17 pro max']"""
    out = []
    for line in (text or "").splitlines() or [""]:     # a model name never runs onto the next line
        out += _models_in_line(_tokens(line))
    return out


def _models_in_line(toks):
    out, i = [], 0
    while i < len(toks):
        b = _brand_at(toks, i)
        if not b:
            i += 1
            continue
        name, n = b
        j = i + n
        if j < len(toks) and _OS_WORD.match(toks[j]):
            i = j + 1
            continue
        while j < len(toks) and toks[j] in _FILLER:     # "vivo er Y21" -> "vivo y21"
            j += 1
        model = []
        # a series name we don't know, if a model number follows: "Walton NEXG N28", "Symphony Innova 30"
        if (j + 1 < len(toks) and not _is_model_token(toks[j]) and toks[j].isalpha() and 2 <= len(toks[j]) <= 10
                and toks[j] not in _FILLER and not _brand_at(toks, j)
                and _is_model_token(toks[j + 1]) and _has_number([toks[j + 1]])):
            model.append(toks[j])
            j += 1
        extra_word = True
        has_num = False
        while j < len(toks) and len(model) < 5:
            t = toks[j]
            if not _is_model_token(t):
                # one unknown word inside the name if a letter+number part follows: "Pova Curve 5G"
                nxt = toks[j + 1] if j + 1 < len(toks) else ""
                if (extra_word and model and not has_num and t.isalpha() and 2 <= len(t) <= 10
                        and t not in _FILLER and not _brand_at(toks, j)
                        and _is_model_token(nxt) and re.search(r"[a-z]", nxt) and _has_number([nxt])):
                    extra_word = False
                    model.append(t)
                    j += 1
                    continue
                break
            nb = _brand_at(toks, j)
            if nb and nb[0] not in (name, "samsung") and model:
                break                                   # another brand starts
            if _has_number([t]):
                if t in _NET:                           # "5g" ends a name
                    if model:
                        model.append(t)
                        j += 1
                    break
                if has_num and not _YEAR.match(t):
                    break                               # a second number: something else starts
                has_num = True
            model.append(t)
            j += 1
        if model and model[0] == "galaxy":
            model = model[1:]
        while model and len(model[-1]) == 1 and model[-1].isalpha() and model[-1] not in ("x", "s", "e"):
            model.pop()                                 # "iphone 13 i ..." -> "iphone 13"
        joined = []
        for t in model:                                 # "vivo y 18" -> "vivo y18"
            if joined and name != "iphone" and len(joined[-1]) == 1 and joined[-1].isalpha() and t.isdigit():
                joined[-1] += t
            elif not joined or joined[-1] != t:         # "5g 5g" -> "5g"
                joined.append(t)
        model = joined
        ok = _has_number(model) or (name == "iphone" and model and model[0] in ("x", "xr", "xs", "se"))
        bare = bool(model) and model[0].isdigit() and len(model[0]) != 4   # "vivo 1906" is an old model number
        if (name in _NEEDS_SERIES and bare) or (name == "symphony helio" and not bare):
            ok = False                                  # "tecno 3"; "Helio G85" is a chip, not a phone
        if ok:
            out.append(f"{name} {' '.join(model)}")
        i = max(j, i + 1)
    return out


def model_without_brand(text):
    """A line that is only a model: 'A70', 'Note 7 pro', 'Y21 cover', 'c55 5g'."""
    stripped = strip_codes(text).strip()
    if not stripped or len(stripped) > 30:
        return None
    toks = [t for t in _tokens(stripped) if t not in _FILLER]
    if not toks or len(toks) > 5 or not all(_is_model_token(t) for t in toks) or not _has_number(toks):
        return None
    first = toks[0]
    if not (re.match(r"^[a-z]{1,4}\d", first) or (first in _SERIES_START and len(toks) > 1)):
        return None                                     # e.g. "350tk", "2 pcs"
    return " ".join(toks)


def _name_in(text):
    """(shop name, known) for the model written in a text, or None. known is False for a model number
    that is not in the phone list ("vivo v2111"), True for one that is, None for a plain name."""
    found = models_with_brand(text)
    if found:
        return resolve(clean_model_name(found[0]), text)
    numbers = names_from_codes(text)
    return (clean_model_name(numbers[0]), True) if numbers else None


def _short(text):
    return repr(" ".join(text.split())[:60])


# "Shot on vivo V30", "AI QUAD CAMERA": the phone that took the picture, not always the customer's phone
_WATERMARK_WORDS = re.compile(r"\b(?:shot|captured)\s*(?:on|by|with)\b|camera", re.I)


def _watermark(rows, k):
    near = " ".join(r[0] for r in rows[max(0, k - 1):k + 2])
    if _WATERMARK_WORDS.search(near):
        return True
    _, y, h = rows[k]
    return y > 0.85 and h < 0.05 and len(rows) <= 6     # small text in the bottom corner of a photo


def _upscale(jpeg, factor=2, limit=2600):
    im = Image.open(io.BytesIO(jpeg)).convert("RGB")
    f = min(factor, limit / max(im.size))
    if f <= 1.05:
        return None
    im = im.resize((round(im.width * f), round(im.height * f)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=92)
    return buf.getvalue()


def _tiles(jpeg, n=2, overlap=0.15, target=1800):
    """The photo cut in n x n overlapping parts, each enlarged: small print (the label on a phone's
    back, the side of a box) becomes big enough to read."""
    im = Image.open(io.BytesIO(jpeg)).convert("RGB")
    tw, th = im.width / n, im.height / n
    for r in range(n):
        for c in range(n):
            box = (round(max(0, (c - overlap) * tw)), round(max(0, (r - overlap) * th)),
                   round(min(im.width, (c + 1 + overlap) * tw)), round(min(im.height, (r + 1 + overlap) * th)))
            tile = im.crop(box)
            f = min(3, target / max(tile.size))
            if f > 1.05:
                tile = tile.resize((round(tile.width * f), round(tile.height * f)), Image.LANCZOS)
            buf = io.BytesIO()
            tile.save(buf, "JPEG", quality=92)
            yield buf.getvalue()


class TextReader:
    """Finds a written model name for a code. Thread-safe (OCR runs one image at a time)."""

    def __init__(self, use_ocr=True, log=print):
        self.use_ocr = use_ocr
        self.log = log
        self._ocr = None
        self._lock = threading.Lock()

    def _read_photo(self, jpeg):
        """Text rows inside a photo, top to bottom: [(text, middle, height)], middle and height
        as fractions of the photo's height. Boxes on one line are joined left to right."""
        if not self.use_ocr or not jpeg:
            return []
        with self._lock:
            if self._ocr is None:
                try:
                    from rapidocr_onnxruntime import RapidOCR
                    self._ocr = RapidOCR()
                except Exception as e:   # not installed / broken: keep working with chat text only
                    self.use_ocr = False
                    self.log(f"Photo text reading (OCR) is not available: {e}")
                    return []
            try:
                result, _ = self._ocr(jpeg)
            except Exception:
                return []
        try:
            height = Image.open(io.BytesIO(jpeg)).height
        except Exception:
            return []
        boxes = []
        for pts, text, score in result or []:
            if score >= 0.5:
                ys = [p[1] for p in pts]
                boxes.append(((min(ys) + max(ys)) / 2, max(ys) - min(ys), min(p[0] for p in pts), text))
        rows = []   # [middle, height, [(left, text)]]
        for mid, h, left, text in sorted(boxes):
            if rows and abs(mid - rows[-1][0]) < 0.5 * min(h, rows[-1][1]):
                rows[-1][2].append((left, text))
            else:
                rows.append([mid, h, [(left, text)]])
        return [(" ".join(t for _, t in sorted(parts)), mid / height, h / height) for mid, h, parts in rows]

    def _in_photo(self, rows, small_print=False):
        """(name, kind, text) for the model in one photo's text rows, or None. kind: 'sure' (a written
        name or a known model number), 'number?' (a model number not in the phone list), 'watermark'."""
        texts = [r[0] for r in rows]
        whole = "\n".join(texts)
        weak = None
        for k, text in enumerate(texts):
            # a name can be split over two lines: "TECNO" logo above "SPARK 8 Pro"
            for lines in ([k], [k, k + 1]) if k + 1 < len(texts) else ([k],):
                cand = " ".join(texts[i] for i in lines)
                found = models_with_brand(cand)
                if not found:
                    continue
                name, known = resolve(clean_model_name(found[0]), whole)
                if known is False:
                    weak = weak or (name, "number?", cand)
                elif not small_print and any(_watermark(rows, i) for i in lines):
                    weak = weak or (name, "watermark", cand)
                else:
                    return name, "sure", cand
                break
        numbers = names_from_codes(whole)
        if numbers:
            return clean_model_name(numbers[0]), "sure", next((t for t in texts if names_from_codes(t)), whole)
        return weak

    @staticmethod
    def _logo(rows):
        for text, _, _ in rows:
            toks = _tokens(text)
            b = _brand_at(toks, 0) if toks else None
            if b and b[1] == len(toks):
                return b[0]
        return None

    def identify(self, code, images, texts, brand_only=True, shared=(), seen=None):
        """texts: chat texts of the code, closest first (order message, captions, messages below).
        shared: chat texts that sit closer to another code - used only when nothing else is found.
        seen: a list that gets the text read in the photos (for the AI's hint).
        Returns Identification, or None when no model name is written anywhere.
        brand_only: if only a brand logo is readable ("REDMI" on the phone's back), return "redmi ?"."""
        later = []   # weaker answers, used when nothing better turns up: (rank, order, Identification)

        def keep(rank, ident):
            later.append((rank, len(later), ident))

        for t in texts:
            hit = _name_in(t)
            if hit and hit[1] is not False:
                return Identification(hit[0], "high", f"written in chat: {_short(t)}")
            if hit:
                keep(3, Identification(f"{hit[0]} ?", "low", f"model number not in the phone list: {_short(t)}"))

        def photo_hit(hit, where):
            name, kind, text = hit
            if kind == "sure":
                return Identification(name, "high", f"{where}: {_short(text)}")
            if kind == "number?":
                keep(3, Identification(f"{name} ?", "low", f"model number not in the phone list, {where}: "
                                                           f"{_short(text)}"))
            else:
                keep(4, Identification(name, "low", f"camera watermark, {where}: {_short(text)} - "
                                                     f"the photo may be taken with another phone"))
            return None

        # text printed in the photos; small text gets a second, enlarged look
        logo = None
        for enlarge in (False, True):
            for k, img in enumerate(images, 1):
                rows = self._read_photo(_upscale(img) if enlarge else img)
                if seen is not None and not enlarge:
                    seen += [r[0] for r in rows]
                hit = self._in_photo(rows)
                if hit:
                    found = photo_hit(hit, f"text in photo {k}")
                    if found:
                        return found
                if not logo:
                    b = self._logo(rows)
                    logo = (b, k) if b else None

        for t in texts:
            for line in t.splitlines():
                found = model_without_brand(line)
                if found:
                    both = models_with_brand(f"{logo[0]} {found}") if logo else []
                    if both:                                      # "Y21" written, "VIVO" on the phone
                        return Identification(clean_model_name(both[0]), "medium",
                                              f"written in chat: {line[:40]!r}, brand in photo {logo[1]}")
                    return Identification(clean_model_name(found), "medium",
                                          f"written in chat (no brand): {line[:40]!r}")

        # a brand is printed on the phone: its model number may be in the small print next to it
        if logo:
            for tile in _tiles(images[logo[1] - 1]):
                hit = self._in_photo(self._read_photo(tile), small_print=True)
                if hit:
                    found = photo_hit(hit, f"small print in photo {logo[1]}")
                    if found:
                        return found

        for t in shared:
            hit = _name_in(t)
            if hit:
                keep(2, Identification(hit[0] + (" ?" if hit[1] is False else ""), "low",
                                       f"written next to another code: {_short(t)}"))
                break
        if later:
            return min(later)[2]
        if logo and brand_only:
            return Identification(f"{logo[0]} ?", "low", f"only the brand '{logo[0]}' is printed (photo {logo[1]})")
        return None
