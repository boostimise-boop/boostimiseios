"""The look of the WhatsApp tools' pages, and the pieces they are built from (solid colours only,
from core/theme.py):

  Header          the tool's section, title and description, and controls on the right
  WaPill          the WhatsApp connection as a coloured pill
  Hero            the navy control card: a progress ring, what is happening, counts and buttons
  Card            a white rounded card with an icon square, a title and a line under it
  LogCard         a card with the log, which folds away
  ToolLayout      header, control card, then the settings cards beside the results; on a narrow
                  page tabs that show one part at a time
  Segmented, OptionCard, ToggleRow, FilterPill, DisclosureButton, IconSquare, CountTile, RingProgress
  CodeChips       a code list as chips, list by list
  RowsDelegate    paints a results table as a list of rows (status square, code chip, coloured status)

A page sets STYLE as its style sheet (plus anything of its own).
"""
from __future__ import annotations

import re

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QIcon, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton, QBoxLayout, QButtonGroup, QCheckBox, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSplitter, QStyle, QStyledItemDelegate, QVBoxLayout, QWidget,
)

from . import codes, theme
from . import webui as W
from .ui import FlowLayout, scrollable

WIDE = 1100          # page width (px) from which tables show every column
STACK_BELOW = 760    # page width (px) under which the page shows one part at a time (tabs)
INFO_MIN = 320       # width (px) the control card keeps for its progress and status beside the counts or buttons
LINE = "#e6ebf3"     # card outlines and rules
TRACK = "#f1f4f9"    # grey tracks and chips on white
WHATSAPP_HINT = ("WhatsApp opens in one Edge window that every tool shares. First time: scan the QR code there "
                 "(WhatsApp › Linked devices); the login is remembered for all tools.")

KIND = Qt.UserRole + 2  # a status cell's kind: ok | working | queued | skipped | none | error | ""
# kind: (soft background, text, strong colour, lucide icon)
KINDS = {
    "ok": (theme.SUCCESS_SOFT, theme.SUCCESS_DARK, theme.SUCCESS, "folder"),
    "working": (theme.ACCENT_SOFT, theme.ACCENT_DARK, theme.ACCENT, "wand-2"),
    "queued": (theme.BG, theme.TEXT_2, theme.MUTED, "clock"),
    "skipped": (theme.NEUTRAL_SOFT, theme.TEXT_2, theme.MUTED, "check"),
    "none": (theme.WARNING_SOFT, theme.WARNING_DARK, theme.WARNING, "circle-alert"),
    "error": (theme.ERROR_SOFT, theme.ERROR_DARK, theme.ERROR, "circle-alert"),
}
# the WhatsApp connection (the workers' states) as the header's pill shows it: soft background, text, dot, words
WA_LOOKS = {
    "ready": (theme.SUCCESS_SOFT, theme.SUCCESS_DARK, theme.SUCCESS, "WhatsApp connected"),
    "starting": (theme.ACCENT_SOFT, theme.ACCENT_DARK, theme.ACCENT, "Connecting…"),
    "loading": (theme.ACCENT_SOFT, theme.ACCENT_DARK, theme.ACCENT, "Connecting…"),
    "qr": (theme.WARNING_SOFT, theme.WARNING_DARK, theme.WARNING, "Scan the QR code"),
    "error": (theme.ERROR_SOFT, theme.ERROR_DARK, theme.ERROR, "WhatsApp error"),
}
WA_IDLE = (theme.NEUTRAL_SOFT, theme.TEXT_2, theme.MUTED, "WhatsApp not connected")
AMBER_ON_NAVY, RED_ON_NAVY = "#f2b544", "#f87171"

