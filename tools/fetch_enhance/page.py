"""Fetch & Enhance: from the code list alone, does Fetch by Code, the Photo Enhancer (4x) and
Rename by Match in one go. Each code's photos are found in WhatsApp, only the photos of people are
kept (phones, screenshots … are never saved) and enhanced. Then Rename by Match runs on them by
itself, the same as on its own page: each list's chat ("… (6-)") is searched, the enhanced photos are
compared with the photos posted there and renamed "2-226 Realme P3 Ultra.jpg", or put in the folder
"2-221 Oppo a6 pro 5g" as img1.jpg, img2.jpg … ("Undo last rename" on its page undoes it).
Without that step the photos get those names straight away.

The page is Fetch by Code's (tools/whatsapp_fetch/page.py) with more on its Settings card: 4× / 2× /
Off, only photos of people, then Rename by Match, the (note). The work is in worker.py and Rename by
Match's engine. Settings are kept in data/fetch_enhance.
"""
from __future__ import annotations

import queue
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QButtonGroup, QComboBox, QFrame, QHBoxLayout, QLabel, QVBoxLayout

from core import account as acc
from core import look, theme
from core.account_widgets import LimitDialog
from tools.match_renamer import engine as rbm
from tools.match_renamer.page import load_settings as rbm_settings
from tools.whatsapp_fetch.helpers import existing_output
from tools.whatsapp_fetch.page import MainWindow

from .worker import FetchEnhanceWorker, existing_named

SCALES = [("4× (recommended)", 4), ("2×", 2), ("Don't enhance", 0)]
SCALE_CARDS = [("4×", "Recommended"), ("2×", "Smaller files"), ("Off", "Don't enhance")]  # in SCALES' order


