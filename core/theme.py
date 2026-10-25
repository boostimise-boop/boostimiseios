"""Look shared by the whole app: the name, a fixed light palette, the colours, the font, one style
sheet for every tool's widgets, and the sidebar icons."""
from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, QDir, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

from .paths import ASSETS

# packaging/build.py reads these four lines (name, publisher, version of the exe and the setup).
APP_NAME = "Boostimise"
APP_PUBLISHER = "Boostimise"
APP_VERSION = "4.6.1"
APP_ID = "Boostimise.Boostimise"  # Windows taskbar grouping; the setup's shortcuts use the same id
APP_TAGLINE = "WA Photos • Enhance • Rename • Match"
APP_NAME_BLUE = "imise"  # the end of the name, which the logo's wordmark writes in blue

# Colours, from the logo (assets/logo.png): a navy "B" around a blue photo, with blue sparkles. Every
# colour is solid: the app uses no gradients.
NAVY = "#0f1d3a"             # the logo's "B": the sidebar, the dark control cards, text
SKY = "#1ea0ff"              # the photo's light blue (the name's blue part, buttons on navy)
ACCENT = "#0a6cf0"           # the sparkles' blue: main buttons, the selected tool's icon
ACCENT_HOVER = "#0858d8"
ACCENT_2 = "#0447cf"         # pressed main buttons
ACCENT_SOFT = "#e8f2ff"      # tint behind hovered / checked things
ACCENT_DARK = "#0a46a8"      # text on ACCENT_SOFT
BG = "#f3f6fb"               # page background
SURFACE = "#ffffff"          # cards, inputs, tables
SURFACE_ALT = "#f7f9fc"      # table headers, read-only boxes
BORDER = "#e1e7f0"           # card and table outlines
BORDER_STRONG = "#cdd6e4"    # input and button outlines
TEXT = NAVY
TEXT_2 = "#53607a"           # hints, secondary text
MUTED = "#8b96ab"
SUCCESS = "#16a34a"
WARNING = "#c27803"
ERROR = "#dc2626"
# Soft backgrounds (icon squares, status labels) and the darker text that reads on them.
SUCCESS_SOFT, SUCCESS_DARK = "#e7f6ec", "#137a39"
WARNING_SOFT, WARNING_DARK = "#fff3dc", "#8a5300"
ERROR_SOFT, ERROR_DARK = "#fdecec", "#b91c1c"
NEUTRAL_SOFT = "#eef1f6"
# On the navy sidebar and control cards.
ON_NAVY = "#c6d2e8"          # text
ON_NAVY_MUTED = "#7f90b0"    # section titles, the version
LIVE = "#34d399"             # a tool that is working, "Live"

# Small glyphs the style sheet draws (combo / spin box arrows, check marks), in assets/.
UI_ICONS = ("chevron-down.svg", "chevron-up.svg", "check.svg")
# Sizes of the app icon: the window's (app_icon) and the exe's (packaging/make_icon.py).
ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)