STYLE = f"""
QWidget#lookPage {{ background: {theme.BG}; }}
QLabel {{ background: transparent; }}
QFrame#sectionChip {{ background: {theme.ACCENT_SOFT}; border-radius: 12px; }}
QLabel#sectionText {{ color: {theme.ACCENT_DARK}; font-size: 11px; font-weight: 700; }}
QLabel#pageTitle {{ color: {theme.NAVY}; font-size: 26px; font-weight: 800; }}
QLabel#pageDesc {{ color: {theme.TEXT_2}; font-size: 13px; }}
QFrame#waPill {{ border-radius: 19px; }}

QFrame#hero {{ background: {theme.NAVY}; border-radius: 22px; }}
QProgressBar#ring {{ background: transparent; border: none; min-width: 78px; max-width: 78px;
                    min-height: 78px; max-height: 78px; }}
QLabel#heroKicker {{ font-size: 11px; font-weight: 800; }}
QLabel#heroTitle {{ color: #ffffff; font-size: 18px; font-weight: 700; }}
QLabel#heroSub {{ color: {theme.ON_NAVY}; font-size: 12.5px; }}
QLabel#heroHint {{ color: {theme.ON_NAVY}; background: rgba(255, 255, 255, 18); border-radius: 12px;
                  padding: 9px 12px; font-size: 12.5px; }}
QPushButton#heroStart {{ background: {theme.SKY}; color: {theme.NAVY}; border: none; border-radius: 12px;
                        padding: 0 20px; min-height: 42px; font-weight: 800; }}
QPushButton#heroStart:hover {{ background: #4cb3ff; color: {theme.NAVY}; }}
QPushButton#heroStart:disabled {{ background: #1d4f86; color: #6f86ab; }}
QPushButton#heroGhost {{ background: rgba(255, 255, 255, 26); color: #ffffff; border: none; border-radius: 12px;
                        padding: 0 16px; min-height: 42px; font-weight: 700; }}
QPushButton#heroGhost:hover {{ background: rgba(255, 255, 255, 46); color: #ffffff; }}
QPushButton#heroGhost:checked {{ background: #ffffff; color: {theme.NAVY}; }}
QPushButton#heroGhost:disabled {{ background: rgba(255, 255, 255, 12); color: #6f7f9e; }}
QPushButton#heroOutline {{ background: transparent; color: #ffffff; border: 1px solid rgba(255, 255, 255, 72);
                          border-radius: 12px; padding: 0 16px; min-height: 40px; font-weight: 700; }}
QPushButton#heroOutline:hover {{ background: rgba(255, 255, 255, 20); color: #ffffff; border-color: rgba(255, 255, 255, 110); }}
QPushButton#heroOutline:disabled {{ color: #6f7f9e; border-color: rgba(255, 255, 255, 36); }}
QPushButton#heroIcon {{ background: rgba(255, 255, 255, 26); border: none; border-radius: 12px; padding: 0;
                       min-width: 42px; max-width: 42px; min-height: 42px; }}
QPushButton#heroIcon:hover {{ background: rgba(255, 255, 255, 46); }}
QPushButton#heroIcon:disabled {{ background: rgba(255, 255, 255, 12); }}

QFrame#card {{ background: {theme.SURFACE}; border: 1px solid {LINE}; border-radius: 20px; }}
QLabel#cardTitle {{ color: {theme.NAVY}; font-size: 15.5px; font-weight: 700; }}
QLabel#cardSub {{ color: {theme.TEXT_2}; font-size: 12.5px; }}
QLabel#field {{ color: {theme.TEXT_2}; font-size: 12px; font-weight: 700; }}
QLabel#hint {{ color: {theme.TEXT_2}; font-size: 12px; }}
QLabel#warn {{ color: {theme.WARNING_DARK}; font-size: 12px; }}
QFrame#rule {{ background: #eef1f6; border: none; }}

QFrame#segment, QFrame#segmentBig {{ background: {TRACK}; border-radius: 11px; }}
QFrame#segmentBig {{ background: #e9eef6; border-radius: 14px; }}
QFrame#segment QPushButton, QFrame#segmentBig QPushButton {{ background: transparent; color: {theme.TEXT_2};
    border: none; border-radius: 8px; padding: 0 12px; min-height: 26px; font-weight: 700; }}
QFrame#segmentBig QPushButton {{ min-height: 40px; border-radius: 11px; font-size: 13.5px; }}
QFrame#segment QPushButton:hover, QFrame#segmentBig QPushButton:hover {{ background: transparent; color: {theme.NAVY}; }}
QFrame#segment QPushButton:checked, QFrame#segmentBig QPushButton:checked {{ background: #ffffff; color: {theme.NAVY}; }}

QPushButton#soft {{ background: {theme.ACCENT_SOFT}; color: {theme.ACCENT_DARK}; border: none; border-radius: 10px;
                   padding: 0 12px; min-height: 34px; font-weight: 700; }}
QPushButton#soft:hover {{ background: #d5e7ff; color: {theme.ACCENT_DARK}; }}
QPushButton#soft:checked {{ background: {theme.NAVY}; color: #ffffff; }}
QPushButton#soft:disabled {{ background: #f3f4f8; color: #aab0c0; }}
QPushButton#plain {{ background: transparent; color: {theme.TEXT_2}; border: none; border-radius: 10px;
                    padding: 0 10px; min-height: 34px; font-weight: 600; }}
QPushButton#plain:hover {{ background: {TRACK}; color: {theme.NAVY}; }}
QPushButton#plain:checked {{ background: {theme.ACCENT_SOFT}; color: {theme.ACCENT_DARK}; }}
QPushButton#plain:disabled {{ color: #aab0c0; }}
QPushButton#mini {{ background: #ffffff; color: {theme.NAVY}; border: 1px solid {theme.BORDER}; border-radius: 9px;
                   padding: 0 12px; min-height: 28px; font-weight: 700; }}
QPushButton#mini:hover {{ background: {theme.ACCENT_SOFT}; color: {theme.ACCENT_DARK}; border-color: #9cc5fb; }}
QPushButton#mini:disabled {{ color: #aab0c0; }}
QPushButton#iconBtn {{ background: transparent; border: none; border-radius: 8px; padding: 0;
                      min-width: 30px; max-width: 30px; min-height: 30px; }}
QPushButton#iconBtn:hover {{ background: {TRACK}; }}
QPushButton#outline {{ background: #ffffff; color: {theme.NAVY}; border: 1px solid #d5dde9; border-radius: 12px;
                      padding: 0 16px; min-height: 36px; font-weight: 700; }}
QPushButton#outline:hover {{ background: {theme.ACCENT_SOFT}; color: {theme.ACCENT_DARK}; border-color: #9cc5fb; }}
QPushButton#outline:checked {{ background: {theme.NAVY}; color: #ffffff; border-color: {theme.NAVY}; }}
QPushButton#outline:disabled {{ color: #aab0c0; background: #f3f4f8; }}

QFrame#pathBox {{ background: {theme.SURFACE_ALT}; border: 1px solid {LINE}; border-radius: 12px; }}
QFrame#pathBox QLineEdit {{ background: transparent; border: none; padding: 0; font-size: 13px; }}
QFrame#card QComboBox, QFrame#card QSpinBox, QFrame#card QDateEdit, QFrame#card QTimeEdit {{
    background: {theme.SURFACE_ALT}; border: 1px solid #d5dde9; border-radius: 12px; min-height: 28px; }}
QFrame#card QSpinBox, QFrame#card QDateEdit, QFrame#card QTimeEdit {{ padding: 3px 24px 3px 10px; }}
QFrame#card QComboBox:hover, QFrame#card QSpinBox:hover, QFrame#card QDateEdit:hover,
QFrame#card QTimeEdit:hover {{ border-color: #9cc5fb; }}
QFrame#card QDateEdit::drop-down {{ subcontrol-origin: border; subcontrol-position: center right; width: 26px;
    border: none; }}
QFrame#card QDateEdit::down-arrow {{ image: url(ui:chevron-down.svg); width: 12px; height: 12px; }}
QFrame#card QTimeEdit::up-button, QFrame#card QTimeEdit::down-button {{ subcontrol-origin: border; width: 22px;
    border: none; background: transparent; }}
QFrame#card QTimeEdit::up-button {{ subcontrol-position: top right; }}
QFrame#card QTimeEdit::down-button {{ subcontrol-position: bottom right; }}
QFrame#card QTimeEdit::up-arrow {{ image: url(ui:chevron-up.svg); width: 10px; height: 10px; }}
QFrame#card QTimeEdit::down-arrow {{ image: url(ui:chevron-down.svg); width: 10px; height: 10px; }}
QFrame#card QLineEdit#field {{ background: {theme.SURFACE_ALT}; border: 1px solid #d5dde9; border-radius: 12px;
    padding: 6px 10px; }}
QFrame#card QListWidget {{ background: {theme.SURFACE_ALT}; border: 1px solid {LINE}; border-radius: 12px; padding: 4px; }}
QFrame#names {{ background: {theme.SURFACE_ALT}; border: 1px solid {LINE}; border-radius: 14px; }}
QLabel#mono {{ color: {theme.NAVY}; font-family: Consolas, 'Cascadia Mono', monospace; font-size: 12.5px; }}
QLabel#monoSub {{ color: {theme.TEXT_2}; font-family: Consolas, 'Cascadia Mono', monospace; font-size: 11.5px; }}

QPlainTextEdit#codes {{ background: {theme.SURFACE_ALT}; border: 1px solid #d5dde9; border-radius: 12px;
    padding: 8px 10px; font-family: Consolas, 'Cascadia Mono', monospace; font-size: 12.5px; }}
QPlainTextEdit#codes:focus {{ border-color: {theme.ACCENT}; }}
QFrame#chip {{ background: #ffffff; border: 1px solid {theme.BORDER}; border-radius: 15px; }}
QLabel#chipCode {{ background: {theme.NAVY}; color: #ffffff; border-radius: 11px; padding: 2px 7px;
                  font-family: Consolas, 'Cascadia Mono', monospace; font-size: 11.5px; font-weight: 700; }}
QLabel#chipName {{ color: {theme.NAVY}; font-size: 12.5px; }}
QLabel#chipDate, QLabel#chipCount {{ color: {theme.TEXT_2}; font-size: 11.5px; }}
QLabel#chipGroup {{ color: {theme.TEXT_2}; font-size: 11px; font-weight: 700; }}
QFrame#chipRule {{ background: {LINE}; border: none; }}

QPushButton#disclosure, QPushButton#logHead {{ background: transparent; border: none; padding: 0;
    min-height: 40px; max-height: 40px; }}
QPushButton#disclosure:hover, QPushButton#logHead:hover {{ background: transparent; }}
QLabel#discTitle {{ color: {theme.NAVY}; font-size: 13.5px; font-weight: 600; }}
QLabel#discSummary {{ color: {theme.TEXT_2}; font-size: 12px; }}

QTableWidget#rows {{ background: {theme.SURFACE}; border: none; border-radius: 0; }}
QTableWidget#rows QHeaderView::section {{ background: {theme.SURFACE}; color: {theme.TEXT_2}; border: none;
    border-bottom: 1px solid #eef1f6; padding: 6px 10px; font-size: 11.5px; font-weight: 700; }}
QPlainTextEdit#log, QTextEdit#log {{ background: {theme.SURFACE_ALT}; border: none; border-radius: 12px; padding: 6px 10px;
    color: {theme.NAVY}; font-family: Consolas, 'Cascadia Mono', monospace; font-size: 12px; }}
QTextEdit#log {{ font-family: 'Segoe UI', 'Nirmala UI'; font-size: 13px; }}
"""


