"""Browser side of the WhatsApp -> Pathao auto-fill: reads order messages in WhatsApp Web and
fills Pathao's New Delivery form. Everything runs on one worker thread that talks to the
window through two queues (commands in, events out).
"""
import collections
import copy
import json
import os
import queue
import statistics
import threading
import time
import traceback
import unicodedata
import urllib.parse
from datetime import datetime, timedelta

from core import whatsapp
from core.paths import data_dir

from .order_parser import find_codes, find_phones, parse_order, split_code, write_phone_to_list

# Settings, the saved history, the log and the browser logins (data/pathao_autofill).
APP_DIR = str(data_dir("pathao_autofill"))
SETTINGS_FILE = os.path.join(APP_DIR, "settings.json")
SAVED_LOG = os.path.join(APP_DIR, "saved_codes.txt")
LOG_FILE = os.path.join(APP_DIR, "autofill.log")

DEFAULT_SETTINGS = {
    "list_file": r"C:\covercanvasbd\productcodes.txt",
    "start_from_bottom": True,
    "always_on_top": False,
    "write_phone_to_list": True,  # after a save, put the phone next to its code in the list file
    # code prefix -> WhatsApp chat name (a prefix missing here uses the chat "<prefix>-")
    "chat_names": {
        "2": "Free Cover BD(2-)",
        "3": "Cover Print BD 0.1 (3-)",
        "4": "Customise Cover BD (4-)",
        "4a": "Customise Cover BD(4a-)",
        "5": "Misty Cover House 3.0 (5-)",
        "5a": "Misty Cover House (5a-)",
        "6": "Customise Phone Case (6-)",
    },
    "pathao_url": "https://merchant.pathao.com/",
    "pathao_create_url": "",  # learned the first time the New Delivery form is found
    "max_scrolls": 400,  # how many screens to scroll up a WhatsApp chat looking for a code
    "placeholders": {
        "order_id": "Type ID (Optional)",
        "amount": "Enter the amount including delivery charge",
        "phone": "Enter phone number",
        "name": "Type Name",
        "address": "Enter full address",
    },
}

# key -> (placeholder key, which match, label shown above the box)
FIELDS = {
    "order_id": ("order_id", 0, "Merchant Order ID"),
    "amount": ("amount", 0, "Amount to Collect"),
    "phone": ("phone", 0, "Recipient's Phone"),
    "phone2": ("phone", 1, "Recipient's Secondary Phone"),
    "name": ("name", 0, "Recipient's Name"),
    "address": ("address", 0, "Recipient's Address"),
}

PENDING = "Pending"
FETCHING = "Reading WhatsApp..."
FILLED = "Filled - waiting for Save"
SAVED = "Saved ✓"
SAVED_EARLIER = "Saved earlier"
SKIPPED = "Skipped"
NOT_FOUND = "Not found"
IN_PATHAO = "Saved (in Pathao)"  # "Check Pathao" found an order with this code as its Merchant Order ID

# ---------------------------------------------------------------------------
# JavaScript run inside the pages
# ---------------------------------------------------------------------------

# Finds a Pathao input by placeholder, falling back to the label text above it.
_JS_FIND = r"""
const __vis = (el) => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
const __find = (spec) => {
  if (!spec) return null;
  const inputs = [...document.querySelectorAll('input, textarea')].filter(__vis);
  const ph = (spec.ph || '').trim().toLowerCase();
  if (ph) {
    const hits = inputs.filter(e => (e.getAttribute('placeholder') || '').trim().toLowerCase() === ph);
    if (hits.length > spec.nth) return hits[spec.nth];
  }
  if (!spec.label) return null;
  const norm = (s) => (s || '').replace(/[\u2018\u2019`]/g, "'").replace(/\s+/g, ' ').trim().toLowerCase();
  const want = norm(spec.label);
  for (const lab of document.querySelectorAll('label, span, div, p, legend, strong, b, h5, h6')) {
    if (norm(lab.textContent) !== want || !__vis(lab)) continue;
    const next = inputs.find(i => lab.compareDocumentPosition(i) & Node.DOCUMENT_POSITION_FOLLOWING);
    if (next) return next;
  }
  return null;
};
"""

# Remembers (in sessionStorage, so it survives a page change) when Save was clicked.
_JS_SAVE_HOOK = r"""
if (!window.__ccbdHooked) {
  window.__ccbdHooked = true;
  const mark = (on) => {
    try { on ? sessionStorage.setItem('__ccbdSave', String(Date.now())) : sessionStorage.removeItem('__ccbdSave'); } catch (e) {}
  };
  document.addEventListener('click', (e) => {
    let el = e.target;
    for (let i = 0; el && i < 5; i++, el = el.parentElement) {
      if (el.matches && el.matches('input, textarea, select') && !['submit', 'button'].includes(el.type)) return;
      const t = ((el.innerText || el.value || '') + '').trim().toLowerCase();
      if (t.length > 40) return;
      if (['save', 'save order', 'submit', 'confirm'].includes(t)) { mark(true); return; }
      if (['cancel', 'reset', 'clear'].includes(t)) { mark(false); return; }
    }
  }, true);
  document.addEventListener('submit', () => mark(true), true);
}
"""

JS_PATHAO_STATE = "(specs) => {" + _JS_FIND + _JS_SAVE_HOOK + r"""
  const val = (k) => { const el = __find(specs[k]); return el ? (el.value || '') : null; };
  const oid = val('order_id');
  const toasts = [];
  document.querySelectorAll('[role="alert"], [role="status"], [role="dialog"], [class*="toast" i], [class*="notif" i], [class*="snack" i], [class*="swal" i], [class*="alert" i], [class*="message" i]').forEach(el => {
    const t = (el.textContent || '').replace(/\s+/g, ' ').trim();
    if (t && t.length <= 300 && /success|created|placed|সফল/i.test(t) && __vis(el)) toasts.push(t);
  });
  let click = 0;
  try { click = parseInt(sessionStorage.getItem('__ccbdSave') || '0', 10) || 0; } catch (e) {}
  return {url: location.href, docStart: performance.timeOrigin, click, form: oid !== null,
          orderId: oid, phone: val('phone'), address: val('address'), toasts};
}"""

JS_MARK_FIELD = "(spec) => {" + _JS_FIND + r"""
  document.querySelectorAll('[data-ccbd-field]').forEach(e => e.removeAttribute('data-ccbd-field'));
  const el = __find(spec);
  if (!el) return false;
  el.setAttribute('data-ccbd-field', '1');
  return true;
}"""