# 24x24 stroke icons, shapes from Feather icons (MIT licence).
_ICONS = {
    "image": '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/>'
             '<polyline points="21 15 16 10 5 21"/>',
    "smile": '<circle cx="12" cy="12" r="10"/><path d="M8 14s1.5 2 4 2 4-2 4-2"/>'
             '<line x1="9" y1="9" x2="9.01" y2="9"/><line x1="15" y1="9" x2="15.01" y2="9"/>',
    "calendar": '<rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/>'
                '<line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/>',
    "list": '<line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/>'
            '<line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/>'
            '<line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/>',
    "smartphone":'<rect x="5" y="2" width="14" height="20" rx="2"/><line x1="12" y1="18" x2="12.01" y2="18"/>',
    "tag": '<path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/>'
           '<line x1="7" y1="7" x2="7.01" y2="7"/>',
    "truck": '<rect x="1" y="3" width="15" height="13"/><polygon points="16 8 20 8 23 11 23 16 16 16 16 8"/>'
             '<circle cx="5.5" cy="18.5" r="2.5"/><circle cx="18.5" cy="18.5" r="2.5"/>',
    "copy": '<rect x="9" y="9" width="13" height="13" rx="2"/>'
            '<path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
    "edit": '<path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/>',
    "folder": '<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>',
    "collapse": '<polyline points="11 17 6 12 11 7"/><polyline points="18 17 13 12 18 7"/>',
    "expand": '<polyline points="13 17 18 12 13 7"/><polyline points="6 17 11 12 6 7"/>',
    # lucide (ISC licence), for the tools that come from the website
    "sparkles": '<path d="M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 '
                '9.937 8.5l1.582-6.135a.5.5 0 0 1 .963 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.581a.5.5 0 0 1 0 '
                '.964L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.963 0z"/><path d="M20 3v4"/>'
                '<path d="M22 5h-4"/><path d="M4 17v2"/><path d="M5 18H3"/>',
    "wand-sparkles": '<path d="m21.64 3.64-1.28-1.28a1.21 1.21 0 0 0-1.72 0L2.36 18.64a1.21 1.21 0 0 0 0 1.72'
                     'l1.28 1.28a1.2 1.2 0 0 0 1.72 0L21.64 5.36a1.2 1.2 0 0 0 0-1.72"/><path d="m14 7 3 3"/>'
                     '<path d="M5 6v4"/><path d="M19 14v4"/><path d="M10 2v2"/><path d="M7 8H3"/>'
                     '<path d="M21 16h-4"/><path d="M11 3H9"/>',
    "store": '<path d="m2 7 4.41-4.41A2 2 0 0 1 7.83 2h8.34a2 2 0 0 1 1.42.59L22 7"/>'
             '<path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8"/><path d="M15 22v-4a2 2 0 0 0-2-2h-2a2 2 0 0 0-2 2v4"/>'
             '<path d="M2 7h20"/><path d="M22 7v3a2 2 0 0 1-2 2a2.7 2.7 0 0 1-2-1a2.7 2.7 0 0 1-4 0a2.7 2.7 0 0 1-4 0'
             'a2.7 2.7 0 0 1-4 0a2.7 2.7 0 0 1-2 1a2 2 0 0 1-2-2V7"/>',
    "layout-dashboard": '<rect width="7" height="9" x="3" y="3" rx="1"/><rect width="7" height="5" x="14" y="3" rx="1"/>'
                        '<rect width="7" height="9" x="14" y="12" rx="1"/><rect width="7" height="5" x="3" y="16" rx="1"/>',
    "crown": '<path d="M11.562 3.266a.5.5 0 0 1 .876 0L15.39 8.87a1 1 0 0 0 1.516.294L21.183 5.5a.5.5 0 0 1 .798.519'
             'l-2.834 10.246a1 1 0 0 1-.956.734H5.81a1 1 0 0 1-.957-.734L2.02 6.02a.5.5 0 0 1 .798-.519l4.276 3.664'
             'a1 1 0 0 0 1.516-.294z"/><path d="M5 21h14"/>',
}


def icon_pixmap(name: str, color: str, size: int, ratio: float = 1.0) -> QPixmap:
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
           f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">{_ICONS[name]}</svg>')
    pm = QPixmap(round(size * ratio), round(size * ratio))
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.Antialiasing)
    QSvgRenderer(QByteArray(svg.encode())).render(painter)
    painter.end()
    pm.setDevicePixelRatio(ratio)
    return pm


@lru_cache(maxsize=1)
def _logo() -> QImage:
    return QImage(str(ASSETS / "logo.png"))