SCALE = 1.0  # font scale of the page's width: smaller text in a smaller window (ToolLayout.set_mode)
SCALES = {"wide": 0.9, "medium": 0.84, "narrow": 0.78}


def fnt(px: float, weight: int = 400, mono: bool = False):
    return W.font(px * SCALE, weight, mono)


def style(scale: float) -> str:
    """STYLE with every font size multiplied by ``scale``, and the plain controls' size set."""
    body = re.sub(r"font-size: ([\d.]+)px", lambda m: f"font-size: {round(float(m[1]) * scale, 1)}px", STYLE)
    plain = ("QLabel, QComboBox, QLineEdit, QSpinBox, QDateEdit, QTimeEdit, QCheckBox, QPlainTextEdit, QTextEdit, "
             f"QListWidget, QTableWidget {{ font-size: {round(13 * scale, 1)}px; }}")
    return plain + "\n" + body


def pixmap(icon: str, color: str, size: int):
    return W.icon_pixmap(icon, color, size, max(2.0, W.dpr()))


def plain_count(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def field(text: str) -> QLabel:
    """A field's name above it."""
    return QLabel(text, objectName="field")


def hint(text: str) -> QLabel:
    return QLabel(text, objectName="hint", wordWrap=True)


def button(text: str, name: str = "soft", icon: str | None = None, icon_color: str = theme.ACCENT_DARK,
           tip: str = "") -> QPushButton:
    """A button of the look: soft | plain | mini | outline | iconBtn, or heroStart | heroGhost |
    heroOutline | heroIcon on the control card."""
    b = QPushButton(text, objectName=name)
    b.setCursor(Qt.PointingHandCursor)
    if icon:
        b.setIcon(QIcon(pixmap(icon, icon_color, 16)))
    if tip:
        b.setToolTip(tip)
    return b


def column(*items, spacing: int = 7) -> QVBoxLayout:
    lay = QVBoxLayout()
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(spacing)
    for w in items:
        if isinstance(w, QWidget):
            lay.addWidget(w)
        else:
            lay.addLayout(w)
    return lay


def row(*items, spacing: int = 8, stretch_at: int | None = None) -> QHBoxLayout:
    lay = QHBoxLayout()
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(spacing)
    for i, w in enumerate(items):
        if w is None:
            lay.addStretch(1)
        elif isinstance(w, QWidget):
            lay.addWidget(w, 1 if i == stretch_at else 0)
        else:
            lay.addLayout(w, 1 if i == stretch_at else 0)
    return lay


def path_box(edit: QWidget, *buttons: QPushButton, icon: str = "folder") -> QFrame:
    """A file or folder as a soft line: its icon, the path (an editable QLineEdit) and buttons."""
    box = QFrame(objectName="pathBox")
    lay = QHBoxLayout(box)
    lay.setContentsMargins(12, 5, 5, 5)
    lay.setSpacing(8)
    ic = QLabel()
    ic.setPixmap(pixmap(icon, theme.ACCENT, 16))
    lay.addWidget(ic)
    lay.addWidget(edit, 1)
    for b in buttons:
        lay.addWidget(b)
    return box


def rule() -> QFrame:
    line = QFrame(objectName="rule")
    line.setFixedHeight(1)
    return line


def wa_kicker(state: str, running: bool) -> tuple[str, str]:
    """The control card's top line for a WhatsApp tool, and its colour on navy."""
    if running:
        return "WORKING", theme.LIVE
    return {"ready": ("CONNECTED", theme.SKY), "starting": ("CONNECTING", theme.SKY),
            "loading": ("CONNECTING", theme.SKY), "qr": ("SCAN THE QR CODE", AMBER_ON_NAVY),
            "error": ("WHATSAPP ERROR", RED_ON_NAVY)}.get(state, ("NOT CONNECTED", theme.ON_NAVY))


# ---- small widgets --------------------------------------------------------------------------
class IconSquare(QLabel):
    """A lucide icon on a small rounded square of a soft colour."""

    def __init__(self, icon: str, bg: str, fg: str, size: int = 36, icon_size: int = 18, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet(f"background: {bg}; border-radius: {round(size * .3)}px;")
        self.setPixmap(pixmap(icon, fg, icon_size))


class Card(QFrame):
    """A white rounded card whose head is an icon square, a title, a line under it and, on the
    right, the card's own controls (``head_right``). ``lay`` holds the card's contents."""

    def __init__(self, icon: str, bg: str, fg: str, title: str, subtitle: QLabel | str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(18, 18, 18, 18)
        self.lay.setSpacing(14)
        head = QHBoxLayout()
        head.setSpacing(12)
        head.addWidget(IconSquare(icon, bg, fg))
        names = QVBoxLayout()
        names.setSpacing(0)
        self.title = QLabel(title, objectName="cardTitle")
        names.addWidget(self.title)
        self.subtitle = subtitle if isinstance(subtitle, QLabel) else QLabel(subtitle)
        self.subtitle.setObjectName("cardSub")
        self.subtitle.setWordWrap(True)
        names.addWidget(self.subtitle)
        if not subtitle:  # a QLabel given is filled in later
            self.subtitle.hide()
        head.addLayout(names, 1)
        self.head_right = QHBoxLayout()
        self.head_right.setSpacing(6)
        head.addLayout(self.head_right)
        self.lay.addLayout(head)

    def set_subtitle(self, text: str):
        self.subtitle.setText(text)
        self.subtitle.setVisible(bool(text))


class Segmented(QFrame):
    """Two or three buttons in a grey track; the chosen one is white. ``changed`` sends its key."""
    changed = Signal(str)

    def __init__(self, options: list[tuple[str, str]], big: bool = False, parent=None):
        super().__init__(parent)
        self.setObjectName("segmentBig" if big else "segment")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(2)
        self.group = QButtonGroup(self)
        self.buttons: dict[str, QPushButton] = {}
        for key, text in options:
            b = QPushButton(text, checkable=True)
            b.setCursor(Qt.PointingHandCursor)
            if big:
                b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self.group.addButton(b)
            self.buttons[key] = b
            lay.addWidget(b)
            b.clicked.connect(lambda _=False, k=key: self.changed.emit(k))
        self.set(options[0][0])

    def set(self, key: str):
        self.buttons[key].setChecked(True)

    def key(self) -> str:
        return next(k for k, b in self.buttons.items() if b.isChecked())

    def set_label(self, key: str, text: str):
        self.buttons[key].setText(text)


class RingProgress(QProgressBar):
    """The control card's progress: a sky-blue ring with "4/6" and the unit ("codes") inside. It is
    a QProgressBar, so the pages drive it with setRange / setValue; an empty range (Qt's "busy")
    shows a quarter of the ring and "…"."""

    def __init__(self, unit: str = "codes", size: int = 78, parent=None):
        super().__init__(parent)
        self.setObjectName("ring")  # STYLE keeps it round: the theme's progress bars set a min-height
        self.unit = unit
        self.setFixedSize(size, size)
        self.setRange(0, 1)
        self.setValue(0)
        self.setTextVisible(False)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pen_w = 8
        r = QRectF(self.rect()).adjusted(pen_w / 2 + 1, pen_w / 2 + 1, -pen_w / 2 - 1, -pen_w / 2 - 1)
        p.setPen(QPen(QColor(255, 255, 255, 30), pen_w))
        p.drawEllipse(r)
        lo, hi, value = self.minimum(), self.maximum(), self.value()
        busy = hi <= lo
        share = .25 if busy else max(0.0, min(1.0, (value - lo) / (hi - lo)))
        if share > 0:
            pen = QPen(QColor(theme.SKY), pen_w)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.drawArc(r, 90 * 16, -round(share * 360 * 16))
        p.setPen(QColor("#ffffff"))
        text = "…" if busy else "0" if hi == 1 and value <= lo else f"{max(value, lo)}/{hi}"  # (0, 1): nothing yet
        p.setFont(fnt(18 if len(text) <= 5 else 14, 800))
        p.drawText(QRectF(0, self.height() / 2 - 16, self.width(), 22), Qt.AlignCenter, text)
        p.setPen(QColor(theme.ON_NAVY))
        p.setFont(fnt(10))
        p.drawText(QRectF(0, self.height() / 2 + 5, self.width(), 14), Qt.AlignCenter, self.unit)
        p.end()


class CountTile(QWidget):
    """One count of the control card: a big number over a coloured dot and its name."""

    def __init__(self, label: str, color: str, parent=None):
        super().__init__(parent)
        self.label, self.color, self.value = label, color, 0
        self.setMinimumSize(86, 62)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_value(self, value: int):
        if value != self.value:
            self.value = value
            self.update()

    def sizeHint(self):
        return QSize(max(96, QFontMetrics(fnt(12)).horizontalAdvance(self.label) + 44), 62)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 18))
        p.drawRoundedRect(QRectF(self.rect()), 14, 14)
        p.setPen(QColor("#ffffff"))
        p.setFont(fnt(22, 800))
        p.drawText(QRectF(14, 6, self.width() - 20, 30), Qt.AlignLeft | Qt.AlignVCenter, str(self.value))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(self.color))
        p.drawEllipse(QRectF(14, 43, 7, 7))
        p.setPen(QColor(theme.ON_NAVY))
        p.setFont(fnt(12))
        p.drawText(QRectF(27, 37, self.width() - 32, 18), Qt.AlignLeft | Qt.AlignVCenter,
                   p.fontMetrics().elidedText(self.label, Qt.ElideRight, self.width() - 32))
        p.end()