class FetchEnhancePage(MainWindow):
    START_TEXT = ("Start", "Start selected")

    def __init__(self):
        self.account = acc.get()
        self.saved_rows: list[int] = []      # rows saved by this run, for Rename by Match
        self.rename_rows: list[int] = []     # rows Rename by Match is working on
        self.row_texts: dict[int, str] = {}  # their status before it
        self.fetch_summary = ""
        self.stopping = False
        self.renamer: rbm.Worker | None = None  # Rename by Match's worker thread (made on first use)
        self.renamer_ui: queue.Queue = queue.Queue()
        super().__init__("Fetch & Enhance", "fetch_enhance")
        self.renamer_timer = QTimer(self, interval=100, timeout=self._poll_renamer)

    # ---- the Settings card's own part -------------------------------------------------
    def _extra_settings(self, card: look.Card):
        # Enhance: 4× / 2× / Off
        self.scale_combo = QComboBox(self)  # the setting itself; the cards show and change it
        self.scale_combo.hide()
        for label, scale in SCALES:
            self.scale_combo.addItem(label, scale)
        self.scale_cards = QButtonGroup(self)
        cards = QHBoxLayout()
        cards.setSpacing(8)
        for i, (label, sub) in enumerate(SCALE_CARDS):
            c = look.OptionCard(label, sub)
            self.scale_cards.addButton(c, i)
            cards.addWidget(c)
        self.scale_cards.idClicked.connect(self.scale_combo.setCurrentIndex)
        self.scale_combo.currentIndexChanged.connect(self._on_scale)
        card.lay.addLayout(look.column(look.field("Enhance"), cards, look.hint(
            "Each photo is enhanced on the Boostimise server, like in the Photo Enhancer "
            "(it counts in your daily enhancements).")))

        self.people_check = look.ToggleRow("scan-face", theme.SUCCESS_SOFT, theme.SUCCESS, "Only photos of people",
                                           "Skip phones, screenshots …")
        self.people_check.setToolTip("A photo is kept when the server finds a face in it big enough for a photo\n"
                                     "of people. The others are never saved.")
        self.rename_check = look.ToggleRow("tag", theme.ACCENT_SOFT, theme.ACCENT, "Then Rename by Match",
                                           "Compare with the photos posted in each list's chat")
        self.rename_check.setToolTip("When all codes are done, Rename by Match searches each list's chat (\"… (6-)\"),\n"
                                     "compares the enhanced photos with the photos posted there and renames them.\n"
                                     "Its strictness and message limit are the ones set on the Rename by Match page.\n"
                                     "Off: the photos get those names straight away, without comparing.")
        self.note_check = look.ToggleRow("message-circle", theme.WARNING_SOFT, theme.WARNING, "Add the (note)",
                                         "From the WhatsApp caption")
        for t in (self.people_check, self.rename_check):
            t.toggled.connect(lambda _: self._refresh_settings_summary())
        card.lay.addLayout(look.column(self.people_check, self.rename_check, self.note_check, spacing=2))
        card.lay.addWidget(self._names_box())

    @staticmethod
    def _names_box() -> QFrame:
        """What the photos are named: one photo, or a folder for two or more."""
        box = QFrame(objectName="names")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)
        lay.addWidget(look.field("Named like Rename by Match"))
        for icon, bg, fg, name, sub, note in (
                ("image", theme.SUCCESS_SOFT, theme.SUCCESS, "2-226 Realme P3 Ultra.jpg", "", "one photo"),
                ("folder", theme.ACCENT_SOFT, theme.ACCENT, "2-221 Oppo a6 pro 5g", "img1.jpg, img2.jpg …", "2 or more")):
            texts = look.column(QLabel(name, objectName="mono"), spacing=0)
            if sub:
                texts.addWidget(QLabel(sub, objectName="monoSub"))
            lay.addLayout(look.row(look.IconSquare(icon, bg, fg, 28, 15), texts, QLabel(note, objectName="hint"),
                                   spacing=10, stretch_at=1))
        return box

    def _on_scale(self, index: int):
        card = self.scale_cards.button(index)
        if card is not None:
            card.setChecked(True)
        self._refresh_settings_summary()

    def _settings_summary(self) -> list[str]:
        scale = self.scale_combo.currentData()
        parts = [f"{scale}× enhance" if scale else "Not enhanced"]
        if self.people_check.isChecked():
            parts.append("people only")
        if self.rename_check.isChecked():
            parts.append("then Rename by Match")
        return parts

    def _hero_override(self) -> tuple[str, str] | None:
        if self.rename_rows:
            return "Rename by Match", self.activity or "Comparing the enhanced photos with WhatsApp…"
        return None

    # ---- settings ------------------------------------------------------------------
    def _load_settings(self):
        super()._load_settings()
        s = self.settings
        self.scale_combo.setCurrentIndex(max(self.scale_combo.findData(int(s.value("scale", 4))), 0))
        self.people_check.setChecked(s.value("people_only", True, type=bool))
        self.rename_check.setChecked(s.value("rename_after", True, type=bool))
        self.note_check.setChecked(s.value("add_note", False, type=bool))
        self._on_scale(self.scale_combo.currentIndex())

    def _save_settings(self):
        s = self.settings
        s.setValue("scale", self.scale_combo.currentData())
        s.setValue("people_only", self.people_check.isChecked())
        s.setValue("rename_after", self.rename_check.isChecked())
        s.setValue("add_note", self.note_check.isChecked())
        super()._save_settings()

    # ---- the hooks of Fetch by Code's page -------------------------------------------
    def _new_worker(self) -> FetchEnhanceWorker:
        return FetchEnhanceWorker(self.account)

    def _extra_options(self) -> dict:
        return {"scale": self.scale_combo.currentData(), "people_only": self.people_check.isChecked(),
                "rename_after": self.rename_check.isChecked(), "add_note": self.note_check.isChecked()}

    def _existing(self, out_dir: Path, base: str) -> Path | None:
        return existing_named(out_dir, base) or existing_output(out_dir, base)

    def _connect(self):
        super()._connect()
        self.worker.limit_hit.connect(self._on_limit)
        self.worker.code_saved.connect(self.saved_rows.append)

    def _start(self, selected_only: bool):
        if not self.job_running and self.scale_combo.currentData():
            p = self.account.profile
            if self.account.user and p and not p.get("onboarded"):
                self.account.refresh()  # the mobile-number step comes first
                return
            if p and p.get("blocked"):
                self._on_limit("blocked")
                return
            q = self.account.quota
            if q is not None and q.remaining == 0:
                self._on_limit("limit")
                return
        if not self.job_running:
            self.saved_rows.clear()
            self.stopping = False
        super()._start(selected_only)

    def _stop(self):
        self.stopping = True
        if self.rename_rows and self.renamer:
            self.renamer.stop_event.set()
            self._log("Stopping Rename by Match…")
        else:
            super()._stop()

    def _job_ended(self, summary: str):
        if self.rename_rows:
            return  # WhatsApp closed while Rename by Match works: it goes on in its own connection
        rows = sorted(set(self.saved_rows))
        self.saved_rows.clear()
        if not rows or self.stopping or not self.rename_check.isChecked() or not self._start_rename(rows, summary):
            super()._job_ended(summary)

    # ---- Rename by Match on the enhanced photos ------------------------------------------
    def _start_rename(self, rows: list[int], summary: str) -> bool:
        """Runs Rename by Match's job on the output folder for the codes saved in this run."""
        out = Path(self.out_edit.text().strip())
        include_date = self.date_check.isChecked()
        rows = [r for r in rows if r < len(self.items) and self.items[r].name]  # no name: nothing to rename to
        if not rows:
            return False
        lines = [self.items[r].base_name(include_date) for r in rows]
        txt = self.data_dir / "rename_list.txt"
        txt.write_text("\n".join(lines) + "\n", encoding="utf-8")
        products, _ = rbm.parse_products(txt)
        groups = rbm.list_groups(products)
        saved = rbm_settings()
        saved_lists = saved.get("lists") if isinstance(saved.get("lists"), dict) else {}
        chats = [self.chat_combo.itemText(i) for i in range(3, self.chat_combo.count())]
        lists = []
        self._log("")
        self._log(f"Rename by Match: {len(products)} code(s) in {out}")
        for g in groups:
            chat = (saved_lists.get(g) or {}).get("chat", "").strip() or next(
                (c for c in chats if rbm.group_in_name(g, c)), "")
            if chat:
                lists.append({"group": g, "on": True, "chat": chat})
                self._log(f"  {g} list  →  {chat}")
            else:
                self._log(f"  {g} list  →  no chat with \"{g}\" in its name: choose it on the Rename by Match "
                          "page once; these photos keep their names for now")
        if not lists:
            return False
        opts = {"txt": str(txt), "folder": str(out), "recursive": False,
                "threshold": int(saved.get("threshold", 10)), "max_messages": int(saved.get("max_messages", 3000)),
                "neighbors": bool(saved.get("neighbors", True)), "add_note": self.note_check.isChecked(),
                "dry_run": False, "lists": lists}
        if self.renamer is None:
            self.renamer = rbm.Worker(self.renamer_ui)
            self.renamer.start()
        self.rename_rows = rows
        self.fetch_summary = summary
        self.row_texts = {r: self.table.item(r, 3).text() for r in rows}
        for r in rows:
            self._set_row(r, "working", "Waiting for Rename by Match…", "")
        self.renamer.submit(self.renamer.job_run, opts)
        self.renamer_timer.start()
        return True

    def _poll_renamer(self):
        try:
            while True:
                kind, val = self.renamer_ui.get_nowait()
                if kind == "log":
                    self._log(val[0])
                elif kind == "status":
                    self.statusBar().showMessage(val)
                elif kind == "progress":
                    value, maximum = val
                    self.progress.setRange(0, max(maximum, 0))
                    self.progress.setValue(value)
                elif kind == "busy" and not val:
                    self._rename_done()
                    return
        except queue.Empty:
            pass

    def _rename_done(self):
        self.renamer_timer.stop()
        rows, self.rename_rows = self.rename_rows, []
        out = Path(self.out_edit.text().strip())
        include_date = self.date_check.isChecked()
        renamed = 0
        for r in rows:
            base = self.items[r].base_name(include_date)
            named = existing_named(out, base)
            before = self.row_texts.get(r, "")
            if named is not None:
                renamed += 1
                self._set_row(r, "ok", f"{before}, renamed by match", str(named))
            else:
                left = existing_output(out, base)
                self._set_row(r, "error", f"{before}, NOT renamed (no match in WhatsApp, see the log)",
                              str(left) if left else "")
        super()._job_ended(f"{self.fetch_summary}  Rename by Match: {renamed} of {len(rows)} code(s) renamed.")

    # ---- quota -----------------------------------------------------------------------
    def _on_limit(self, code: str):
        if code == "onboarding":
            self.account.refresh()
        elif code == "update_required":
            from core import guard
            guard.get().check()
        else:
            LimitDialog(self, code, on_plans=self._open_plans).exec()

    def _open_plans(self):
        from core.shell import find_shell
        shell = find_shell(self)
        if shell is not None:
            shell.open_tool("plans")

    def closeEvent(self, event):
        if self.renamer:
            self.renamer.stop_event.set()
            self.renamer.jobs.put(None)
        super().closeEvent(event)
