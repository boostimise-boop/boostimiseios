"""Model numbers -> the name the shop uses: "SM-A065F" -> "samsung a06", "V2111" -> "vivo y21".

About-phone screenshots, box labels and the small print on a phone's back often show only the
model number. Samsung numbers follow a pattern (rules below). Other brands need the phone list
assets/phone_models.tsv ("KEY<TAB>brand<TAB>name" lines, made from Google Play's list of
Android devices by running this file: python -m tools.model_detect.model_codes supported_devices.csv).
"""
import csv
import io
import re
import sys
import threading

from core.paths import ASSETS

from .codes import strip_codes

TABLE_PATH = ASSETS / "phone_models.tsv"

# ---------------------------------------------------------------------------------- Samsung
# exact model numbers (letter + 3 digits) that the rules below would get wrong or don't cover
_SAMSUNG = {
    "A013": "a01 core", "A022": "a02", "A025": "a02s", "A032": "a03 core", "A042": "a04e",
    "A102": "a10e", "A127": "a12", "A137": "a13", "A202": "a20e", "A260": "a2 core",
    "A320": "a3 2017", "A510": "a5 2016", "A520": "a5 2017", "A530": "a8 2018", "A600": "a6",
    "A605": "a6 plus", "A710": "a7 2016", "A720": "a7 2017", "A730": "a8 plus 2018", "A750": "a7 2018",
    "A908": "a90 5g", "A910": "a9 pro", "A920": "a9 2018",
    "M013": "m01 core", "M017": "m01s", "M022": "m02", "M025": "m02s", "M127": "m12", "F127": "f12",
    "J100": "j1", "J105": "j1 mini", "J106": "j1 mini prime", "J110": "j1 ace", "J120": "j1 2016",
    "J200": "j2", "J210": "j2 2016", "J250": "j2 pro", "J260": "j2 core", "J320": "j3 2016",
    "J330": "j3 pro", "J337": "j3 2018", "J400": "j4", "J410": "j4 core", "J415": "j4 plus",
    "J500": "j5", "J510": "j5 2016", "J530": "j5 pro", "J600": "j6", "J610": "j6 plus", "J700": "j7",
    "J701": "j7 core", "J710": "j7 2016", "J720": "j7 duo", "J730": "j7 pro", "J737": "j7 2018",
    "J810": "j8",
    "G355": "core 2", "G360": "core prime", "G361": "core prime", "G530": "grand prime",
    "G531": "grand prime", "G532": "j2 prime", "G550": "on5", "G570": "j5 prime", "G600": "on7",
    "G610": "j7 prime", "G611": "j7 prime 2", "G615": "j7 max", "G720": "grand max",
    "G770": "s10 lite", "G780": "s20 fe", "G781": "s20 fe 5g", "G900": "s5", "G920": "s6",
    "G925": "s6 edge", "G928": "s6 edge plus", "G930": "s7", "G935": "s7 edge", "G950": "s8",
    "G955": "s8 plus", "G960": "s9", "G965": "s9 plus", "G970": "s10e", "G973": "s10",
    "G975": "s10 plus", "G977": "s10 5g", "G980": "s20", "G981": "s20 5g", "G985": "s20 plus",
    "G986": "s20 plus 5g", "G988": "s20 ultra", "G990": "s21 fe", "G991": "s21", "G996": "s21 plus",
    "G998": "s21 ultra",
    "N900": "note 3", "N910": "note 4", "N920": "note 5", "N930": "note 7", "N935": "note fe",
    "N950": "note 8", "N960": "note 9", "N970": "note 10", "N971": "note 10 5g", "N975": "note 10 plus",
    "N976": "note 10 plus 5g", "N770": "note 10 lite", "N980": "note 20", "N981": "note 20 5g",
    "N985": "note 20 ultra", "N986": "note 20 ultra 5g",
    "F700": "z flip", "F711": "z flip 3", "F721": "z flip 4", "F731": "z flip 5", "F741": "z flip 6",
    "F916": "z fold 2", "F926": "z fold 3", "F936": "z fold 4", "F946": "z fold 5", "F956": "z fold 6",
}
# SM-A065F: "A" series, "06" the number, "5" the kind (5 = 4G, 6 = 5G, 7 = "s", 8 = "s 5G")
_SAMSUNG_RE = re.compile(r"(?<![A-Z0-9])SM[-_ ]?([AMFJGNS])([0-9O]{3})[0-9]?[A-Z]{0,3}(?![A-Z0-9])")
_SAMSUNG_KIND = {"5": "", "6": " 5g", "7": "s", "8": "s 5g"}
_S_VARIANT = {"1": "", "6": " plus", "7": " edge", "8": " ultra"}