class OptionCard(QAbstractButton):
    """A checkable card with a big label and a small line under it (4× / Recommended)."""

    def __init__(self, label: str, sub: str, big: int = 19, height: int = 62, parent=None):
        super().__init__(parent)
        self.label, self.sub, self.big, self.h = label, sub, big, height
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def sizeHint(self):
        fm, big = QFontMetrics(fnt(11.5)), QFontMetrics(fnt(self.big, 800))
        return QSize(max(84, fm.horizontalAdvance(self.sub) + 28, big.horizontalAdvance(self.label) + 28), self.h)

    def minimumSizeHint(self):
        return QSize(64, self.h)

    def enterEvent(self, e):
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        on = self.isChecked()
        if not self.isEnabled():
            p.setOpacity(.5)
        border = theme.ACCENT if on else theme.BORDER_STRONG if self.underMouse() else theme.BORDER
        p.setPen(QPen(QColor(border), 1.5))
        p.setBrush(QColor(theme.ACCENT_SOFT if on else theme.SURFACE))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(.75, .75, -.75, -.75), 14, 14)
        p.setPen(QColor(theme.ACCENT_DARK if on else theme.NAVY))
        p.setFont(fnt(self.big, 800))
        top = QRectF(12, 4, self.width() - 20, self.h * 0.5) if self.sub else QRectF(12, 0, self.width() - 20, self.height())
        p.drawText(top, Qt.AlignLeft | Qt.AlignVCenter,
                   p.fontMetrics().elidedText(self.label, Qt.ElideRight, self.width() - 20))
        if self.sub:
            p.setPen(QColor(theme.TEXT_2))
            p.setFont(fnt(11.5))
            p.drawText(QRectF(12, self.h * 0.55, self.width() - 20, 18), Qt.AlignLeft | Qt.AlignVCenter,
                       p.fontMetrics().elidedText(self.sub, Qt.ElideRight, self.width() - 20))
        p.end()


