"""
PhotoMatch Renamer - the work behind the "Rename by Match" page (no window code here).
1. Reads a product list such as "6-852 Nubia a56" (starting from the BOTTOM line).
2. Looks for the code only ("6-852") in a WhatsApp chat (WhatsApp Web).
3. Compares the photos of that WhatsApp message with the photos in a folder on
   this PC and renames the matching photo to "6-852 Nubia a56.jpg".
(WhatsApp is the tab of the Edge window that all tools share, see core/whatsapp.py.)
"""

import base64
import csv
import hashlib
import io
import json
import math
import os
import queue
import re
import shutil
import threading
import time
import traceback
from collections import defaultdict
from dataclasses import dataclass, field
from functools import lru_cache
from datetime import datetime
from pathlib import Path

from core import codes as product_codes, whatsapp

APP_TITLE = "PhotoMatch Renamer"
APP_VERSION = "1.1.0"
# Data folder keeps its first name so the WhatsApp login and settings survive updates
DATA_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "PhotoRenameByMatch"
LOG_DIR = DATA_DIR / "rename-logs"            # used by "Undo last rename"
SETTINGS_FILE = DATA_DIR / "settings.json"
ERROR_FILE = DATA_DIR / "last_error.txt"
REPORT_FILE = DATA_DIR / "last_run_report.txt"
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".jfif"}
GIVE_UP_MARGIN = 20   # stop looking for 6-847 once only codes <= 6-827 are being loaded
SCROLL_STEP = 0.75    # scroll up 75% of the chat's height per step, so every message is seen

# Product codes are read in the shop's code style (core/codes.py): "6-852", "4a-75", "5A - 380", "6:852" ...
# or "CC1023", "#1023". A list ("group") is the codes of one prefix, "6-" / "4a-" / "cc-"; the codes
# without a prefix (#1023, Order 1023) are one list, "".
# File/folder names that already start with a code were renamed before -> never rename them again
DONE_RE = re.compile(r"^(\d{1,2}[A-Za-z]?)\s*-\s*(\d{1,5})(?!\d)")
# Sub folders for one list, e.g. "6-" or "5a-"
GROUP_DIR_RE = re.compile(r"^\d{1,2}[A-Za-z]?\s*-$")
NEVER_RE = re.compile(r"(?!)")


def done_re(reader=None):
    """Names starting with a code (group 1 prefix, 2 number): "6-852 Nubia a56", "CC1023 …", "#1023 …"."""
    r = reader or product_codes.reader()
    return DONE_RE if r.kind == "prefix" else re.compile(r.js_any_code, re.I)


def group_dir_re(reader=None):
    """Sub folders for one list: "6-" or "5a-"; "CC" or "CC-" in the letters style; none for #1023."""
    r = reader or product_codes.reader()
    if r.kind == "letters":
        return re.compile(r"^(?:" + "|".join(r.prefixes()) + r")\s*-?$", re.I)
    return GROUP_DIR_RE if r.kind == "prefix" else NEVER_RE


class StopRequested(Exception):
    pass


# --------------------------------------------------------------------------- product list

@dataclass
class Product:
    code: str    # normalised code used for searching, e.g. "6-852" / "4a-75"
    label: str   # new file name, e.g. "6-852 Nubia a56"
    group: str   # the list it belongs to, e.g. "6-" / "4a-"


norm_code = product_codes.norm
split_code = product_codes.split_code


def group_of(prefix):
    """The list of a code's prefix: "6-", "cc-"; "" for codes without a prefix."""
    return f"{prefix.lower()}-" if prefix else ""


def group_label(group):
    """"6- list", or "Orders list" for the codes without a prefix."""
    return f"{group} list" if group else "Orders list"


def list_groups(products):
    """The lists in the file, bottom list first: {"6-": [products bottom first], "5a-": …}."""
    groups = {}
    for p in products:
        groups.setdefault(p.group, []).append(p)
    return groups


def list_folder(root, group):
    """The folder with the photos of one list: in the day folder "24.09.26" the 6- list uses
    the sub folder "6-". None if the day folder has list folders but not this one."""
    root = Path(root)
    if not group:
        return root      # the codes without a prefix are one list: no list folders
    names = {group} if group[0].isdigit() else {group, group[:-1]}  # "cc-" is in "CC-" or "CC"
    same = lambda name: re.sub(r"\s+", "", name).lower() in names
    if same(root.name):
        return root
    dir_re = group_dir_re()
    try:
        subs = [d for d in root.iterdir() if d.is_dir() and dir_re.match(d.name)]
    except OSError:
        return None
    for d in subs:
        if same(d.name):
            return d
    if subs or dir_re.match(root.name):
        return None
    return root          # a plain folder without list folders: used for every list


def group_in_name(group, name):
    """True if a chat name is for this list, e.g. "My Shop (6-)" for "6-"."""
    if not group:
        return False     # the orders chat comes from the shop settings
    pattern = r"(?<![0-9A-Za-z])" + re.escape(group[:-1]) + r"\s*-(?!\s*\d)"
    return re.search(pattern, name or "", re.IGNORECASE) is not None


def read_text(path):
    raw = Path(path).read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def parse_products(path):
    """Return (products bottom line first, codes skipped because they have no name)."""
    r = product_codes.reader()
    products, skipped = [], []
    for line in read_text(path).splitlines():
        line = line.strip()
        m = r.code_re.match(line)
        if not m:
            continue
        name = line[m.end():].strip()
        code_text = r.written(m.group(1), m.group(2))
        if not any(ch.isalnum() for ch in name):   # "4-337 ......" = no product name
            skipped.append(code_text)
            continue
        products.append(Product(norm_code(m.group(1), m.group(2)), f"{code_text} {name}", group_of(m.group(1))))
    products.reverse()
    unique, seen = [], set()
    for p in products:
        if p.code not in seen:
            seen.add(p.code)
            unique.append(p)
    return unique, skipped


def safe_filename(name):
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', " ", name)
    name = re.sub(r"\s+", " ", name).strip().rstrip(". ")
    return name[:150].rstrip(". ") or "unnamed"


def caption_note(captions, label):
    """Text in (brackets) from the WhatsApp caption that is not already in the name."""
    notes = []
    for cap in captions:
        for m in re.finditer(r"\(([^()\n]*)\)?", cap):
            text = m.group(1).strip()
            if text and text.lower() not in label.lower() and text not in notes:
                notes.append(text)
    return f" ({'; '.join(notes)})" if notes else ""


def _is_name(name, base):
    """"6-852 Nubia a56", with only (bracketed) text after it: a note or "(2)"."""
    name, base = name.lower(), base.lower()
    return name.startswith(base) and re.fullmatch(r"(\s*\([^()]*\))*", name[len(base):]) is not None


def named_for(path, base):
    """True if the photo already has the name its code gives it ("6-852 Nubia a56.jpg", "… (2).jpg",
    or img1.jpg in the folder "6-852 Nubia a56"): it is left as it is. Any other name, also one
    starting with a code ("6-852 Nubia a56 1.jpg", "6-840 …"), is renamed."""
    path = Path(path)
    if re.fullmatch(r"img\d+", path.stem, re.IGNORECASE) and _is_name(path.parent.name, base):
        return True
    return _is_name(path.stem, base)


# --------------------------------------------------------------------------- photo matching

