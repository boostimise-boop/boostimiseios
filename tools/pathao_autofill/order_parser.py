"""Pull order details (name, phones, amount, address) out of a WhatsApp order message.

Messages are Bangla/English mixed and every sender writes them a little differently:

    6-872                                  2-570
    নাম: ইয়ামিন ইসলাম মাহিম                Johir
    গ্রাম: হাতিগাড়া                         জেলা, বরগুনা, থানা, ... মোবাইল 01756004421
    জেলা: পাবনা                             Walton,zenix 1
    vivo y21                               Due 320
    মোবাইল নম্বর: ০১৭৩৪-৪৮৪০৪০
    01734484040
    300
"""
import codecs
import os
import re
import unicodedata

from core import codes

_BN_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")

# One code per line in the list file: "6-830", "5a-380" (also typed "6:830", "6_830+" ...)
_LIST_LINE = re.compile(r"^\s*(" + codes.PREFIX + r")\s*" + codes.SEP + r"\s*(\d{1,6})[\s+\-]*$", re.I)
# The code at the start of one of the first lines of a message, with the codes of the same list written
# after it ("3-163+164", "2-151-152", "2:152+2-153"); see core/codes.py.
_CODE_LINE = re.compile(r"^\W*(?:(?:order|code|id|অর্ডার|কোড)\W*)?" + codes.CHAT_CODE, re.I)
# Any code written with a dash, with the codes after it ("2-151-152"), so its numbers are not read as the amount
_CODE_ANY = re.compile(r"(?<!\w)" + codes.CHAT_CODE.replace(codes.SEP, codes.DASH, 1), re.I)

# Bangladeshi mobile numbers: 01XXXXXXXXX, +8801XXXXXXXXX, 01734-484040, 1734484040
_PHONE = re.compile(r"(?<!\d)(?:\+?88[ -]?)?(0?1[3-9](?:[ -]?\d){8})(?!\d)")

_NAME_LABEL = re.compile(
    r"^(?:customer|receiver|recipient|কাস্টমার|গ্রাহক|প্রাপক)?(?:'s|’s|er|ের)?\s*"
    r"(?:name|নাম)(?![a-zঀ-ং঄-৿])\s*[:ঃ=\-–—.।]*\s*(.*)$",
    re.I,
)
# Where a name written on the same line as other details stops
_NAME_STOP = re.compile(
    r"(?i)(?<![a-z])(?:mobile|mob|phone|cell|contact|number|address|vill|village|district|thana)(?![a-z])"
    r"|মোবাইল|ফোন|নম্বর|নাম্বার|ঠিকানা|গ্রাম|জেলা|থানা|উপজেলা"
)
_BRANDS = (
    "walton|vivo|oppo|samsung|xiaomi|redmi|realme|nokia|symphony|itel|tecno|infinix|huawei|honor|"
    "nubia|iphone|apple|oneplus|motorola|moto|poco|lava|zte|benco|maximus|helio|pixel|google|sony|"
    "lenovo|asus|htc|alcatel|micromax|gionee|meizu|primo|xanon|zenix|galaxy|note|pro|max|plus|ultra|"
    "mini|lite|prime|neo|cover|case"
)
# Lines that are clearly not a person's name (address parts, phone models, prices...)
_NOT_NAME = re.compile(
    r"(?i)জেলা|থানা|উপজেলা|গ্রাম|পোস্ট|পোষ্ট|ডাকঘর|ইউনিয়ন|বাজার|রোড|সদর|মোবাইল|ফোন|নম্বর|নাম্বার|মডেল|টাকা|ঠিকানা|বাকি|দাম"
    r"|\b(?:district|dist|zila|zilla|thana|upazila|upozila|village|vill|post|road|house|holding|sadar|bazar|"
    r"mobile|phone|number|model|tk|taka|due|price|address|cod|total|" + _BRANDS + r")\b"
)