class ToggleRow(QCheckBox):
    """A whole-width check box drawn as an icon square, a title with a line under it, and a switch.
    It stays a QCheckBox, so the page reads and saves it like any other."""

    def __init__(self, icon: str, bg: str, fg: str, title: str, sub: str = "", parent=None):
        super().__init__(title, parent)
        self.icon, self.bg, self.fg, self.title, self.sub = icon, bg, fg, title, sub
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(52 if sub else 44)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def sizeHint(self):
        return QSize(260, self.height())

    def minimumSizeHint(self):
        return QSize(180, self.height())

    def hitButton(self, pos):
        return self.rect().contains(pos)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        if not self.isEnabled():
            p.setOpacity(.5)
        square = QRectF(0, (self.height() - 32) / 2, 32, 32)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(self.bg))
        p.drawRoundedRect(square, 10, 10)
        p.drawPixmap(QPointF(square.center().x() - 8, square.center().y() - 8), pixmap(self.icon, self.fg, 16))
        sw = QRectF(self.width() - 42, (self.height() - 24) / 2, 42, 24)
        text_w = sw.left() - 56
        p.setPen(QColor(theme.NAVY))
        p.setFont(fnt(13.5, 600))
        title_box = QRectF(44, 7, text_w, 20) if self.sub else QRectF(44, 0, text_w, self.height())
        p.drawText(title_box, Qt.AlignLeft | Qt.AlignVCenter,
                   p.fontMetrics().elidedText(self.title, Qt.ElideRight, int(text_w)))
        if self.sub:
            p.setPen(QColor(theme.TEXT_2))
            p.setFont(fnt(12))
            p.drawText(QRectF(44, 26, text_w, 18), Qt.AlignLeft | Qt.AlignVCenter,
                       p.fontMetrics().elidedText(self.sub, Qt.ElideRight, int(text_w)))
        on = self.isChecked()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.ACCENT if on else theme.BORDER_STRONG))
        p.drawRoundedRect(sw, 12, 12)
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(QRectF(sw.right() - 21 if on else sw.left() + 3, sw.top() + 3, 18, 18))
        if self.hasFocus():
            p.setPen(QPen(QColor(theme.ACCENT), 1.5))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(sw.adjusted(-2, -2, 2, 2), 14, 14)
        p.end()


class FilterPill(QAbstractButton):
    """A result filter: "Saved  2". The chosen one is navy with its count on sky blue."""

    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        self.label, self.count = label, 0
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(32)

    def set_count(self, count: int):
        self.count = count
        self.updateGeometry()
        self.update()

    def sizeHint(self):
        fm = QFontMetrics(fnt(12.5, 700))
        cw = max(20, QFontMetrics(fnt(11, 700)).horizontalAdvance(str(self.count)) + 12)
        return QSize(12 + fm.horizontalAdvance(self.label) + 6 + cw + 6, self.height())

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        on = self.isChecked()
        r = QRectF(self.rect())
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.NAVY if on else TRACK))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        p.setFont(fnt(12.5, 700))
        p.setPen(QColor("#ffffff" if on else theme.TEXT_2))
        tw = p.fontMetrics().horizontalAdvance(self.label)
        p.drawText(QRectF(12, 0, tw + 2, r.height()), Qt.AlignLeft | Qt.AlignVCenter, self.label)
        p.setFont(fnt(11, 700))
        cw = max(20, p.fontMetrics().horizontalAdvance(str(self.count)) + 12)
        bubble = QRectF(r.right() - 6 - cw, (r.height() - 20) / 2, cw, 20)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.SKY if on else "#ffffff"))
        p.drawRoundedRect(bubble, 10, 10)
        p.setPen(QColor(theme.NAVY))
        p.drawText(bubble, Qt.AlignCenter, str(self.count))
        p.end()


class FilterPills(QWidget):
    """A row of FilterPills (wrapping), one of them chosen; ``changed`` sends its key."""
    changed = Signal(str)

    def __init__(self, filters: list[tuple[str, str]], parent=None):
        super().__init__(parent)
        flow = FlowLayout(self, 6, 6)
        self.group = QButtonGroup(self)
        self.pills: dict[str, FilterPill] = {}
        for key, label in filters:
            pill = FilterPill(label)
            pill.clicked.connect(lambda _=False, k=key: self.changed.emit(k))
            self.group.addButton(pill)
            flow.addWidget(pill)
            self.pills[key] = pill
        self.pills[filters[0][0]].setChecked(True)

    def set_count(self, key: str, count: int):
        self.pills[key].set_count(count)


class DisclosureButton(QPushButton):
    """ "More options", a short summary of what is folded away, and a chevron that turns."""

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.setObjectName("disclosure")
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        title = QLabel(text, objectName="discTitle")
        self.summary = QLabel(objectName="discSummary")
        self.chevron = QLabel()
        for w in (title, self.summary, self.chevron):
            w.setAttribute(Qt.WA_TransparentForMouseEvents)
        lay.addWidget(title)
        lay.addStretch(1)
        lay.addWidget(self.summary)
        lay.addWidget(self.chevron)
        self.toggled.connect(self._turn)
        self._turn(False)

    def _turn(self, open_: bool):
        self.chevron.setPixmap(pixmap("chevron-up" if open_ else "chevron-down", theme.TEXT_2, 16))
        self.summary.setVisible(not open_)


def code_group(code: str) -> str:
    """The list a code belongs to: "2-" for 2-558, "" for codes without a prefix."""
    parts = codes.reader().code_parts(code)
    prefix = parts.get("prefix", "") if parts else ""
    return f"{prefix.lower()}-" if prefix else ""