JS_FIELD_VALUE = "(spec) => {" + _JS_FIND + r"""
  const el = __find(spec);
  return el ? (el.value || '') : null;
}"""

JS_CLEAR_SAVE_FLAG = "() => { try { sessionStorage.removeItem('__ccbdSave'); } catch (e) {} }"

# The orders created from `from` to `to` (YYYY-MM-DD), read from the API behind the merchant panel's
# Orders page with the panel's own login. Archived orders are left out, like on that page, and so are
# return orders (RC...): they carry the merchant as recipient, and the delivery they return may be older.
JS_PATHAO_ORDERS = r"""async ({from, to}) => {
  let token = '';
  try { token = JSON.parse(localStorage.getItem('pathaoMerchant:auth') || '{}').token || ''; } catch (e) {}
  if (!token) return {error: 'login'};
  const orders = [];
  for (let page = 1; page <= 100; page++) {
    const qs = new URLSearchParams({page, limit: 500, archive: 0, from_date: from, to_date: to});
    let r;
    for (let tries = 1; ; tries++) {  // "Too Many Requests": wait a little longer each time
      r = await fetch('/api/v1/orders/all?' + qs, {headers: {Authorization: 'Bearer ' + token, Accept: 'application/json'}});
      if (r.status !== 429 || tries === 6) break;
      await new Promise(done => setTimeout(done, 3000 * tries));
    }
    if (r.status === 401 || r.status === 403) return {error: 'login'};
    if (!r.ok) return {error: 'Pathao answered ' + r.status};
    const d = (await r.json()).data || {};
    for (const o of d.data || []) {
      if (o.order_type === 'Return') continue;
      orders.push({id: o.merchant_order_id || '', phone: o.recipient_phone || '', name: o.recipient_name || '',
                   amount: o.order_amount, created: o.order_created_at || '', status: o.order_status || '',
                   consignment: o.order_consignment_id || ''});
    }
    if (!d.last_page || page >= d.last_page) break;
  }
  return {orders};
}"""

PATHAO_LOOKBACK_DAYS = 45  # how far back "Check Pathao" reads the orders
RESTART_DROP = 40  # a prefix starting its codes again (6-980 -> 6-47..6-1) drops at least this much...
RESTART_RATIO = 3  # ...to at most a third of the numbers before


def round_starts(orders):
    """{prefix: created time} of when each prefix last started its codes again from 1. The same
    code comes back in every round (5-36 on 5 Oct and again after 5-1 on 13 Oct), so only the
    orders of the current round say whether a code of the list is in Pathao. A restart is where the
    10 orders before were high and the 10 from there on are low (a list is often saved from the
    bottom, so a round can start 6-47, 6-46...); one late order of an old round in between, or a
    few late ones of this round, don't count as one."""
    by_prefix = collections.defaultdict(list)
    for o in orders:
        for code in find_codes(o["id"]):
            prefix, number = split_code(code)
            by_prefix[prefix].append((o["created"], number))
    starts = {}
    for prefix, rows in by_prefix.items():
        rows.sort()
        nums = [n for _, n in rows]
        last = None
        for i in range(1, len(nums)):
            before, after = statistics.median(nums[max(0, i - 10):i]), statistics.median(nums[i:i + 10])
            if (before - after >= RESTART_DROP and after * RESTART_RATIO <= before
                    and nums[i] - after < RESTART_DROP):  # this order is one of the low ones itself
                if last is None or i - last > 10:
                    starts[prefix] = rows[i][0]  # the first order of a new round
                last = i
    return starts


JS_WA_READY = """() => !!document.querySelector('#pane-side, #side, [aria-label="Chat list"]')"""

JS_WA_HEADER = "() => { const h = document.querySelector('#main header'); return h ? h.innerText : ''; }"

JS_WA_MARK_SEARCH = r"""() => {
  document.querySelectorAll('[data-ccbd-search]').forEach(e => e.removeAttribute('data-ccbd-search'));
  const vis = (el) => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
  const boxes = [...document.querySelectorAll('input, [contenteditable="true"]')]
    .filter(el => vis(el) && !el.closest('#main') && !el.closest('footer'));
  const words = (el) => [el.getAttribute('aria-label'), el.getAttribute('placeholder'), el.getAttribute('title'),
                         el.getAttribute('data-placeholder'), el.parentElement && el.parentElement.getAttribute('aria-label')].join(' ');
  let box = boxes.find(el => /search|খুঁজ|অনুসন্ধান|সার্চ/i.test(words(el)));
  const side = document.querySelector('#side');
  if (!box && side) box = boxes.find(el => side.contains(el));
  if (!box) return false;
  box.setAttribute('data-ccbd-search', '1');
  return true;
}"""

# Marks the search result whose title is the chat name, ignoring case, spaces, brackets and
# emoji: "Customise Cover BD(4a-)" matches "Customise Cover BD (4a-)" but not "... (4-)".
JS_WA_MARK_CHAT = r"""(name) => {
  document.querySelectorAll('[data-ccbd-chat]').forEach(e => e.removeAttribute('data-ccbd-chat'));
  const norm = (s) => (s || '').toLowerCase().replace(/[^\p{L}\p{M}\p{N}]+/gu, ' ').trim();
  const want = norm(name);
  const root = document.querySelector('#side') || document.querySelector('#pane-side') || document.body;
  const hit = [...root.querySelectorAll('span[title]')]
    .find(s => !s.closest('#main') && norm(s.getAttribute('title')) === want);
  if (!hit) return false;
  hit.setAttribute('data-ccbd-chat', '1');
  return true;
}"""