_PRICE_STRONG = re.compile(
    r"(?i)(?<![a-z])(?:due|cod|total|collect|condition|amount)(?![a-z])|কন্ডিশন|বাকি|কালেক্ট|মোট"
)
_PRICE_WEAK = re.compile(r"(?i)(?<![a-z])(?:tk|taka|price|bill|bdt)(?![a-z])|টাকা|৳|/-|/=|দাম|মূল্য")
_PURE_NUMBER = re.compile(r"^(?:৳|tk\.?|bdt)?\s*(\d{2,6})\s*(?:tk|taka|টাকা|৳|/-|/=|=|bdt)?\s*\.?$", re.I)
_NUMBER = re.compile(r"(?<![A-Za-z\d])(\d{1,3}(?:,\d{3})+|\d+)(?!\d)")
_NUMBER_ALONE = re.compile(r"(?<![A-Za-z\d])(\d{1,3}(?:,\d{3})+|\d+)(?![A-Za-z\d])")

# A phone number together with the words in front of it: "মোবাইল নম্বর: 01734-484040", "Mobile no 017..."
_PHONE_LABEL = (
    r"(?:(?<![a-z])(?:mobile|mob|phone|cell|contact|whatsapp|number|num|no)(?![a-z])\.?"
    r"|মোবাইল|ফোন|নম্বর|নাম্বার|হোয়াটসঅ্যাপ|যোগাযোগ)"
)
_PHONE_WITH_LABEL = re.compile(r"(?:" + _PHONE_LABEL + r"\s*[:ঃ=\-–—.।]*\s*)*" + _PHONE.pattern, re.I)
_ONLY_PHONE_LABELS = re.compile(r"^(?:" + _PHONE_LABEL + r"|[\s:ঃ=\-–—.।,;|/()])*$", re.I)
_ADDRESS_LABEL = re.compile(
    r"^(?:(?:customer|receiver|recipient|delivery|full|কাস্টমার|গ্রাহক|প্রাপক|ডেলিভারি|পূর্ণ|সম্পূর্ণ)"
    r"(?:'s|’s|er|ের)?\s*)*(?:address|addr|ঠিকানা)(?![a-zঀ-ং঄-৿])\s*[:ঃ=\-–—.।]*\s*",
    re.I,
)
# Bangla letters like য় / ড় are written two ways; compare these patterns in NFC form
_nfc = lambda s: unicodedata.normalize("NFC", s)
_PHONE_MODEL = re.compile(_nfc(
    r"(?i)(?<![a-z])(?:model|walton|vivo|oppo|samsung|xiaomi|redmi|realme|nokia|symphony|itel|tecno|infinix|"
    r"huawei|nubia|iphone|oneplus|motorola|moto|poco|lava|zte|benco|maximus|helio|pixel|lenovo|asus|htc|"
    r"alcatel|micromax|gionee|meizu|primo|xanon|zenix|galaxy)(?![a-z])"
    r"|মডেল|ওয়ালটন|ভিভো|অপো|ওপো|স্যামসাং|শাওমি|রেডমি|রিয়েলমি|নোকিয়া|সিম্ফনি|আইটেল|টেকনো|ইনফিনিক্স|"
    r"হুয়াওয়ে|আইফোন|পোকো|মটোরোলা|নুবিয়া"
))
# A model written without the brand: "a56", "Y21", "13C pro"
_MODEL_ONLY = re.compile(r"(?i)^[a-z]{1,3} ?\d{1,3}[a-z]{0,2}\+?(?: (?:pro|max|plus|lite|prime|ultra|neo|power|play|[45]g))*$")
_ADDRESS_HINT = re.compile(_nfc(
    r"(?i)জেলা|থানা|উপজেলা|গ্রাম|পোস্ট|পোষ্ট|ডাকঘর|ইউনিয়ন|বাজার|রোড|সদর|বাসা|বাড়ি|মোড়"
    r"|\b(?:district|dist|zila|zilla|thana|upazila|upozila|village|vill|post|road|house|holding|sadar|bazar|"
    r"sector|block|lane|area)\b"
))
_EDGE_CHARS = " \t,;:|/\\-–—.।ঃ="