@dataclass
class Signature:
    sha1: str
    dhash: int
    phash: int


# Cosine table for the 8 lowest frequencies of a 32-point DCT (used by phash)
_DCT = [[math.cos(math.pi * (2 * x + 1) * u / 64) for x in range(32)] for u in range(8)]


def image_signature(data):
    """Exact hash + two perceptual hashes, so the same photo still matches after
    WhatsApp has resized / recompressed it."""
    from PIL import Image, ImageOps

    with Image.open(io.BytesIO(data)) as im:
        im.draft("L", (160, 160))           # fast JPEG decoding at reduced size
        im = ImageOps.exif_transpose(im)
        gray = im.convert("L")

    px = list(gray.resize((9, 8), Image.Resampling.LANCZOS).getdata())
    dhash = 0
    for r in range(8):
        for c in range(8):
            dhash = (dhash << 1) | (px[r * 9 + c] > px[r * 9 + c + 1])

    px = list(gray.resize((32, 32), Image.Resampling.LANCZOS).getdata())
    rows = [px[r * 32:(r + 1) * 32] for r in range(32)]
    tmp = [[sum(v * c for v, c in zip(row, cos)) for cos in _DCT] for row in rows]
    coef = [sum(tmp[y][u] * _DCT[v][y] for y in range(32)) for v in range(8) for u in range(8)]
    ordered = sorted(coef)
    median = (ordered[31] + ordered[32]) / 2
    phash = 0
    for c in coef:
        phash = (phash << 1) | (c > median)

    return Signature(hashlib.sha1(data).hexdigest(), dhash, phash)


def difference(a, b):
    """0 = identical file, small = same photo, large = different photo."""
    if a.sha1 == b.sha1:
        return 0
    return max(bin(a.dhash ^ b.dhash).count("1"), bin(a.phash ^ b.phash).count("1"))


@dataclass
class LocalPhoto:
    path: Path
    sig: Signature
    done_code: str = ""       # its name already starts with this code (renamed before this run)
    current: Path = None
    used_for: list = field(default_factory=list)

    def __post_init__(self):
        self.current = self.path


# --------------------------------------------------------------------------- WhatsApp Web

