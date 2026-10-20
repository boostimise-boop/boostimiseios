"""Shop settings - the website's app/shop/ShopView.tsx: the shop's name, the reactions the Reaction
List looks for and what each one means, and the WhatsApp chat of each code list (Pathao Auto-fill).
Saved on this PC and, signed in, in the account so every Boostimise app uses the same (core/shop.py).
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

from core import account as acc
from core import shop
from core import webui as W
from tools.reaction_list.helpers import split_emojis

from .dashboard import card as _card

try:  # the code style section (core/code_style_widgets.py, with the code styles)
    from core.code_style_widgets import CodeStyleBox
except ImportError:
    CodeStyleBox = None


def card(title: str, description: str):
    box, lay = _card(title, description)
    box.description.setWordWrap(True)
    return box, lay


class _Rows(QWidget):
    """Rows of two boxes with a remove button each, and an Add button under them."""

    def __init__(self, first: tuple[str, int], second: str, add_text: str, on_change):
        super().__init__()
        self.first, self.second, self.on_change = first, second, on_change
        self.rows: list[tuple[QWidget, W.Input, W.Input]] = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.list = QVBoxLayout()
        self.list.setSpacing(8)
        lay.addLayout(self.list)
        add = W.Button(add_text, "plus", "outline", "sm")
        add.clicked.connect(lambda: (self.add("", ""), self.rows[-1][1].setFocus()))
        lay.addLayout(W.h_row(add, None))

    def add(self, a: str, b: str):
        row = QWidget()
        one = W.Input(self.first[0])
        one.setFixedWidth(self.first[1])
        one.setText(a)
        two = W.Input(self.second)
        two.setText(b)
        remove = W.Button("", "x", "ghost", "icon")
        remove.setToolTip("Remove")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lay.addWidget(one)
        lay.addWidget(two, 1)
        lay.addWidget(remove)
        entry = (row, one, two)
        remove.clicked.connect(lambda: self.remove(entry))
        one.textEdited.connect(lambda _: self.on_change())
        two.textEdited.connect(lambda _: self.on_change())
        self.rows.append(entry)
        self.list.addWidget(row)
        self.on_change()

    def remove(self, entry):
        self.rows.remove(entry)
        entry[0].deleteLater()
        self.on_change()

    def set(self, pairs: list[tuple[str, str]]):
        for row, _, _ in self.rows:
            row.deleteLater()
        self.rows = []
        for a, b in pairs:
            self.add(a, b)

    def values(self) -> list[tuple[str, str]]:
        return [(one.text().strip(), two.text().strip()) for _, one, two in self.rows]


class ShopPage(W.WebPage):
    def __init__(self):
        super().__init__()
        self.shop = shop.get()
        self.account = acc.get()
        self._loading = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; }")
        scroll.viewport().setAutoFillBackground(False)
        outer.addWidget(scroll)
        page = W.WebPage()
        scroll.setWidget(page)
        holder = QHBoxLayout(page)
        holder.setContentsMargins(24, 32, 24, 48)
        col_w = QWidget()
        col_w.setMaximumWidth(760)
        holder.addStretch(1)
        holder.addWidget(col_w, 1000)
        holder.addStretch(1)
        col = QVBoxLayout(col_w)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(20)

        col.addWidget(W.label("Shop settings", 28, 700))
        self.where = W.label("", 14, 400, W.MUTED_FG, wrap=True)
        col.addWidget(self.where)

        box, lay = card("Your shop", "Shown on your Boostimise apps.")
        self.name = W.Input("Shop name")
        self.name.textEdited.connect(lambda _: self._changed())
        lay.addWidget(self.name)
        col.addWidget(box)

        if CodeStyleBox is not None:
            box, lay = card("Code style", "How your product codes are written in WhatsApp.")
            lay.addWidget(CodeStyleBox())
            col.addWidget(box)

        box, lay = card("Reactions", "The reactions Reaction List looks for, and what each one means to you "
                        "(e.g. ❌ Cancelled, 📵 Phone off). The meaning is written next to the reaction in the list.")
        self.reactions = _Rows(("Emoji", 72), "What it means (optional)", "Add reaction", self._changed)
        lay.addWidget(self.reactions)
        reset = W.Button("Use ❌ 📵 😥", None, "ghost-muted", "sm")
        reset.setToolTip("Start again from the three reactions Boostimise comes with")
        reset.clicked.connect(lambda: self.reactions.set([(r["emoji"], r["label"]) for r in shop.DEFAULT_REACTIONS]))
        lay.addLayout(W.h_row(reset, None))
        col.addWidget(box)

        box, lay = card("WhatsApp chats", "The chat where each code list's orders are posted, e.g. the 6- codes "
                        "in “My Shop (6-)”. A list without a chat here uses the chat named like the list (“6-”).")
        self.chats = _Rows(("List, e.g. 6", 120), "WhatsApp chat name", "Add chat", self._changed)
        lay.addWidget(self.chats)
        col.addWidget(box)

        self.error = W.label("", 13, 400, W.DESTRUCTIVE, wrap=True)
        self.error.hide()
        col.addWidget(self.error)
        self.save_btn = W.Button("Save", "check")
        self.save_btn.clicked.connect(self.save)
        self.undo_btn = W.Button("Undo changes", None, "outline")
        self.undo_btn.clicked.connect(self.load)
        col.addLayout(W.h_row(self.save_btn, self.undo_btn, None))
        col.addStretch(1)

        self.shop.changed.connect(self._on_shop_changed)
        self.account.changed.connect(self._render_where)
        self.load()

    def page_shown(self):
        if not self.save_btn.isEnabled():  # nothing typed: show what the other apps saved
            self.load()

    # ---- showing ---------------------------------------------------------------------------
    def load(self):
        self._loading = True
        self.name.setText(self.shop.shop_name)
        self.reactions.set([(r["emoji"], r["label"]) for r in self.shop.reactions()])
        self.chats.set(sorted(self.shop.chats().items()))
        self._loading = False
        self.error.hide()
        self._set_dirty(False)
        self._render_where()

    def _on_shop_changed(self):
        if not self.save_btn.isEnabled():
            self.load()

    def _render_where(self):
        a = self.account
        if a.state == acc.READY and not a.offline:
            self.where.setText("Saved in your Boostimise account: the website and the Windows, Android and "
                               "iPhone apps all use these settings.")
        else:
            self.where.setText("Saved on this PC. Sign in to use the same settings on the website and your phone.")

    def _changed(self):
        if not self._loading:
            self._set_dirty(True)

    def _set_dirty(self, on: bool):
        self.save_btn.setEnabled(on)
        self.undo_btn.setEnabled(on)

    # ---- saving ----------------------------------------------------------------------------
    def save(self):
        reactions, problems = [], []
        for emoji, label in self.reactions.values():
            if not emoji and not label:
                continue
            parts = split_emojis(emoji)
            if len(parts) != 1 or parts[0].isascii():
                problems.append(f"“{emoji or label}”: put one emoji in the Emoji box.")
                continue
            reactions.append({"emoji": parts[0], "label": label})
        if not reactions:
            problems.append("Add at least one reaction.")
        chats = {}
        for prefix, name in self.chats.values():
            prefix = prefix.strip().rstrip("-").lower()
            if not prefix and not name:
                continue
            if not prefix or not name:
                problems.append("Each chat needs both the list (e.g. 6) and the chat's name.")
                continue
            chats[prefix] = name
        if problems:
            self.error.setText("\n".join(dict.fromkeys(problems)))
            self.error.show()
            return
        self.shop.save(reactions=reactions, chats=chats, shop_name=self.name.text())
        self.load()
        W.toast.success("Shop settings saved.")
