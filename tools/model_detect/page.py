"""Mobile Model Detect - finds each product code in your WhatsApp groups, takes the photos
posted with it and writes the phone model next to the code ("5a-403 samsung a70").
"""
import json
import os
import queue
import re
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

import anthropic
from playwright.sync_api import Error as PWError
from PySide6.QtCore import Qt, QThread, Signal

from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QFileDialog, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QMessageBox, QPlainTextEdit, QSpinBox, QTableWidget, QTableWidgetItem, QWidget,
)

from core import look, shop, theme, whatsapp
from core.look import KIND
from core.paths import data_dir

from .codes import CodeList, is_done, read_results, write_results
from .identifier import DEFAULT_MODEL, EFFORTS, MODELS, ModelIdentifier
from .local_identifier import DEFAULT_LOCAL_MODEL, LOCAL_MODELS, LocalAIError, LocalIdentifier
from .text_identifier import TextReader
from .wa_driver import StopRequested, WhatsAppDriver, build_groups

APP_DIR = str(data_dir("model_detect"))  # settings and log (WhatsApp: the shared window, core/whatsapp.py)
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
LOG_PATH = os.path.join(APP_DIR, "mobilemodeldetect.log")
DEFAULT_CODES = os.path.join(os.path.expanduser("~"), "Documents", "productcodes.txt")

MODE_BELOW = "below"
MODE_ABOVE = "above"
MODE_LABELS = {
    MODE_BELOW: "Code first, photos below it (until the next code)",
    MODE_ABOVE: "Photos first, code below them (back to the previous code)",
}

ENGINE_TEXT = "text"
ENGINE_LOCAL = "local"
ENGINE_CLAUDE = "claude"
ENGINE_NONE = "none"
ENGINE_LABELS = {
    ENGINE_TEXT: "Read the model name from chat text and from text in the photos (free, offline)",
    ENGINE_LOCAL: "Read text first; if no name is written, ask free AI on this PC (Ollama)",
    ENGINE_CLAUDE: "Read text first; if no name is written, ask Claude API (paid)",
    ENGINE_NONE: "Don't identify - only save the photos (I type the models)",
}
ENGINE_HINTS = {
    ENGINE_TEXT: "Finds names like \"samsung a70 5g\", \"iphone xs max\", \"redmi note 7\" in the caption or the "
                 "message below the photos, or printed inside the photos. No internet, no API key. Codes with no "
                 "written name are left empty - double-click them in Results to see their photos.",
    ENGINE_LOCAL: "Photos with no written name go to a vision AI on your PC. Install Ollama once from "
                  "<a href='https://ollama.com'>ollama.com</a>, then run <b>ollama pull qwen2.5vl:7b</b> (about 6 GB). "
                  "On your CPU expect 0.5-2 minutes per photo set, and check low-confidence answers.",
    ENGINE_CLAUDE: "Photos with no written name go to Claude. Needs an API key from console.anthropic.com "
                   "(pay per use, a few US cents per code).",
    ENGINE_NONE: "Each code's photos are saved in a folder. Double-click a code in Results to open them, "
                 "then type the model in the table - the result file updates immediately.",
}

COL_CODE, COL_MODEL, COL_CONF, COL_PHOTOS, COL_CHAT, COL_NOTE = range(6)
MAP_USE, MAP_PREFIX, MAP_COUNT, MAP_RANGE, MAP_CHAT = range(5)


def shop_chat(prefix: str) -> str:
    """The chat the shop settings give a code group, or the orders chat for the codes without a prefix;
    "" when the shop settings have none (core/shop.py)."""
    s = shop.get().settings
    if not prefix:
        return (s.get("orders_chat") or "").strip()
    return (s.get("chats") or {}).get(prefix, "").strip()


