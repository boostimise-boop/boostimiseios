"""WhatsApp Web automation with Playwright.

Every method must be called from the thread that called start() (Playwright's sync API
is bound to one thread). WhatsApp is the tab of the browser window all tools share
(core/whatsapp.py), so the QR code only has to be scanned once for every tool.
"""
import base64
import io
import time
import unicodedata
from dataclasses import dataclass, field

from PIL import Image, ImageOps
from playwright.sync_api import Error as PWError, sync_playwright

from core import whatsapp

from .codes import code_matches, codes_in_text, split_code

MARKER_MAX_LEN = 120     # a message that doesn't START with the code must be this short to count
GROUP_MAX_MSGS = 80      # never look further than this many messages away from a code
NEAR_MSGS = 12           # a model name written in chat must be this close to the code
RANGE_BELOW = 40         # codes this far below the list's smallest number still count as boundaries
RANGE_ABOVE = 300        # ... and this far above its largest number


class StopRequested(Exception):
    pass


@dataclass
class Msg:
    id: str
    text: str = ""
    imgs: int = 0
    codes: list = field(default_factory=list)
    rendered: bool = False   # WhatsApp only draws messages near the screen; others are empty shells


@dataclass
class Group:
    code: str
    marker_id: str
    marker_text: str
    image_ids: list = field(default_factory=list)
    other_ids: list = field(default_factory=list)  # rest of the group, tried if image_ids give no photo
    context: list = field(default_factory=list)    # all chat text in the group, closest first
    near: list = field(default_factory=list)       # order message, captions and the messages right by the photos
    shared: list = field(default_factory=list)     # near texts that sit closer to another code's message


# --------------------------------------------------------------------------- JavaScript
_HELPERS = r"""
function mmdScroller() {
  const main = document.querySelector('#main');
  if (!main) return null;
  const cached = main.querySelector('[data-mmd-scroller]');
  if (cached && cached.clientHeight >= 150) return cached;
  if (cached) cached.removeAttribute('data-mmd-scroller');
  let best = null, bestH = 0;
  for (const d of main.querySelectorAll('div')) {
    if (d.clientHeight < 150) continue;
    const oy = getComputedStyle(d).overflowY;
    if ((oy === 'auto' || oy === 'scroll') && d.clientHeight > bestH) { best = d; bestH = d.clientHeight; }
  }
  if (best) best.setAttribute('data-mmd-scroller', '1');
  return best;
}
function mmdPaneScroller() {
  const pane = document.querySelector('#pane-side');
  if (!pane) return null;
  if (pane.scrollHeight > pane.clientHeight + 5) return pane;
  for (const d of pane.querySelectorAll('div')) {
    const oy = getComputedStyle(d).overflowY;
    if ((oy === 'auto' || oy === 'scroll') && d.scrollHeight > d.clientHeight + 5) return d;
  }
  let p = pane.parentElement;
  while (p && p !== document.body) {
    const oy = getComputedStyle(p).overflowY;
    if ((oy === 'auto' || oy === 'scroll') && p.scrollHeight > p.clientHeight + 5) return p;
    p = p.parentElement;
  }
  return pane;
}
function mmdTopMsgs() {
  const main = document.querySelector('#main');
  if (!main) return [];
  return Array.from(main.querySelectorAll('[data-id]'))
    .filter(e => !(e.parentElement && e.parentElement.closest('[data-id]')));
}
function mmdFindMsg(id) {
  for (const e of mmdTopMsgs()) if (e.getAttribute('data-id') === id) return e;
  return null;
}
const MMD_QUOTED = '.quoted-mention, [aria-label*="uoted"], [data-testid*="quoted"]';
function mmdSrc(e) {
  if (e.tagName === 'IMG') return e.getAttribute('src') || '';
  const m = /url\(["']?(.*?)["']?\)/.exec(e.style.backgroundImage || '');
  return m ? m[1] : '';
}
function mmdPick(el, selector) {
  const out = [], seen = new Set();
  for (const e of el.querySelectorAll(selector)) {
    if (e.closest(MMD_QUOTED) || e.closest('.emoji, [data-plain-text]')) continue;
    const src = mmdSrc(e);
    if (!/^(blob:|data:image)/.test(src) || seen.has(src)) continue;   // skips profile pictures (https)
    if (e.tagName === 'IMG' && e.complete && e.naturalWidth > 0 && e.naturalWidth < 30) continue;  // spacer gifs
    const r = e.getBoundingClientRect();
    if (r.width < 60 || r.height < 60) continue;                        // emoji, icons, quoted thumbnails
    seen.add(src);
    out.push(e);
  }
  return out;
}
function mmdImgs(el) {
  let cand = mmdPick(el, 'img');
  if (!cand.length) cand = mmdPick(el, 'div[style*="background-image"]');
  // WhatsApp stacks a small blurred preview under each photo: keep one picture per tile, the sharpest
  const tiles = new Map();
  for (const e of cand) {
    const r = e.getBoundingClientRect();
    const key = Math.round(r.left / 8) + ':' + Math.round(r.top / 8) + ':' + Math.round(r.width / 8);
    const prev = tiles.get(key);
    if (!prev || (e.naturalWidth || 0) > (prev.naturalWidth || 0)) tiles.set(key, e);
  }
  return Array.from(tiles.values());
}
function mmdLoaded(imgs) {
  // a photo counts as loaded once the sharp version is in (not the ~100px preview)
  return imgs.filter(i => mmdSrc(i).startsWith('blob:')
                          && (i.tagName !== 'IMG' || (i.complete && i.naturalWidth >= 250))).length;
}
function mmdText(el) {
  const parts = [];
  for (const s of el.querySelectorAll('span.selectable-text')) {
    if (s.closest(MMD_QUOTED)) continue;
    if (s.parentElement && s.parentElement.closest('span.selectable-text')) continue;
    const t = (s.innerText || s.textContent || '').trim();
    if (t) parts.push(t);
  }
  if (!parts.length) {
    const c = el.querySelector('.copyable-text');
    if (c && !c.closest(MMD_QUOTED)) {
      const t = (c.innerText || '').trim();
      if (t) parts.push(t);
    }
  }
  return parts.join('\n').slice(0, 1000);
}
function mmdState(sc) {
  const r = sc.getBoundingClientRect();
  return {top: sc.scrollTop, height: sc.scrollHeight, client: sc.clientHeight,
          cx: r.left + r.width / 2, cy: r.top + r.height / 2};
}
"""