# Helpers injected into the WhatsApp Web page. They only rely on long-standing hooks
# (#main, [data-id] message rows, .selectable-text, .quoted-mention, blob: images).
JS_HELPERS = r"""
() => {
  if (window.__prbm && window.__prbm.v === __V__) return true;
  const CODE = new RegExp(__CODE__, 'gim');
  const MORE = __MORE__;
  const P = { v: __V__ };
  const inside = (el, boxes) => boxes.some((b) => b.contains(el));
  const scrollable = (e) => {
    const o = getComputedStyle(e).overflowY;
    return (o === 'auto' || o === 'scroll') && e.scrollHeight > e.clientHeight + 4;
  };

  P.main = () => document.querySelector('#main');
  P.loggedIn = () => !!document.querySelector('#pane-side, #side, [aria-label="Chat list"]');
  P.rows = () => {
    const main = P.main();
    if (!main) return [];
    return Array.from(main.querySelectorAll('[data-id]'))
      .filter((n) => !(n.parentElement && n.parentElement.closest('[data-id]')));
  };
  P.count = () => P.rows().length;
  P.row = (id) => {
    const main = P.main();
    return main ? main.querySelector('[data-id="' + CSS.escape(id) + '"]') : null;
  };

  P.scroller = () => {
    const main = P.main();
    if (!main) return null;
    if (P._sc && P._sc.isConnected && main.contains(P._sc)) return P._sc;
    const first = main.querySelector('[data-id]');
    for (let e = first ? first.parentElement : null; e && e !== main; e = e.parentElement) {
      if (scrollable(e)) return (P._sc = e);
    }
    let best = null;
    main.querySelectorAll('div').forEach((e) => {
      if (scrollable(e) && (!best || e.scrollHeight > best.scrollHeight)) best = e;
    });
    return (P._sc = best);
  };
  P.toBottom = () => {
    const sc = P.scroller();
    if (!sc) return false;
    sc.scrollTop = sc.scrollHeight;
    sc.dispatchEvent(new Event('scroll'));
    return true;
  };
  P.stepUp = (frac) => {
    const sc = P.scroller();
    if (!sc) return null;
    const before = sc.scrollTop;
    sc.scrollTop = Math.max(0, before - Math.round(sc.clientHeight * frac));
    sc.dispatchEvent(new Event('scroll'));
    return { before, after: sc.scrollTop, height: sc.scrollHeight };
  };
  P.stepDown = (frac) => {
    const sc = P.scroller();
    if (!sc) return null;
    const before = sc.scrollTop;
    sc.scrollTop = before + Math.round(sc.clientHeight * frac);
    sc.dispatchEvent(new Event('scroll'));
    return { before, after: sc.scrollTop, height: sc.scrollHeight };
  };
  P.height = () => {
    const sc = P.scroller();
    return sc ? sc.scrollHeight : 0;
  };
  P.topRowId = () => {
    const sc = P.scroller();
    if (!sc) return null;
    const top = sc.getBoundingClientRect().top;
    const hit = P.rows().find((n) => n.getBoundingClientRect().bottom > top + 4);
    return hit ? hit.getAttribute('data-id') : null;
  };
  P.scrollToId = (id, block) => {
    const n = P.row(id);
    if (!n) return false;
    n.scrollIntoView({ block: block || 'center' });
    return true;
  };
  P.clickOlder = () => {
    const main = P.main();
    if (!main) return false;
    const hit = Array.from(main.querySelectorAll('button, [role="button"]')).find((e) => {
      const t = e.textContent || '';
      return t.length < 150 && /older messages|earlier messages/i.test(t);
    });
    if (hit) hit.click();
    return !!hit;
  };

  // Quoted (replied-to) parts of a message must not count as that message's code/photo
  P.quoteBoxes = (n) => {
    const boxes = new Set();
    const onlyQuote = (b) => !Array.from(b.querySelectorAll('.selectable-text'))
      .some((s) => !s.closest('.quoted-mention'));
    n.querySelectorAll('.quoted-mention').forEach((q) => {
      const b = q.closest('[role="button"]');
      boxes.add(b && b !== n && n.contains(b) && onlyQuote(b) ? b : q);
    });
    n.querySelectorAll('[aria-label*="uoted"]').forEach((b) => {
      if (b !== n && onlyQuote(b)) boxes.add(b);
    });
    return Array.from(boxes);
  };
  P.images = (n, boxes) => {
    const out = [];
    const seen = new Set();
    n.querySelectorAll('img').forEach((im) => {
      if (inside(im, boxes)) return;
      const src = im.currentSrc || im.src || '';
      if (!(src.startsWith('blob:') || src.startsWith('data:image')) || seen.has(src)) return;
      const r = im.getBoundingClientRect();
      const shown = Math.max(r.width, r.height);
      if (shown ? shown < 90 : Math.max(im.naturalWidth, im.naturalHeight) < 90) return;  // emoji, avatar
      seen.add(src);
      out.push({ im, src, blob: src.startsWith('blob:') });
    });
    // A downloaded photo is a blob: image; data: images are only the blurry preview
    return out.some((x) => x.blob) ? out.filter((x) => x.blob) : out;
  };
  P.text = (n, boxes) => {
    const parts = [];
    n.querySelectorAll('.selectable-text').forEach((s) => {
      if (inside(s, boxes) || (s.parentElement && s.parentElement.closest('.selectable-text'))) return;
      const t = (s.innerText || '').trim();
      if (t) parts.push(t);
    });
    if (parts.length) return parts.join('\n');
    let t = n.innerText || '';
    boxes.forEach((b) => {
      const q = (b.innerText || '').trim();
      if (q) t = t.split(q).join(' ');
    });
    return t.trim();
  };
  P.sender = (n) => {
    const p = (n.getAttribute('data-id') || '').split('_');
    if (p[0] === 'true' || p[0] === 'false') return p[0] + '|' + (p.length > 3 ? p.slice(3).join('_') : '');
    return n.querySelector('.message-out') ? 'out' : 'in';
  };
  // A photo is ready when WhatsApp has downloaded the real picture (not the blurry preview)
  const ready = (x) => x.blob && x.im.complete && Math.max(x.im.naturalWidth, x.im.naturalHeight) >= 200;
  // A photo that is not loaded yet still takes its space: a big box without text
  const emptyBox = (n) => Array.from(n.querySelectorAll('div, span')).some((e) => {
    const r = e.getBoundingClientRect();
    return r.width >= 150 && r.height >= 150 && (e.innerText || '').trim().length < 30;
  });
  const pressDownload = (n) => {
    const d = n.querySelector('[data-icon*="download"]');
    if (d) (d.closest('button,[role="button"]') || d).click();
  };
  P.view = () => {
    const sc = P.scroller();
    return sc ? sc.getBoundingClientRect() : null;
  };
  P.info = (n, vb) => {
    const boxes = P.quoteBoxes(n);
    const text = P.text(n, boxes);
    const codes = [];
    for (const m of text.matchAll(CODE)) {
      const key = (n) => (m[1] ? m[1].toLowerCase() + '-' : '') + parseInt(n, 10);  // as codes.norm
      codes.push(key(m[2]));
      const more = new RegExp(MORE.split('__P__').join(m[1]), 'gi');
      for (const x of m[3].matchAll(more)) codes.push(key(x[1]));
    }
    const imgs = P.images(n, boxes);
    const r = n.getBoundingClientRect();
    return {
      id: n.getAttribute('data-id'),
      sender: P.sender(n),
      codes,
      caption: codes.length ? text.slice(0, 600) : '',
      n: imgs.length,
      blob: imgs.filter((x) => x.blob).length,
      ready: imgs.filter(ready).length,
      media: imgs.length > 0 || !!n.querySelector('[data-icon*="download"], video') || emptyBox(n),
      vis: !!vb && r.bottom > vb.top && r.top < vb.bottom,
    };
  };
  P.snapshot = () => {
    if (!P.main()) return null;
    const vb = P.view();
    return P.rows().map((n) => P.info(n, vb));
  };
  P.rowInfo = (id) => {
    const n = P.row(id);
    return n ? P.info(n, P.view()) : null;
  };
  // Messages on screen whose photo WhatsApp is still downloading
  P.visibleWaiting = (press) => {
    const vb = P.view();
    if (!vb) return 0;
    let k = 0;
    P.rows().forEach((n) => {
      const r = n.getBoundingClientRect();
      if (r.bottom <= vb.top || r.top >= vb.bottom) return;
      const i = P.info(n, vb);
      if (!i.media || (i.n && i.ready === i.n)) return;
      k++;
      if (press && i.n && !i.blob) pressDownload(n);
    });
    return k;
  };
  P.pressDownload = (id) => {
    const n = P.row(id);
    if (n) pressDownload(n);
    return !!n;
  };
  P.fetchImage = async (id, idx, nudge, allowPreview) => {
    const n = P.row(id);
    if (!n) return { err: 'gone' };
    const it = P.images(n, P.quoteBoxes(n))[idx];
    const big = !!it && ready(it);
    if (!big && !(allowPreview && it)) {
      if (nudge) {
        // Photo not loaded (or unloaded again): bring it on screen and press its download button
        n.scrollIntoView({ block: 'center' });
        if (!(it && it.blob)) pressDownload(n);
      }
      return { err: it ? 'pending' : 'missing' };
    }
    if (it.src.startsWith('data:')) return { b64: it.src.split(',')[1] || '', preview: true };
    try {
      const r = await fetch(it.src);
      const buf = new Uint8Array(await r.arrayBuffer());
      let s = '';
      for (let i = 0; i < buf.length; i += 0x8000) s += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
      return { b64: btoa(s), preview: !big };
    } catch (e) {
      try {
        const c = document.createElement('canvas');
        c.width = it.im.naturalWidth;
        c.height = it.im.naturalHeight;
        c.getContext('2d').drawImage(it.im, 0, 0);
        return { b64: c.toDataURL('image/jpeg', 0.95).split(',')[1], preview: !big };
      } catch (e2) {
        if (nudge) n.scrollIntoView({ block: 'center' });
        return { err: 'pending' };
      }
    }
  };

  P.chatTitle = () => {
    const h = document.querySelector('#main header');
    if (!h) return '';
    const s = h.querySelector('span[dir="auto"]') || h.querySelector('[title]');
    return s ? (s.getAttribute('title') || s.textContent || '').trim() : '';
  };
  P.markChat = (name) => {
    document.querySelectorAll('[data-prbm-target]').forEach((e) => e.removeAttribute('data-prbm-target'));
    const want = name.trim().toLowerCase().replace(/\s+/g, ' ');
    const root = document.querySelector('#side') || document;
    const hit = Array.from(root.querySelectorAll('span[title]')).find((s) => !s.closest('#main') &&
      (s.getAttribute('title') || '').trim().toLowerCase().replace(/\s+/g, ' ') === want);
    if (hit) hit.setAttribute('data-prbm-target', '1');
    return !!hit;
  };
  P.markSearch = () => {
    document.querySelectorAll('[data-prbm-search]').forEach((e) => e.removeAttribute('data-prbm-search'));
    const root = document.querySelector('#side') || document;
    const el = root.querySelector('[contenteditable="true"], input[type="text"], input[type="search"], input:not([type])') ||
      document.querySelector('[contenteditable="true"][data-tab="3"], [role="textbox"][aria-label*="earch"], input[aria-label*="earch"]');
    if (el) el.setAttribute('data-prbm-search', '1');
    return !!el;
  };
  // Chat list (left side): names of the chats that are shown, and scrolling it
  P.pane = () => {
    const pane = document.querySelector('#pane-side');
    if (!pane) return null;
    if (scrollable(pane)) return pane;
    for (let e = pane.parentElement; e && e !== document.body; e = e.parentElement) if (scrollable(e)) return e;
    return Array.from(pane.querySelectorAll('div')).find(scrollable) || pane;
  };
  P.chatNames = () => {
    const pane = document.querySelector('#pane-side');
    if (!pane) return [];
    const out = [];
    const add = (s) => {
      const t = s ? (s.getAttribute('title') || '').trim() : '';
      if (t && !out.includes(t)) out.push(t);
    };
    const rows = pane.querySelectorAll('[role="listitem"], [role="row"]');
    if (rows.length) rows.forEach((r) => add(r.querySelector('span[title]')));
    else pane.querySelectorAll('span[title]').forEach(add);
    return out;
  };
  P.paneTop = () => {
    const p = P.pane();
    if (p) p.scrollTop = 0;
    return !!p;
  };
  P.paneStep = (frac) => {
    const p = P.pane();
    if (!p) return false;
    const before = p.scrollTop;
    p.scrollTop = before + Math.round(p.clientHeight * frac);
    return p.scrollTop > before;
  };
  P.sideTitles = () => {
    const root = document.querySelector('#side') || document;
    const out = [];
    root.querySelectorAll('span[title]').forEach((s) => {
      const t = (s.getAttribute('title') || '').trim();
      if (t && !s.closest('#main') && !out.includes(t)) out.push(t);
    });
    return out;
  };

  window.__prbm = P;
  return true;
}
"""