# =============================================================================== worker
class Worker(QThread):
    """Owns the browser. Runs one command at a time from a queue, so the window never freezes."""

    log = Signal(str)
    status = Signal(str, str)        # text, colour
    connected = Signal(bool)
    chats = Signal(list)
    result = Signal(dict)
    busy = Signal(bool, str)

    def __init__(self):
        super().__init__()
        self.cmds = queue.Queue()
        self.stop_event = threading.Event()
        self.drv = None
        self._ai_disabled = False

    def submit(self, cmd, *args):
        self.cmds.put((cmd, args))

    def stop(self):
        self.stop_event.set()

    def run(self):
        while True:
            cmd, args = self.cmds.get()
            if cmd == "quit":
                break
            self.stop_event.clear()
            self.busy.emit(True, cmd)
            try:
                getattr(self, "do_" + cmd)(*args)
            except StopRequested:
                self.log.emit("Stopped.")
            except PWError as e:
                if self.drv and not self.drv.alive():
                    self.log.emit("The browser window was closed. Click 'Open WhatsApp' again.")
                    self._drop_driver()
                else:
                    self.log.emit(f"Browser error during {cmd}: {e}")
            except Exception as e:
                self.log.emit(f"ERROR during {cmd}: {e}\n{traceback.format_exc()}")
            finally:
                self.busy.emit(False, cmd)
        if self.drv:
            self.drv.close()

    def _drop_driver(self):
        if self.drv:
            self.drv.close()
        self.drv = None
        self.connected.emit(False)
        self.status.emit("Not connected", "#888888")

    def _ready_driver(self):
        if self.drv and self.drv.alive():
            return True
        self.log.emit("WhatsApp is not open. Click 'Open WhatsApp' first.")
        self._drop_driver()
        return False

    def _whatsapp_turn(self):
        """WhatsApp to itself while this tool scrolls in it (the window is shared with the other tools)."""
        def waiting(tool):
            self.status.emit(f"Waiting for {tool} to finish with WhatsApp...", "#d08000")
            self.log.emit(f"{tool} is using WhatsApp. This goes on as soon as it is done.")
        return whatsapp.turn("Model Detect", self.drv.check_stop, waiting)

    # ---- commands
    def do_connect(self, groups_only):
        if not (self.drv and self.drv.alive()):
            if self.drv:
                self.drv.close()
            self.status.emit("Starting browser...", "#d08000")
            self.drv = WhatsAppDriver(log=self.log.emit, should_stop=self.stop_event.is_set)
            self.drv.start()

        def on_state(state):
            if state == "qr":
                self.status.emit("Scan the QR code in the browser (WhatsApp > Linked devices)", "#d08000")
                self.log.emit("Waiting for the QR code to be scanned...")
            elif state == "loading":
                self.status.emit("Loading WhatsApp...", "#d08000")

        if not self.drv.wait_until_ready(timeout=600, on_state=on_state):
            self.status.emit("WhatsApp did not load (timeout)", "#c03030")
            return
        self.status.emit("Connected", "#208020")
        self.connected.emit(True)
        self.log.emit("WhatsApp connected.")
        self.do_list_chats(groups_only)

    def do_list_chats(self, groups_only):
        if not self._ready_driver():
            return
        with self._whatsapp_turn() as waited:
            if waited:
                self.status.emit("Connected", "#208020")
            self.log.emit("Reading the chat list...")
            names = self.drv.list_chats(groups_only=groups_only)
        self.log.emit(f"{len(names)} chats found.")
        self.chats.emit(names)

    def do_scan(self, jobs, opts):
        if not self._ready_driver():
            return
        ident = None
        workers = 1
        self._ai_disabled = False
        reader = TextReader(log=self.log.emit) if opts["engine"] != ENGINE_NONE else None
        if reader:
            self.log.emit("Model names written in the chat or inside the photos will be read.")
        if opts["engine"] == ENGINE_LOCAL:
            local = LocalIdentifier(model=opts["model"])
            try:
                local.check()
                ident = local
                self.log.emit(f"Photos will be identified on this PC with {opts['model']} (Ollama).")
            except LocalAIError as e:
                self.log.emit(f"Local AI not ready: {e}\nContinuing: photos will be saved without models.")
        elif opts["engine"] == ENGINE_CLAUDE:
            if opts["api_key"] or os.environ.get("ANTHROPIC_API_KEY"):
                ident = ModelIdentifier(api_key=opts["api_key"], model=opts["model"], effort=opts["effort"])
                workers = 3
                self.log.emit(f"Photos will be identified with {opts['model']} (effort {opts['effort']}).")
            else:
                self.log.emit("No Claude API key - only written model names will be used.")
        elif opts["engine"] == ENGINE_NONE:
            self.log.emit("Identification is off - photos will be saved for you to check.")

        pool = ThreadPoolExecutor(max_workers=workers)
        try:
            with self._whatsapp_turn() as waited:
                if waited:
                    self.status.emit("Connected", "#208020")
                for job in jobs:
                    self._scan_job(job, opts, reader, ident, pool)
        except BaseException:
            pool.shutdown(wait=True, cancel_futures=True)
            raise
        if reader:
            self.log.emit("Finishing the last codes...")
        pool.shutdown(wait=True)
        self.log.emit("All done.")

    def _scan_job(self, job, opts, reader, ident, pool):
        chat, codes, ranges = job["chat"], job["codes"], job["ranges"]
        self.log.emit(f"=== {chat}: {len(codes)} codes, searching from {codes[-1]} up to {codes[0]} ===")
        if not self.drv.open_chat(chat):
            self.log.emit(f"Could not open chat '{chat}'. Check the name.")
            for c in codes:
                self._emit(c, chat, final=True, note="chat not found in WhatsApp")
            return
        order, msgs, found = self.drv.scan_chat(
            ranges, set(codes), extra_screens=opts["extra_screens"], max_steps=opts["max_steps"])
        self.log.emit(f"  {len(order)} messages read, {len(found)}/{len(codes)} codes found.")
        groups = build_groups(order, msgs, found, opts["mode"], opts["max_images"])

        for code in reversed(codes):  # bottom of the list first
            self.drv.check_stop()
            g = groups.get(code)
            if not g:
                note = ("older than what WhatsApp Web could load - keep your phone online and run again"
                        if self.drv.reached_top else "code message not found in this chat")
                self._emit(code, chat, final=True, model="NOT FOUND", note=note)
                continue
            self._emit(code, chat, note="collecting photos...")
            images = []
            for mid in g.image_ids:
                if len(images) >= opts["max_images"]:
                    break
                images += self.drv.fetch_images(mid, opts["max_images"] - len(images))
            if not images:  # photos can arrive after WhatsApp drew the message: look at the whole group
                for mid in g.other_ids:
                    if len(images) >= opts["max_images"]:
                        break
                    images += self.drv.fetch_images(mid, opts["max_images"] - len(images), photo_wait=2)
            if not images:
                self._emit(code, chat, final=True, model="NO IMAGE",
                           note=f"code found ('{g.marker_text[:40]}') but no photos next to it")
                continue
            folder = self._save_photos(opts, code, images)
            # chat text for the optional AI, without customers' phone numbers
            context = re.sub(r"\d{6,}", "#", "\n".join(dict.fromkeys(g.near + g.context)))[:800]
            if reader:
                self._emit(code, chat, images=len(images), folder=folder, note="reading...")
                pool.submit(self._identify, reader, ident, code, chat, images, g.near, context, folder, g.shared)
            else:
                self._emit(code, chat, final=True, images=len(images), folder=folder,
                           note="photos saved - double-click to open")

    def _save_photos(self, opts, code, images):
        if not opts["photo_dir"]:
            return ""
        folder = os.path.join(opts["photo_dir"], code)
        os.makedirs(folder, exist_ok=True)
        for f in os.listdir(folder):
            if f.startswith("photo") and f.endswith(".jpg"):
                os.remove(os.path.join(folder, f))
        for i, img in enumerate(images, 1):
            with open(os.path.join(folder, f"photo{i}.jpg"), "wb") as fh:
                fh.write(img)
        return folder

    def _identify(self, reader, ident, code, chat, images, near, context, folder, shared=()):
        """Written name first (chat text, then text inside the photos); AI only if nothing sure is written."""
        if self.stop_event.is_set():
            return
        try:
            seen = []
            r = reader.identify(code, images, near, shared=shared, seen=seen)
            # nothing written, only the brand logo ("redmi ?") or an unsure reading: let the AI look at the phone
            if (r is None or r.confidence == "low") and ident and not self._ai_disabled \
                    and not self.stop_event.is_set():
                hint = f"\nRead so far (not sure): {r.model.rstrip(' ?')} - {r.reason}" if r else ""
                if seen:
                    hint += "\nText printed in the photos: " + " | ".join(dict.fromkeys(seen))[:400]
                ai = ident.identify(code, images, context + hint)
                if r is None or ai.model != "unknown":
                    r = ai
            if r is None:
                self._emit(code, chat, final=True, images=len(images), folder=folder,
                           note="no model name written - double-click to see the photos")
                self.log.emit(f"  {code}: no model name found")
                return
            self._emit(code, chat, final=True, model=r.model, confidence=r.confidence,
                       images=len(images), folder=folder, note=r.reason)
            self.log.emit(f"  {code}: {r.model} ({r.confidence})")
        except LocalAIError as e:
            if not self._ai_disabled:
                self._ai_disabled = True
                self.log.emit(f"Local AI stopped working - identification turned off. ({e})")
            self._emit(code, chat, final=True, images=len(images), folder=folder, note="local AI error")
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError, anthropic.NotFoundError) as e:
            if not self._ai_disabled:
                self._ai_disabled = True
                self.log.emit(f"Claude API refused the key or model - identification turned off. ({e})")
            self._emit(code, chat, final=True, images=len(images), folder=folder, note="API key / model error")
        except anthropic.APIError as e:
            self.log.emit(f"  {code}: API error: {e}")
            self._emit(code, chat, final=True, images=len(images), folder=folder, note=f"API error: {e}"[:200])
        except Exception as e:
            self.log.emit(f"  {code}: {e}")
            self._emit(code, chat, final=True, images=len(images), folder=folder, note=f"error: {e}"[:200])

    def _emit(self, code, chat, final=False, **fields):
        self.result.emit(dict(code=code, chat=chat, final=final, **fields))