def _js(params, body, is_async=False):
    return f"{'async ' if is_async else ''}{params} => {{\n{_HELPERS}\n{body}\n}}"


JS_APP_STATE = r"""() => {
  if (document.querySelector('#pane-side')) return 'ready';
  if (document.querySelector('canvas')) return 'qr';
  return 'loading';
}"""

JS_CHATLIST = _js("()", r"""
  const pane = document.querySelector('#pane-side');
  if (!pane) return null;
  let rows = pane.querySelectorAll('[role="listitem"]');
  if (!rows.length) rows = pane.querySelectorAll('[role="row"]');
  const names = [];
  if (rows.length) {
    for (const r of rows) { const t = r.querySelector('span[title]'); if (t) names.push(t.getAttribute('title')); }
  } else {
    for (const t of pane.querySelectorAll('span[title][dir="auto"]')) names.push(t.getAttribute('title'));
  }
  const sc = mmdPaneScroller();
  return {names, top: sc.scrollTop, height: sc.scrollHeight, client: sc.clientHeight};
""")

JS_CHATLIST_SCROLL = _js("(y)", r"""
  const sc = mmdPaneScroller();
  if (!sc) return -1;
  sc.scrollTop = y;
  return sc.scrollTop;
""")

JS_FILTER_CHIP = r"""(labels) => {
  const side = document.querySelector('#side') || document.body;
  for (const b of side.querySelectorAll('button, [role="tab"], [role="button"]')) {
    const t = (b.innerText || '').trim().toLowerCase();
    if (labels.some(l => t === l || t.startsWith(l + '\n'))) return b;
  }
  return null;
}"""

JS_FIND_CHAT = r"""(name) => {
  const pane = document.querySelector('#pane-side');
  if (!pane) return null;
  const want = name.trim(), low = want.toLowerCase();
  const spans = Array.from(pane.querySelectorAll('span[title]'));
  return spans.find(s => (s.getAttribute('title') || '').trim() === want)
      || spans.find(s => (s.getAttribute('title') || '').trim().toLowerCase() === low) || null;
}"""