# Finds the scrolling message list of the open chat and scrolls it. arg is 'bottom' / 'none', or
# {where: 'above', anchor: <message id>}: put that message at 80% of the view's height, so the
# messages just above it come on screen. WhatsApp only draws the messages near the screen (the rest
# are empty [data-virtualized="true"] boxes), so the chat is read a screen at a time.
# Returns the scroll position, how many messages on screen are not drawn yet, and where the anchor
# message is (px from the top of the view; null when it is not in the page).
JS_WA_SCROLL = r"""(arg) => {
  const main = document.querySelector('#main');
  if (!main) return null;
  let sc = main.querySelector('[data-ccbd-scroller]');
  if (!sc) {
    for (const d of main.querySelectorAll('div')) {
      if (d.scrollHeight <= d.clientHeight + 20) continue;
      const oy = getComputedStyle(d).overflowY;
      if (oy !== 'auto' && oy !== 'scroll') continue;
      if (!d.querySelector('[data-id], [role="row"], [data-pre-plain-text]')) continue;
      if (!sc || d.scrollHeight > sc.scrollHeight) sc = d;
    }
    if (!sc) return null;
    sc.setAttribute('data-ccbd-scroller', '1');
  }
  const where = typeof arg === 'string' ? arg : (arg || {}).where;
  const id = typeof arg === 'string' ? null : (arg || {}).anchor;
  const anchorEl = () => id ? sc.querySelector('[data-id="' + CSS.escape(id) + '"]') : null;
  const offset = (el) => el.getBoundingClientRect().top - sc.getBoundingClientRect().top;
  if (where === 'bottom') sc.scrollTop = sc.scrollHeight;
  else if (where === 'top') sc.scrollTop = 0;
  else if (where === 'above') {
    const a = anchorEl();
    const step = a ? offset(a) - Math.round(sc.clientHeight * 0.8) : -Math.round(sc.clientHeight * 0.6);
    sc.scrollTop = Math.max(0, sc.scrollTop + step);
  }
  const box = sc.getBoundingClientRect();
  let undrawn = 0;
  for (const v of sc.querySelectorAll('[data-virtualized="true"]')) {
    const b = v.getBoundingClientRect();
    if (b.bottom > box.top && b.top < box.bottom) undrawn++;
  }
  const a = anchorEl();
  const first = main.querySelector('[data-id], [role="row"]');
  return {first: first ? (first.getAttribute('data-id') || (first.textContent || '').slice(0, 80)) : '',
          height: sc.scrollHeight, top: sc.scrollTop, view: sc.clientHeight, undrawn,
          anchorY: a ? Math.round(offset(a)) : null};
}"""

# Puts the message with this data-id in the middle of the chat and marks it with a yellow band (a
# style rule, so the mark survives WhatsApp redrawing the row). null removes the mark.
# False when the message is not in the page.
JS_WA_SHOW = r"""(id) => {
  let st = document.getElementById('ccbd-mark');
  if (!st) {
    st = document.createElement('style');
    st.id = 'ccbd-mark';
    document.head.appendChild(st);
  }
  if (!id) { st.textContent = ''; return true; }
  const sel = '#main [data-id="' + CSS.escape(id) + '"]';
  const el = document.querySelector(sel);
  if (!el) return false;
  el.scrollIntoView({block: 'center'});
  st.textContent = sel + ' { background-color: rgba(255, 196, 0, .32) !important; border-radius: 8px;'
                 + ' box-shadow: inset 5px 0 0 #f0a500 !important; }';
  return true;
}"""

JS_WA_OLDER = r"""() => {
  const main = document.querySelector('#main');
  if (!main) return false;
  for (const el of main.querySelectorAll('button, [role="button"]')) {
    if (/older messages|পুরোনো|পুরনো/i.test(el.textContent || '')) { el.click(); return true; }
  }
  return false;
}"""