def samsung_name(letter, digits):
    """'A', '065' -> 'samsung a06'; None if the number does not follow a known pattern."""
    digits = digits.replace("O", "0")
    exact = _SAMSUNG.get(letter + digits)
    if exact:
        return "samsung " + exact
    if letter in "AMF" and digits[2] in _SAMSUNG_KIND:
        return f"samsung {letter.lower()}{digits[:2]}{_SAMSUNG_KIND[digits[2]]}"
    if letter == "S" and digits[0] == "9" and digits[2] in _S_VARIANT:   # S911 = s23, S928 = s24 ultra
        return f"samsung s{22 + int(digits[1])}{_S_VARIANT[digits[2]]}"
    if letter == "S" and digits[0] == "7" and digits[2] == "1":          # S711 = s23 fe
        return f"samsung s{22 + int(digits[1])} fe"
    return None


# ---------------------------------------------------------------------------------- phone list
_table = None
_table_lock = threading.Lock()


def _key(text):
    return re.sub(r"[^A-Z0-9]", "", (text or "").upper())


def _load():
    global _table
    with _table_lock:
        if _table is None:
            table = {}
            try:
                with open(TABLE_PATH, encoding="utf-8") as f:
                    for line in f:
                        parts = line.rstrip("\n").split("\t")
                        if len(parts) == 3:
                            table[parts[0]] = (parts[1], parts[2])
            except OSError:
                pass   # no list: only the Samsung rules work
            _table = table
    return _table


def has_table():
    return bool(_load())


def _variants(key):
    """OCR mixes up O/0 and I/1 inside model numbers."""
    yield key
    if "O" in key:
        yield key.replace("O", "0")
    if "I" in key:
        yield key.replace("I", "1")


def lookup(token, text=""):
    """The shop name for one model number ('V2111', 'CPH2269', 'X6528'), or None.
    Short numbers ('KG5', '1906') only count when their brand is written in `text` too."""
    table = _load()
    key = _key(token)
    if len(key) < 3 or not table:
        return None
    low = (text or "").lower()
    for k in _variants(key):
        hit = table.get(k)
        if hit and (len(k) >= 5 or hit[0] in low):
            return hit[1]
    return None


_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{2,16}")


def names_from_codes(text):
    """Shop names of the model numbers written in a text, best first."""
    text = strip_codes(text)   # "2-246" is a product code, not a model number
    up = text.upper()
    out = []
    for m in _SAMSUNG_RE.finditer(up):
        name = lookup(m.group(0), text) or samsung_name(m.group(1), m.group(2))
        if name:
            out.append(name)
    for tok in _TOKEN.findall(text):
        if re.search(r"\d", tok) and (re.search(r"[A-Za-z]", tok) or len(_key(tok)) == 4):
            name = lookup(tok, text)
            if name:
                out.append(name)
    return list(dict.fromkeys(out))