class CodeChips(QWidget):
    """The code list as chips, list by list: "2- LIST · 4 codes", then 2-221 Oppo a6 pro 5g 21/10 …"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(12)
        self.groups = 0

    def set_items(self, items):
        W.clear_layout(self.lay)
        groups: dict[str, list] = {}
        for it in items:
            groups.setdefault(code_group(it.code), []).append(it)
        self.groups = len(groups)
        for group, members in groups.items():
            head = QHBoxLayout()
            head.setSpacing(8)
            head.addWidget(QLabel(f"{group} LIST" if group else "ORDERS", objectName="chipGroup"))
            line = QFrame(objectName="chipRule")
            line.setFixedHeight(1)
            head.addWidget(line, 1)
            head.addWidget(QLabel(plain_count(len(members), "code"), objectName="chipCount"))
            self.lay.addLayout(head)
            box = QWidget()
            flow = FlowLayout(box, 6, 6)
            for it in members:
                flow.addWidget(self._chip(it))
            self.lay.addWidget(box)

    @staticmethod
    def _chip(it) -> QFrame:
        chip = QFrame(objectName="chip")
        chip.setFixedHeight(30)
        lay = QHBoxLayout(chip)
        lay.setContentsMargins(4, 0, 10, 0)
        lay.setSpacing(7)
        lay.addWidget(QLabel(it.code, objectName="chipCode"))
        if it.name:
            lay.addWidget(QLabel(it.name, objectName="chipName"))
        if it.date:
            lay.addWidget(QLabel(it.date, objectName="chipDate"))
        chip.setToolTip(" ".join(x for x in (it.code, it.name, it.date) if x))
        return chip


class RowsDelegate(QStyledItemDelegate):
    """Paints a results table as the rows of a list. ``roles`` gives each column's look:
      "code"    the row's status square, then the text as a code chip ("chip": the chip alone)
      "name"    bold
      "pill"    in a grey pill
      "status"  a dot and the text in the colour of the row's kind, two lines at most
      "path"    a folder icon and the text, muted, cut in the middle
      "muted"   grey;  "big": large and centred (emojis);  anything else: plain
    The row's kind (KINDS) is the KIND data of column ``kind_col``; an item's own foreground colour
    (setForeground) wins over the role's."""

    def __init__(self, table, roles: dict[int, str], kind_col: int, height: int = 58, parent=None):
        super().__init__(parent)
        self.table, self.roles, self.kind_col, self.height = table, roles, kind_col, height

    def paint(self, p: QPainter, option, index):
        p.save()
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        r = QRectF(option.rect)
        selected = bool(option.state & QStyle.State_Selected)
        p.fillRect(r, QColor(theme.ACCENT_SOFT if selected else theme.SURFACE))
        p.setPen(QPen(QColor("#eef1f6"), 1))
        p.drawLine(r.bottomLeft(), r.bottomRight())
        row, col = index.row(), index.column()
        kind_item = self.table.item(row, self.kind_col)
        kind = kind_item.data(KIND) if kind_item else ""
        bg, fg, strong, icon = KINDS.get(kind or "", (theme.BG, theme.TEXT_2, theme.BORDER_STRONG, ""))
        text = index.data() or ""
        own = index.data(Qt.ForegroundRole)
        role = self.roles.get(col, "")
        if role in ("code", "chip"):
            left = r.left() + 8
            if role == "code":
                square = QRectF(r.left() + 10, r.center().y() - 18, 36, 36)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(bg))
                p.drawRoundedRect(square, 11, 11)
                if icon:
                    p.drawPixmap(QPointF(square.center().x() - 9, square.center().y() - 9), pixmap(icon, strong, 18))
                left = square.right() + 10
            if text:
                p.setFont(fnt(12, 700, mono=True))
                w = p.fontMetrics().horizontalAdvance(text) + 12
                chip = QRectF(left, r.center().y() - 11, max(0.0, min(w, r.right() - left - 4)), 22)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(TRACK))
                p.drawRoundedRect(chip, 6, 6)
                p.setPen(QColor(theme.ACCENT_DARK))
                p.drawText(chip, Qt.AlignCenter, p.fontMetrics().elidedText(text, Qt.ElideRight, int(chip.width()) - 8))
        elif role == "pill" and text:
            p.setFont(fnt(12, 600))
            w = p.fontMetrics().horizontalAdvance(text) + 18
            pill = QRectF(r.left() + 6, r.center().y() - 12, min(w, r.width() - 12), 24)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(TRACK))
            p.drawRoundedRect(pill, 12, 12)
            p.setPen(QColor(theme.TEXT_2))
            p.drawText(pill, Qt.AlignCenter, p.fontMetrics().elidedText(text, Qt.ElideRight, int(pill.width()) - 10))
        elif role == "status" and text:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(strong))
            p.drawEllipse(QRectF(r.left() + 8, r.center().y() - 3.5, 7, 7))
            p.setPen(own.color() if own is not None else QColor(fg))
            p.setFont(fnt(12.5, 600))
            box = r.adjusted(22, 4, -8, -4)
            if p.fontMetrics().horizontalAdvance(text) > box.width():
                p.drawText(box, Qt.AlignLeft | Qt.AlignVCenter | Qt.TextWordWrap, text)
            else:
                p.drawText(box, Qt.AlignLeft | Qt.AlignVCenter, text)
        elif role == "path" and text:
            p.drawPixmap(QPointF(r.left() + 8, r.center().y() - 7), pixmap("folder", theme.MUTED, 14))
            p.setPen(QColor(theme.TEXT_2))
            p.setFont(fnt(12.5))
            box = r.adjusted(28, 0, -8, 0)
            p.drawText(box, Qt.AlignLeft | Qt.AlignVCenter, p.fontMetrics().elidedText(text, Qt.ElideMiddle, int(box.width())))
        elif text:
            weights = {"name": (13.5, 600, theme.NAVY), "muted": (12.5, 400, theme.TEXT_2), "big": (17, 400, theme.NAVY)}
            px, weight, color = weights.get(role, (13, 400, theme.NAVY))
            p.setFont(fnt(px, weight))
            p.setPen(own.color() if own is not None else QColor(color))
            box = r.adjusted(8, 0, -8, 0)
            align = Qt.AlignCenter if role == "big" else Qt.AlignLeft | Qt.AlignVCenter
            p.drawText(box, align, p.fontMetrics().elidedText(text, Qt.ElideRight, int(box.width())))
        p.restore()

    def sizeHint(self, option, index):
        return QSize(super().sizeHint(option, index).width(), self.height)


def rows_table(table, roles: dict[int, str], kind_col: int, height: int = 58, painter: bool = True):
    """Gives a QTableWidget the look of a list of rows: RowsDelegate paints them, or with ``painter``
    False (a table with tick boxes or widgets in its cells) only the frame, grid and heights change."""
    table.setObjectName("rows")
    table.setShowGrid(False)
    table.setFrameShape(QFrame.NoFrame)
    table.setWordWrap(True)
    if painter:
        table.setItemDelegate(RowsDelegate(table, roles, kind_col, height, table))
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(height)
    hdr = table.horizontalHeader()
    hdr.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    hdr.setHighlightSections(False)
    return table