# =============================================================================== window
class MainWindow(QMainWindow):
    busy_changed = Signal(bool)

    def __init__(self):
        super().__init__()
        self.cfg = self._load_config()
        self.codelist = None
        self.results = {}        # code -> {model, confidence, images, chat, note, folder}
        self.code_rows = {}      # code -> results table row
        self.map_rows = []       # prefixes in mapping-table order
        self.all_chats = []
        self.is_connected = False
        self.is_busy = False
        self._filling_table = False
        self.done_count = 0
        self.total_count = 0
        self.status_text = "Not connected"
        self.setStyleSheet(look.STYLE)

        self._build_ui()
        self.worker = Worker()
        self.worker.log.connect(self.add_log)
        self.worker.status.connect(self.set_status)
        self.worker.connected.connect(self.on_connected)
        self.worker.chats.connect(self.on_chats)
        self.worker.result.connect(self.on_result)
        self.worker.busy.connect(self.on_busy)
        self.worker.start()

        # A fresh start shows the remembered file but doesn't load it: Load or Start does.
        # The chat list starts empty too, with nothing ticked, until WhatsApp is opened.
        path = self.cfg.get("codes_file") or (DEFAULT_CODES if os.path.exists(DEFAULT_CODES) else "")
        if path and os.path.exists(path):
            self.codes_edit.setText(path)

    # ---- UI construction: the look of core/look.py
    def _build_ui(self):
        self.wa_pill = look.WaPill()
        self.btn_connect = look.button("Open WhatsApp", "outline", "message-circle", theme.NAVY)
        self.btn_connect.clicked.connect(self.connect_whatsapp)
        self.hero = look.Hero("codes", [("ok", "Found", theme.LIVE), ("low", "Unsure", look.AMBER_ON_NAVY),
                                        ("none", "Not found", look.RED_ON_NAVY)])
        self.progress = self.hero.progress
        self.btn_start = look.button("Start", "heroStart", "play", theme.NAVY)
        self.btn_start.clicked.connect(self.start_scan)
        self.btn_stop = look.button("Stop", "heroOutline", "square", "#ffffff")
        self.btn_stop.clicked.connect(self.stop_scan)
        self.btn_stop.setEnabled(False)
        self.hero.add(self.btn_start, self.btn_stop)

        results = self._build_results_panel()
        self.log_card = look.LogCard(QPlainTextEdit())
        self.log_view = self.log_card.edit
        self.log_view.setReadOnly(True)
        self.look = look.ToolLayout(
            look.Header("model_detect", [self.wa_pill, self.btn_connect]), self.hero,
            [("chats", self._build_chat_panel()), ("codes", self._build_codes_panel()),
             ("codes", self._build_map_panel()), ("settings", self._build_settings_panel())],
            [(results, 1), (self.log_card, 0)],
            [("chats", "Chats"), ("codes", "Code list"), ("settings", "Settings"), ("results", "Results")], "results",
            left_width=(430, 380), on_mode=self._on_mode)
        self.setCentralWidget(self.look)
        self.set_status("Not connected", "#888888")

    def _on_mode(self, mode):
        for col in (COL_CHAT, COL_NOTE):
            self.table.setColumnHidden(col, mode != "wide")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.look.resize_to(event.size().width())

    def _build_chat_panel(self):
        card = look.Card("message-circle", theme.ACCENT_SOFT, theme.ACCENT, "WhatsApp chats",
                         "Tick the chats that hold your code lists")
        self.btn_reload_chats = look.button("", "iconBtn", "refresh-cw", theme.TEXT_2, "Reload the chat list")
        self.btn_reload_chats.clicked.connect(self.reload_chats)
        card.head_right.addWidget(self.btn_reload_chats, 0, Qt.AlignmentFlag.AlignTop)
        self.groups_only = look.ToggleRow("list", theme.NEUTRAL_SOFT, theme.NAVY, "List groups only")
        self.groups_only.setChecked(self.cfg.get("groups_only", True))
        card.lay.addWidget(self.groups_only)
        self.chat_filter = QLineEdit(objectName="field")
        self.chat_filter.setPlaceholderText("Search chats...")
        self.chat_filter.textChanged.connect(self.filter_chats)
        card.lay.addWidget(self.chat_filter)
        self.chat_list = QListWidget()
        self.chat_list.setMinimumHeight(180)
        self.chat_list.itemChanged.connect(self.on_chat_checked)
        card.lay.addWidget(self.chat_list)
        self.chat_count = QLabel("No chats loaded", objectName="hint")
        untick = look.button("Untick all", "plain")
        untick.clicked.connect(lambda: self.set_all_checked(False))
        self.btn_ticked_only = look.button("Show ticked only", "plain")
        self.btn_ticked_only.setCheckable(True)
        self.btn_ticked_only.toggled.connect(self.filter_chats)
        card.lay.addLayout(look.row(self.chat_count, None, untick, self.btn_ticked_only, spacing=4))
        return card

    def _build_codes_panel(self):
        card = look.Card("file-text", theme.WARNING_SOFT, theme.WARNING, "Code list file")
        self.codes_info = card.subtitle
        card.set_subtitle("No code list loaded")
        self.codes_edit = QLineEdit()
        browse = look.button("Browse", "mini")
        browse.clicked.connect(self.browse_codes)
        load = look.button("Load", "mini")
        load.clicked.connect(self.load_codes)
        card.lay.addWidget(look.path_box(self.codes_edit, browse, load, icon="file-text"))
        return card

    def _build_map_panel(self):
        card = look.Card("tag", theme.SUCCESS_SOFT, theme.SUCCESS, "Chat for each code group",
                         "Searched from the bottom of the list")
        self.map_table = QTableWidget(0, 5)
        self.map_table.setHorizontalHeaderLabels(["Search", "Group", "Codes", "Range (bottom -> top)", "WhatsApp chat"])
        look.rows_table(self.map_table, {}, MAP_USE, height=46, painter=False)  # its own tick boxes and chat boxes
        hh = self.map_table.horizontalHeader()
        for c in (MAP_USE, MAP_PREFIX, MAP_COUNT, MAP_RANGE):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(MAP_CHAT, QHeaderView.ResizeMode.Stretch)
        self.map_table.setColumnHidden(MAP_RANGE, True)  # the range is the row's tooltip
        self.map_table.setMinimumHeight(190)
        card.lay.addWidget(self.map_table)
        b = look.button("Auto-match by chat name", "soft", "refresh-cw")
        b.clicked.connect(self.auto_match)
        card.lay.addWidget(b)
        card.lay.addWidget(look.hint("A chat whose name contains the group code (e.g. \"5a\") is matched automatically."))
        return card

    def _build_settings_panel(self):
        card = look.Card("sliders-horizontal", theme.NEUTRAL_SOFT, theme.NAVY, "Settings")
        self.out_edit = QLineEdit(self.cfg.get("output_file", ""))
        b = look.button("Browse", "mini")
        b.clicked.connect(self.browse_output)
        card.lay.addLayout(look.column(look.field("Result file"), look.path_box(self.out_edit, b, icon="file-text")))

        self.mode_combo = QComboBox()
        for key, label in MODE_LABELS.items():
            self.mode_combo.addItem(label, key)
        self.mode_combo.setCurrentIndex(max(0, self.mode_combo.findData(self.cfg.get("mode", MODE_BELOW))))
        card.lay.addLayout(look.column(look.field("In the chat"), self.mode_combo))

        self.max_images = QSpinBox()
        self.max_images.setRange(1, 10)
        self.max_images.setValue(self.cfg.get("max_images", 4))
        self.extra_screens = QSpinBox()
        self.extra_screens.setRange(2, 500)
        self.extra_screens.setValue(self.cfg.get("extra_screens", 15))
        self.extra_screens.setToolTip("After the codes are found, keep scrolling up this many screens "
                                      "looking for the original posts before stopping.")
        self.max_steps = QSpinBox()
        self.max_steps.setRange(50, 100000)
        self.max_steps.setValue(self.cfg.get("max_steps", 3000))
        card.lay.addLayout(look.row(look.column(look.field("Photos per code"), self.max_images),
                                    look.column(look.field("Extra screens to scroll"), self.extra_screens), spacing=10))
        card.lay.addLayout(look.row(look.column(look.field("Max scroll steps per chat"), self.max_steps), None))

        self.engine_combo = QComboBox()
        for key, label in ENGINE_LABELS.items():
            self.engine_combo.addItem(label, key)
        self.engine_combo.setCurrentIndex(max(0, self.engine_combo.findData(self.cfg.get("engine", ENGINE_TEXT))))
        card.lay.addLayout(look.column(look.field("Identify phones with"), self.engine_combo))

        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.effort_label = look.field("Effort")
        self.effort_combo = QComboBox()
        self.effort_combo.addItems(EFFORTS)
        self.effort_combo.setCurrentText(self.cfg.get("effort", "high"))
        card.lay.addLayout(look.column(look.field("AI model"),
                                       look.row(self.model_combo, self.effort_label, self.effort_combo, stretch_at=0)))

        self.key_edit = QLineEdit(self.cfg.get("api_key", ""), objectName="field")
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("sk-ant-...  (or leave empty to use the ANTHROPIC_API_KEY environment variable)")
        b = look.button("Show", "mini")
        b.setCheckable(True)
        b.toggled.connect(lambda on: self.key_edit.setEchoMode(
            QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password))
        self.key_row_label = look.field("Claude API key")
        self.key_row = QWidget()
        self.key_row.setLayout(look.row(self.key_edit, b, stretch_at=0))
        card.lay.addLayout(look.column(self.key_row_label, self.key_row))
        self.engine_hint = QLabel(objectName="hint", wordWrap=True)
        self.engine_hint.setOpenExternalLinks(True)
        card.lay.addWidget(self.engine_hint)
        self._engine = None
        self.engine_combo.currentIndexChanged.connect(self.on_engine_changed)
        self.on_engine_changed()

        self.save_photos = look.ToggleRow("image", theme.SUCCESS_SOFT, theme.SUCCESS, "Save the photos of each code",
                                          "For checking")
        self.save_photos.setChecked(self.cfg.get("save_photos", True))
        self.skip_done = look.ToggleRow("check", theme.ACCENT_SOFT, theme.ACCENT, "Skip codes that already have a model",
                                        "In the result file")
        self.skip_done.setChecked(self.cfg.get("skip_done", True))
        card.lay.addLayout(look.column(self.save_photos, self.skip_done, spacing=2))
        return card

    def _build_results_panel(self):
        card = look.Card("smartphone", theme.SUCCESS_SOFT, theme.SUCCESS, "Results",
                         "Double-click a code to open its photos. Edit a model to correct it.")
        card.lay.setContentsMargins(18, 18, 18, 10)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Code", "Phone model", "Confidence", "Photos", "Chat", "Note"])
        look.rows_table(self.table, {COL_CODE: "code", COL_MODEL: "name", COL_CONF: "pill", COL_PHOTOS: "muted",
                                     COL_CHAT: "muted", COL_NOTE: "muted"}, COL_CODE, height=52)
        hh = self.table.horizontalHeader()
        for c in (COL_CODE, COL_CONF, COL_PHOTOS):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(COL_MODEL, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(COL_CHAT, QHeaderView.ResizeMode.Interactive)
        hh.setSectionResizeMode(COL_NOTE, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(COL_CHAT, 160)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.itemChanged.connect(self.on_table_edit)
        self.table.cellDoubleClicked.connect(self.open_photo_folder)
        card.lay.addWidget(self.table, 1)
        return card

    # ---- config
    def _load_config(self):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def save_config(self):
        cfg = dict(self.cfg)
        cfg.update(
            codes_file=self.codes_edit.text().strip(),
            output_file=self.out_edit.text().strip(),
            mode=self.mode_combo.currentData(),
            max_images=self.max_images.value(),
            api_key=self.key_edit.text().strip(),
            engine=self.engine_combo.currentData(),
            effort=self.effort_combo.currentText(),
            save_photos=self.save_photos.isChecked(),
            skip_done=self.skip_done.isChecked(),
            extra_screens=self.extra_screens.value(),
            max_steps=self.max_steps.value(),
            groups_only=self.groups_only.isChecked(),
        )
        cfg.pop("chats", None)          # older versions kept the chat list and ticks between runs
        cfg.pop("checked_chats", None)
        if self._engine == ENGINE_LOCAL:
            cfg["local_model"] = self.model_combo.currentText().strip()
        elif self._engine == ENGINE_CLAUDE:
            cfg["model"] = self.model_combo.currentText().strip()
        mapping = cfg.get("mapping", {})
        enabled = cfg.get("enabled", {})
        for r, prefix in enumerate(self.map_rows):
            mapping[prefix] = self.map_table.cellWidget(r, MAP_CHAT).currentText().strip()
            enabled[prefix] = self.map_table.item(r, MAP_USE).checkState() == Qt.CheckState.Checked
        cfg["mapping"], cfg["enabled"] = mapping, enabled
        self.cfg = cfg
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2, ensure_ascii=False)
        except OSError as e:
            self.add_log(f"Could not save settings: {e}")

    def on_engine_changed(self, *_):
        # remember the model chosen for the engine we are leaving
        if self._engine == ENGINE_LOCAL:
            self.cfg["local_model"] = self.model_combo.currentText().strip()
        elif self._engine == ENGINE_CLAUDE:
            self.cfg["model"] = self.model_combo.currentText().strip()
        engine = self.engine_combo.currentData()
        self._engine = engine
        self.model_combo.clear()
        if engine == ENGINE_LOCAL:
            self.model_combo.addItems(LOCAL_MODELS)
            self.model_combo.setCurrentText(self.cfg.get("local_model") or DEFAULT_LOCAL_MODEL)
        elif engine == ENGINE_CLAUDE:
            self.model_combo.addItems(MODELS)
            self.model_combo.setCurrentText(self.cfg.get("model") or DEFAULT_MODEL)
        self.model_combo.setEnabled(engine in (ENGINE_LOCAL, ENGINE_CLAUDE))
        for wdg in (self.key_row, self.key_row_label, self.effort_label, self.effort_combo):
            wdg.setVisible(engine == ENGINE_CLAUDE)
        self.engine_hint.setText(ENGINE_HINTS[engine])

    # ---- log / status
    def add_log(self, text):
        line = f"[{time.strftime('%H:%M:%S')}] {text}"
        self.log_view.appendPlainText(line)
        self.log_card.set_lines(self.log_view.blockCount())
        try:
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass

    def set_status(self, text, color):
        """The worker's WhatsApp status (text, colour) on the header's pill and the control card."""
        self.status_text = text
        state = {"#208020": "ready", "#d08000": "qr" if "QR" in text else "loading", "#c03030": "error"}.get(color, "idle")
        bg, fg, dot, words = look.WA_LOOKS.get(state, look.WA_IDLE)
        self.wa_pill.set_look(bg, fg, dot, "Waiting for WhatsApp" if text.startswith("Waiting") else words, tip=text)
        self.wa_state = state
        self.refresh_hero()

    def refresh_hero(self):
        kicker, color = look.wa_kicker(getattr(self, "wa_state", "idle"), self.is_busy)
        n = len(self.codelist.codes) if self.codelist else 0
        if self.is_busy and self.total_count:
            title = f"Finding the phone models of {self.total_count} codes"
        elif not n:
            title = "Load the code list"
        else:
            done = sum(1 for c in self.codelist.codes if is_done(self.results.get(c, {}).get("model")))
            title = f"{done} of {n} codes have a model"
        self.hero.set_status(kicker, color, title, self.status_text)
        self.hero.show_hint(not self.is_busy and getattr(self, "wa_state", "idle") == "idle")
        counts = {"ok": 0, "low": 0, "none": 0}
        for r in self.results.values():
            kind = self._kind(r)
            if kind == "ok":
                counts["ok"] += 1
            elif kind in ("none", "error"):
                counts["low" if kind == "none" else "none"] += 1
        for key, tile in self.hero.counts.items():
            tile.set_value(counts[key])

    @staticmethod
    def _kind(res):
        """A result's row kind (core/look.py KINDS): a model found, unsure, or none."""
        model = res.get("model", "")
        if model in ("NOT FOUND", "NO IMAGE") or model == "unknown":
            return "error"
        if res.get("confidence") == "low":
            return "none"
        return "ok" if is_done(model) else ""

    # ---- WhatsApp / chats
    def connect_whatsapp(self):
        self.save_config()
        self.worker.submit("connect", self.groups_only.isChecked())

    def reload_chats(self):
        if not self.is_connected:
            self.connect_whatsapp()
        else:
            self.worker.submit("list_chats", self.groups_only.isChecked())

    def on_connected(self, ok):
        self.is_connected = ok
        self.btn_connect.setText("Reconnect" if ok else "Open WhatsApp")
        self.refresh_hero()

    def on_chats(self, names):
        ticked = set(self.checked_chats())  # a reload keeps this session's ticks; the first load has none
        self.all_chats = list(names)
        self.chat_list.blockSignals(True)
        self.chat_list.clear()
        for n in names:
            it = QListWidgetItem(n)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if n in ticked else Qt.CheckState.Unchecked)
            self.chat_list.addItem(it)
        self.chat_list.blockSignals(False)
        self.filter_chats()
        self.refresh_chat_combos()
        self.save_config()
        if not any(self.map_table.cellWidget(r, MAP_CHAT).currentText() for r in range(len(self.map_rows))):
            self.auto_match()

    def checked_chats(self):
        return [self.chat_list.item(i).text() for i in range(self.chat_list.count())
                if self.chat_list.item(i).checkState() == Qt.CheckState.Checked]

    def set_all_checked(self, on):
        self.chat_list.blockSignals(True)
        for i in range(self.chat_list.count()):
            self.chat_list.item(i).setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
        self.chat_list.blockSignals(False)
        self.on_chat_checked()

    def filter_chats(self, *_):
        text = self.chat_filter.text().strip().lower()
        ticked_only = self.btn_ticked_only.isChecked()
        shown = 0
        for i in range(self.chat_list.count()):
            it = self.chat_list.item(i)
            hide = (text and text not in it.text().lower()) or (
                ticked_only and it.checkState() != Qt.CheckState.Checked)
            it.setHidden(bool(hide))
            shown += not hide
        self.chat_count.setText(f"{self.chat_list.count()} chats, {len(self.checked_chats())} ticked"
                                if self.chat_list.count() else "No chats loaded")

    def on_chat_checked(self, *_):
        self.filter_chats()
        self.refresh_chat_combos()

    def chat_options(self):
        return self.checked_chats() or self.all_chats

    def refresh_chat_combos(self):
        options = self.chat_options()
        for r in range(len(self.map_rows)):
            combo = self.map_table.cellWidget(r, MAP_CHAT)
            current = combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            combo.addItems([""] + options)
            combo.setCurrentText(current)
            combo.blockSignals(False)

    def auto_match(self):
        options = self.chat_options()
        matched = 0
        for r, prefix in enumerate(self.map_rows):
            pat = re.compile(rf"(?<![0-9a-z]){re.escape(prefix)}(?![0-9a-z])", re.I)
            given = shop_chat(prefix).lower()  # the chat the shop settings give this group
            hits = ([c for c in options if c.strip().lower() == given] if given
                    else [c for c in options if prefix and pat.search(c)])
            if hits:
                self.map_table.cellWidget(r, MAP_CHAT).setCurrentText(hits[0])
                matched += 1
        self.add_log(f"Auto-match: {matched} of {len(self.map_rows)} code groups matched to a chat by name.")

    # ---- codes file
    def browse_codes(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose the code list", self.codes_edit.text() or APP_DIR,
                                              "Text files (*.txt);;All files (*)")
        if path:
            self.codes_edit.setText(path)
            self.out_edit.setText("")
            self.load_codes()

    def browse_output(self):
        path, _ = QFileDialog.getSaveFileName(self, "Result file", self.out_edit.text() or APP_DIR,
                                              "Text files (*.txt)")
        if path:
            self.out_edit.setText(path)
            self.load_codes()

    def default_output(self, codes_path):
        base, _ = os.path.splitext(codes_path)
        return base + "_models.txt"

    def load_codes(self):
        path = self.codes_edit.text().strip()
        if not path or not os.path.exists(path):
            QMessageBox.warning(self, "Code list", "Choose an existing code list file.")
            return
        if self.map_rows:
            self.save_config()  # keep the chat choices made so far
        try:
            self.codelist = CodeList(path)
        except OSError as e:
            QMessageBox.warning(self, "Code list", f"Could not read the file:\n{e}")
            return
        if not self.out_edit.text().strip():
            self.out_edit.setText(self.default_output(path))
        existing = read_results(self.out_edit.text().strip())
        self.results = {c: {"model": existing.get(c, "")} for c in self.codelist.codes}

        blocks = self.codelist.blocks
        n_codes = len(self.codelist.codes)
        self.codes_info.setText(f"{n_codes} codes in {len(blocks)} groups: "
                                + ", ".join(f"{p} ({len(v)})" for p, v in blocks.items())
                                + (f"   -   {sum(is_done(existing.get(c)) for c in self.codelist.codes)} "
                                   f"already have a model in the result file" if existing else ""))
        self._fill_map_table()
        self._fill_results_table()
        self.save_config()

    def _fill_map_table(self):
        mapping = self.cfg.get("mapping", {})
        enabled = self.cfg.get("enabled", {})
        self.map_rows = list(self.codelist.blocks)
        self.map_table.setRowCount(len(self.map_rows))
        options = self.chat_options()
        for r, prefix in enumerate(self.map_rows):
            codes = self.codelist.blocks[prefix]
            use = QTableWidgetItem()
            use.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            use.setCheckState(Qt.CheckState.Checked if enabled.get(prefix, True) else Qt.CheckState.Unchecked)
            self.map_table.setItem(r, MAP_USE, use)
            for col, text in ((MAP_PREFIX, prefix or "Orders"), (MAP_COUNT, str(len(codes))),
                              (MAP_RANGE, f"{codes[-1]}  ->  {codes[0]}")):
                it = QTableWidgetItem(text)
                it.setFlags(Qt.ItemFlag.ItemIsEnabled)
                it.setToolTip(f"{len(codes)} codes, {codes[-1]} (bottom) up to {codes[0]}")
                self.map_table.setItem(r, col, it)
            combo = QComboBox()
            combo.setEditable(True)
            combo.addItems([""] + options)
            combo.setCurrentText(mapping.get(prefix, "") or shop_chat(prefix))
            combo.lineEdit().setPlaceholderText("choose or type the chat name")
            self.map_table.setCellWidget(r, MAP_CHAT, combo)

    def _fill_results_table(self):
        self._filling_table = True
        self.table.setRowCount(0)
        self.code_rows = {}
        for code in self.codelist.codes:
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.code_rows[code] = r
            for col in range(6):
                it = QTableWidgetItem("")
                if col != COL_MODEL:
                    it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self.table.setItem(r, col, it)
            self.table.item(r, COL_CODE).setText(self.codelist.label[code])
            self._show_result(code)
        self._filling_table = False

    def _show_result(self, code):
        r = self.code_rows.get(code)
        if r is None:
            return
        res = self.results.get(code, {})
        was = self._filling_table
        self._filling_table = True
        model = res.get("model", "")
        self.table.item(r, COL_MODEL).setText(model)
        self.table.item(r, COL_CONF).setText(res.get("confidence", ""))
        self.table.item(r, COL_PHOTOS).setText(str(res["images"]) if res.get("images") else "")
        self.table.item(r, COL_CHAT).setText(res.get("chat", ""))
        self.table.item(r, COL_NOTE).setText(res.get("note", ""))
        self.table.item(r, COL_CODE).setData(KIND, self._kind(res))  # the row's status square
        self._filling_table = was
        self.refresh_hero()

    def write_output(self):
        if not self.codelist or not self.out_edit.text().strip():
            return
        try:
            write_results(self.out_edit.text().strip(), self.codelist,
                          {c: r.get("model", "") for c, r in self.results.items()})
        except OSError as e:
            self.add_log(f"Could not write the result file: {e}")

    def on_table_edit(self, item):
        if self._filling_table or item.column() != COL_MODEL or not self.codelist:
            return
        code = self.codelist.codes[item.row()]
        self.results.setdefault(code, {})["model"] = item.text().strip()
        self._show_result(code)
        self.write_output()

    def open_photo_folder(self, row, _col):
        if not self.codelist:
            return
        folder = self.results.get(self.codelist.codes[row], {}).get("folder")
        if not folder:
            folder = os.path.join(self.photo_dir(), self.codelist.codes[row])
        if os.path.isdir(folder):
            os.startfile(folder)

    def photo_dir(self):
        base, _ = os.path.splitext(self.out_edit.text().strip() or self.default_output(self.codes_edit.text()))
        return base + "_photos"

    # ---- run
    def start_scan(self):
        if self.is_busy:
            return
        if not self.codelist and os.path.isfile(self.codes_edit.text().strip()):
            self.load_codes()
        if not self.codelist:
            QMessageBox.warning(self, "Start", "Load the code list first.")
            return
        if not self.is_connected:
            QMessageBox.warning(self, "Start", "Open WhatsApp and wait until it says Connected.")
            return
        out = self.out_edit.text().strip()
        if not out:
            QMessageBox.warning(self, "Start", "Choose a result file.")
            return

        # group the chosen code groups by chat, bottom group first
        jobs = {}
        skipped_done = 0
        for r, prefix in reversed(list(enumerate(self.map_rows))):
            if self.map_table.item(r, MAP_USE).checkState() != Qt.CheckState.Checked:
                continue
            chat = self.map_table.cellWidget(r, MAP_CHAT).currentText().strip()
            if not chat:
                QMessageBox.warning(self, "Start", f"Choose a WhatsApp chat for code group '{prefix or 'Orders'}' "
                                                   f"(or untick it).")
                return
            codes = self.codelist.blocks[prefix]
            todo = codes
            if self.skip_done.isChecked():
                todo = [c for c in codes if not is_done(self.results.get(c, {}).get("model"))]
                skipped_done += len(codes) - len(todo)
            if not todo:
                continue
            job = jobs.setdefault(chat, {"chat": chat, "codes": [], "ranges": {}})
            job["codes"] = todo + job["codes"]  # keep file order (top -> bottom)
            job["ranges"][prefix] = self.codelist.prefix_range(prefix)
        if not jobs:
            QMessageBox.information(self, "Start", "Nothing to do - every chosen code already has a model.")
            return

        api_key = self.key_edit.text().strip()
        engine = self.engine_combo.currentData()
        if engine == ENGINE_CLAUDE and not api_key and not os.environ.get("ANTHROPIC_API_KEY"):
            if QMessageBox.question(
                    self, "No API key",
                    "No Claude API key is set, so phone models cannot be identified.\n\n"
                    "Continue anyway and only save the photos of each code?") != QMessageBox.StandardButton.Yes:
                return

        self.save_config()
        opts = dict(
            mode=self.mode_combo.currentData(),
            max_images=self.max_images.value(),
            engine=engine,
            api_key=api_key,
            model=self.model_combo.currentText().strip()
            or (DEFAULT_LOCAL_MODEL if engine == ENGINE_LOCAL else DEFAULT_MODEL),
            effort=self.effort_combo.currentText(),
            # without AI the saved photos are the only way to check a code, so always keep them then
            photo_dir=self.photo_dir() if (self.save_photos.isChecked() or engine == ENGINE_NONE) else "",
            extra_screens=self.extra_screens.value(),
            max_steps=self.max_steps.value(),
        )
        job_list = list(jobs.values())
        self.total_count = sum(len(j["codes"]) for j in job_list)
        self.done_count = 0
        self.progress.setMaximum(self.total_count)
        self.progress.setValue(0)
        self.add_log(f"Starting: {self.total_count} codes in {len(job_list)} chats"
                     + (f" ({skipped_done} already done, skipped)" if skipped_done else "") + ".")
        if self.look.mode == "narrow":
            self.look.show_tab("results")
        self.worker.submit("scan", job_list, opts)

    def stop_scan(self):
        self.worker.stop()
        self.add_log("Stopping...")

    def on_busy(self, busy, cmd):
        self.is_busy = busy
        self.busy_changed.emit(busy)
        self.btn_start.setEnabled(not busy)
        self.btn_connect.setEnabled(not busy)
        self.btn_reload_chats.setEnabled(not busy)
        self.btn_stop.setEnabled(busy)
        self.refresh_hero()
        if not busy and cmd == "scan":
            self.write_output()
            self.add_log(f"Result file: {self.out_edit.text().strip()}")

    def on_result(self, d):
        code = d.pop("code")
        final = d.pop("final")
        res = self.results.setdefault(code, {})
        res.update({k: v for k, v in d.items() if v is not None})
        self._show_result(code)
        if "model" in d:
            self.write_output()
        if final:
            self.done_count += 1
            self.progress.setValue(min(self.done_count, self.progress.maximum()))
        r = self.code_rows.get(code)
        if r is not None:
            self.table.scrollToItem(self.table.item(r, COL_CODE))

    def closeEvent(self, event):
        self.save_config()
        self.worker.stop()
        self.worker.submit("quit")
        self.worker.wait(20000)
        event.accept()