def icon_image(size: int) -> QImage:
    """The app icon: the logo (assets/logo.png, 1024 px, scaled down so small sizes stay sharp) on a
    white rounded tile, so the navy "B" also shows on a dark taskbar and on the sidebar. The exe's
    icon is the same drawing (packaging/make_icon.py)."""
    img = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(SURFACE))
    p.drawRoundedRect(QRectF(0, 0, size, size), size * 0.22, size * 0.22)
    pad = max(1, round(size * 0.1))
    logo = _logo().scaled(size - 2 * pad, size - 2 * pad, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    p.drawImage((size - logo.width()) // 2, (size - logo.height()) // 2, logo)
    p.end()
    return img


def app_icon() -> QIcon:
    icon = QIcon()
    for size in ICON_SIZES:
        icon.addPixmap(QPixmap.fromImage(icon_image(size)))
    return icon


def light_palette() -> QPalette:
    """Fixed light palette so every page looks the same when Windows is in dark mode."""
    p = QPalette()
    colors = {
        QPalette.Window: BG, QPalette.WindowText: TEXT, QPalette.Base: SURFACE,
        QPalette.AlternateBase: SURFACE_ALT, QPalette.Text: TEXT, QPalette.Button: SURFACE,
        QPalette.ButtonText: TEXT, QPalette.ToolTipBase: TEXT, QPalette.ToolTipText: "#ffffff",
        QPalette.Highlight: ACCENT, QPalette.HighlightedText: "#ffffff", QPalette.PlaceholderText: MUTED,
        QPalette.Link: ACCENT, QPalette.BrightText: "#ffffff", QPalette.Light: "#ffffff",
        QPalette.Midlight: "#eceef4", QPalette.Mid: BORDER_STRONG, QPalette.Dark: MUTED, QPalette.Shadow: TEXT_2,
    }
    for role, color in colors.items():
        p.setColor(role, QColor(color))
    for role in (QPalette.Text, QPalette.ButtonText, QPalette.WindowText):
        p.setColor(QPalette.Disabled, role, QColor("#a8adbd"))
    return p


# One look for every tool. A page's own style sheet only adds what is special to it (its labels,
# a progress bar's height...); rules a page sets itself win over these.
STYLESHEET = f"""
QMainWindow, QDialog {{ background: {BG}; }}
QToolTip {{ background: {TEXT}; color: #ffffff; border: none; padding: 6px 9px; }}

QGroupBox {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px; margin-top: 14px;
            padding: 6px 6px 6px 6px; font-weight: 600; }}
QGroupBox::title {{ subcontrol-origin: margin; subcontrol-position: top left; left: 14px; padding: 0 6px;
                   color: {ACCENT_DARK}; }}
QGroupBox QWidget {{ font-weight: 400; }}

QPushButton {{ background: {SURFACE}; color: {TEXT}; border: 1px solid {BORDER_STRONG}; border-radius: 8px;
              padding: 5px 14px; min-height: 18px; }}
QPushButton:hover {{ background: {ACCENT_SOFT}; border-color: #9cc5fb; color: {ACCENT_DARK}; }}
QPushButton:pressed {{ background: #d5e7ff; }}
QPushButton:checked {{ background: {ACCENT_SOFT}; border-color: {ACCENT}; color: {ACCENT_DARK}; }}
QPushButton:disabled {{ background: #f3f4f8; color: #aab0c0; border-color: #e7e9f0; }}
QPushButton#primary, QPushButton#accent {{ background: {ACCENT}; color: #ffffff; border: none;
              font-weight: 600; padding: 6px 18px; }}
QPushButton#primary:hover, QPushButton#accent:hover {{ background: {ACCENT_HOVER}; color: #ffffff; }}
QPushButton#primary:pressed, QPushButton#accent:pressed {{ background: {ACCENT_2}; }}
QPushButton#primary:disabled, QPushButton#accent:disabled {{ background: #b3d1fa; color: #ffffff; }}

QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QComboBox {{ background: {SURFACE}; color: {TEXT};
              border: 1px solid {BORDER_STRONG}; border-radius: 8px;
              selection-background-color: {ACCENT}; selection-color: #ffffff; }}
QLineEdit {{ padding: 4px 8px; }}
QPlainTextEdit, QTextEdit {{ padding: 4px; }}
QLineEdit:hover, QSpinBox:hover, QComboBox:hover {{ border-color: #9cc5fb; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled {{ background: #f3f4f8; color: #aab0c0;
              border-color: #e7e9f0; }}
QLineEdit:read-only, QPlainTextEdit[readOnly="true"], QTextEdit[readOnly="true"] {{ background: {SURFACE_ALT}; }}
QComboBox {{ padding: 4px 28px 4px 8px; }}
QComboBox::drop-down {{ subcontrol-origin: border; subcontrol-position: center right; width: 26px; border: none; }}
QComboBox::down-arrow {{ image: url(ui:chevron-down.svg); width: 12px; height: 12px; }}
QComboBox QAbstractItemView {{ background: {SURFACE}; border: 1px solid {BORDER}; padding: 4px; outline: 0;
              selection-background-color: {ACCENT_SOFT}; selection-color: {ACCENT_DARK}; }}
QComboBox QLineEdit, QSpinBox QLineEdit {{ border: none; padding: 0; background: transparent; border-radius: 0; }}
QSpinBox {{ padding: 3px 24px 3px 8px; }}
QSpinBox::up-button, QSpinBox::down-button {{ subcontrol-origin: border; width: 22px; border: none;
              background: transparent; }}
QSpinBox::up-button {{ subcontrol-position: top right; border-top-right-radius: 8px; }}
QSpinBox::down-button {{ subcontrol-position: bottom right; border-bottom-right-radius: 8px; }}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {{ background: {ACCENT_SOFT}; }}
QSpinBox::up-arrow {{ image: url(ui:chevron-up.svg); width: 10px; height: 10px; }}
QSpinBox::down-arrow {{ image: url(ui:chevron-down.svg); width: 10px; height: 10px; }}

QCheckBox, QRadioButton {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 16px; height: 16px; }}
QCheckBox::indicator {{ border: 1px solid #a9b0c3; border-radius: 5px; background: {SURFACE}; }}
QRadioButton::indicator {{ border: 1px solid #a9b0c3; border-radius: 8px; background: {SURFACE}; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {ACCENT}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; image: url(ui:check.svg); }}
QRadioButton::indicator:checked {{ border-color: {ACCENT}; background: qradialgradient(cx:0.5, cy:0.5, radius:0.5,
              fx:0.5, fy:0.5, stop:0 #ffffff, stop:0.32 #ffffff, stop:0.42 {ACCENT}, stop:1 {ACCENT}); }}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{ background: #f0f1f5; border-color: #d9dce6; }}
QCheckBox::indicator:checked:disabled {{ background: #b3d1fa; border-color: #b3d1fa; }}
QAbstractItemView::indicator {{ width: 15px; height: 15px; border: 1px solid #a9b0c3; border-radius: 4px;
              background: {SURFACE}; }}
QAbstractItemView::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; image: url(ui:check.svg); }}

QTableView, QTableWidget, QListView, QListWidget, QTreeView {{ background: {SURFACE}; border: 1px solid {BORDER};
              border-radius: 10px; gridline-color: #eef0f5; outline: 0;
              selection-background-color: {ACCENT_SOFT}; selection-color: {TEXT}; alternate-background-color: {SURFACE_ALT}; }}
QHeaderView {{ background: transparent; border: none; }}
QHeaderView::section {{ background: {SURFACE_ALT}; color: {TEXT_2}; border: none; border-bottom: 1px solid {BORDER};
              border-right: 1px solid #eef0f5; padding: 7px 8px; font-weight: 600; }}
QHeaderView::section:last {{ border-right: none; }}
QTableCornerButton::section {{ background: {SURFACE_ALT}; border: none; border-bottom: 1px solid {BORDER}; }}

QTabWidget::pane {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 10px; top: -1px; }}
QTabBar::tab {{ background: transparent; color: {TEXT_2}; border: none; border-bottom: 2px solid transparent;
              padding: 7px 16px; margin-right: 2px; font-weight: 600; }}
QTabBar::tab:hover {{ color: {TEXT}; }}
QTabBar::tab:selected {{ color: {ACCENT}; border-bottom-color: {ACCENT}; }}

QProgressBar {{ background: #eaecf4; border: none; border-radius: 7px; text-align: center; color: {TEXT};
              font-weight: 600; min-height: 14px; }}
QProgressBar::chunk {{ border-radius: 7px; background: {SKY}; }}

QScrollArea {{ background: transparent; border: none; }}
QAbstractScrollArea::corner {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 12px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #cdd1de; border-radius: 4px; min-height: 32px; }}
QScrollBar::handle:horizontal {{ background: #cdd1de; border-radius: 4px; min-width: 32px; }}
QScrollBar::handle:hover {{ background: #a9afc4; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

QSplitter::handle {{ background: transparent; }}
QSplitter::handle:hover {{ background: {ACCENT_SOFT}; }}
QSplitter::handle:horizontal {{ width: 8px; }}
QSplitter::handle:vertical {{ height: 8px; }}

QMenu {{ background: {SURFACE}; border: 1px solid {BORDER}; padding: 5px; }}
QMenu::item {{ padding: 7px 26px 7px 12px; border-radius: 6px; color: {TEXT}; }}
QMenu::item:selected {{ background: {ACCENT_SOFT}; color: {ACCENT_DARK}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 8px; }}
QStatusBar {{ background: transparent; color: {TEXT_2}; }}
QStatusBar::item {{ border: none; }}

QLabel#hint, QLabel#dim, QLabel#small {{ color: {TEXT_2}; }}
"""


def apply(app: QApplication):
    app.setStyle("Fusion")
    try:
        app.styleHints().setColorScheme(Qt.ColorScheme.Light)
    except AttributeError:  # Qt < 6.8
        pass
    app.setPalette(light_palette())
    font = QFont()
    font.setFamilies(["Segoe UI", "Nirmala UI"])  # Nirmala UI draws the Bangla in order messages
    font.setPointSize(10)
    app.setFont(font)
    QDir.addSearchPath("ui", str(ASSETS))  # url(ui:check.svg) in the style sheet, also when the path has spaces
    app.setStyleSheet(STYLESHEET)
    app.setWindowIcon(app_icon())