# ---- the page's parts ------------------------------------------------------------------------
class WaPill(QFrame):
    """The WhatsApp connection as a pill: a dot and a few words in the state's colour."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("waPill")
        self.setFixedHeight(38)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 0, 16, 0)
        lay.setSpacing(8)
        self.dot = QLabel()
        self.dot.setFixedSize(8, 8)
        self.text = QLabel()
        lay.addWidget(self.dot)
        lay.addWidget(self.text)
        self.set_state("idle", "Not connected")

    def set_state(self, state: str, message: str = ""):
        self.set_look(*WA_LOOKS.get(state, WA_IDLE), tip=message)

    def set_look(self, bg: str, fg: str, dot: str, words: str, tip: str = ""):
        self.setStyleSheet(f"QFrame#waPill {{ background: {bg}; }}")
        self.setToolTip(tip)
        self.dot.setStyleSheet(f"background: {dot}; border-radius: 4px;")
        self.text.setText(words)
        self.text.setStyleSheet(f"color: {fg}; font-size: 13px; font-weight: 700;")


class Header(QWidget):
    """The page's top: the tool's section as a chip, its title and description; ``right`` (the
    WhatsApp pill and button) beside it, or under it on a narrow page."""

    def __init__(self, tool_key: str, right: list[QWidget], parent=None):
        super().__init__(parent)
        from tools import TOOLS  # the sidebar's titles and descriptions

        tool = next(t for t in TOOLS if t.key == tool_key)
        chip = QFrame(objectName="sectionChip")
        cl = QHBoxLayout(chip)
        cl.setContentsMargins(8, 3, 10, 3)
        cl.setSpacing(6)
        icon = QLabel()
        icon.setPixmap(theme.icon_pixmap(tool.icon, theme.ACCENT_DARK, 13, max(2.0, W.dpr())))
        cl.addWidget(icon)
        cl.addWidget(QLabel(tool.section.upper(), objectName="sectionText"))
        title = QLabel(tool.title, objectName="pageTitle")
        f = title.font()
        f.setLetterSpacing(QFont.AbsoluteSpacing, -0.5)
        title.setFont(f)
        names = column(chip, title, QLabel(tool.description, objectName="pageDesc", wordWrap=True), spacing=4)
        names.setAlignment(chip, Qt.AlignLeft)
        side = QWidget()
        sl = QHBoxLayout(side)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(10)
        for w in right:
            sl.addWidget(w)
        self.lay = QBoxLayout(QBoxLayout.LeftToRight, self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(10)
        self.lay.addLayout(names, 1)
        self.lay.addWidget(side, 0, Qt.AlignBottom | Qt.AlignLeft)

    def set_narrow(self, narrow: bool):
        self.lay.setDirection(QBoxLayout.TopToBottom if narrow else QBoxLayout.LeftToRight)


class Hero(QFrame):
    """The navy control card: a progress ring (``progress``), a coloured top line, a title and a
    line under it, the counts (``counts``) and the buttons (``add``). It takes the first arrangement
    its width fits, with INFO_MIN for the progress and the buttons on two lines at most: all in one
    row; the buttons beside the progress (the counts under it when ``counts_always``); stacked."""

    def __init__(self, unit: str = "codes", counts: list[tuple[str, str, str]] = (), counts_always: bool = False,
                 hint_text: str = WHATSAPP_HINT, parent=None):
        super().__init__(parent)
        self.setObjectName("hero")
        self.counts_always = counts_always
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(22, 20, 22, 20)
        self.grid.setHorizontalSpacing(24)
        self.grid.setVerticalSpacing(16)

        self.progress = RingProgress(unit)
        self.kicker = QLabel(objectName="heroKicker")
        f = self.kicker.font()
        f.setLetterSpacing(QFont.AbsoluteSpacing, 1)
        self.kicker.setFont(f)
        self.title = QLabel(objectName="heroTitle", wordWrap=True)
        self.sub = QLabel(objectName="heroSub", wordWrap=True)
        # the parts have the card as parent from the start: place() shows and hides them
        self.info = QWidget(self)
        il = QHBoxLayout(self.info)
        il.setContentsMargins(0, 0, 0, 0)
        il.setSpacing(16)
        il.addWidget(self.progress)
        il.addLayout(column(self.kicker, self.title, self.sub, spacing=3), 1)

        self.counts = {key: CountTile(label, color) for key, label, color in counts}
        self.count_box = QWidget(self)
        cl = QHBoxLayout(self.count_box)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(8)
        for tile in self.counts.values():
            cl.addWidget(tile)

        self.button_box = QWidget(self)
        self.buttons = FlowLayout(self.button_box, 8, 8)
        self.hint = QLabel(hint_text, self, objectName="heroHint", wordWrap=True)
        self.hint.setVisible(bool(hint_text))
        self.grid.setColumnStretch(0, 1)
        self._layout = None
        self.place()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.place()

    def show_hint(self, shown: bool):
        self.hint.setVisible(shown and bool(self.hint.text()))
        self.place()

    def add(self, *buttons: QPushButton):
        for b in buttons:
            self.buttons.addWidget(b)

    def set_status(self, kicker: str, color: str, title: str, sub: str = ""):
        self.kicker.setText(kicker)
        self.kicker.setStyleSheet(f"color: {color};")
        self.title.setText(title)
        self.sub.setText(sub)
        self.sub.setVisible(bool(sub))
        self.place()  # a longer text can take more lines

    def place(self):
        """Arranges the card for its width (on each resize, and after the page's text size changed) and
        asks for the height that needs, so a short window scrolls the page instead of cutting off buttons."""
        g = self.grid
        m = g.contentsMargins()
        width, gap = self.width() - m.left() - m.right(), g.horizontalSpacing()
        two_lines = 2 * self.buttons.sizeHint().height() + 8

        def fits(cell):  # wider than the buttons' least width, or the card could never get narrow enough to
            # change arrangement (the page scrolls sideways instead)
            return cell > self.buttons.minimumSize().width() and self.buttons.heightForWidth(cell) <= two_lines

        # without counts "two" is the row (a row's empty counts column would still cost a gap under the hint)
        counts = self.count_box.sizeHint().width() + gap
        layout = ("row" if self.counts and fits(width - INFO_MIN - gap - counts) else
                  "two" if fits(width - INFO_MIN - gap) else "stack")
        if layout != self._layout:
            self._arrange(layout)
        self.setMinimumHeight(self.heightForWidth(self.width()))

    def _arrange(self, layout: str):
        g = self.grid
        self._layout = layout
        for w in (self.info, self.count_box, self.button_box, self.hint):
            g.removeWidget(w)
        # beside the counts and buttons the status keeps its room (else it wraps a word a line)
        g.setColumnMinimumWidth(0, 0 if layout == "stack" else INFO_MIN)
        right = Qt.AlignRight | Qt.AlignVCenter
        show_counts = bool(self.counts) and (layout == "row" or self.counts_always)
        if layout == "row":
            g.addWidget(self.info, 0, 0)
            g.addWidget(self.count_box, 0, 1)
            g.addWidget(self.button_box, 0, 2, right)
            g.addWidget(self.hint, 1, 0, 1, 3)
        elif layout == "two":
            g.addWidget(self.info, 0, 0)
            g.addWidget(self.button_box, 0, 1, right)
            g.addWidget(self.count_box, 1, 0, 1, 2)
            g.addWidget(self.hint, 2, 0, 1, 2)
        else:
            g.addWidget(self.info, 0, 0)
            g.addWidget(self.count_box, 1, 0)
            g.addWidget(self.button_box, 2, 0)
            g.addWidget(self.hint, 3, 0)
        self.count_box.setVisible(show_counts)


class LogCard(QFrame):
    """A card with the log (a QPlainTextEdit or QTextEdit, given), which folds away. ``grow``: the
    log takes the height that is left instead of a fixed one."""

    def __init__(self, edit: QWidget, height: int = 120, grow: bool = False, title: str = "Log", parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.edit = edit
        edit.setObjectName("log")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 10, 18, 14)
        lay.setSpacing(8)
        self.head = QPushButton(objectName="logHead", checkable=True)
        self.head.setChecked(True)
        self.head.setCursor(Qt.PointingHandCursor)
        hl = QHBoxLayout(self.head)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(12)
        self.count = QLabel(objectName="cardSub")
        self.chevron = QLabel()
        for w in (IconSquare("terminal", theme.NEUTRAL_SOFT, theme.NAVY, 32, 16), QLabel(title, objectName="cardTitle"),
                  self.count):
            w.setAttribute(Qt.WA_TransparentForMouseEvents)
            hl.addWidget(w)
        hl.addStretch(1)
        self.chevron.setAttribute(Qt.WA_TransparentForMouseEvents)
        hl.addWidget(self.chevron)
        if grow:
            edit.setMinimumHeight(height)
        else:
            edit.setFixedHeight(height)
        self.head.toggled.connect(self._show)
        lay.addWidget(self.head)
        lay.addWidget(edit, 1)
        self.grow = grow
        self._show(True)

    def _show(self, shown: bool):
        self.edit.setVisible(shown)
        self.chevron.setPixmap(pixmap("chevron-up" if shown else "chevron-down", theme.TEXT_2, 16))
        if self.grow:
            self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding if shown else QSizePolicy.Fixed)

    def set_lines(self, n: int):
        self.count.setText(plain_count(n, "line"))


class ToolLayout(QWidget):
    """A tool page: the header, the control card, then the settings cards (``left``, a scrolling
    column) beside ``right`` (the results and the log). On a narrow page (under STACK_BELOW) tabs
    show one part at a time: ``tabs`` are (key, label), each left card belongs to a tab
    (``left`` is (tab key, card)), and ``right`` to the tab ``right_tab``.

    The page calls ``resize_to(width)`` from its resizeEvent; ``on_mode(mode)`` lets it adapt to
    wide | medium | narrow (hide table columns ...)."""

    def __init__(self, header: Header, hero: Hero, left: list[tuple[str, QWidget]],
                 right: list[tuple[QWidget, int]], tabs: list[tuple[str, str]], right_tab: str,
                 left_width: tuple[int, int] = (400, 370), on_mode=None, parent=None):
        super().__init__(parent)
        self.setObjectName("lookPage")
        self.header, self.hero, self.left_cards, self.right_tab = header, hero, left, right_tab
        self.left_width, self.on_mode = left_width, on_mode
        self.mode = ""

        self.tabs = Segmented(tabs, big=True)
        self.tabs.changed.connect(lambda _: self._apply_tab())
        col = QWidget()
        lv = QVBoxLayout(col)
        lv.setContentsMargins(0, 0, 4, 0)
        lv.setSpacing(14)
        for _, card in left:
            lv.addWidget(card)
        lv.addStretch(1)
        # a plain scroll area: it keeps the column at the height its widgets need (ScrollPage's height-for-width
        # estimate squeezed fixed-height cards over each other), and scrolls sideways rather than cutting a card off
        self.left = QScrollArea()
        self.left.setFrameShape(QFrame.NoFrame)
        self.left.setWidgetResizable(True)
        self.left.setWidget(col)
        self.right = QWidget()
        rv = QVBoxLayout(self.right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(14)
        for w, stretch in right:
            rv.addWidget(w, stretch)
        self.right.setMinimumSize(300, 240)
        self.split = QSplitter(Qt.Horizontal, childrenCollapsible=False)
        self.split.addWidget(self.left)
        self.split.addWidget(self.right)
        self.split.setStretchFactor(1, 1)

        self.lay = QVBoxLayout(self)
        self.lay.setSpacing(16)
        self.lay.addWidget(header)
        self.lay.addWidget(hero)
        self.lay.addWidget(self.tabs)
        self.lay.addWidget(self.split, 1)
        self.set_mode("wide")

    def resize_to(self, width: int):
        mode = "wide" if width >= WIDE else "medium" if width >= STACK_BELOW else "narrow"
        if mode != self.mode:
            self.set_mode(mode)
            self.hero.place()  # the buttons and counts changed size with the text

    def set_mode(self, mode: str):
        """A short window scrolls the page (core/ui.py ScrollPage) instead of squeezing the parts."""
        global SCALE
        self.mode = mode
        SCALE = SCALES[mode]
        self.setStyleSheet(style(SCALE))
        for w in self.findChildren(QWidget):  # painted widgets and size hints use the new font
            w.updateGeometry()
            w.update()
        side = {"wide": 28, "medium": 20, "narrow": 16}[mode]
        self.lay.setContentsMargins(side, 22 if mode == "wide" else 16, side, side - 4)
        self.header.set_narrow(mode == "narrow")
        self.tabs.setVisible(mode == "narrow")
        self.split.setSizes([self.left_width[0], 1000] if mode == "wide" else [self.left_width[1], 600])
        # a narrow page scrolls anyway: the one part it shows gets room for a good number of rows
        self.split.setMinimumHeight(600 if mode == "narrow" else 380)
        self._apply_tab()
        if self.on_mode:
            self.on_mode(mode)

    def _apply_tab(self):
        narrow, tab = self.mode == "narrow", self.tabs.key()
        for key, card in self.left_cards:  # a card the page folded away (property "folded") stays hidden
            card.setVisible((not narrow or key == tab) and not card.property("folded"))
        self.left.setVisible(not narrow or any(key == tab for key, _ in self.left_cards))
        self.right.setVisible(not narrow or tab == self.right_tab)

    def refresh_cards(self):
        """After the page folded a card away or back (its "folded" property)."""
        self._apply_tab()

    def show_tab(self, key: str):
        self.tabs.set(key)
        self._apply_tab()

    def set_tab_label(self, key: str, text: str):
        self.tabs.set_label(key, text)