@lru_cache(maxsize=4)
def _helpers_for(style_key):
    r = product_codes.Reader(json.loads(style_key))
    return (JS_HELPERS.replace("__CODE__", json.dumps(r.js_chat_code)).replace("__MORE__", json.dumps(r.js_more_item))
            .replace("__V__", json.dumps(f"7 {style_key}")))


def js_helpers(reader=None):
    """JS_HELPERS reading the codes of the shop's style (core/codes.py: the reader's js_chat_code, whose
    group 3 is the rest of a list like "+164-165", read item by item with js_more_item)."""
    r = reader or product_codes.reader()
    return _helpers_for(json.dumps(r.style, sort_keys=True))


@dataclass
class WaHit:
    keys: list = field(default_factory=list)       # (message id, photo index)
    captions: list = field(default_factory=list)


class MessageStore:
    """Every WhatsApp message seen while scrolling, oldest first. WhatsApp only keeps
    the messages near the screen on the page, so the program remembers them itself."""

    def __init__(self):
        self.order = []
        self.info = {}

    def __len__(self):
        return len(self.order)

    def rows(self):
        return [self.info[i] for i in self.order]

    def update(self, r):
        old = self.info.get(r["id"])
        if old is None:
            self.info[r["id"]] = dict(r, codes=list(r["codes"]))
            return
        old["n"] = max(old["n"], r["n"])          # a photo that has loaded since
        old["blob"] = max(old["blob"], r["blob"])
        old["media"] = old["media"] or r["media"]
        old["codes"] += [c for c in r["codes"] if c not in old["codes"]]
        old["caption"] = old["caption"] or r["caption"]

    def merge(self, snap):
        """Add the messages currently on the page (oldest first) in the right place."""
        if not snap:
            return
        known = [i for i, r in enumerate(snap) if r["id"] in self.info]
        if not known:
            self.order[0:0] = [r["id"] for r in snap]
            for r in snap:
                self.update(r)
            return
        f = known[0]
        pos = self.order.index(snap[f]["id"])
        self.order[pos:pos] = [r["id"] for r in snap[:f]]
        for r in snap[:f + 1]:
            self.update(r)
        last = pos + f
        for r in snap[f + 1:]:
            rid = r["id"]
            if rid in self.info:
                if last + 1 < len(self.order) and self.order[last + 1] == rid:
                    last += 1
                else:
                    last = self.order.index(rid)
            else:
                last += 1
                self.order.insert(last, rid)
            self.update(r)


def group_messages(rows, wanted, use_neighbors):
    """rows = WhatsApp messages, oldest first. Returns {code: [(msg id, photo idx)]}
    and {code: [caption text]}."""
    assign, captions, claimed = defaultdict(list), defaultdict(list), set()

    def add(codes, row):
        for c in codes:
            for k in range(row["n"]):
                if (row["id"], k) not in assign[c]:
                    assign[c].append((row["id"], k))
        claimed.add(row["id"])

    def plain_photo(row, sender):
        # a photo message without any code, from the same person, not used yet
        return row["n"] and not row["codes"] and row["sender"] == sender and row["id"] not in claimed

    wanted_codes = [[c for c in dict.fromkeys(r["codes"]) if c in wanted] for r in rows]

    # Photo with the code in its caption (+ caption-less photos sent right after it)
    for i, r in enumerate(rows):
        codes = wanted_codes[i]
        if not codes:
            continue
        for c in codes:
            if r["caption"] and r["caption"] not in captions[c]:
                captions[c].append(r["caption"])
        if r["n"]:
            add(codes, r)
            if use_neighbors:
                for s in rows[i + 1:i + 7]:
                    if not plain_photo(s, r["sender"]):
                        break
                    add(codes, s)

    # Code sent as a text message: use the caption-less photos just before (or after) it
    if use_neighbors:
        for i, r in enumerate(rows):
            codes = wanted_codes[i]
            if not codes or r["n"]:
                continue
            got = []
            for s in reversed(rows[max(0, i - 6):i]):
                if not plain_photo(s, r["sender"]):
                    break
                got.insert(0, s)
            if not got:
                for s in rows[i + 1:i + 7]:
                    if not plain_photo(s, r["sender"]):
                        break
                    got.append(s)
            for s in got:
                add(codes, s)
    return assign, captions


def codes_to_give_up(rows, wanted, found, margin=GIVE_UP_MARGIN, region=40):
    """Codes that can no longer appear: the oldest messages loaded so far only have
    much lower codes of the same group (e.g. still missing 6-849, already at 6-8xx < 829)."""
    oldest = defaultdict(list)
    for r in rows[:region]:
        for c in r["codes"]:
            g, n = split_code(c)
            oldest[g].append(n)
    seen_groups = {split_code(c)[0] for r in rows for c in r["codes"]}
    lowest = {}
    for c in wanted:
        g, n = split_code(c)
        lowest[g] = min(lowest.get(g, n), n)

    def passed(g, n):
        return len(oldest[g]) >= 2 and max(oldest[g]) <= n - margin

    out = {c for c in wanted - found if passed(*split_code(c))}
    groups_here = [g for g in lowest if g in seen_groups]
    if groups_here and all(passed(g, lowest[g]) for g in groups_here):
        # already older than the whole list: groups never seen in this chat won't show up
        out |= {c for c in wanted - found if split_code(c)[0] not in seen_groups}
    return out


def passed_list_top(rows, wanted, region=20):
    """True once the search is just above the first code of the list (e.g. above 6-830):
    the oldest messages loaded show at least two of the codes right before it (6-829, 6-828)."""
    lowest = {}
    for c in wanted:
        g, n = split_code(c)
        lowest[g] = min(lowest.get(g, n), n)
    before = defaultdict(set)
    for r in rows[:region]:
        for c in r["codes"]:
            g, n = split_code(c)
            if g in lowest and lowest[g] - 10 <= n < lowest[g]:
                before[g].add(n)
    return bool(lowest) and all(len(before[g]) >= 2 for g in lowest)


@dataclass
class ScanResult:
    hits: dict
    sigs: dict
    previews: set
    failed: set
    store: MessageStore
    given_up: set
    end_reason: str = ""
    stopped: bool = False