normalize_code = codes.norm
split_code = codes.split_code


def read_code_list(path):
    """Codes from the list file, in file order, without duplicates or blank lines."""
    found, seen = [], set()
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        for line in f:
            m = _LIST_LINE.match(line.translate(_BN_DIGITS))
            if m:
                code = normalize_code(*m.groups())
                if code not in seen:
                    seen.add(code)
                    found.append(code)
    return found


def write_phone_to_list(path, code, phone):
    """Marks a code as done in the list file the way it is done by hand: "3-620<TAB>  01814120910".
    Only a line holding just that code is changed; everything else in the file (line endings,
    BOM, other lines) stays byte for byte. Returns False when there was no such line."""
    with open(path, "rb") as f:
        raw = f.read()
    bom = codecs.BOM_UTF8 if raw.startswith(codecs.BOM_UTF8) else b""
    lines = raw[len(bom):].decode("utf-8", errors="surrogateescape").split("\n")
    changed = False
    for i, line in enumerate(lines):
        body = line.rstrip("\r")
        m = _LIST_LINE.match(body.translate(_BN_DIGITS))
        if m and normalize_code(*m.groups()) == code:
            lines[i] = body.rstrip() + "\t  " + phone + line[len(body):]
            changed = True
    if changed:
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(bom + "\n".join(lines).encode("utf-8", errors="surrogateescape"))
        os.replace(tmp, path)
    return changed


def _clean(line):
    line = line.translate(_BN_DIGITS).replace(" ", " ")
    line = re.sub(r"[*~`]|(?<!\d)_|_(?!\d)", "", line)  # WhatsApp *bold* / _italic_ markers, not "2_152"
    return re.sub(r"\s+", " ", line).strip()


def find_codes(text):
    """The order codes a message starts with: ["6-872"], ["3-163", "3-164"] for "3-163+164", or []."""
    lines = [l for l in (_clean(l) for l in (text or "").splitlines()) if l]
    for line in lines[:3]:
        m = _CODE_LINE.match(line)
        if m:
            return codes.match_codes(m)
    return []


def find_phones(text):
    phones = []
    for m in _PHONE.finditer((text or "").translate(_BN_DIGITS)):
        digits = re.sub(r"\D", "", m.group(1))
        if len(digits) == 10:
            digits = "0" + digits
        if len(digits) == 11 and digits not in phones:
            phones.append(digits)
    return phones


def _clean_name(s):
    s = _PHONE.sub(" ", s)
    m = _NAME_STOP.search(s)
    if m:
        s = s[: m.start()]
    s = re.sub(r"\s+", " ", s)
    return s.strip(" \t,;:|/\\-–—.।ঃ()[]")


def _looks_like_name(line):
    s = line.strip()
    return (
        2 <= len(s) <= 40
        and not re.search(r"\d", s)
        and "," not in s
        and len(s.split()) <= 5
        and not _NOT_NAME.search(s)
    )


def _find_name(lines):
    """Returns (name, indexes of the lines it was taken from)."""
    for i, line in enumerate(lines):
        m = _NAME_LABEL.match(re.sub(r"^[^\w]+", "", line))
        if m:
            name, used = _clean_name(m.group(1)), [i]
            if not name and i + 1 < len(lines) and _looks_like_name(lines[i + 1]):
                name, used = _clean_name(lines[i + 1]), [i, i + 1]  # "নাম:" alone, the name is on the next line
            if name:
                return name, used
    for i, line in enumerate(lines[:3]):  # no label: the name is usually the first line after the code
        if _looks_like_name(line):
            return _clean_name(line), [i]
    return "", []


def _numbers(line, alone=False):
    t = _PHONE.sub(" ", line)
    t = _CODE_ANY.sub(" ", t)
    t = re.sub(r"(?i)(tk|taka|bdt|৳)(?=\d)", r"\1 ", t)
    values = []
    for m in (_NUMBER_ALONE if alone else _NUMBER).finditer(t):
        v = int(m.group(1).replace(",", ""))
        if 10 <= v <= 999999:
            values.append(v)
    return values


