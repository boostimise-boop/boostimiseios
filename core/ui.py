"""Layout helpers that let the tools fit any screen, from a 1024x600 laptop to a 4K monitor.

  FlowLayout      a row of widgets that wraps onto more lines when the width runs out
  scrollable()    puts a widget in a frameless scroll area that only scrolls when it must
  fit_combos()    stops long drop-down texts from forcing the window wide
  wrap_forms()    form rows put their label above the field when there is no room beside it
"""
from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtWidgets import (
    QComboBox, QFormLayout, QFrame, QLayout, QScrollArea, QSizePolicy, QSpacerItem, QVBoxLayout, QWidget,
)


class FlowLayout(QLayout):
    """Lays widgets out left to right and wraps them onto a new line when the width runs out.
    Widgets that expand sideways (a line edit) and stretches share the spare width of their line."""

    def __init__(self, parent=None, hspace=6, vspace=6):
        super().__init__(parent)
        self._items = []
        self._hspace, self._vspace = hspace, vspace

    def addStretch(self):
        self.addItem(QSpacerItem(0, 0, QSizePolicy.Expanding, QSizePolicy.Minimum))

    def addItem(self, item):
        self._items.append(item)
        self.invalidate()

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        if not 0 <= index < len(self._items):
            return None
        self.invalidate()
        return self._items.pop(index)

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._arrange(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._arrange(rect, apply=True)

    def sizeHint(self):  # everything on one line
        items = self._shown()
        m = self.contentsMargins()
        width = sum(i.sizeHint().width() for i in items) + self._hspace * max(0, len(items) - 1)
        height = max((i.sizeHint().height() for i in items), default=0)
        return QSize(width + m.left() + m.right(), height + m.top() + m.bottom())

    def minimumSize(self):  # one widget per line
        m = self.contentsMargins()
        size = QSize()
        for item in self._shown():
            size = size.expandedTo(item.minimumSize())
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _shown(self):
        return [i for i in self._items if not i.isEmpty() or i.spacerItem()]

    def _arrange(self, rect, apply):
        """Places the items inside rect (when apply) and returns the height they need."""
        m = self.contentsMargins()
        area = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        lines, line, used = [], [], 0
        for item in self._shown():
            width = item.sizeHint().width()
            if line and used + self._hspace + width > area.width():
                lines.append(line)
                line, used = [], 0
            used += (self._hspace if line else 0) + width
            line.append(item)
        if line:
            lines.append(line)
        y = area.y()
        for line in lines:
            height = max(i.sizeHint().height() for i in line)
            if apply:
                spare = area.width() - sum(i.sizeHint().width() for i in line) - self._hspace * (len(line) - 1)
                grows = [bool(i.expandingDirections() & Qt.Horizontal) for i in line]
                extra = max(0, spare) // max(1, sum(grows))
                x = area.x()
                for item, grow in zip(line, grows):
                    hint = item.sizeHint()
                    width = min(hint.width() + (extra if grow else 0), item.maximumSize().width(), area.width())
                    item.setGeometry(QRect(x, y + (height - hint.height()) // 2, width, hint.height()))
                    x += width + self._hspace
            y += height + self._vspace
        return y - (self._vspace if lines else 0) - rect.y() + m.bottom()


class _Holder(QWidget):
    """Holds a widget whose rows wrap (a FlowLayout) and asks for the least height it needs at a
    width. A scroll area otherwise sizes it to its *preferred* height and scrolls too early."""

    def __init__(self, widget: QWidget):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(widget)

    def heightForWidth(self, width):
        return self.layout().minimumHeightForWidth(width)


class ScrollPage(QScrollArea):
    """A frameless scroll area around a page or panel. While there is room it just shows the widget
    at full size; when the window is smaller than the widget can be, it scrolls instead of the
    window growing past the screen (or the widget being squeezed until its contents overlap)."""

    def __init__(self, widget: QWidget, horizontal=True, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.NoFrame)
        self.setWidgetResizable(True)
        if not horizontal:
            self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        layout = widget.layout()
        self.setWidget(_Holder(widget) if layout is not None and layout.hasHeightForWidth() else widget)

    def minimumSizeHint(self):
        return QSize(120, 80)


def scrollable(widget: QWidget, horizontal=False) -> ScrollPage:
    return ScrollPage(widget, horizontal)


def fit_combos(root: QWidget, max_chars: int = 26):
    """A combo box is normally as wide as its longest entry, so one long entry (or a long chat name
    loaded later) makes the whole page wider than a laptop screen. Cap the width at about
    max_chars characters; the drop-down list still opens wide enough to read every entry."""
    for combo in root.findChildren(QComboBox):
        texts = [combo.itemText(i) for i in range(combo.count())] + [combo.currentText()]
        longest = max(texts, key=len, default="")
        combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        if combo.minimumContentsLength() == 0:  # a page may have chosen its own length
            combo.setMinimumContentsLength(max(6, min(len(longest), max_chars)))
        if len(longest) > max_chars:
            policy = combo.sizePolicy()
            policy.setHorizontalPolicy(QSizePolicy.Expanding)
            combo.setSizePolicy(policy)
            combo.view().setMinimumWidth(combo.fontMetrics().horizontalAdvance(longest) + 48)


def wrap_forms(root: QWidget):
    """Form rows keep label and field side by side while they fit, else the field goes under its
    label; fields always use the full width of the form."""
    for form in root.findChildren(QFormLayout):
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)


def make_responsive(page: QWidget):
    """Applied by the main window to every tool page once it is built."""
    fit_combos(page)
    wrap_forms(page)