class WhatsAppWeb:
    def __init__(self, log, status, check_stop):
        self.log, self.status, self.check_stop = log, status, check_stop
        self._pw = self.page = None
        self.chat_title = ""

    # ---- browser
    def is_open(self):
        try:
            return self.page is not None and not self.page.is_closed()
        except Exception:
            return False

    def open(self):
        if self.is_open():
            try:
                self.page.bring_to_front()
            except Exception:
                pass
            return
        self.close()
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise RuntimeError("Playwright is not installed. Run:  python -m pip install playwright")
        self._pw = sync_playwright().start()
        try:
            _, ctx = whatsapp.connect(self._pw, self.log)
            self.page = whatsapp.whatsapp_page(ctx)
        except BaseException:
            self.close()
            raise
        self.page.bring_to_front()

    def close(self):
        """Lets go of the WhatsApp window; it stays open for the other tools."""
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self._pw = self.page = None

    def turn(self):
        """WhatsApp to itself while this tool scrolls in it (the window is shared with the other tools)."""
        def waiting(tool):
            self.log(f"{tool} is using WhatsApp. This goes on as soon as it is done.", "warn")
            self.status(f"Waiting for {tool} to finish with WhatsApp…")
        return whatsapp.turn("Rename by Match", self.check_stop, waiting)

    def wait(self, ms):
        self.check_stop()
        self.page.wait_for_timeout(ms)

    def js(self, name, *args):
        return self.page.evaluate(
            f"(a) => {{ ({js_helpers()})(); return window.__prbm.{name}(...a); }}", list(args))

    def safe_js(self, name, *args, default=None):
        try:
            return self.js(name, *args)
        except Exception:
            if not self.is_open():
                raise
            return default   # page still loading

    # ---- login / chat
    def wait_logged_in(self, timeout=600):
        t0, told = time.time(), False
        while not self.safe_js("loggedIn", default=False):
            if not told and time.time() - t0 > 5:
                self.log("Scan the QR code in the WhatsApp window with your phone "
                         "(WhatsApp > Settings > Linked devices > Link a device).", "warn")
                self.status("Waiting for WhatsApp login…")
                told = True
            if time.time() - t0 > timeout:
                raise RuntimeError("WhatsApp login timed out.")
            self.wait(1000)
        if told:
            self.log("WhatsApp is logged in.", "ok")

    @staticmethod
    def _same_chat(title, name):
        a, b = (re.sub(r"\s+", " ", x or "").strip().lower() for x in (title, name))
        return bool(a) and (a == b or b in a)

    def open_chat(self, name):
        name = (name or "").strip()
        if name and not self._same_chat(self.safe_js("chatTitle", default=""), name):
            if not self.safe_js("markChat", name, default=False) and self.safe_js("markSearch", default=False):
                self.page.click("[data-prbm-search]")
                self.page.keyboard.press("Control+A")
                self.page.keyboard.type(name, delay=25)
                self.wait(2500)
            if self.safe_js("markChat", name, default=False):
                self.page.click("[data-prbm-target]")
                self.wait(2500)

        t0, told = time.time(), False
        while True:
            title = self.safe_js("chatTitle", default="")
            if self.safe_js("count", default=0) > 0 and (not name or self._same_chat(title, name)):
                break
            if not told:
                what = f'the chat "{name}"' if name else "the chat that has the product photos"
                self.log(f"Please open {what} in the WhatsApp window…", "warn")
                self.status("Waiting for the WhatsApp chat to be opened…")
                told = True
            if time.time() - t0 > 900:
                raise RuntimeError("No WhatsApp chat was opened.")
            self.wait(1000)
        self.chat_title = title
        self.log(f'WhatsApp chat: "{title}"')

    def chat_names(self):
        """Names of the chats in WhatsApp's chat list (scrolls through it once)."""
        names = []
        self.safe_js("paneTop")
        self.wait(300)
        for _ in range(40):
            names += [n for n in (self.safe_js("chatNames", default=[]) or []) if n not in names]
            if not self.safe_js("paneStep", 0.8, default=False):
                break
            self.wait(250)
        self.safe_js("paneTop")
        return names

    def search_chats(self, text):
        """Type in WhatsApp's search box and return the chat names it shows."""
        if not self.safe_js("markSearch", default=False):
            return []
        self.page.click("[data-prbm-search]")
        self.page.keyboard.press("Control+A")
        self.page.keyboard.type(text, delay=25)
        self.wait(2000)
        names = self.safe_js("sideTitles", default=[]) or []
        self.page.keyboard.press("Control+A")
        self.page.keyboard.press("Backspace")
        self.page.keyboard.press("Escape")
        self.wait(500)
        return names

    # ---- scanning
    def _wait_for_photos(self):
        """Give WhatsApp time to download the photos that are on screen (up to ~2.5 s)."""
        self.wait(150)
        for i in range(10):
            if not self.js("visibleWaiting", i == 0):
                return
            self.wait(250)

    def _step_up(self):
        r = self.js("stepUp", SCROLL_STEP)
        if r is None:
            raise RuntimeError("The WhatsApp chat was closed.")
        if r["after"] < r["before"] - 1:
            return True
        # At the top of what is loaded: wait for WhatsApp to load older messages
        try:
            box = self.page.locator("#main").bounding_box()
            if box:
                self.page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 3)
                self.page.mouse.wheel(0, -1500)
        except Exception:
            pass
        for _ in range(20):
            self.wait(400)
            if self.js("height") > r["height"] + 10:
                return True
        if self.js("clickOlder"):
            self.wait(3000)
        return self.js("height") > r["height"] + 10

    def _keep(self, key, res, sigs, previews, failed):
        try:
            sigs[key] = image_signature(base64.b64decode(res["b64"]))
            if res.get("preview"):
                previews.add(key)
        except Exception as e:
            failed.add(key)
            self.log(f"  could not read a WhatsApp photo: {e}", "warn")

    def _grab_visible(self, snap, sigs, previews, failed):
        """Copy every photo that is on screen now. WhatsApp unloads photos once they
        scroll away, so this is the moment to take them."""
        for r in reversed(snap):
            if not (r["vis"] and r["ready"]):
                continue
            for k in range(r["n"]):
                key = (r["id"], k)
                if key in sigs or key in failed:
                    continue
                res = self.js("fetchImage", r["id"], k, False, False) or {}
                if res.get("b64"):
                    self._keep(key, res, sigs, previews, failed)

    def _bring(self, rid, store):
        """Scroll until the message is on the page again (WhatsApp may have removed it)."""
        for _ in range(60):
            if self.js("scrollToId", rid, "center"):
                self.wait(300)
                return True
            store.merge(self.js("snapshot") or [])
            index = {x: i for i, x in enumerate(store.order)}
            top = self.js("topRowId")
            if rid not in index or top not in index:
                return False
            r = self.js("stepDown" if index[rid] > index[top] else "stepUp", SCROLL_STEP)
            if not r or abs(r["after"] - r["before"]) < 1:
                return False
            self.wait(300)
        return False

    def _add_hits(self, assign, captions, hits, labels):
        for code, keys in assign.items():
            hit = hits.get(code)
            if hit is None:
                hit = hits[code] = WaHit()
                if labels:
                    self.log(f"  found {labels[code].split(' ')[0]} in WhatsApp")
            hit.keys += [k for k in keys if k not in hit.keys]
            hit.captions += [c for c in captions.get(code, []) if c not in hit.captions]

    def _finish(self, store, wanted, use_neighbors, hits, sigs, previews, failed):
        """Go back to the code photos that were not on screen long enough to load."""
        tried = set()
        for _ in range(3):
            rows = store.rows()
            assign, captions = group_messages(rows, wanted, use_neighbors)
            self._add_hits(assign, captions, hits, None)
            todo = {k[0] for ks in assign.values() for k in ks if k not in sigs and k not in failed}
            near = set()     # photo messages next to a code whose photo never showed up
            for i, r in enumerate(rows):
                if set(r["codes"]) & wanted:
                    near.update(x["id"] for x in rows[max(0, i - 6):i + 7])
            todo |= {r["id"] for r in rows if r["media"] and not r["n"] and r["id"] in near}
            todo -= tried
            if not todo:
                return
            index = {x: i for i, x in enumerate(store.order)}
            for n, rid in enumerate(sorted(todo, key=lambda x: -index.get(x, 0)), 1):
                self.status(f"Loading WhatsApp photos that were missed… {n}/{len(todo)}")
                tried.add(rid)
                if not self._bring(rid, store):
                    failed.update((rid, k) for k in range(store.info[rid]["n"]))
                    continue
                info = None
                for t in range(20):                      # up to ~8 s to download
                    info = self.js("rowInfo", rid)
                    if info and info["n"] and info["ready"] == info["n"]:
                        break
                    if t % 4 == 0:
                        self.js("pressDownload", rid)
                    self.wait(400)
                if info:
                    store.update(info)
                for k in range(store.info[rid]["n"]):
                    key = (rid, k)
                    if key in sigs or key in failed:
                        continue
                    res = self.js("fetchImage", rid, k, True, True) or {}
                    if res.get("b64"):
                        self._keep(key, res, sigs, previews, failed)
                    else:
                        failed.add(key)

    def collect(self, products, max_messages, use_neighbors):
        """Scroll the open chat from the newest message upward, a bit less than one screen
        at a time, and copy the photos of every product code while they are on screen."""
        wanted = {p.code for p in products}
        labels = {p.code: p.label for p in products}
        store, hits, sigs = MessageStore(), {}, {}
        previews, failed, given_up = set(), set(), set()
        stalls = extra = 0
        res = ScanResult(hits, sigs, previews, failed, store, given_up)

        self.js("toBottom")
        self.wait(1000)
        self.js("toBottom")
        try:
            while True:
                self.check_stop()
                self._wait_for_photos()
                snap = self.js("snapshot")
                if snap is None:
                    raise RuntimeError("The WhatsApp chat was closed.")
                store.merge(snap)
                self._grab_visible(snap, sigs, previews, failed)
                rows = store.rows()
                assign, captions = group_messages(rows, wanted, use_neighbors)
                self._add_hits(assign, captions, hits, labels)

                given_up |= codes_to_give_up(rows, wanted, set(hits))
                missing = wanted - set(hits) - given_up
                self.status(f"Searching WhatsApp: {len(store)} messages checked, "
                            f"{len(hits)} of {len(wanted)} codes found…")
                at_top = passed_list_top(rows, wanted)
                if not missing or at_top:
                    extra += 1              # a little further, for photos sent before a code text
                    if extra > 2:
                        res.end_reason = ("all codes found" if not (wanted - set(hits)) else
                                          "reached the top of the list" if at_top else
                                          "reached messages older than the codes in the list")
                        break
                if len(store) >= max_messages:
                    res.end_reason = f"checked {len(store)} messages (limit in Options)"
                    self.log(f"Stopped searching after {len(store)} messages (limit in Options).", "warn")
                    break
                if len(store) >= 800 and not hits and not any(split_code(c)[0] in
                        {split_code(x)[0] for r in rows for x in r["codes"]} for c in wanted):
                    res.end_reason = "none of these codes are in this chat"
                    self.log("None of these codes were found in the last 800 messages. "
                             "Is the right chat open?", "warn")
                    break
                if self._step_up():
                    stalls = 0
                else:
                    stalls += 1
                    if stalls >= 2:
                        res.end_reason = "reached the beginning of the chat"
                        self.log("Reached the beginning of the WhatsApp chat.")
                        break
            self._finish(store, wanted, use_neighbors, hits, sigs, previews, failed)
        except StopRequested:
            res.stopped = True
            res.end_reason = "stopped by you"
        return res