JS_HEADER_IS = r"""(name) => {
  const h = document.querySelector('#main header');
  if (!h) return false;
  const norm = s => (s || '').normalize('NFKC').replace(/[^\p{L}\p{N}]+/gu, '').toLowerCase();
  const want = norm(name);
  if (!want) return true;
  for (const s of h.querySelectorAll('span[title]')) if (norm(s.getAttribute('title')) === want) return true;
  const lines = (h.innerText || '').split('\n').map(norm).filter(Boolean);
  return lines.slice(0, 2).some(l => l === want);
}"""

JS_HAS_MESSAGES = r"""() => !!document.querySelector('#main [data-id]')"""

JS_SCROLLER_STATE = _js("()", r"""
  const sc = mmdScroller();
  return sc ? mmdState(sc) : null;
""")

JS_SCROLL_BOTTOM = _js("()", r"""
  const sc = mmdScroller();
  if (!sc) return null;
  sc.scrollTop = sc.scrollHeight;
  return mmdState(sc);
""")

JS_SCROLL_UP = _js("(frac)", r"""
  const sc = mmdScroller();
  if (!sc) return null;
  const before = sc.scrollTop;
  sc.scrollTop = Math.max(0, before - sc.clientHeight * frac);
  const s = mmdState(sc);
  s.before = before;
  return s;
""")

JS_COLLECT = _js("(win)", r"""
  const sc = mmdScroller();
  let lo = -Infinity, hi = Infinity, vtop = -Infinity, vbot = Infinity;
  if (sc) {
    const r = sc.getBoundingClientRect();
    vtop = r.top; vbot = r.bottom;
    lo = r.top - r.height * win;
    hi = r.bottom + r.height * win;
  }
  const msgs = [];
  let pending = 0;   // messages on screen that WhatsApp has not drawn yet
  for (const el of mmdTopMsgs()) {
    const r = el.getBoundingClientRect();
    if (r.bottom < lo || r.top > hi) continue;
    const imgs = mmdImgs(el);
    const rendered = (el.innerText || '').trim().length > 0 || imgs.length > 0;
    if (!rendered && r.bottom > vtop && r.top < vbot) pending++;
    msgs.push({id: el.getAttribute('data-id'), text: rendered ? mmdText(el) : '', imgs: imgs.length, r: rendered});
  }
  return {msgs, pending};
""")

JS_OLDER_BUTTON = r"""() => {
  const main = document.querySelector('#main');
  if (!main) return null;
  for (const el of main.querySelectorAll('button, [role="button"], span, div')) {
    if (el.children.length > 3) continue;
    const t = (el.innerText || '').trim();
    if (t && t.length < 120 && /older messages/i.test(t)) return el;
  }
  return null;
}"""

JS_SCROLL_INTO_VIEW = _js("(id)", r"""
  const el = mmdFindMsg(id);
  if (!el) return false;
  el.scrollIntoView({block: 'center'});
  return true;
""")

JS_FIND_MSG = _js("(id)", "return mmdFindMsg(id);")

JS_MSG_STATE = _js("(id)", r"""
  const el = mmdFindMsg(id);
  if (!el) return null;
  const imgs = mmdImgs(el);
  return {imgs: imgs.length, loaded: mmdLoaded(imgs), dl: !!el.querySelector('[data-icon*="download"]'),
          rendered: (el.innerText || '').trim().length > 0 || imgs.length > 0};
""")

JS_DOWNLOAD_BTN = _js("(id)", r"""
  const el = mmdFindMsg(id);
  if (!el) return null;
  const i = el.querySelector('[data-icon*="download"]');
  return i ? (i.closest('button, [role="button"]') || i) : null;
""")

JS_FETCH_IMAGES = _js("([id, maxN])", r"""
  const el = mmdFindMsg(id);
  if (!el) return [];
  const out = [];
  for (const img of mmdImgs(el)) {
    if (out.length >= maxN) break;
    const src = mmdSrc(img);
    try {
      if (src.startsWith('blob:')) {
        const b = await (await fetch(src)).blob();
        const data = await new Promise((ok, bad) => {
          const fr = new FileReader();
          fr.onload = () => ok(fr.result);
          fr.onerror = () => bad(fr.error);
          fr.readAsDataURL(b);
        });
        out.push({data, full: true});
        continue;
      }
    } catch (e) { /* blob gone - copy the drawn picture instead */ }
    try {
      if (img.tagName === 'IMG' && img.complete && img.naturalWidth > 60) {
        const c = document.createElement('canvas');
        c.width = img.naturalWidth; c.height = img.naturalHeight;
        c.getContext('2d').drawImage(img, 0, 0);
        out.push({data: c.toDataURL('image/jpeg', 0.92), full: src.startsWith('blob:')});
      } else if (src.startsWith('data:image')) {
        out.push({data: src, full: false});
      }
    } catch (e) { /* skip this image */ }
  }
  return out;
""", is_async=True)