def _find_amount(lines):
    """Returns (amount, guessed, index of the line it was taken from)."""
    best = None  # (rank, line index, value)
    for i, line in enumerate(lines):
        rank = 2 if _PRICE_STRONG.search(line) else 1 if _PRICE_WEAK.search(line) else 0
        if not rank:
            continue
        values = _numbers(line)
        if values and (best is None or (rank, i) > best[:2]):
            best = (rank, i, max(values))
    if best:
        return str(best[2]), False, best[1]
    for i in range(len(lines) - 1, -1, -1):  # a line that is just the price: "300"
        m = _PURE_NUMBER.match(lines[i])
        if m:
            return str(int(m.group(1))), False, i
    for i in range(len(lines) - 1, max(len(lines) - 3, -1), -1):  # last resort: a number at the end
        values = [v for v in _numbers(lines[i], alone=True) if v >= 50]
        if values:
            return str(max(values)), True, i
    return "", False, -1


def _is_phone_model(s):
    if _MODEL_ONLY.match(s):
        return True
    s = _nfc(s)
    return bool(_PHONE_MODEL.search(s)) and len(s.split()) <= 6 and not _ADDRESS_HINT.search(s)


def _find_address(lines, name, name_lines, amount, amount_line):
    """Every line that is not the name, a phone number, the phone model or the price, joined with ", "."""
    parts = []
    for i, line in enumerate(lines):
        s = line
        m = _NAME_LABEL.match(re.sub(r"^[^\w]+", "", s))
        if m or i in name_lines:
            s = m.group(1) if m else s
            if name:
                s = s.replace(name, " ", 1)  # keep what follows the name: "নাম: করিম, গ্রাম: ..."
        if i == amount_line:
            s = _PRICE_WEAK.sub(" ", _PRICE_STRONG.sub(" ", s))
            s = _NUMBER.sub(lambda n: " " if n.group(1).replace(",", "") == amount else n.group(0), s)
        s = _PHONE_WITH_LABEL.sub(" ", s)
        s = re.sub(r"\(\s*\)|\[\s*\]", " ", s)
        s = _ADDRESS_LABEL.sub("", s.strip(_EDGE_CHARS))
        s = re.sub(r"\s*,[\s,]*", ", ", s)
        s = re.sub(r"\s+", " ", s).strip(_EDGE_CHARS)
        if not re.search(r"[^\W\d_]", s) or _ONLY_PHONE_LABELS.match(s) or _is_phone_model(s):
            continue
        if s not in parts:
            parts.append(s)
    return ", ".join(parts)


def parse_order(text):
    lines = [l for l in (_clean(l) for l in (text or "").splitlines()) if l]
    code, code_line = "", -1
    for i, line in enumerate(lines[:3]):
        m = _CODE_LINE.match(line)
        if m:
            code, code_line = normalize_code(m.group(1), m.group(3)), i
            break
    body = [l for i, l in enumerate(lines) if i != code_line]

    phones = find_phones("\n".join(body))
    name, name_lines = _find_name(body)
    amount, guessed, amount_line = _find_amount(body)
    # a guessed amount may be a number inside the address, so leave that line alone
    address = _find_address(body, name, name_lines, amount, -1 if guessed else amount_line)

    warnings = []
    if not name:
        warnings.append("name not found")
    if not phones:
        warnings.append("phone number not found")
    if not address:
        warnings.append("address not found")
    if not amount:
        warnings.append("amount not found")
    elif guessed:
        warnings.append(f"amount {amount} is a guess")
    if len(phones) > 2:
        warnings.append(f"{len(phones)} phone numbers in the message, only the first two are used")
    return {
        "code": code,
        "name": name,
        "phone": phones[0] if phones else "",
        "phone2": phones[1] if len(phones) > 1 else "",
        "address": address,
        "amount": amount,
        "warnings": warnings,
    }