# --------------------------------------------------------------------------- renaming

class Renamer:
    def __init__(self, dry_run):
        self.dry = dry_run
        self.reserved = set()
        self.log_path = None
        self.count = 0

    def free_path(self, folder, base, ext):
        k, cand = 1, folder / f"{base}{ext}"
        while cand.exists() or str(cand).lower() in self.reserved:
            k += 1
            cand = folder / f"{base} ({k}){ext}"
        self.reserved.add(str(cand).lower())
        return cand

    def _record(self, action, src, dst):
        self.count += 1
        if self.log_path is None:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            self.log_path = LOG_DIR / f"rename_{datetime.now():%Y%m%d_%H%M%S}.csv"
        with open(self.log_path, "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow([action, str(src), str(dst)])

    def next_numbered(self, folder, prefix, ext):
        """"img1.jpg", or the next number not used yet in the folder ("img4.jpg" after img1-3)."""
        taken = {p.stem.lower() for p in folder.iterdir()} if folder.exists() else set()
        k = 1
        while (f"{prefix}{k}".lower() in taken
               or any(r.startswith(str(folder / f"{prefix}{k}.").lower()) for r in self.reserved)):
            k += 1
        cand = folder / f"{prefix}{k}{ext}"
        self.reserved.add(str(cand).lower())
        return cand

    def mkdir(self, folder):
        if not self.dry and not folder.exists():
            folder.mkdir()
            self._record("mkdir", "", folder)

    def move(self, src, dst):
        if not self.dry:
            os.rename(src, dst)
            self._record("move", src, dst)

    def copy(self, src, dst):
        if not self.dry:
            shutil.copy2(src, dst)
            self._record("copy", src, dst)


# --------------------------------------------------------------------------- worker thread

class Worker(threading.Thread):
    """Runs every long job (and owns the browser) so the window never freezes."""

    def __init__(self, ui_queue):
        super().__init__(daemon=True)
        self.ui = ui_queue
        self.jobs = queue.Queue()
        self.stop_event = threading.Event()
        self.wa = WhatsAppWeb(self.log, self.status, self.check_stop)

    def log(self, msg, tag=""):
        self.ui.put(("log", (msg, tag)))

    def status(self, text):
        self.ui.put(("status", text))

    def progress(self, value, maximum):
        self.ui.put(("progress", (value, maximum)))

    def check_stop(self):
        if self.stop_event.is_set():
            raise StopRequested()

    def submit(self, fn, *args):
        self.jobs.put((fn, args))

    def run(self):
        while True:
            job = self.jobs.get()
            if job is None:
                break
            fn, args = job
            self.stop_event.clear()
            self.ui.put(("busy", True))
            try:
                fn(*args)
            except StopRequested:
                self.log("Stopped.", "warn")
            except Exception as e:
                msg = str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
                if "closed" in msg.lower() and not self.wa.is_open():
                    msg = "The WhatsApp window was closed. Press the button again to reopen it."
                self.log(f"Error: {msg}", "err")
                try:
                    DATA_DIR.mkdir(parents=True, exist_ok=True)
                    ERROR_FILE.write_text(traceback.format_exc(), encoding="utf-8")
                except OSError:
                    pass
            finally:
                self.progress(0, 1)
                self.status("Ready.")
                self.ui.put(("busy", False))
        self.wa.close()

    # ---- jobs
    def job_open_whatsapp(self, groups, shop_chats=None):
        """Open WhatsApp and find the chat of each list: the one the shop settings give it
        (`shop_chats`, {group: chat name}), or else the one named for it ("My Shop (6-)" for 6-)."""
        self.wa.open()
        self.wa.wait_logged_in()
        if not groups:
            self.log("WhatsApp is ready. Choose the product list, then press Start.", "ok")
            return
        shop_chats = shop_chats or {}
        with self.wa.turn():
            self.status("Looking for the chat of each list…")
            names = self.wa.chat_names()
            found = {}
            for g in groups:
                want = (shop_chats.get(g) or "").strip().lower()
                same = lambda n: want and n.strip().lower() == want
                hit = next((n for n in names if same(n) or (not want and group_in_name(g, n))), None)
                if hit is None and (want or g):
                    more = self.wa.search_chats(shop_chats.get(g) or g)
                    names += [n for n in more if n not in names]
                    hit = next((n for n in more if same(n) or (not want and group_in_name(g, n))), None)
                if hit:
                    found[g] = hit
        self.ui.put(("chats", (names, found)))
        self.log("Chats for the lists:", "head")
        for g in groups:
            if g in found:
                self.log(f"  {group_label(g)}  →  {found[g]}", "ok")
            elif shop_chats.get(g):
                self.log(f"  {group_label(g)}  →  no chat \"{shop_chats[g]}\" (Shop settings), please choose it", "warn")
            elif g:
                self.log(f"  {group_label(g)}  →  no chat with \"{g}\" in its name, please choose it", "warn")
            else:
                self.log(f"  {group_label(g)}  →  set your orders chat in Shop settings, or choose it", "warn")
        self.log("Check the chats (you can change them), then press Start.")

    def job_run(self, opts):
        root = Path(opts["folder"])
        products, skipped = parse_products(opts["txt"])
        groups = list_groups(products)
        lists = [l for l in opts["lists"] if l["on"] and l["group"] in groups]
        if not lists:
            raise RuntimeError("Tick at least one list.")
        if not root.is_dir():
            raise RuntimeError("Please choose the photo folder.")

        total = sum(len(groups[l["group"]]) for l in lists)
        self.log("")
        self.log(f"{total} product codes in {len(lists)} list(s): "
                 f"{', '.join(l['group'] or 'Orders' for l in lists)} (bottom list first)", "head")
        if skipped:
            self.log(f"Skipped (no product name): {', '.join(skipped)}")

        renamer = Renamer(opts["dry_run"])
        stats = defaultdict(int)
        report, all_photos = [], []
        for l in lists:
            self.check_stop()
            ps = groups[l["group"]]
            self.log("")
            self.log(f"── {group_label(l['group'])}: {len(ps)} codes, from {ps[0].code} up to {ps[-1].code} ──", "head")
            folder = list_folder(root, l["group"])
            if folder is None:
                self.log(f'No folder "{l["group"]}" in {root}, so this list is skipped.', "warn")
                continue
            photos = self.scan_folder(root, folder, opts["recursive"])
            all_photos += photos
            if not photos:
                self.log(f"No photos in folder {folder.name}, so this list is skipped.", "warn")
                continue
            self.status("Opening WhatsApp…")
            self.progress(0, 0)
            self.wa.open()
            self.wa.wait_logged_in()
            with self.wa.turn():
                self.wa.open_chat(l["chat"])
                self.log(f"Searching from the newest message up to {ps[-1].code}…")
                scan = self.wa.collect(ps, opts["max_messages"], opts["neighbors"])
            if scan.stopped:
                self.stop_event.clear()
                self.log("Search stopped. Renaming the photos found so far (press Stop again to cancel)…", "warn")
            lines = self.match_list(root, ps, scan, photos, opts, renamer, stats)
            report += self.report_lines(l["group"], scan, lines)
            if scan.stopped:
                break
        self.summary(root, all_photos, stats, renamer, opts["dry_run"])
        self.save_report(root, report, all_photos)

    def scan_folder(self, root, folder, recursive):
        """Photos of one list's folder (plus the folders earlier runs made, like "6-840 …")."""
        done = done_re()
        if recursive:
            files = list(folder.rglob("*"))
        else:
            files = list(folder.iterdir())
            files += [p for d in list(files) if d.is_dir() and done.match(d.name) for p in d.iterdir()]
        entries = []
        for p in files:
            if not (p.is_file() and p.suffix.lower() in IMAGE_EXTS):
                continue
            parts = [p.stem] + list(reversed(p.relative_to(folder).parts[:-1]))
            m = next((done.match(x) for x in parts if done.match(x)), None)
            entries.append((p, norm_code(m.group(1), m.group(2)) if m else ""))
        entries.sort(key=lambda e: str(e[0]).lower())
        done = sum(1 for _, c in entries if c)
        self.log(f"Folder {folder.name}: {len(entries)} photos" +
                 (f" ({done} already named with a code: they are renamed again if their name is not "
                  "the one in the list)" if done else ""))
        photos = []
        for i, (p, code) in enumerate(entries, 1):
            self.check_stop()
            self.status(f"Reading photos in the folder… {i}/{len(entries)}")
            self.progress(i, len(entries))
            try:
                photos.append(LocalPhoto(p, image_signature(p.read_bytes()), code))
            except Exception as e:
                self.log(f"  skipped {p.name} (cannot read it: {e})", "warn")
        return photos

    @staticmethod
    def rel(root, path):
        try:
            return os.path.relpath(path, root)
        except ValueError:
            return str(path)

    def match_list(self, root, products, scan, photos, opts, renamer, stats):
        """Match one list's WhatsApp photos with the folder and rename. Returns report lines."""
        rel = lambda path: self.rel(root, path)
        limit = opts["threshold"]
        report = []
        self.log("Matching the WhatsApp photos with the photos in the folder" +
                 (" (PREVIEW ONLY, nothing is renamed)" if renamer.dry else ""))
        for i, p in enumerate(products, 1):
            self.check_stop()
            self.progress(i, len(products))
            self.status(f"Matching {i}/{len(products)}: {p.label}")
            hit = scan.hits.get(p.code)
            if not hit:
                why = " (the search had already passed it)" if p.code in scan.given_up else ""
                self.log(f"✗ {p.label}: not found in WhatsApp", "warn")
                report.append(f"{p.label}: not found in WhatsApp{why}")
                stats["not_found"] += 1
                continue
            wa = [(k, scan.sigs[k]) for k in hit.keys if scan.sigs.get(k)]
            if not wa:
                self.log(f"✗ {p.label}: found in WhatsApp, but its photo could not be loaded", "warn")
                report.append(f"{p.label}: found in WhatsApp, {len(hit.keys)} photo(s), none could be loaded")
                stats["no_match"] += 1
                continue

            chosen, closest = [], None
            for key, wsig in wa:
                # Among matching photos prefer: already named with this code, then a photo
                # not used in this run, and only then one used for another code (it gets copied)
                best, best_rank = None, None
                for ph in photos:
                    d = difference(wsig, ph.sig)
                    closest = d if closest is None else min(closest, d)
                    if d > limit:
                        continue
                    kind = 0 if ph.done_code == p.code else 1 if not ph.used_for else 2
                    if best_rank is None or (kind, d) < best_rank:
                        best, best_rank = ph, (kind, d)
                if best is not None and all(best is not c for c, _, _ in chosen):
                    chosen.append((best, best_rank[1], key))
            report.append(f"{p.label}: {len(wa)} WhatsApp photo(s)"
                          f"{' (small preview only)' if any(k in scan.previews for k, _ in wa) else ''}; " +
                          (", ".join(f"{rel(ph.path)} diff {d}" for ph, d, _ in chosen) or
                           f"no match, closest diff {closest}"))
            if not chosen:
                why = f" (closest difference {closest}, limit {limit})" if closest is not None else ""
                self.log(f"✗ {p.label}: {len(wa)} WhatsApp photo(s), none of them is in the folder{why}", "warn")
                stats["no_match"] += 1
                continue

            # photos that already have this code's name stay as they are; any other name is renamed
            plain = safe_filename(p.label)
            total = len(chosen)
            already = [c for c in chosen if not c[0].used_for and named_for(c[0].current, plain)]
            chosen = [c for c in chosen if all(c is not a for a in already)]
            for ph, _, _ in already:
                ph.used_for.append(p.label)
                self.log(f"✓ {p.label}: already named ({rel(ph.current)})", "dim")
            if not chosen:
                stats["already"] += 1
                continue

            note = caption_note(hit.captions, p.label) if opts["add_note"] else ""
            base = safe_filename(p.label + note)
            try:
                self.apply_names(p, base, chosen, renamer, scan.previews, rel, total)
                stats["codes"] += 1
                stats["photos"] += len(chosen)
            except OSError as e:
                self.log(f"✗ {p.label}: could not rename ({e})", "err")
                stats["errors"] += 1
        return report

    @staticmethod
    def home(ph):
        """Where a photo's new name goes: its folder, or the one above when that is already a
        code's folder ("6-840 …/img1.jpg", copied for a second code)."""
        parent = ph.path.parent
        return parent.parent if ph.done_code and done_re().match(parent.name) else parent

    def apply_names(self, product, base, chosen, renamer, previews, rel, total):
        """One photo: "2-226 Realme P3 Ultra.jpg". Several (total, with the ones already named):
        a folder "2-221 Oppo a6 pro 5g" with img1.jpg, img2.jpg …"""
        if total == 1:
            ph = chosen[0][0]
            targets = [renamer.free_path(self.home(ph), base, ph.path.suffix)]
        else:
            folder = self.home(chosen[0][0]) / base
            renamer.mkdir(folder)
            targets = [renamer.next_numbered(folder, "img", ph.path.suffix.lower()) for ph, _, _ in chosen]

        for (ph, d, key), dst in zip(chosen, targets):
            how = "perfect match" if d == 0 else f"difference {d}"
            if key in previews:
                how += ", compared with WhatsApp's small preview"
            if ph.used_for:
                renamer.copy(ph.current, dst)
                other = ph.used_for[0] if ph.used_for else rel(ph.path)
                self.log(f"✓ {product.label}: same photo as {other}, "
                         f"{'would copy' if renamer.dry else 'copied'} to {rel(dst)}", "ok")
            else:
                renamer.move(ph.current, dst)
                if not renamer.dry:
                    ph.current = dst
                self.log(f"✓ {product.label}: {ph.path.name}  →  {rel(dst)}  ({how})", "ok")
            ph.used_for.append(product.label)

    def summary(self, root, photos, stats, renamer, dry):
        unused = [ph for ph in photos if not ph.used_for and not ph.done_code]
        self.log("")
        self.log("Summary", "head")
        self.log(f"  {'Would rename' if dry else 'Renamed'}: {stats['photos']} photo(s) for {stats['codes']} code(s)", "ok")
        if stats["already"]:
            self.log(f"  Already named right: {stats['already']} code(s)")
        self.log(f"  Not found in WhatsApp: {stats['not_found']}")
        self.log(f"  Found in WhatsApp but no matching photo in the folder: {stats['no_match']}")
        if stats["errors"]:
            self.log(f"  Errors: {stats['errors']}", "err")
        self.log(f"  Photos in the folder that matched no code: {len(unused)}")
        for ph in unused[:40]:
            self.log(f"     {self.rel(root, ph.path)}")
        if len(unused) > 40:
            self.log(f"     … and {len(unused) - 40} more")
        if dry:
            self.log("Preview only - nothing was changed. Untick 'Preview only' and press Start to rename.", "warn")
        elif renamer.count:
            self.log("Done. If something is wrong, press 'Undo last rename'.", "ok")

    def report_lines(self, group, scan, code_lines):
        rows = scan.store.rows()
        lines = ["", f"=== {group_label(group)} - chat \"{self.wa.chat_title}\"",
                 f"Messages checked: {len(rows)} (with a photo: {sum(1 for r in rows if r['n'])}, "
                 f"photo not loaded: {sum(1 for r in rows if r['media'] and not r['n'])})",
                 f"Search ended: {scan.end_reason}",
                 f"WhatsApp photos loaded: {len(scan.sigs)}, small previews: {len(scan.previews)}, "
                 f"failed: {len(scan.failed)}", ""]
        lines += code_lines
        lines += ["", "Messages that contain a code (oldest first):"]
        lines += [f"  {r['id'][-10:]}  codes={','.join(r['codes'])}  photos={r['n']}  "
                  f"loaded={r['blob']}  media={r['media']}" for r in rows if r["codes"]]
        return lines

    def save_report(self, root, report, photos):
        """Details of the last run (codes, file names and numbers only), to find problems."""
        unused = [ph for ph in photos if not ph.used_for and not ph.done_code]
        lines = [f"{APP_TITLE} - {datetime.now():%d-%m-%Y %H:%M}"] + report
        lines += ["", "Photos that matched no code:"] + [f"  {self.rel(root, ph.path)}" for ph in unused]
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            REPORT_FILE.write_text("\n".join(lines), encoding="utf-8")
        except OSError:
            pass

    def job_undo(self):
        logs = sorted(p for p in LOG_DIR.glob("rename_*.csv") if not p.name.endswith(".undone.csv"))
        if not logs:
            self.log("Nothing to undo.", "warn")
            return
        path = logs[-1]
        with open(path, newline="", encoding="utf-8") as f:
            rows = [r for r in csv.reader(f) if len(r) == 3]
        ok = failed = 0
        for action, src, dst in reversed(rows):
            try:
                if action == "move":
                    if Path(dst).exists() and not Path(src).exists():
                        os.rename(dst, src)
                        ok += 1
                    else:
                        failed += 1
                        self.log(f"  could not move back: {dst}", "warn")
                elif action == "copy" and Path(dst).exists():
                    os.remove(dst)
                    ok += 1
                elif action == "mkdir":
                    try:
                        os.rmdir(dst)
                    except OSError:
                        pass
            except OSError as e:
                failed += 1
                self.log(f"  {e}", "err")
        path.rename(path.with_name(path.stem + ".undone.csv"))
        self.log(f"Undo finished: {ok} change(s) reverted" +
                 (f", {failed} could not be reverted." if failed else "."), "ok" if not failed else "warn")