# --------------------------------------------------------------------------- helpers
def prepare_image(raw, max_side=1600):
    """Decode any image bytes -> JPEG bytes (max 1600px: WhatsApp's own size, so small print stays
    readable), or None if unusable."""
    try:
        im = Image.open(io.BytesIO(raw))
        im.load()
        im = ImageOps.exif_transpose(im)
        if min(im.size) < 48:
            return None
        if im.mode != "RGB":
            im = im.convert("RGB")
        im.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=88)
        return buf.getvalue()
    except Exception:
        return None


def _decode_data_url(url):
    try:
        return base64.b64decode(url.split(",", 1)[1])
    except Exception:
        return b""


def _merge(order, known, ids):
    """Merge a chronological window of message ids into the chronological list `order`."""
    had_known = any(i in known for i in ids)
    pending = []
    for mid in ids:
        if mid in known:
            if pending:
                pos = order.index(mid)
                order[pos:pos] = pending
                known.update(pending)
                pending = []
        else:
            pending.append(mid)
    if pending:
        if had_known:
            order.extend(pending)       # newer than everything we know
        else:
            order[0:0] = pending        # a window with no overlap while scrolling up = older
        known.update(pending)


def marker_codes(text, has_images, ranges):
    """Codes (from the searched blocks) that label this message.

    A message that starts with the code is a label however long it is (order messages:
    "2-577 / name / address / Due 300"). Otherwise the code must sit in a short message
    or in a photo caption.
    """
    text = (text or "").strip()
    if not text:
        return []
    m = next(code_matches(text), None)
    starts_with_code = bool(m and m.start() == 0)
    if not starts_with_code and len(text) > MARKER_MAX_LEN and not has_images:
        return []
    out = []
    for code in codes_in_text(text):
        p, n = split_code(code)
        if p in ranges and ranges[p][0] - RANGE_BELOW <= n <= ranges[p][1] + RANGE_ABOVE:
            out.append(code)
    return out


def build_groups(order, msgs, found, mode, max_images):
    """For every found code pick the messages (photos + text) that belong to it.

    Messages labelled with the same code one after another (the order message, then the
    photo album whose caption repeats the code) form ONE group.
    mode "below": the group runs from the code's first message until another code starts.
    mode "above": the group runs back from the code's last message to the previous code.
    If a code appears in several places, the place with photos wins, then the oldest.
    """
    # runs of consecutive code messages that share a code
    segments = []  # [first_index, last_index, set(codes)]
    for i, mid in enumerate(order):
        codes = set(msgs[mid].codes)
        if not codes:
            continue
        if segments and codes & segments[-1][2]:
            segments[-1][1] = i
            segments[-1][2] |= codes
        else:
            segments.append([i, i, codes])

    groups = {}
    for k, (first, last, codes) in enumerate(segments):
        if mode == "above":
            start = segments[k - 1][1] + 1 if k > 0 else 0
            span = list(range(last, max(start, last - GROUP_MAX_MSGS) - 1, -1))   # closest to the code first
            other = start - 1 if k > 0 else None      # the previous code's message
        else:
            end = segments[k + 1][0] if k + 1 < len(segments) else len(order)
            span = list(range(first, min(end, first + GROUP_MAX_MSGS)))
            other = end if k + 1 < len(segments) else None
        for code in codes:
            if code not in found:
                continue
            g = Group(code=code, marker_id=order[first], marker_text=msgs[order[first]].text)
            count = 0
            has_photos = False
            for pos, j in enumerate(span):
                m = msgs[order[j]]
                # a message never drawn while scrolling may still hold photos: try it too
                if (m.imgs or not m.rendered) and count < max_images:
                    g.image_ids.append(m.id)
                    count += max(m.imgs, 1)
                    has_photos = has_photos or m.imgs > 0
                elif pos <= NEAR_MSGS:
                    g.other_ids.append(m.id)   # photos may have arrived after the message was read
                if m.text and not m.codes:
                    g.context.append(m.text)
                if m.text and pos <= NEAR_MSGS:
                    # "Tecno spark 8 pro" posted right by the previous code belongs to that code
                    mine = j - last if j > last else first - j if j < first else 0
                    closer_to_other = other is not None and abs(j - other) < mine
                    (g.shared if closer_to_other else g.near).append(m.text)
            score = (1 if has_photos else 0, -first)
            if code not in groups or score > groups[code][0]:
                groups[code] = (score, g)
    return {code: g for code, (_, g) in groups.items()}