# what a model number looks like after each brand ("infinix x6528", "vivo v2111", "tecno kg5")
_CODE_SHAPE = {
    "vivo": r"v\d{4}[a-z]?|\d{4}", "oppo": r"cph\d{4}", "realme": r"rmx\d{4}", "infinix": r"x\d{3,4}[a-z]?",
    "tecno": r"[a-z]{2}\d[a-z]?", "itel": r"[a-z]\d{3,4}[a-z]?|it\d{4}", "xiaomi": r"\d{4,}[a-z0-9]*|m\d{4}\w+",
    "redmi": r"\d{4,}[a-z0-9]*|m\d{4}\w+", "poco": r"\d{4,}[a-z0-9]*|m\d{4}\w+",
}


def resolve(name, text=""):
    """'infinix x6528' -> ('infinix hot 40i', True) when the model number is in the phone list,
    (name, False) when it is a model number that is not, (name, None) for an ordinary name."""
    words = name.split()
    if len(words) != 2:
        return name, None
    known = lookup(words[1], f"{text} {words[0]}")
    if known:
        return known, True
    shape = _CODE_SHAPE.get(words[0])
    if shape and re.fullmatch(shape, words[1]):
        return name, False
    return name, None


# ---------------------------------------------------------------------------------- list builder
_BRANDS = {
    "samsung": "samsung", "xiaomi": "xiaomi", "redmi": "redmi", "poco": "poco", "realme": "realme",
    "oppo": "oppo", "vivo": "vivo", "iqoo": "iqoo", "infinix": "infinix", "infinix mobility limited": "infinix",
    "tecno": "tecno", "tecno mobile limited": "tecno", "itel": "itel", "oneplus": "oneplus",
    "nokia": "nokia", "hmd global": "nokia", "motorola": "motorola", "honor": "honor", "huawei": "huawei",
    "symphony": "symphony", "walton": "walton", "google": "google", "nothing": "nothing", "lava": "lava",
    "zte": "zte", "asus": "asus",
}


def _shop_name(brand, marketing):
    from .identifier import clean_model_name
    low = marketing.lower()
    if low.startswith((brand, "redmi", "poco", "iqoo", "galaxy", "mi ")):
        brand = "samsung" if low.startswith("galaxy") else ""
    return clean_model_name(re.sub(r"\s+-\s+|[()]", " ", f"{brand} {marketing}"))


_BRAND_PREFIX = re.compile(r"^(tecno|infinix|itel|vivo|oppo|realme|xiaomi|redmi|symphony|walton)[\s_-]+", re.I)
_NUMBER_NAME = re.compile(r"^(?!20[0-3]\d$)[a-z]{0,3}\d{4,}")   # a model number, but not a year


def build(csv_path, out_path=TABLE_PATH):
    """Google Play's supported_devices.csv (UTF-16) -> assets/phone_models.tsv."""
    raw = open(csv_path, "rb").read()
    text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8-sig")
    counts = {}
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 4:
            continue
        brand = _BRANDS.get(row[0].strip().lower())
        marketing, model = row[1].strip(), row[3].strip()
        if not brand or not marketing or not model or not marketing.isascii():
            continue   # Chinese-market names are no use in a Bangladeshi shop
        key = _key(_BRAND_PREFIX.sub("", model))
        name = _shop_name(brand, marketing)
        if not (3 <= len(key) <= 16 and re.search(r"\d", key)) or (key.isdigit() and len(key) != 4) \
                or _key(name).endswith(key) or any(_NUMBER_NAME.match(w) for w in name.split()[1:]):
            continue   # e.g. marketing name "vivo 1906" or "vivo pd1612": the list knows only a number
        per_key = counts.setdefault(key, {})
        per_key[(brand, name)] = per_key.get((brand, name), 0) + 1
    written = 0
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        for key in sorted(counts):
            (brand, name), _ = max(counts[key].items(), key=lambda kv: kv[1])
            if name != "unknown":
                f.write(f"{key}\t{brand}\t{name}\n")
                written += 1
    return written


if __name__ == "__main__":
    print(build(sys.argv[1]), "model numbers written to", TABLE_PATH)
