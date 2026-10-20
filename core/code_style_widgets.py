"""The shop's code style (core/codes.py) on the Shop settings page: pick one of the three styles, or
type a few of your codes and let the app pick; paste a message to see the codes it finds. Saved in the
shop settings (core/shop.py: "code_style", and "orders_chat" for codes without a prefix).
"""
from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QButtonGroup, QVBoxLayout, QWidget

from core import codes, shop
from core import webui as W

_STYLES = (  # kind, tile text
    ("prefix", "Prefix and number, like 2-558"),
    ("letters", "Letters and number, like CC1023 or AB-12"),
    ("keyword", "# or a word and number, like #1023 or Order 1023"),
)
_SEPS = (("", "CC1023"), ("-", "CC-1023"), (" ", "CC 1023"))


def follow_shop():
    """Every tool reads codes in the shop's style from now on, also after it changes here or on
    another device."""
    s = shop.get()

    def apply():
        codes.set_style(s.settings.get("code_style"))
    apply()
    s.changed.connect(apply)


def _words(text: str) -> list[str]:
    return [w for w in text.replace(",", " ").split() if w]


class CodeStyleBox(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.shop = shop.get()
        self._loading = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        self.group = QButtonGroup(self)
        self.tiles: dict[str, W.ToggleTile] = {}
        for kind, text in _STYLES:
            tile = W.ToggleTile(text, None, 40)
            self.group.addButton(tile)
            self.tiles[kind] = tile
            lay.addWidget(tile)
            tile.toggled.connect(lambda on: on and self._changed())

        # the letters of the letters style, and how a code is written
        self.letters_box = QWidget()
        col = W.v_col(margins=(0, 0, 0, 0))
        self.letters_box.setLayout(col)
        col.addWidget(W.label("Your letters", 14, 500))
        self.letters = W.Input("e.g. CC, AB")
        self.letters.textEdited.connect(lambda _: self._changed())
        self.sep = W.Select([(sep, f"Written {ex}") for sep, ex in _SEPS], height=40)
        self.sep.currentIndexChanged.connect(lambda _: self._changed())
        col.addLayout(W.h_row(self.letters, self.sep, stretch_at=0))
        col.addWidget(W.label("Each letters' codes are in the WhatsApp chat set for those letters below "
                              "(e.g. CC), like a list of prefix codes.", 13, 400, W.MUTED_FG, wrap=True))
        lay.addWidget(self.letters_box)

        # the # or words of the keyword style, and the one chat its codes are in
        self.keyword_box = QWidget()
        col = W.v_col(margins=(0, 0, 0, 0))
        self.keyword_box.setLayout(col)
        col.addWidget(W.label("Your # or word", 14, 500))
        self.keywords = W.Input("e.g. #, Order")
        self.keywords.textEdited.connect(lambda _: self._changed())
        col.addWidget(self.keywords)
        col.addWidget(W.label("Orders chat", 14, 500))
        self.orders_chat = W.Input("WhatsApp chat name")
        self.orders_chat.textEdited.connect(lambda _: self._changed())
        col.addWidget(self.orders_chat)
        col.addWidget(W.label("These codes have no prefix, so all of them are looked for in this one chat.",
                              13, 400, W.MUTED_FG, wrap=True))
        lay.addWidget(self.keyword_box)

        # type a few codes instead
        lay.addWidget(W.Separator())
        lay.addWidget(W.label("Not sure? Type two or three of your codes", 14, 500))
        self.examples = W.Input("e.g. CC1023, CC1050")
        self.examples.returnPressed.connect(self._guess)
        pick = W.Button("Pick the style", "wand-2", "outline", height=40)
        pick.clicked.connect(self._guess)
        lay.addLayout(W.h_row(self.examples, pick, stretch_at=0))
        self.guess_note = W.label("", 13, 400, W.MUTED_FG, wrap=True)
        self.guess_note.hide()
        lay.addWidget(self.guess_note)

        # try it on a message
        lay.addWidget(W.label("Try it: paste a message from WhatsApp", 14, 500))
        self.sample = W.TextArea("e.g. CC1023+1024 Realme C85, 850 tk", rows=3)
        self.sample.textChanged.connect(self._try_later)
        lay.addWidget(self.sample)
        self.found = W.label("", 13, 400, W.MUTED_FG, wrap=True, selectable=True)
        lay.addWidget(self.found)
        self._try_timer = QTimer(self, singleShot=True, interval=250)
        self._try_timer.timeout.connect(self._try)

        self.error = W.label("", 13, 400, W.DESTRUCTIVE, wrap=True)
        self.error.hide()
        lay.addWidget(self.error)
        self.save_btn = W.Button("Save code style", "check")
        self.save_btn.clicked.connect(self.save)
        self.undo_btn = W.Button("Undo", None, "outline")
        self.undo_btn.clicked.connect(self.load)
        lay.addLayout(W.h_row(self.save_btn, self.undo_btn, None))

        self.shop.changed.connect(self._on_shop_changed)
        self.load()

    # ---- showing -------------------------------------------------------------------------------
    def load(self):
        """Shows the saved style."""
        self._show(codes.clean_style(self.shop.settings.get("code_style")),
                   str(self.shop.settings.get("orders_chat") or ""))
        self.error.hide()
        self._set_dirty(False)

    def _show(self, style: dict, orders_chat: str | None = None):
        self._loading = True
        self.tiles[style["kind"]].setChecked(True)
        if style["kind"] == "letters":
            self.letters.setText(", ".join(style["letters"]))
            self.sep.set_value(style["sep"])
        elif style["kind"] == "keyword":
            self.keywords.setText(", ".join(style["keywords"]))
        if orders_chat is not None:
            self.orders_chat.setText(orders_chat)
        self._loading = False
        self._show_fields()
        self._try()

    def _on_shop_changed(self):
        if not self.save_btn.isEnabled():  # nothing changed here: show what was saved (maybe elsewhere)
            self.load()

    def _kind(self) -> str:
        return next((k for k, t in self.tiles.items() if t.isChecked()), "prefix")

    def _show_fields(self):
        kind = self._kind()
        self.letters_box.setVisible(kind == "letters")
        self.keyword_box.setVisible(kind == "keyword")

    def _changed(self):
        self._show_fields()
        self._try()
        if not self._loading:
            self._set_dirty(self._style_typed() != codes.clean_style(self.shop.settings.get("code_style"))
                            or self.orders_chat.text().strip() != (self.shop.settings.get("orders_chat") or ""))

    def _set_dirty(self, on: bool):
        self.save_btn.setEnabled(on)
        self.undo_btn.setEnabled(on)

    # ---- the style typed in the box ------------------------------------------------------------
    def _style_typed(self) -> dict:
        """The style as the box shows it (clean_style makes it the prefix style when it is not complete)."""
        kind = self._kind()
        if kind == "letters":
            return codes.clean_style({"kind": "letters", "letters": _words(self.letters.text()),
                                      "sep": self.sep.value() or ""})
        if kind == "keyword":
            return codes.clean_style({"kind": "keyword", "keywords": _words(self.keywords.text())})
        return dict(codes.DEFAULT_STYLE)

    def _problems(self) -> list[str]:
        kind = self._kind()
        problems = []
        if kind == "letters":
            bad = [w for w in _words(self.letters.text()) if not w.isascii() or not w.isalpha() or len(w) > 4]
            if bad:
                problems.append(f"Letters are 1 to 4 letters A-Z, not “{bad[0]}”.")
            elif not _words(self.letters.text()):
                problems.append("Type your letters, e.g. CC.")
        elif kind == "keyword":
            bad = [w for w in _words(self.keywords.text()) if w != "#" and not (w.isascii() and w.isalpha() and len(w) <= 12)]
            if bad:
                problems.append(f"Type # or one word of the letters A-Z, not “{bad[0]}”.")
            elif not _words(self.keywords.text()):
                problems.append("Type # or the word before your numbers, e.g. Order.")
            if not self.orders_chat.text().strip():
                problems.append("Type the name of the WhatsApp chat your orders are in.")
        return problems

    def _guess(self):
        style, problem = codes.guess_style(self.examples.text())
        if style is None:
            self.guess_note.setText(problem)
            W.set_color(self.guess_note, W.DESTRUCTIVE)
        else:
            self._show(style)
            self._changed()
            self.guess_note.setText(f"{codes.describe(style)}. Save it if this is right.")
            W.set_color(self.guess_note, W.MUTED_FG)
        self.guess_note.show()

    def _try_later(self):
        self._try_timer.start()

    def _try(self):
        text = self.sample.toPlainText()
        if not text.strip():
            self.found.setText(f"Codes will look like {codes.example(self._style_typed())}.")
            return
        r = codes.Reader(self._style_typed())
        found = [r.written(*codes.split_code(key)) for key in r.codes_in_text(text)]
        self.found.setText(f"Codes found: {', '.join(found)}" if found
                           else f"No codes found. A code in this style looks like {codes.example(r.style)}.")

    # ---- saving ---------------------------------------------------------------------------------
    def save(self):
        problems = self._problems()
        if problems:
            self.error.setText("\n".join(problems))
            self.error.show()
            return
        style = self._style_typed()
        orders_chat = self.orders_chat.text().strip()
        self.shop.save(code_style=None if style == codes.DEFAULT_STYLE else style,
                       orders_chat=orders_chat or None)
        self.load()
        W.toast.success(f"Code style saved: {codes.describe(style)}.")