# --------------------------------------------------------------------------- driver
class WhatsAppDriver:
    def __init__(self, log=print, should_stop=lambda: False):
        self.log = log
        self.should_stop = should_stop
        self.pw = None
        self.page = None
        self.reached_top = False   # last scan hit the oldest message WhatsApp Web would load

    # ---- lifecycle
    def start(self):
        """Connects to the WhatsApp window that all tools share (opens it if needed)."""
        self.pw = sync_playwright().start()
        try:
            _, ctx = whatsapp.connect(self.pw, self.log)
            self.page = whatsapp.whatsapp_page(ctx)
        except BaseException:
            self.close()
            raise

    def alive(self):
        """True while the browser window is open (WhatsApp may be busy reloading its page)."""
        for _ in range(6):
            try:
                if self.page is None or self.page.is_closed():
                    return False
                if self.page.evaluate("1") == 1:
                    return True
            except Exception:
                pass
            time.sleep(0.5)
        return False

    def close(self):
        """Lets go of the WhatsApp window; it stays open for the other tools."""
        try:
            if self.pw:
                self.pw.stop()
        except Exception:
            pass
        self.page = self.pw = None

    def check_stop(self):
        if self.should_stop():
            raise StopRequested()

    def _sleep(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            self.check_stop()
            time.sleep(min(0.2, max(0.0, end - time.time())))

    # ---- login
    def wait_until_ready(self, timeout=300, on_state=None):
        last = None
        end = time.time() + timeout
        while time.time() < end:
            self.check_stop()
            try:
                state = self.page.evaluate(JS_APP_STATE)
            except PWError:
                state = "loading"
            if state != last and on_state:
                on_state(state)
            last = state
            if state == "ready":
                self._sleep(2)  # let the chat list render
                return True
            self._sleep(1)
        return False

    # ---- chat list
    def _click_filter(self, labels):
        el = self.page.evaluate_handle(JS_FILTER_CHIP, labels).as_element()
        if not el:
            return False
        try:
            el.click(timeout=3000)
            self._sleep(1.2)
            return True
        except PWError:
            return False

    def list_chats(self, groups_only=False, on_progress=None):
        filtered = groups_only and self._click_filter(["groups", "গ্রুপ", "গ্রুপগুলো"])
        if groups_only and not filtered:
            self.log("'Groups' filter button not found - listing all chats.")
        names = []
        seen = set()
        self.page.evaluate(JS_CHATLIST_SCROLL, 0)
        self._sleep(0.6)
        stuck = 0
        for _ in range(2000):
            info = self.page.evaluate(JS_CHATLIST)
            if not info:
                break
            for n in info["names"]:
                n = (n or "").strip()
                if n and n not in seen:
                    seen.add(n)
                    names.append(n)
            if on_progress:
                on_progress(len(names))
            new_top = self.page.evaluate(JS_CHATLIST_SCROLL, info["top"] + max(200, info["client"] * 0.7))
            if new_top <= info["top"] + 1:
                stuck += 1
                if stuck >= 3:
                    break
            else:
                stuck = 0
            self._sleep(0.35)
        self.page.evaluate(JS_CHATLIST_SCROLL, 0)
        if filtered:
            self._click_filter(["all", "সব", "সকল"])
        return names

    # ---- open a chat
    def _header_is(self, name):
        try:
            return bool(self.page.evaluate(JS_HEADER_IS, name))
        except PWError:
            return False

    def _wait_header(self, name, timeout):
        end = time.time() + timeout
        while time.time() < end:
            if self._header_is(name):
                return True
            self._sleep(0.3)
        return False

    def _click_chat(self, name):
        el = self.page.evaluate_handle(JS_FIND_CHAT, name).as_element()
        if not el:
            return False
        try:
            el.scroll_into_view_if_needed(timeout=3000)
            el.click(timeout=5000)
            return True
        except PWError:
            return False

    def _search_box(self):
        for sel in (
            '#side div[contenteditable="true"]',
            '#side input[type="text"]',
            '#side input:not([type])',
            '#side [role="textbox"]',
            'div[contenteditable="true"][data-tab="3"]',
            'input[aria-label*="Search"]',
        ):
            loc = self.page.locator(sel).first
            try:
                if loc.count() and loc.is_visible():
                    return loc
            except PWError:
                pass
        return None

    def _clear_search(self, box):
        try:
            box.click(timeout=3000)
            self.page.keyboard.press("Control+A")
            self.page.keyboard.press("Backspace")
        except PWError:
            pass

    def open_chat(self, name):
        if self._header_is(name):
            return self._wait_messages()
        if self._click_chat(name) and self._wait_header(name, 6):
            return self._wait_messages()

        box = self._search_box()
        if box:
            # type the name without emoji/symbols (letters, Bengali vowel signs, digits, punctuation stay)
            query = "".join(ch for ch in name if unicodedata.category(ch)[0] in "LMNPZ").strip() or name
            self._clear_search(box)
            self.page.keyboard.type(query, delay=25)
            end = time.time() + 6
            while time.time() < end:
                self._sleep(0.4)
                if self._click_chat(name) and self._wait_header(name, 6):
                    self._clear_search(box)
                    return self._wait_messages()
            self._clear_search(box)
            self._sleep(1)

        # last resort: scroll through the chat list
        self.page.evaluate(JS_CHATLIST_SCROLL, 0)
        self._sleep(0.5)
        for _ in range(1000):
            if self._click_chat(name) and self._wait_header(name, 6):
                return self._wait_messages()
            info = self.page.evaluate(JS_CHATLIST)
            if not info:
                break
            new_top = self.page.evaluate(JS_CHATLIST_SCROLL, info["top"] + max(200, info["client"] * 0.7))
            if new_top <= info["top"] + 1:
                break
            self._sleep(0.35)
        return False

    def _wait_messages(self, timeout=10):
        end = time.time() + timeout
        while time.time() < end:
            if self.page.evaluate(JS_HAS_MESSAGES):
                self._sleep(1)
                return True
            self._sleep(0.4)
        return True  # empty chat - still opened

    # ---- scan messages
    def _scroll_to_bottom(self):
        last = None
        for _ in range(15):
            st = self.page.evaluate(JS_SCROLL_BOTTOM)
            if st is None:
                return
            self._sleep(0.8)
            st = self.page.evaluate(JS_SCROLLER_STATE)
            if not st or st["height"] == last:
                return
            last = st["height"]

    def _wait_growth(self, height, timeout):
        end = time.time() + timeout
        while time.time() < end:
            self._sleep(0.4)
            st = self.page.evaluate(JS_SCROLLER_STATE)
            if st and st["height"] > height + 5:
                return True
        return False

    def _try_load_older(self, height):
        st = self.page.evaluate(JS_SCROLLER_STATE)
        if st:
            try:
                self.page.mouse.move(st["cx"], st["cy"])
                for _ in range(3):
                    self.page.mouse.wheel(0, -1500)
                    self._sleep(0.3)
            except PWError:
                pass
            if self._wait_growth(height, 4):
                return True
        el = self.page.evaluate_handle(JS_OLDER_BUTTON).as_element()
        if el:
            self.log("Asking the phone for older messages (keep the phone online)...")
            try:
                el.click(timeout=3000)
            except PWError:
                return False
            return self._wait_growth(height, 40)
        return False

    def scan_chat(self, ranges, targets, extra_screens=15, max_steps=2000, step_delay=0.7):
        """Scroll the open chat upward from the newest message, reading every message,
        until all target codes (and the code just before the oldest one) have been seen.

        ranges:  {prefix: (smallest number, largest number)} of the searched code block(s)
        targets: set of codes to find
        Returns (order, msgs, found): message ids oldest->newest, {id: Msg}, {code: [ids]}.
        """
        order, known, msgs, found = [], set(), {}, {}
        self.reached_top = False
        lower_seen = {p: False for p in ranges}
        last_progress = 0
        top_hits = 0
        self._scroll_to_bottom()
        step = 0
        while True:
            self.check_stop()
            batch = self._collect()
            _merge(order, known, [m["id"] for m in batch])
            for m in batch:
                mid = m["id"]
                rec = msgs.get(mid)
                if rec is None:
                    rec = msgs[mid] = Msg(mid)
                rec.imgs = max(rec.imgs, m["imgs"])
                if rec.rendered or not m["r"]:
                    continue  # already read, or still an empty shell (read it once WhatsApp draws it)
                rec.rendered = True
                rec.text = m["text"]
                rec.codes = marker_codes(rec.text, rec.imgs > 0, ranges)
                for code in rec.codes:
                    p, n = split_code(code)
                    if code in targets:
                        if code not in found:
                            found[code] = []
                            last_progress = step
                            self.log(f"  found {code}  ({len(found)}/{len(targets)})")
                        found[code].append(mid)
                    elif n < ranges[p][0] and found and not lower_seen[p]:
                        lower_seen[p] = True
                        last_progress = step
                        self.log(f"  reached {code} (older than the list)")

            all_found = len(found) == len(targets)
            all_lower = all(lower_seen.values())
            if all_found and all_lower and step - last_progress >= 2:
                break
            if (all_found or all_lower) and step - last_progress >= extra_screens:
                break
            if step >= max_steps:
                self.log(f"  stopped after {max_steps} scroll steps")
                break

            st = self.page.evaluate(JS_SCROLL_UP, 0.85)
            if st is None:
                break  # chat is too short to scroll: everything is already read
            if st["before"] <= 1:
                grew = self._wait_growth(st["height"], 5) or self._try_load_older(st["height"])
                if grew:
                    top_hits = 0
                else:
                    top_hits += 1
                    if top_hits >= 3:
                        self.reached_top = True
                        self.log("  reached the start of what WhatsApp Web can load for this chat")
                        break
            else:
                self._sleep(step_delay)
            step += 1
            if step % 25 == 0:
                self.log(f"  scrolling... {len(order)} messages read, {len(found)}/{len(targets)} codes found")
        unread = sum(1 for m in msgs.values() if not m.rendered)
        if unread:
            self.log(f"  note: {unread} messages were never drawn by WhatsApp while scrolling")
        return order, msgs, found

    def _collect(self):
        """Read the messages around the screen, giving WhatsApp a moment to draw the visible ones."""
        end = time.time() + 2.5
        while True:
            data = self.page.evaluate(JS_COLLECT, 1.5) or {"msgs": [], "pending": 0}
            if not data["pending"] or time.time() > end:
                return data["msgs"]
            self._sleep(0.3)

    # ---- photos
    def fetch_images(self, msg_id, max_n, photo_wait=4):
        """Scroll a message into view, wait for its photos to download and return them as JPEG bytes.
        photo_wait: seconds to wait for photos to appear in a message that is drawn without any."""
        if max_n <= 0 or not self.page.evaluate(JS_SCROLL_INTO_VIEW, msg_id):
            return []
        t0 = time.time()
        clicked = False
        while True:
            self.check_stop()
            st = self.page.evaluate(JS_MSG_STATE, msg_id)
            if not st:
                return []
            waited = time.time() - t0
            if not st["rendered"] or not st["imgs"]:
                # WhatsApp draws a message (and then its photos) a moment after it scrolls into view
                if waited > (10 if not st["rendered"] else photo_wait):
                    return []
                self._sleep(0.3)
                continue
            if st["loaded"] >= min(st["imgs"], max_n):
                break
            if st["dl"] and not clicked and waited > 1.0:
                btn = self.page.evaluate_handle(JS_DOWNLOAD_BTN, msg_id).as_element()
                if btn:
                    try:
                        btn.click(timeout=3000)
                        clicked = True
                    except PWError:
                        pass
            if waited > (15 if clicked else 5):
                break
            self._sleep(0.4)

        out = []
        for item in self.page.evaluate(JS_FETCH_IMAGES, [msg_id, max_n]) or []:
            img = prepare_image(_decode_data_url(item.get("data", "")))
            if img:
                out.append(img)
        if not out:  # fall back to a screenshot of the message bubble
            el = self.page.evaluate_handle(JS_FIND_MSG, msg_id).as_element()
            if el:
                try:
                    img = prepare_image(el.screenshot(type="png", timeout=5000))
                    if img:
                        out.append(img)
                except PWError:
                    pass
        return out[:max_n]