# Text of every drawn message in the open chat, oldest first: {ok, messages: [{id, text}], top}.
# top = the topmost message of the unbroken run of drawn messages that reaches the screen; everything
# from there down is read now. ok is false when `prev` (the previous step's top) is not in that run,
# i.e. there is an undrawn gap between the two and nothing may be read yet.
JS_WA_EXTRACT = r"""(prev) => {
  const main = document.querySelector('#main');
  if (!main) return {ok: false, messages: [], top: null};
  const sc = main.querySelector('[data-ccbd-scroller]') || main;
  const box = sc.getBoundingClientRect();
  const rows = [...sc.querySelectorAll('[data-id]')];
  const drawn = (r) => !r.querySelector('[data-virtualized="true"]');
  let i = rows.findIndex(r => r.getBoundingClientRect().bottom > box.top);
  let lo = i, hi = i;
  if (i >= 0 && drawn(rows[i])) {
    while (lo > 0 && drawn(rows[lo - 1])) lo--;
    while (hi < rows.length - 1 && drawn(rows[hi + 1])) hi++;
  }
  const top = i >= 0 && drawn(rows[i]) ? rows[lo].getAttribute('data-id') : null;
  if (prev && !(top && rows.slice(lo, hi + 1).some(r => r.getAttribute('data-id') === prev))) {
    return {ok: false, messages: [], top};
  }
  for (const b of main.querySelectorAll('[role="button"], button')) {
    const t = (b.textContent || '').trim().toLowerCase();
    if (t === 'read more' || t === 'আরও পড়ুন' || t === 'আরো পড়ুন') { try { b.click(); } catch (e) {} }
  }
  const out = new Map();
  for (const el of main.querySelectorAll('[data-pre-plain-text], span.selectable-text')) {
    if (el.closest('footer') || el.closest('[contenteditable="true"]')) continue;
    let textEl = el, pri = 2;
    if (el.hasAttribute('data-pre-plain-text')) {
      textEl = el.querySelector('span.selectable-text') || el;
    } else {
      if (el.closest('[data-pre-plain-text]')) continue;
      if (el.parentElement && el.parentElement.closest('span.selectable-text')) continue;
      pri = 1;
    }
    const text = (textEl.innerText || '').trim();
    if (!text) continue;
    const row = el.closest('[data-id]');
    const key = row ? row.getAttribute('data-id') : 't:' + text.slice(0, 150);
    const old = out.get(key);
    if (!old || pri > old.pri) out.set(key, {id: key, text, pri});
  }
  return {ok: true, messages: [...out.values()], top};
}"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_settings():
    s = copy.deepcopy(DEFAULT_SETTINGS)
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            user = json.load(f)
    except (OSError, ValueError):
        user = {}
    for k, v in user.items():
        if isinstance(v, dict) and isinstance(s.get(k), dict):
            s[k].update(v)
        else:
            s[k] = v
    return s


def save_settings(s):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def read_saved_codes():
    try:
        with open(SAVED_LOG, encoding="utf-8") as f:
            return {line.split("\t", 1)[0].strip() for line in f if line.strip()}
    except OSError:
        return set()


def append_saved(code, data):
    data = data or {}
    row = [code, datetime.now().strftime("%Y-%m-%d %H:%M:%S")] + [
        str(data.get(k, "")).replace("\t", " ").replace("\n", " ")
        for k in ("name", "phone", "phone2", "amount", "address")
    ]
    with open(SAVED_LOG, "a", encoding="utf-8") as f:
        f.write("\t".join(row) + "\n")


def norm_name(name):
    """Chat name for comparing: lower case, emoji and punctuation dropped."""
    kept = [ch if ch.isalnum() or unicodedata.category(ch).startswith("M") else " " for ch in (name or "").lower()]
    return " ".join("".join(kept).split())


def url_key(url):
    p = urllib.parse.urlsplit(url or "")
    return f"{p.netloc}{p.path.rstrip('/')}"


class NeedUser(Exception):
    """Something the user has to do in the browser first (log in, open a page, fix a name)."""


class Aborted(Exception):
    """A newer command from the window interrupts the current step."""


# Commands that interrupt a WhatsApp search that is still running
INTERRUPTING = {"pause", "skip", "jump", "mark_saved", "load", "quit", "refetch", "refill"}


# ---------------------------------------------------------------------------
# Browser worker (all Playwright calls happen on this thread)
# ---------------------------------------------------------------------------

class Worker(threading.Thread):
    def __init__(self, cmd_q, evt_q, settings):
        super().__init__(daemon=True)
        self.cmd_q, self.evt_q = cmd_q, evt_q
        self.settings = settings
        self.pending = collections.deque()
        self.pw = self.browser = self.ctx = None
        self.wa = self.pathao = None
        self.hooked_pages = set()
        self.items = []  # [{"code", "status", "data", "raw"}] in processing order
        self.list_file = ""  # the list file the items came from
        self.cur = None  # index of the code that is on the Pathao form right now
        self.running = False
        self.waiting = False  # the form is filled, waiting for the user's Save
        self.cache = {}  # code -> {"id", "text", "data"} read from WhatsApp
        self.duplicates = {}  # code -> phones of different order messages with that code
        self.scanned = {}  # prefix -> (low, high): codes low < n <= high were all read in one pass of its chat
        self.progress = {}  # prefix -> {"anchor", "trail"}: how far up its chat the searches have read
        self.open_chat = None
        self.responses = []  # (start ms, status, method, url) of Pathao POST/PUT calls
        self.reported = set()
        self.baseline_toasts = set()
        self.fill_url = ""
        self.armed_ms = 0
        self.success_hits = self.reset_hits = 0
        self.reset_warned = False

    # -- plumbing --------------------------------------------------------
    def emit(self, *event):
        self.evt_q.put(event)

    def log(self, msg):
        self.emit("log", msg)

    def attention(self, msg):
        self.emit("attention", msg)

    def run(self):
        try:
            from playwright.sync_api import sync_playwright

            self.pw = sync_playwright().start()
        except Exception as e:
            self.attention(f"Playwright is not available ({e}). Install it with:  pip install playwright")
            return
        while True:
            cmd = self._next_cmd(0.4)
            if cmd:
                if cmd[0] == "quit":
                    break
                self._safe(self.handle, cmd)
            elif self.running:
                self._safe(self.check_saved if self.waiting else self.process_next)
        try:
            self.pw.stop()  # only disconnects; the browser window stays open
        except Exception:
            pass

    def _next_cmd(self, timeout):
        if self.pending:
            return self.pending.popleft()
        try:
            return self.cmd_q.get(timeout=timeout)
        except queue.Empty:
            return None

    def _check_abort(self):
        while True:
            try:
                self.pending.append(self.cmd_q.get_nowait())
            except queue.Empty:
                break
        if any(c[0] in INTERRUPTING for c in self.pending):
            raise Aborted()

    def _safe(self, fn, *args):
        try:
            fn(*args)
        except Aborted:
            pass
        except NeedUser as e:
            self.running = self.waiting = False
            self.attention(str(e))
        except Exception as e:
            self.running = self.waiting = False
            self.attention(f"Stopped because of an error: {e}")
            self.log(traceback.format_exc(limit=4))

    def _index(self, code):
        return next((i for i, it in enumerate(self.items) if it["code"] == code), None)

    def set_status(self, idx, status):
        item = self.items[idx]
        item["status"] = status
        self.emit("row", item["code"], status, item.get("data"))

    def chat_for(self, prefix):
        return (self.settings.get("chat_names", {}).get(prefix) or f"{prefix}-").strip()

    # -- commands from the window ---------------------------------------
    def handle(self, cmd):
        kind = cmd[0]
        if kind == "settings":
            old = self.settings.get("chat_names")
            self.settings = cmd[1]
            if old != self.settings.get("chat_names"):
                self.cache.clear()
                self.duplicates.clear()
                self.scanned.clear()
                self.progress.clear()
                self.open_chat = None
        elif kind == "load":
            self.items = [{"code": c, "status": s, "data": None, "raw": ""} for c, s in cmd[1]]
            self.list_file = cmd[2]
            self.cur, self.running, self.waiting = None, False, False
        elif kind == "connect":
            self.connect()
            wa = self.whatsapp_page()
            self.pathao_page()
            logged_in = self._eval(wa, JS_WA_READY)
            self.log("WhatsApp Web: " + ("logged in." if logged_in else "not logged in yet - scan the QR code in the WhatsApp tab."))
        elif kind == "start":
            self.connect()
            self.running = True
            if self.cur is not None and self.items[self.cur]["status"] == FILLED:
                self.arm()
                self.emit("state", f"{self.items[self.cur]['code']} is on the Pathao form - click Save there.", "wait")
            else:
                self.emit("state", "Running...", "busy")
        elif kind == "pause":
            self.running = self.waiting = False
            self.emit("state", "Paused. Press Start to continue.", "info")
        elif kind == "skip":
            idx = self.cur if self.cur is not None else self._next_pending()
            if idx is not None:
                self.set_status(idx, SKIPPED)
                self.log(f"{self.items[idx]['code']}: skipped.")
            self.cur, self.waiting, self.running = None, False, True
        elif kind == "mark_saved":
            if self.cur is None:
                self.log("No code is on the Pathao form right now.")
                return
            self.record_saved(self.cur)
            self.cur, self.waiting, self.running = None, False, True
        elif kind == "jump":
            idx = self._index(cmd[1])
            if idx is not None:
                self.running = True
                self.process(idx, prepare=True, explicit=True)
        elif kind == "refetch":
            if self.cur is None:
                self.log("No code is on the Pathao form to read again.")
                return
            self.cache.pop(self.items[self.cur]["code"], None)
            self.open_chat = None
            self.running = True
            self.process(self.cur, prepare=False, explicit=True)
        elif kind == "refill":
            self.refill(cmd[1], cmd[2])
        elif kind == "check_pathao":
            self.check_pathao()

    # -- browser ---------------------------------------------------------
    def connect(self):
        if self.browser is not None:
            try:
                if self.browser.is_connected():
                    return
            except Exception:
                pass
        # WhatsApp and Pathao are tabs of the browser window all tools share, so one login is enough
        try:
            self.browser, self.ctx = whatsapp.connect(self.pw, self.log)
        except whatsapp.BrowserError as e:
            raise NeedUser(str(e))
        self.wa = self.pathao = None
        self.open_chat = None
        self.emit("browser", "Browser connected", True)
        self.log("Browser connected. The first time, log in to WhatsApp Web (scan QR) and Pathao in that window.")

    def _eval(self, page, js, arg=None):
        try:
            return page.evaluate(js, arg)
        except Exception:
            return None

    def whatsapp_page(self):
        self.connect()
        if self.wa is not None and not self.wa.is_closed() and "web.whatsapp.com" in self.wa.url:
            return self.wa
        self.wa = whatsapp.whatsapp_page(self.ctx)
        self.open_chat = None
        return self.wa

    def pathao_page(self):
        self.connect()
        p = self.pathao
        if p is not None and not p.is_closed() and "pathao" in (p.url or ""):
            return p
        pages = [p for p in self.ctx.pages if "pathao" in (p.url or "")]
        chosen = None
        for p in pages:
            st = self._state(p)
            if st and st["form"]:
                chosen = p
                self._learn_create_url(st["url"])
                break
        if chosen is None and pages:
            chosen = pages[0]
        if chosen is None:
            chosen = self.ctx.new_page()
            chosen.goto(self.settings.get("pathao_create_url") or self.settings["pathao_url"],
                        wait_until="domcontentloaded", timeout=60000)
        if id(chosen) not in self.hooked_pages:
            chosen.on("response", self._on_response)
            self.hooked_pages.add(id(chosen))
        self.pathao = chosen
        return chosen

    def _on_response(self, resp):
        try:
            req = resp.request
            if req.method not in ("POST", "PUT", "PATCH") or req.resource_type not in ("xhr", "fetch", "document"):
                return
            start = (req.timing or {}).get("startTime", -1)
            if not start or start <= 0:
                start = time.time() * 1000
            self.responses.append((start, resp.status, req.method, req.url))
            del self.responses[:-200]
        except Exception:
            pass

    # -- WhatsApp ----------------------------------------------------------
    def _found(self, code):
        """True when the order message of this code (one with a phone number) has been read."""
        rec = self.cache.get(code)
        return bool(rec and rec["data"]["phone"])

    def lookup(self, code, reread=False):
        if self._found(code):
            return self.cache[code]
        prefix, number = split_code(code)
        chat = self.chat_for(prefix)
        wa = self.whatsapp_page()
        wa.bring_to_front()
        end = time.time() + 60
        while not self._eval(wa, JS_WA_READY):
            if time.time() > end:
                raise NeedUser("WhatsApp Web is not logged in. Scan the QR code in the WhatsApp tab, then press Start.")
            self._check_abort()
            wa.wait_for_timeout(500)
        self.emit("state", f"Looking for {code} in WhatsApp chat '{chat}'...", "busy")
        self.open_whatsapp_chat(wa, chat)

        # Never scroll far above the lowest code of this prefix in the loaded list (e.g. 6-855 for
        # 6-855..6-878): once older codes show up, everything in the list has been read.
        floor = min([number] + [split_code(it["code"])[1] for it in self.items
                                if split_code(it["code"])[0] == prefix])
        done = self.scanned.get(prefix)
        if done and done[0] < number <= done[1] and not reread:
            return self.cache.get(code)  # this part of the chat was already read: the code is not there

        # Read the chat from the newest message upwards. Each step puts the topmost message read so
        # far near the bottom of the screen, so no message between two steps is skipped - not even
        # when WhatsApp loads older messages and moves the view up by itself.
        # It stops once the last 3 different codes read are all below `stop`. A single old code in
        # between (a note like "6-790 background change" posted among the 6-89x orders) is not enough.
        # The next search in this chat goes on from where this one stopped: everything below that
        # message has been read, and the list goes from newer to older codes, so the next code is
        # just above. A double-click (reread) starts again from the newest message.
        stop = max(floor, number - 40)
        pos = self.progress.get(prefix)
        here = pos and self._eval(wa, JS_WA_SCROLL, {"where": "none", "anchor": pos["anchor"]})
        if reread or not here or here["anchorY"] is None:
            self._eval(wa, JS_WA_SCROLL, "bottom")
            # anchor = topmost message read so far; trail = code numbers in the order read, newest first
            pos = self.progress[prefix] = {"anchor": None, "trail": []}
        trail = pos["trail"]
        last_top = None
        stale = 0
        for _ in range(max(400, int(self.settings.get("max_scrolls", 400))) + 1):
            self._check_abort()
            read = self._read_screen(wa, pos["anchor"])
            for n in self.ingest(read["messages"], {prefix}):
                if n not in trail:
                    trail.append(n)
            pos["anchor"] = read["top"] or pos["anchor"]
            if self._found(code):
                return self.cache[code]
            if len(trail) >= 3 and all(n < stop for n in trail[-3:]):
                self.scanned[prefix] = (stop - 1, max(trail))
                break  # scrolled past where this code would be
            before = self._eval(wa, JS_WA_SCROLL, "none")
            if not before:
                break
            if before["top"] > 0 and before["top"] != last_top:
                last_top = before["top"]
                self._eval(wa, JS_WA_SCROLL, {"where": "above", "anchor": pos["anchor"]})
                continue
            # At the top of what is loaded: wait for WhatsApp to load older messages.
            if self._wait_for_older(wa, before):
                stale = 0
            else:
                stale += 1
                self._eval(wa, JS_WA_OLDER)
                if stale >= 4:
                    break  # reached the beginning of the chat
        return self.cache.get(code)

    def _read_screen(self, wa, anchor):
        """The drawn messages, read once the screen joins up with `anchor` (the topmost message of the
        previous step) and WhatsApp has drawn everything on screen."""
        for _ in range(20):
            self._check_abort()
            now = self._eval(wa, JS_WA_SCROLL, {"where": "none", "anchor": anchor})
            if not now:
                break
            y = now["anchorY"]
            if y is not None and not 0 <= y <= now["view"] * 0.9:
                # WhatsApp loaded older messages and moved the view away: go back next to the anchor
                self._eval(wa, JS_WA_SCROLL, {"where": "above", "anchor": anchor})
            elif not now["undrawn"]:
                read = self._eval(wa, JS_WA_EXTRACT, anchor if y is not None else None)
                if read and read["ok"]:
                    return read
            wa.wait_for_timeout(150)
        return self._eval(wa, JS_WA_EXTRACT, None) or {"ok": False, "messages": [], "top": None}

    def _wait_for_older(self, wa, before):
        for _ in range(14):
            wa.wait_for_timeout(250)
            now = self._eval(wa, JS_WA_SCROLL, "none")
            if now and (now["first"] != before["first"] or now["height"] > before["height"]):
                return True
        return False

    def show_in_whatsapp(self, code):
        """Scrolls WhatsApp to the order message of this code and marks it, so it can be checked
        against the Pathao form. WhatsApp is not brought to the front: Pathao keeps the keyboard."""
        rec = self.cache.get(code)
        msg_id = (rec or {}).get("id") or ""
        if not msg_id or msg_id.startswith("t:"):
            return
        try:
            wa = self.whatsapp_page()
            chat = self.chat_for(split_code(code)[0])
            if not self._header_is(wa, chat):
                self.open_whatsapp_chat(wa, chat)
            if self._eval(wa, JS_WA_SHOW, msg_id):
                return
            # WhatsApp dropped the message from the page (the chat was scrolled far away):
            # go to the newest message and load older ones until it is back.
            self._to_newest(wa)
            stale = 0
            for _ in range(60):
                self._check_abort()
                if self._eval(wa, JS_WA_SHOW, msg_id):
                    return
                before = self._eval(wa, JS_WA_SCROLL, "top")
                if not before:
                    break
                if self._wait_for_older(wa, before):
                    stale = 0
                else:
                    stale += 1
                    if stale >= 3:
                        break
            self.log(f"{code}: could not scroll WhatsApp to its message.")
        except Aborted:
            raise
        except Exception as e:  # the form is filled already; don't stop the run over this
            self.log(f"{code}: could not show its message in WhatsApp ({e}).")

    def _to_newest(self, wa):
        """Scrolls to the newest message. WhatsApp loads newer messages while the view sits at the
        bottom, so this repeats until nothing changes."""
        last = None
        for _ in range(15):
            now = self._eval(wa, JS_WA_SCROLL, "bottom")
            if not now or (now["first"], now["height"]) == last:
                return
            last = (now["first"], now["height"])
            wa.wait_for_timeout(350)

    def _header_is(self, wa, chat):
        text = self._eval(wa, JS_WA_HEADER) or ""
        return any(norm_name(line) == norm_name(chat) for line in text.splitlines())

    def open_whatsapp_chat(self, wa, chat):
        if self._header_is(wa, chat):
            if self.open_chat != chat:
                self.open_chat = chat
                self._eval(wa, JS_WA_SCROLL, "bottom")
                wa.wait_for_timeout(600)
            return
        if not self._eval(wa, JS_WA_MARK_SEARCH):
            raise NeedUser("Could not find the WhatsApp search box. Is WhatsApp Web fully loaded?")
        wa.locator('[data-ccbd-search="1"]').first.click(timeout=5000)
        wa.keyboard.press("Control+A")
        wa.keyboard.press("Backspace")
        # Search with the words before "(": "Customise Cover BD (4-)" -> "Customise Cover BD".
        # The exact chat is then picked from the results.
        wa.keyboard.type(chat.split("(")[0].strip() or chat, delay=60)
        end = time.time() + 12
        while True:
            self._check_abort()
            if self._eval(wa, JS_WA_MARK_CHAT, chat):
                wa.wait_for_timeout(600)  # WhatsApp may still be redrawing the results
                if self._eval(wa, JS_WA_MARK_CHAT, chat):
                    try:
                        wa.locator('[data-ccbd-chat="1"]').first.click(timeout=2000)
                        break
                    except Exception:
                        pass  # the list was redrawn under the click; find the chat again
            if time.time() > end:
                raise NeedUser(f"WhatsApp chat '{chat}' was not found. Type the exact chat name next to this "
                               f"code prefix in 'WhatsApp chat name for each code prefix', then press Start.")
            wa.wait_for_timeout(300)
        end = time.time() + 10
        while not self._header_is(wa, chat):
            if time.time() > end:
                raise NeedUser(f"WhatsApp chat '{chat}' did not open.")
            wa.wait_for_timeout(300)
        self.open_chat = chat
        wa.wait_for_timeout(800)
        for _ in range(3):  # make sure the newest messages are loaded
            self._eval(wa, JS_WA_SCROLL, "bottom")
            wa.wait_for_timeout(400)

    def ingest(self, messages, prefixes):
        """Caches the order messages; returns the code numbers seen (photo captions included)."""
        seen = []
        for m in reversed(messages):  # newest first, so the newest message of a code wins
            text = m.get("text") or ""
            for code in find_codes(text):  # "3-163+164": one message for both codes
                prefix, number = split_code(code)
                if prefix not in prefixes:
                    continue
                seen.append(number)
                prev = self.cache.get(code)
                if prev and prev["id"] == m.get("id") and prev["text"] == text:
                    continue
                data = dict(parse_order(text), code=code)
                if not (data["phone"] or data["name"] or data["address"]):
                    continue  # just the code, e.g. the caption of the order's photos
                if (prev and prev["id"] != m.get("id") and data["phone"] and prev["data"]["phone"]
                        and data["phone"] != prev["data"]["phone"]):
                    self.duplicates.setdefault(code, set()).update({data["phone"], prev["data"]["phone"]})
                if prev is None or prev["id"] == m.get("id") or (data["phone"] and not prev["data"]["phone"]):
                    self.cache[code] = {"id": m.get("id"), "text": text, "data": data}
        return seen

    # -- Pathao ------------------------------------------------------------
    def _spec(self, key):
        ph_key, nth, label = FIELDS[key]
        return {"ph": self.settings["placeholders"].get(ph_key, ""), "nth": nth, "label": label}

    def _state(self, page):
        specs = {k: self._spec(k) for k in ("order_id", "phone", "address")}
        return self._eval(page, JS_PATHAO_STATE, specs)

    def _learn_create_url(self, url):
        if url and url != self.settings.get("pathao_create_url"):
            self.settings["pathao_create_url"] = url
            self.emit("learned", "pathao_create_url", url)

    def _wait_form(self, page, timeout=40):
        end = time.time() + timeout
        while time.time() < end:
            self._check_abort()
            st = self._state(page)
            if st and st["form"]:
                self._learn_create_url(st["url"])
                return
            page.wait_for_timeout(500)
        raise NeedUser("Pathao's 'New Delivery' form is not open. Log in to Pathao, click New Delivery, then press Start.")

    def prepare_form(self, page):
        """Every code starts on a fresh, empty New Delivery form."""
        st = self._state(page)
        if st and st["form"] and not (st["orderId"] or st["phone"] or st["address"]):
            self._learn_create_url(st["url"])
            return
        url = self.settings.get("pathao_create_url")
        if not url:
            if st and st["form"]:
                return
            raise NeedUser("Open Pathao's 'New Delivery' page in the browser, then press Start.")
        self.emit("state", "Opening a fresh Pathao New Delivery form...", "busy")
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        self._wait_form(page)

    def _set_field(self, page, key, value):
        if not self._eval(page, JS_MARK_FIELD, self._spec(key)):
            if key in ("phone2", "address"):
                if value:
                    self.log(f"Could not find the '{FIELDS[key][2]}' box - fill {value} by hand.")
                return
            raise NeedUser(f"Could not find the '{FIELDS[key][2]}' box on the Pathao page. "
                           f"Open the New Delivery form and press Start.")
        page.locator('[data-ccbd-field="1"]').first.fill(value or "", timeout=10000)

    def fill_pathao(self, idx, prepare=True):
        item = self.items[idx]
        data = item.get("data") or {}
        page = self.pathao_page()
        page.bring_to_front()
        if prepare:
            self.prepare_form(page)
        else:
            self._wait_form(page, 15)
        values = [
            ("order_id", item["code"]),
            ("amount", data.get("amount", "")),
            ("phone", data.get("phone", "")),
            ("phone2", data.get("phone2", "")),
            ("name", data.get("name", "")),
        ]
        if data.get("address"):  # no address found: leave whatever Pathao suggests for the phone
            values.append(("address", data["address"]))
        for key, value in values:
            self._set_field(page, key, value)
            if key == "phone2":
                page.wait_for_timeout(700)  # Pathao may look the phone number up and fill things in
        page.wait_for_timeout(600)
        for key, value in values:  # put our values back if Pathao's lookup overwrote any
            current = self._eval(page, JS_FIELD_VALUE, self._spec(key))
            if current is not None and current.strip() != value.strip():
                self._set_field(page, key, value)
        if self._eval(page, JS_MARK_FIELD, self._spec("address")):
            try:
                box = page.locator('[data-ccbd-field="1"]').first
                box.focus(timeout=3000)
                box.press("End")  # a key press also wakes up Pathao's area suggestion, if it listens for keys
            except Exception:
                pass

    def arm(self):
        """Start watching the Pathao page for the user's Save."""
        page = self.pathao
        self._eval(page, JS_CLEAR_SAVE_FLAG)
        st = self._state(page) or {}
        self.baseline_toasts = set(st.get("toasts") or [])
        self.fill_url = st.get("url") or page.url
        self.armed_ms = time.time() * 1000
        self.items[self.cur].pop("form_phone", None)
        self.responses = []
        self.reported = set()
        self.success_hits = self.reset_hits = 0
        self.reset_warned = False
        self.waiting = True

    def _posted_since(self, ms):
        return any(start >= ms and 200 <= status < 300 for start, status, _, _ in self.responses)

    def check_saved(self):
        page = self.pathao
        if self.cur is None or page is None:
            self.waiting = False
            return
        if page.is_closed():
            raise NeedUser("The Pathao tab was closed. Open New Delivery again and press Start.")
        st = self._state(page)
        if not st:
            return  # page is loading
        code = self.items[self.cur]["code"]
        if st["form"] and (st["orderId"] or "").strip().lower() == code and st["phone"]:
            self.items[self.cur]["form_phone"] = st["phone"]  # the phone as it is saved, fixes included

        if not st["click"]:
            # No Save click. If the form got emptied (page reloaded / Cancel), fill it again.
            cleared = st["form"] and not st["orderId"] and not st["phone"]
            self.reset_hits = self.reset_hits + 1 if cleared else 0
            if self.reset_hits == 3:
                if not self._posted_since(self.armed_ms):
                    self.log(f"{code}: the Pathao form was emptied without Save - filling it again.")
                    self.fill_pathao(self.cur, prepare=False)
                    self.arm()
                elif not self.reset_warned:
                    self.reset_warned = True
                    self.attention(f"The Pathao form was emptied. If {code} was saved press 'Saved -> Next', "
                                   f"otherwise press 'Fill Pathao'.")
            return

        for start, status, method, url in self.responses:
            if start >= st["click"] - 500 and status >= 400 and (start, url) not in self.reported:
                self.reported.add((start, url))
                self.log(f"{code}: Pathao answered {status} to the save - check the form for errors.")

        # Saved = Save was clicked, Pathao accepted a POST after it, and the page shows it
        # (success message, went to another page, or the form was emptied).
        if not self._posted_since(st["click"] - 500):
            self.success_hits = 0
            return
        new_toast = any(t not in self.baseline_toasts for t in st["toasts"])
        moved = url_key(st["url"]) != url_key(self.fill_url)
        cleared = (not st["form"] or not st["orderId"]) and st["docStart"] < st["click"]
        if new_toast or moved:
            self.on_saved()
        elif cleared:
            self.success_hits += 1
            if self.success_hits >= 2:
                self.on_saved()
        else:
            self.success_hits = 0

    def on_saved(self):
        self.record_saved(self.cur)
        self.cur, self.waiting = None, False
        try:
            self.pathao.wait_for_timeout(1000)  # let Pathao finish its own page change first
        except Exception:
            time.sleep(1)

    def record_saved(self, idx):
        item = self.items[idx]
        append_saved(item["code"], item.get("data"))
        self.set_status(idx, SAVED)
        self.log(f"✓ {item['code']} saved.")
        if self.settings.get("write_phone_to_list", True):
            self.write_phone(item)

    def write_phone(self, item):
        code, path = item["code"], self.list_file
        phones = find_phones(item.get("form_phone") or "") or find_phones((item.get("data") or {}).get("phone", ""))
        if not phones:
            self.log(f"{code}: ⚠ no phone number to write in the list - type it there yourself.")
            return
        try:
            done = write_phone_to_list(path, code, phones[0])
        except OSError as e:
            self.log(f"{code}: ⚠ could not write {phones[0]} in {os.path.basename(path)} ({e}) - type it there yourself.")
            return
        if done:
            self.log(f"{code}: wrote {phones[0]} next to it in {os.path.basename(path)}.")
        else:
            self.log(f"{code}: {os.path.basename(path)} has no line with just {code} any more - phone not written.")

    def check_pathao(self):
        """Looks the loaded codes up in Pathao's orders (the code is an order's Merchant Order ID),
        counting only the orders of each prefix's current round of codes (see round_starts). A code
        found there is marked as saved with that order's phone, and the phone is written next to it
        in the list file; the codes not found stay as they are."""
        if not self.items:
            self.log("Load the code list first.")
            return
        self.emit("state", "Reading the orders from Pathao...", "busy")
        today = datetime.now()
        span = {"from": (today - timedelta(days=PATHAO_LOOKBACK_DAYS)).strftime("%Y-%m-%d"),
                "to": today.strftime("%Y-%m-%d")}
        page = self.pathao_page()
        try:
            res = page.evaluate(JS_PATHAO_ORDERS, span)
        except Exception as e:
            raise NeedUser(f"Could not read the orders from Pathao ({e}). Open merchant.pathao.com in the browser and try again.")
        if res.get("error") == "login":
            raise NeedUser("Pathao is not logged in. Log in to merchant.pathao.com in the browser, then press Check Pathao again.")
        if res.get("error"):
            raise NeedUser(f"Could not read the orders from Pathao: {res['error']}.")
        starts = round_starts(res["orders"])
        by_code = {}
        for o in res["orders"]:  # newest first, as the Orders page lists them
            for code in find_codes(o["id"]):  # "2-257+258": one order for both codes
                if o["created"] >= starts.get(split_code(code)[0], ""):
                    by_code.setdefault(code, []).append(o)
        for prefix in dict.fromkeys(split_code(it["code"])[0] for it in self.items):
            if prefix in starts:
                self.log(f"{prefix}-: codes started again from 1 on {starts[prefix][:16]} - older orders are not counted.")

        write = self.settings.get("write_phone_to_list", True)
        found, missing, written = [], [], 0
        for idx, item in enumerate(self.items):
            code, hits = item["code"], by_code.get(item["code"])
            if not hits:
                missing.append(code)
                continue
            if idx == self.cur and item["status"] == FILLED:
                self.log(f"{code}: ⚠ Pathao already has an order {hits[0]['consignment']} with this code - "
                         f"it is on the form now, check it is not saved twice.")
                continue
            phones = list(dict.fromkeys(p for o in hits for p in find_phones(o["phone"])))
            o = hits[0]
            item["data"] = {**(item.get("data") or {}), "name": o["name"], "phone": phones[0] if phones else o["phone"],
                            "amount": str(o["amount"] if o["amount"] is not None else "")}
            self.set_status(idx, IN_PATHAO)
            found.append(code)
            if len(hits) > 1:
                self.log(f"{code}: ⚠ {len(hits)} Pathao orders of the current round have this code "
                         f"({', '.join(h['consignment'] + ' ' + h['phone'] for h in hits)}). The newest is shown.")
            if write and phones and self.list_file:
                try:
                    written += write_phone_to_list(self.list_file, code, phones[0])
                except OSError as e:
                    self.log(f"{code}: ⚠ could not write {phones[0]} in {os.path.basename(self.list_file)} ({e}).")

        self.log(f"Read {len(res['orders'])} Pathao orders of the last {PATHAO_LOOKBACK_DAYS} days; {len(found)} of the "
                 f"{len(self.items)} codes are among them" + (f", phone written next to {written} in "
                                                             f"{os.path.basename(self.list_file)}" if written else "") + ".")
        if missing:
            self.log(f"Not in Pathao ({len(missing)}): " + ", ".join(missing))
        self.emit("state", f"{len(found)} of {len(self.items)} codes are already in Pathao; {len(missing)} are not.", "ok")

    # -- the main flow -----------------------------------------------------
    def _next_pending(self):
        return next((i for i, it in enumerate(self.items) if it["status"] == PENDING), None)

    def process_next(self):
        idx = self._next_pending()
        if idx is None:
            self.running = False
            if self.items:
                self.emit("state", "All codes in the list are done.", "ok")
                self.log("Reached the end of the list.")
            return
        self.process(idx)

    def process(self, idx, prepare=True, explicit=False):
        # WhatsApp to itself from reading the order message until the message is shown next to the
        # filled form: the window is shared with the other tools, which wait meanwhile (and it for them)
        def waiting(tool):
            self.emit("state", f"Waiting for {tool} to finish with WhatsApp...", "busy")
        with whatsapp.turn("Pathao Auto-fill", self._check_abort, waiting):
            self._process(idx, prepare, explicit)

    def _process(self, idx, prepare, explicit):
        item = self.items[idx]
        code = item["code"]
        if self.cur is not None and self.cur != idx and self.items[self.cur]["status"] == FILLED:
            self.set_status(self.cur, PENDING)
        self.cur, self.waiting = idx, False
        self.set_status(idx, FETCHING)
        try:
            rec = self.lookup(code, reread=explicit)
        except BaseException:
            self.set_status(idx, PENDING)
            self.cur = None
            raise
        if rec is None:
            self.set_status(idx, NOT_FOUND)
            self.cur = None
            if self.wa is not None:
                self._eval(self.wa, JS_WA_SHOW, None)  # don't leave the previous code's message marked
            msg = f"{code}: no message starting with {code} in WhatsApp chat '{self.chat_for(split_code(code)[0])}'."
            if explicit:
                self.running = False
                self.emit("current", code, {}, "(Message not found. Type the details here and press 'Fill Pathao'.)")
                self.attention(msg + " Type the details in the panel and press 'Fill Pathao', or press Start to go on.")
            else:
                self.log(msg + " Skipped - double-click it later to try again.")
            return
        item["data"], item["raw"] = dict(rec["data"]), rec["text"]
        self.emit("current", code, item["data"], item["raw"])
        for w in item["data"].get("warnings", []):
            self.log(f"{code}: ⚠ {w} - check it before saving.")
        if code in self.duplicates:
            self.log(f"{code}: ⚠ WhatsApp has more than one order message with this code (phones "
                     f"{', '.join(sorted(self.duplicates[code]))}). The newest was used - check it before saving.")
        try:
            self.fill_pathao(idx, prepare=prepare)
        except BaseException:
            self.set_status(idx, PENDING)
            self.cur = None
            raise
        self.set_status(idx, FILLED)
        self.arm()
        self.emit("state", f"{code} is filled in Pathao. Check it, finish the form and click Save; "
                           f"the next code fills itself after the save.", "wait")
        self.show_in_whatsapp(code)

    def refill(self, code, data):
        idx = self._index(code)
        if idx is None:
            return
        if self.cur is not None and self.cur != idx and self.items[self.cur]["status"] == FILLED:
            self.set_status(self.cur, PENDING)
        prepare = self.cur != idx
        item = self.items[idx]
        item["data"] = {**(item.get("data") or {}), **data}
        self.cur, self.waiting = idx, False
        self.fill_pathao(idx, prepare=prepare)
        self.set_status(idx, FILLED)
        self.running = True
        self.arm()
        self.emit("state", f"{code} is filled in Pathao with your values. Click Save there.", "wait")
