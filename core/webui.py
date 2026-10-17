"""The website's look (boostimise.com: Tailwind CSS 4 + shadcn "radix-nova", app/globals.css) for
the Qt pages that come from it: the sign-in screens, the Photo Enhancer, Dashboard and Plans.

Colours are the site's tokens (its oklch values converted to sRGB), sizes are its Tailwind sizes
in pixels, icons are the lucide icons the site uses. The other tools keep core/theme.py.

  icon_pixmap / qicon     lucide icons
  Button, ToggleTile      shadcn buttons (default, outline, secondary, ghost, destructive, link, gradient)
  Badge, Card, Box, Alert, Separator, Switch, Slider, CheckBox, Select, Input, TextArea, Avatar, Spinner
  WebDialog               a centred card over a dimmed window (shadcn Dialog)
  toast                   sonner-style notifications at the top of the window (richColors)
  WebPage                 a white page with the hero glow
  Logo, GradientTitle, ResponsiveGrid, Meter
"""
from __future__ import annotations

from functools import lru_cache
from typing import Callable

from PySide6.QtCore import (
    QByteArray, QEasingCurve, QEvent, QPointF, QRect, QRectF, QSize, Qt, QTimer, QVariantAnimation, Signal,
)
from PySide6.QtGui import (
    QBrush, QColor, QCursor, QFont, QFontMetricsF, QIcon, QImage, QLinearGradient, QPainter, QPainterPath, QPen,
    QPixmap, QRadialGradient,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QAbstractButton, QApplication, QCheckBox, QComboBox, QDialog, QFrame, QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QSizePolicy,
    QSlider, QVBoxLayout, QWidget,
)

from .paths import ASSETS

# ---- tokens (app/globals.css :root) --------------------------------------------------------
NAVY = "#0b1730"          # --brand-navy, --foreground
BLUE = "#0062e9"          # --brand-blue, --primary
SKY = "#0a84f0"           # --brand-sky
FG = NAVY
BG = "#ffffff"
CARD = "#ffffff"
MUTED = "#f1f5f9"         # --muted, --secondary, --accent
MUTED_FG = "#62748e"      # --muted-foreground
BORDER = "#e5e5e5"        # --border, --input
RING = "#4da3ff"          # --ring
PRIMARY = BLUE
DESTRUCTIVE = "#e7000b"
EMERALD = "#00bc7d"       # Tailwind 4 emerald-500
EMERALD_700 = "#007a55"
AMBER = "#fe9a00"         # amber-500
AMBER_600 = "#e17100"
ROSE = "#ff2056"          # rose-500
ROSE_950 = "#4d0218"
ROSE_100 = "#ffe4e6"
GLOW_1 = "#217bf4"        # .hero-glow
GLOW_2 = "#069ff9"
MONO = "'Cascadia Mono', 'Consolas', monospace"
RADIUS = 10               # --radius 0.625rem: rounded-lg; rounded-xl 14, rounded-2xl 18, rounded-3xl 22


def mix(a: str, b: str, t: float) -> str:
    """a blended toward b by t (0..1): Tailwind's color/opacity over a white page."""
    ca, cb = QColor(a), QColor(b)
    return QColor(round(ca.red() + (cb.red() - ca.red()) * t), round(ca.green() + (cb.green() - ca.green()) * t),
                  round(ca.blue() + (cb.blue() - ca.blue()) * t)).name()


def tint(color: str, opacity: float, over: str = BG) -> str:
    """``color`` at ``opacity`` over ``over`` (e.g. bg-primary/10 -> tint(PRIMARY, .1))."""
    return mix(over, color, opacity)


def rgba(color: str, opacity: float) -> str:
    c = QColor(color)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {round(opacity * 255)})"


def font(px: float, weight: int = 400, mono: bool = False) -> QFont:
    f = QFont(["Cascadia Mono", "Consolas"] if mono else QApplication.font().families())
    f.setPixelSize(max(1, round(px)))
    f.setWeight(QFont.Weight(weight))
    return f


# ---- lucide icons (ISC licence), 24x24, stroke 2 --------------------------------------------
ICONS = {
    "sparkles": '<path d="M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .963 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.581a.5.5 0 0 1 0 .964L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.963 0z"/><path d="M20 3v4"/><path d="M22 5h-4"/><path d="M4 17v2"/><path d="M5 18H3"/>',
    "scan-face": '<path d="M3 7V5a2 2 0 0 1 2-2h2"/><path d="M17 3h2a2 2 0 0 1 2 2v2"/><path d="M21 17v2a2 2 0 0 1-2 2h-2"/><path d="M7 21H5a2 2 0 0 1-2-2v-2"/><path d="M8 14s1.5 2 4 2 4-2 4-2"/><path d="M9 9h.01"/><path d="M15 9h.01"/>',
    "history": '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/><path d="M12 7v5l4 2"/>',
    "maximize-2": '<path d="M15 3h6v6"/><path d="M9 21H3v-6"/><path d="M21 3l-7 7"/><path d="M3 21l7-7"/>',
    "shield-check": '<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/><path d="m9 12 2 2 4-4"/>',
    "image-up": '<path d="M10.3 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v10l-3.1-3.1a2 2 0 0 0-2.814.014L6 21"/><path d="m14 19.5 3-3 3 3"/><path d="M17 22v-5.5"/><circle cx="9" cy="9" r="2"/>',
    "sliders-horizontal": '<path d="M21 4h-7"/><path d="M10 4H3"/><path d="M21 12h-9"/><path d="M8 12H3"/><path d="M21 20h-5"/><path d="M12 20H3"/><path d="M14 2v4"/><path d="M8 10v4"/><path d="M16 18v4"/>',
    "download": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5"/><path d="M12 15V3"/>',
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "arrow-left": '<path d="m12 19-7-7 7-7"/><path d="M19 12H5"/>',
    "chevron-down": '<path d="m6 9 6 6 6-6"/>',
    "chevron-up": '<path d="m18 15-6-6-6 6"/>',
    "chevrons-up-down": '<path d="m7 15 5 5 5-5"/><path d="m7 9 5-5 5 5"/>',
    "infinity": '<path d="M12 12c-2-2.67-4-4-6-4a4 4 0 1 0 0 8c2 0 4-1.33 6-4Zm0 0c2 2.67 4 4 6 4a4 4 0 0 0 0-8c-2 0-4 1.33-6 4Z"/>',
    "refresh-cw": '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/>',
    "server-crash": '<path d="M6 10H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v4a2 2 0 0 1-2 2h-2"/><path d="M6 14H4a2 2 0 0 0-2 2v4a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-4a2 2 0 0 0-2-2h-2"/><path d="M6 6h.01"/><path d="M6 18h.01"/><path d="m13 6-4 6h6l-4 6"/>',
    "wand-2": '<path d="m21.64 3.64-1.28-1.28a1.21 1.21 0 0 0-1.72 0L2.36 18.64a1.21 1.21 0 0 0 0 1.72l1.28 1.28a1.2 1.2 0 0 0 1.72 0L21.64 5.36a1.2 1.2 0 0 0 0-1.72"/><path d="m14 7 3 3"/><path d="M5 6v4"/><path d="M19 14v4"/><path d="M10 2v2"/><path d="M7 8H3"/><path d="M21 16h-4"/><path d="M11 3H9"/>',
    "triangle-alert": '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/><path d="M12 9v4"/><path d="M12 17h.01"/>',
    "loader": '<path d="M21 12a9 9 0 1 1-6.219-8.56"/>',
    "x": '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
    "clock": '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
    "plus": '<path d="M5 12h14"/><path d="M12 5v14"/>',
    "minus": '<path d="M5 12h14"/>',
    "upload": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m17 8-5-5-5 5"/><path d="M12 3v12"/>',
    "brain": '<path d="M12 5a3 3 0 1 0-5.997.125 4 4 0 0 0-2.526 5.77 4 4 0 0 0 .556 6.588A4 4 0 1 0 12 18Z"/><path d="M12 5a3 3 0 1 1 5.997.125 4 4 0 0 1 2.526 5.77 4 4 0 0 1-.556 6.588A4 4 0 1 1 12 18Z"/><path d="M15 13a4.5 4.5 0 0 1-3-4 4.5 4.5 0 0 1-3 4"/><path d="M17.599 6.5a3 3 0 0 0 .399-1.375"/><path d="M6.003 5.125A3 3 0 0 0 6.401 6.5"/><path d="M3.477 10.896a4 4 0 0 1 .585-.396"/><path d="M19.938 10.5a4 4 0 0 1 .585.396"/><path d="M6 18a4 4 0 0 1-1.967-.516"/><path d="M19.967 17.484A4 4 0 0 1 18 18"/>',
    "circle-check": '<circle cx="12" cy="12" r="10"/><path d="m9 12 2 2 4-4"/>',
    "circle-x": '<circle cx="12" cy="12" r="10"/><path d="m15 9-6 6"/><path d="m9 9 6 6"/>',
    "circle-alert": '<circle cx="12" cy="12" r="10"/><path d="M12 8v4"/><path d="M12 16h.01"/>',
    "octagon-x": '<path d="m15 9-6 6"/><path d="M2.586 16.726A2 2 0 0 1 2 15.312V8.688a2 2 0 0 1 .586-1.414l4.688-4.688A2 2 0 0 1 8.688 2h6.624a2 2 0 0 1 1.414.586l4.688 4.688A2 2 0 0 1 22 8.688v6.624a2 2 0 0 1-.586 1.414l-4.688 4.688a2 2 0 0 1-1.414.586H8.688a2 2 0 0 1-1.414-.586z"/><path d="m9 9 6 6"/>',
    "info": '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/>',
    "gauge": '<path d="m12 14 4-4"/><path d="M3.34 19a10 10 0 1 1 17.32 0"/>',
    "timer": '<path d="M10 2h4"/><path d="M12 14l3-3"/><circle cx="12" cy="14" r="8"/>',
    "chevrons-left-right": '<path d="m9 7-5 5 5 5"/><path d="m15 7 5 5-5 5"/>',
    "maximize": '<path d="M8 3H5a2 2 0 0 0-2 2v3"/><path d="M21 8V5a2 2 0 0 0-2-2h-3"/><path d="M3 16v3a2 2 0 0 0 2 2h3"/><path d="M16 21h3a2 2 0 0 0 2-2v-3"/>',
    "scan-search": '<path d="M3 7V5a2 2 0 0 1 2-2h2"/><path d="M17 3h2a2 2 0 0 1 2 2v2"/><path d="M21 17v2a2 2 0 0 1-2 2h-2"/><path d="M7 21H5a2 2 0 0 1-2-2v-2"/><circle cx="12" cy="12" r="3"/><path d="m16 16-1.9-1.9"/>',
    "zap": '<path d="M4 14a1 1 0 0 1-.78-1.63l9.9-10.2a.5.5 0 0 1 .86.46l-1.92 6.02A1 1 0 0 0 13 10h7a1 1 0 0 1 .78 1.63l-9.9 10.2a.5.5 0 0 1-.86-.46l1.92-6.02A1 1 0 0 0 11 14z"/>',
    "ban": '<circle cx="12" cy="12" r="10"/><path d="m4.9 4.9 14.2 14.2"/>',
    "crown": '<path d="M11.562 3.266a.5.5 0 0 1 .876 0L15.39 8.87a1 1 0 0 0 1.516.294L21.183 5.5a.5.5 0 0 1 .798.519l-2.834 10.246a1 1 0 0 1-.956.734H5.81a1 1 0 0 1-.957-.734L2.02 6.02a.5.5 0 0 1 .798-.519l4.276 3.664a1 1 0 0 0 1.516-.294z"/><path d="M5 21h14"/>',
    "layout-dashboard": '<rect width="7" height="9" x="3" y="3" rx="1"/><rect width="7" height="5" x="14" y="3" rx="1"/><rect width="7" height="9" x="14" y="12" rx="1"/><rect width="7" height="5" x="3" y="16" rx="1"/>',
    "log-out": '<path d="m16 17 5-5-5-5"/><path d="M21 12H9"/><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/>',
    "mail": '<rect width="20" height="16" x="2" y="4" rx="2"/><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/>',
    "phone": '<path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"/>',
    "calendar-days": '<path d="M8 2v4"/><path d="M16 2v4"/><rect width="18" height="18" x="3" y="4" rx="2"/><path d="M3 10h18"/><path d="M8 14h.01"/><path d="M12 14h.01"/><path d="M16 14h.01"/><path d="M8 18h.01"/><path d="M12 18h.01"/><path d="M16 18h.01"/>',
    "calendar-range": '<rect width="18" height="18" x="3" y="4" rx="2"/><path d="M16 2v4"/><path d="M3 10h18"/><path d="M8 2v4"/><path d="M17 14h-6"/><path d="M13 18H7"/><path d="M7 14h.01"/><path d="M17 18h.01"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="m4.93 4.93 1.41 1.41"/><path d="m17.66 17.66 1.41 1.41"/><path d="M2 12h2"/><path d="M20 12h2"/><path d="m6.34 17.66-1.41 1.41"/><path d="m19.07 4.93-1.41 1.41"/>',
    "image": '<rect width="18" height="18" x="3" y="3" rx="2" ry="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21"/>',
    "radio": '<path d="M4.9 19.1C1 15.2 1 8.8 4.9 4.9"/><path d="M7.8 16.2c-2.3-2.3-2.3-6.1 0-8.5"/><circle cx="12" cy="12" r="2"/><path d="M16.2 7.8c2.3 2.3 2.3 6.1 0 8.5"/><path d="M19.1 4.9C23 8.8 23 15.1 19.1 19"/>',
    "user": '<path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
    "user-round": '<circle cx="12" cy="8" r="5"/><path d="M20 21a8 8 0 0 0-16 0"/>',
    "smartphone": '<rect width="14" height="20" x="5" y="2" rx="2" ry="2"/><path d="M12 18h.01"/>',
    "chart-column": '<path d="M3 3v16a2 2 0 0 0 2 2h16"/><path d="M18 17V9"/><path d="M13 17V5"/><path d="M8 17v-3"/>',
    "file-archive": '<path d="M10 12v-1"/><path d="M10 18v-2"/><path d="M10 7V6"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M15.5 22H18a2 2 0 0 0 2-2V7l-5-5H6a2 2 0 0 0-2 2v16a2 2 0 0 0 .274 1.01"/><circle cx="10" cy="20" r="2"/>',
    "rotate-ccw": '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/>',
    "pencil": '<path d="M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.352a.5.5 0 0 0 .623.622l4.353-1.32a2 2 0 0 0 .83-.497z"/><path d="m15 5 4 4"/>',
    "folder-open": '<path d="m6 14 1.5-2.9A2 2 0 0 1 9.24 10H20a2 2 0 0 1 1.94 2.5l-1.54 6a2 2 0 0 1-1.95 1.5H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3.9a2 2 0 0 1 1.69.9l.81 1.2a2 2 0 0 0 1.67.9H18a2 2 0 0 1 2 2v2"/>',
    "search": '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
    "external-link": '<path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
    "wifi-off": '<path d="M12 20h.01"/><path d="M8.5 16.429a5 5 0 0 1 7 0"/><path d="M5 12.859a10 10 0 0 1 5.17-2.69"/><path d="M19 12.859a10 10 0 0 0-2.007-1.523"/><path d="M2 8.82a15 15 0 0 1 4.177-2.643"/><path d="M22 8.82a15 15 0 0 0-11.288-3.764"/><path d="m2 2 20 20"/>',
}

GOOGLE_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
    '<path fill="#4285F4" d="M23.52 12.27c0-.85-.08-1.67-.22-2.45H12v4.64h6.46a5.52 5.52 0 0 1-2.4 3.62v3h3.88c2.27-2.09 3.58-5.17 3.58-8.81Z"/>'
    '<path fill="#34A853" d="M12 24c3.24 0 5.96-1.07 7.94-2.91l-3.88-3c-1.07.72-2.45 1.15-4.06 1.15-3.12 0-5.77-2.11-6.71-4.95H1.28v3.1A12 12 0 0 0 12 24Z"/>'
    '<path fill="#FBBC05" d="M5.29 14.29A7.2 7.2 0 0 1 4.91 12c0-.8.14-1.57.38-2.29v-3.1H1.28A12 12 0 0 0 0 12c0 1.94.46 3.77 1.28 5.39l4.01-3.1Z"/>'
    '<path fill="#EA4335" d="M12 4.75c1.76 0 3.34.61 4.59 1.8l3.44-3.44C17.95 1.19 15.24 0 12 0A12 12 0 0 0 1.28 6.61l4.01 3.1C6.23 6.86 8.88 4.75 12 4.75Z"/>'
    '</svg>')


def _render_svg(svg: str, size: int, ratio: float, rotate: float = 0) -> QPixmap:
    px = max(1, round(size * ratio))
    pm = QPixmap(px, px)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    if rotate:
        p.translate(px / 2, px / 2)
        p.rotate(rotate)
        p.translate(-px / 2, -px / 2)
    QSvgRenderer(QByteArray(svg.encode())).render(p, QRectF(0, 0, px, px))
    p.end()
    pm.setDevicePixelRatio(ratio)
    return pm


@lru_cache(maxsize=1024)
def icon_pixmap(name: str, color: str = FG, size: int = 16, ratio: float = 1.0, rotate: float = 0,
                stroke: float = 2) -> QPixmap:
    if name == "google":
        return _render_svg(GOOGLE_SVG, size, ratio)
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
           f'stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round">{ICONS[name]}</svg>')
    return _render_svg(svg, size, ratio, rotate)


def dpr() -> float:
    app = QApplication.instance()
    screen = app.primaryScreen() if app else None
    return max(1.0, screen.devicePixelRatio() if screen else 1.0)


def qicon(name: str, color: str = FG, size: int = 16) -> QIcon:
    return QIcon(icon_pixmap(name, color, size, max(2.0, dpr())))


class Icon(QLabel):
    """A lucide icon as a label (size in px)."""

    def __init__(self, name: str, color: str = FG, size: int = 16, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.set(name, color)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet("background: transparent;")

    def set(self, name: str, color: str | None = None):
        self._name = name
        self._color = color or getattr(self, "_color", FG)
        self.setPixmap(icon_pixmap(name, self._color, self.width(), max(2.0, dpr())))


class Spinner(QLabel):
    """lucide Loader2 with animate-spin."""

    def __init__(self, size: int = 16, color: str = PRIMARY, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setStyleSheet("background: transparent;")
        self._size, self._color, self._angle = size, color, 0
        self._timer = QTimer(self, interval=40, timeout=self._tick)
        self._paint()

    def _tick(self):
        self._angle = (self._angle + 18) % 360
        self._paint()

    def _paint(self):
        self.setPixmap(icon_pixmap("loader", self._color, self._size, max(2.0, dpr()), self._angle))

    def showEvent(self, e):
        self._timer.start()
        super().showEvent(e)

    def hideEvent(self, e):
        self._timer.stop()
        super().hideEvent(e)


# ---- text ----------------------------------------------------------------------------------
def label(text: str = "", px: float = 14, weight: int = 400, color: str = FG, mono: bool = False,
          wrap: bool = False, align=None, upper_spacing: float = 0, selectable: bool = False) -> QLabel:
    lab = QLabel(text)
    lab.setFont(font(px, weight, mono))
    if upper_spacing:
        f = lab.font()
        f.setLetterSpacing(QFont.AbsoluteSpacing, upper_spacing)
        lab.setFont(f)
    lab.setStyleSheet(f"color: {color}; background: transparent;")
    lab.setWordWrap(wrap)
    if align is not None:
        lab.setAlignment(align)
    if selectable:
        lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return lab


def set_color(lab: QLabel, color: str):
    lab.setStyleSheet(f"color: {color}; background: transparent;")


# ---- buttons -------------------------------------------------------------------------------
_SIZES = {  # height, horizontal padding, font px, icon px, radius
    "xs": (24, 8, 12, 12, 8),
    "sm": (28, 10, 12.8, 14, 8),
    "default": (32, 10, 14, 16, 10),
    "lg": (36, 10, 14, 16, 10),
    "xl": (44, 20, 15, 16, 10),     # h-11 px-5 (the big calls to action)
    "2xl": (48, 16, 16, 20, 10),    # h-12 text-base (Enhance photo)
    "icon-sm": (28, 0, 14, 14, 8),
    "icon": (32, 0, 14, 16, 10),
    "icon-lg": (36, 0, 14, 16, 10),
}


def _variant_colors(variant: str) -> dict:
    """bg / fg / border / hover-bg for a shadcn button variant."""
    if variant == "default":
        return dict(bg=PRIMARY, fg="#ffffff", border=PRIMARY, hover=tint(PRIMARY, .8), icon="#ffffff")
    if variant == "outline":
        return dict(bg=BG, fg=FG, border=BORDER, hover=MUTED, icon=FG)
    if variant == "secondary":
        return dict(bg=MUTED, fg=FG, border=MUTED, hover=mix(MUTED, FG, .05), icon=FG)
    if variant == "ghost":
        return dict(bg="transparent", fg=FG, border="transparent", hover=MUTED, icon=FG)
    if variant == "ghost-muted":
        return dict(bg="transparent", fg=MUTED_FG, border="transparent", hover=MUTED, icon=MUTED_FG)
    if variant == "destructive":
        return dict(bg=tint(DESTRUCTIVE, .1), fg=DESTRUCTIVE, border=tint(DESTRUCTIVE, .1),
                    hover=tint(DESTRUCTIVE, .2), icon=DESTRUCTIVE)
    if variant == "link":
        return dict(bg="transparent", fg=PRIMARY, border="transparent", hover="transparent", icon=PRIMARY)
    if variant == "white":
        return dict(bg="#ffffff", fg=FG, border=BORDER, hover=MUTED, icon=FG)
    raise ValueError(variant)


class Button(QPushButton):
    """shadcn <Button>: variant default | outline | secondary | ghost | ghost-muted | destructive | link |
    gradient (brand sky -> blue, the Enhance button), size xs | sm | default | lg | xl | 2xl | icon*."""

    def __init__(self, text: str = "", icon: str | None = None, variant: str = "default", size: str = "default",
                 parent=None, full: bool = False, height: int | None = None, weight: int = 600,
                 icon_size: int | None = None, font_px: float | None = None):
        super().__init__(text, parent)
        self.variant, self.size_name = variant, size
        h, pad, px, ipx, radius = _SIZES[size]
        self._h = height or h
        self._icon_px = icon_size or ipx
        self._icon_name = icon
        self._loading = False
        self._angle = 0
        self._spin = QTimer(self, interval=40, timeout=self._spin_tick)
        self.setCursor(Qt.PointingHandCursor)
        self.setFont(font(font_px or px, weight))
        self.setFixedHeight(self._h)
        if size.startswith("icon"):
            self.setFixedWidth(self._h)
        if full:
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setIconSize(QSize(self._icon_px, self._icon_px))
        self._radius, self._pad = radius, pad
        self._style()
        self._apply_icon()

    def _style(self):
        r, pad = self._radius, self._pad
        if self.variant == "gradient":
            a, b = SKY, BLUE
            ha, hb = mix(SKY, "#ffffff", .1), mix(BLUE, "#ffffff", .1)
            da, db = tint(SKY, .5), tint(BLUE, .5)
            self.setStyleSheet(f"""
                QPushButton {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {a}, stop:1 {b});
                    color: #ffffff; border: none; border-radius: {r}px; padding: 0 {pad}px;
                    min-height: {self._h}px; max-height: {self._h}px; }}
                QPushButton:hover {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ha}, stop:1 {hb}); }}
                QPushButton:pressed {{ padding-top: 1px; }}
                QPushButton:disabled {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {da}, stop:1 {db});
                    color: #ffffff; }}""")
            return
        c = _variant_colors(self.variant)
        dis_bg = "transparent" if c["bg"] == "transparent" else tint(c["bg"], .5)
        dis_fg = tint(c["fg"], .5)
        dis_border = "transparent" if c["border"] == "transparent" else tint(c["border"], .5)
        underline = "QPushButton:hover { text-decoration: underline; }" if self.variant == "link" else ""
        self.setStyleSheet(f"""
            QPushButton {{ background: {c['bg']}; color: {c['fg']}; border: 1px solid {c['border']};
                border-radius: {r}px; padding: 0 {pad}px; min-height: {self._h - 2}px; max-height: {self._h - 2}px;
                text-align: center; }}
            QPushButton:hover {{ background: {c['hover']}; color: {c['fg']}; border-color: {c['border'] if self.variant != 'default' else c['hover']}; }}
            QPushButton:pressed {{ padding-top: 1px; }}
            QPushButton:disabled {{ background: {dis_bg}; color: {dis_fg}; border-color: {dis_border}; }}
            {underline}""")

    def icon_color(self) -> str:
        if self.variant == "gradient":
            return "#ffffff"
        color = _variant_colors(self.variant)["icon"]
        return color if self.isEnabled() else tint(color, .5)

    def set_icon(self, name: str | None):
        self._icon_name = name
        self._apply_icon()

    def set_variant(self, variant: str):
        self.variant = variant
        self._style()
        self._apply_icon()

    def _apply_icon(self):
        if self._loading:
            self.setIcon(QIcon(icon_pixmap("loader", self.icon_color(), self._icon_px, max(2.0, dpr()), self._angle)))
        elif self._icon_name:
            self.setIcon(QIcon(icon_pixmap(self._icon_name, self.icon_color(), self._icon_px, max(2.0, dpr()))))
        else:
            self.setIcon(QIcon())

    def changeEvent(self, e):
        if e.type() == QEvent.EnabledChange:
            self._apply_icon()
        super().changeEvent(e)

    def set_loading(self, on: bool):
        self._loading = on
        if on:
            self._spin.start()
        else:
            self._spin.stop()
        self._apply_icon()

    def _spin_tick(self):
        self._angle = (self._angle + 18) % 360
        self._apply_icon()


class ToggleTile(QPushButton):
    """An item of the outline ToggleGroup (Enhancement mode, 2x / 4x): border-input, checked ->
    primary border, primary/10 background, primary text."""

    def __init__(self, text: str, icon: str | None = None, height: int = 44, px: float = 14, weight: int = 400,
                 align_left: bool = True, parent=None):
        super().__init__(text, parent)
        self._icon_name = icon
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(height)
        self.setFont(font(px, weight))
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setIconSize(QSize(16, 16))
        align = "left" if align_left else "center"
        pad = "0 12px" if align_left else "0"
        self.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {FG}; border: 1px solid {BORDER}; border-radius: {RADIUS}px;
                padding: {pad}; text-align: {align}; min-height: {height - 2}px; max-height: {height - 2}px; }}
            QPushButton:hover {{ background: {MUTED}; }}
            QPushButton:checked {{ background: {tint(PRIMARY, .1)}; color: {PRIMARY}; border-color: {PRIMARY}; }}
            QPushButton:disabled {{ color: {tint(FG, .5)}; border-color: {tint(BORDER, .5)}; background: transparent; }}
            QPushButton:checked:disabled {{ background: {tint(PRIMARY, .05)}; color: {tint(PRIMARY, .5)};
                border-color: {tint(PRIMARY, .5)}; }}""")
        self.toggled.connect(lambda _: self._apply_icon())
        self._apply_icon()

    def _apply_icon(self):
        if self._icon_name:
            color = PRIMARY if self.isChecked() else FG
            color = color if self.isEnabled() else tint(color, .5)
            self.setIcon(QIcon(icon_pixmap(self._icon_name, color, 16, max(2.0, dpr()))))

    def changeEvent(self, e):
        if e.type() == QEvent.EnabledChange:
            self._apply_icon()
        super().changeEvent(e)


# ---- surfaces ------------------------------------------------------------------------------
class Box(QFrame):
    """A rounded box: background, 1px border (or a navy/10 ring like shadcn <Card>), radius, padding."""

    def __init__(self, radius: int = 14, bg: str = CARD, border: str | None = BORDER, padding=16,
                 layout: str = "v", spacing: int = 0, parent=None, dashed: bool = False, border_width: int = 1):
        super().__init__(parent)
        self.setObjectName("box")
        style = "dashed" if dashed else "solid"
        line = f"{border_width}px {style} {border}" if border else "none"
        self.setStyleSheet(f"QFrame#box {{ background: {bg}; border: {line}; border-radius: {radius}px; }}")
        lay = QVBoxLayout(self) if layout == "v" else QHBoxLayout(self)
        if isinstance(padding, int):
            padding = (padding,) * 4
        lay.setContentsMargins(*padding)
        lay.setSpacing(spacing)
        self.lay = lay

    def add(self, w, stretch: int = 0, align=None):
        if isinstance(w, QWidget):
            if align is not None:
                self.lay.addWidget(w, stretch, align)
            else:
                self.lay.addWidget(w, stretch)
        else:
            self.lay.addLayout(w, stretch)
        return w


def Card(padding: int = 16, spacing: int = 16, parent=None) -> Box:
    """shadcn <Card>: rounded-xl, ring-1 ring-foreground/10, py/px 16 (sm: 12)."""
    return Box(radius=14, bg=CARD, border=tint(FG, .1), padding=padding, spacing=spacing, parent=parent)


def shadow(widget: QWidget, blur: int = 30, dy: int = 10, alpha: float = .1, color: str = "#000000"):
    eff = QGraphicsDropShadowEffect(widget)
    eff.setBlurRadius(blur)
    eff.setOffset(0, dy)
    c = QColor(color)
    c.setAlphaF(alpha)
    eff.setColor(c)
    widget.setGraphicsEffect(eff)
    return eff


class Separator(QFrame):
    def __init__(self, vertical: bool = False, color: str = BORDER, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"background: {color}; border: none;")
        if vertical:
            self.setFixedWidth(1)
        else:
            self.setFixedHeight(1)


class Badge(QFrame):
    """shadcn <Badge>: h-5, rounded-full, px-2, text-xs font-medium."""

    def __init__(self, text: str = "", variant: str = "default", icon: str | None = None, px: float = 12,
                 height: int = 20, upper: bool = False, mono: bool = False, fg: str | None = None, parent=None):
        super().__init__(parent)
        self.setObjectName("badge")
        self._icon = Icon(icon, size=12) if icon else None
        self._text = label(text.upper() if upper else text, px, 600, mono=mono)
        if upper:
            f = self._text.font()
            f.setLetterSpacing(QFont.AbsoluteSpacing, .4)
            self._text.setFont(f)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6 if icon else 8, 0, 8, 0)
        lay.setSpacing(4)
        if self._icon:
            lay.addWidget(self._icon)
        lay.addWidget(self._text)
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._fg_override = fg
        self.set_variant(variant)

    def set_variant(self, variant: str):
        self.variant = variant
        bg, fg, border = {
            "default": (PRIMARY, "#ffffff", PRIMARY),
            "secondary": (MUTED, FG, MUTED),
            "outline": ("transparent", FG, BORDER),
            "destructive": (tint(DESTRUCTIVE, .1), DESTRUCTIVE, tint(DESTRUCTIVE, .1)),
        }[variant]
        fg = self._fg_override or fg
        radius = self.height() // 2 if self.height() else 10
        self.setStyleSheet(f"QFrame#badge {{ background: {bg}; border: 1px solid {border}; border-radius: {radius}px; }}")
        set_color(self._text, fg)
        if self._icon:
            self._icon.set(self._icon._name, fg)

    def set_text(self, text: str):
        self._text.setText(text)


class Alert(QFrame):
    """shadcn <Alert>: rounded-lg border px-2.5 py-2, icon + title (font-medium) + description."""

    def __init__(self, title: str, text: str = "", variant: str = "default", icon: str = "info", parent=None):
        super().__init__(parent)
        self.setObjectName("alert")
        color = DESTRUCTIVE if variant == "destructive" else FG
        desc_color = mix(BG, DESTRUCTIVE, .9) if variant == "destructive" else MUTED_FG
        self.setStyleSheet(f"QFrame#alert {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: {RADIUS}px; }}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(8)
        ic = Icon(icon, color, 16)
        lay.addWidget(ic, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(2)
        col.setContentsMargins(0, 0, 0, 0)
        self.title = label(title, 14, 600, color, wrap=True)
        col.addWidget(self.title)
        self.text = label(text, 14, 400, desc_color, wrap=True)
        self.text.setVisible(bool(text))
        col.addWidget(self.text)
        lay.addLayout(col, 1)


# ---- inputs --------------------------------------------------------------------------------
class Switch(QAbstractButton):
    """shadcn <Switch>: 32 x 18, primary when on, input-grey when off, white 16 px thumb."""

    def __init__(self, checked: bool = False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(32, 18)
        self._pos = 1.0 if checked else 0.0
        self._anim = QVariantAnimation(self, duration=120, easingCurve=QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._set_pos)
        self.toggled.connect(self._animate)

    def _set_pos(self, v):
        self._pos = float(v)
        self.update()

    def _animate(self, on: bool):
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if not self.isEnabled():
            p.setOpacity(.5)
        track = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        on, off = QColor(PRIMARY), QColor(BORDER)
        t = self._pos
        color = QColor(round(off.red() + (on.red() - off.red()) * t), round(off.green() + (on.green() - off.green()) * t),
                       round(off.blue() + (on.blue() - off.blue()) * t))
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawRoundedRect(track, track.height() / 2, track.height() / 2)
        d = 16
        x = 1 + t * (self.width() - d - 2)
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(QRectF(x, (self.height() - d) / 2, d, d))
        p.end()

    def sizeHint(self):
        return QSize(32, 18)


class Slider(QSlider):
    """shadcn <Slider>: 4 px muted track, primary range, 12 px white thumb with a ring border.
    ``dim`` = the opacity-70 look of a slider that is on "Auto"."""

    def __init__(self, minimum: int = 0, maximum: int = 100, value: int = 0, parent=None):
        super().__init__(Qt.Horizontal, parent)
        self.setRange(minimum, maximum)
        self.setValue(value)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(16)
        self._dim = False
        self._style()

    def set_dim(self, dim: bool):
        if dim != self._dim:
            self._dim = dim
            self._style()

    def _style(self):
        k = .7 if self._dim else 1.0
        rng, track, ring = tint(PRIMARY, k), MUTED, tint(RING, k)
        self.setStyleSheet(f"""
            QSlider {{ background: transparent; }}
            QSlider::groove:horizontal {{ height: 4px; background: {track}; border-radius: 2px; }}
            QSlider::sub-page:horizontal {{ background: {rng}; border-radius: 2px; }}
            QSlider::add-page:horizontal {{ background: {track}; border-radius: 2px; }}
            QSlider::handle:horizontal {{ background: #ffffff; border: 1px solid {ring}; width: 10px; height: 10px;
                margin: -4px 0; border-radius: 6px; }}
            QSlider::handle:horizontal:hover {{ border: 2px solid {RING}; width: 8px; height: 8px; }}
            QSlider::sub-page:horizontal:disabled {{ background: {tint(PRIMARY, .5)}; }}
            QSlider::handle:horizontal:disabled {{ border-color: {tint(RING, .5)}; }}""")


class CheckBox(QCheckBox):
    """shadcn <Checkbox>: 16 px, rounded 4 px, border-input; primary with a white tick when checked."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setFont(font(14, 600))
        self.setStyleSheet(f"""
            QCheckBox {{ color: {FG}; spacing: 12px; background: transparent; }}
            QCheckBox::indicator {{ width: 14px; height: 14px; border: 1px solid {mix(BORDER, FG, .12)};
                border-radius: 4px; background: #ffffff; }}
            QCheckBox::indicator:hover {{ border-color: {RING}; }}
            QCheckBox::indicator:checked {{ background: {PRIMARY}; border-color: {PRIMARY}; image: url(ui:check.svg); }}""")


def _field_style(cls: str, height: int | None, px: float) -> str:
    h = f"min-height: {height - 2}px; max-height: {height - 2}px;" if height else ""
    return f"""
        {cls} {{ background: transparent; color: {FG}; border: 1px solid {BORDER}; border-radius: {RADIUS}px;
            padding: 0 10px; {h} font-size: {px}px; selection-background-color: {PRIMARY}; selection-color: #ffffff; }}
        {cls}:hover {{ border-color: {mix(BORDER, FG, .12)}; }}
        {cls}:focus {{ border: 1px solid {RING}; }}
        {cls}:disabled {{ color: {tint(FG, .5)}; background: {tint(BORDER, .5)}; }}"""


class Input(QLineEdit):
    def __init__(self, placeholder: str = "", height: int = 40, px: float = 14, parent=None):
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setFixedHeight(height)
        self.setStyleSheet(_field_style("QLineEdit", None, px)
                           + f"QLineEdit[invalid=\"true\"] {{ border-color: {DESTRUCTIVE}; }}")

    def set_invalid(self, on: bool):
        self.setProperty("invalid", "true" if on else "false")
        self.style().unpolish(self)
        self.style().polish(self)


class TextArea(QPlainTextEdit):
    def __init__(self, placeholder: str = "", rows: int = 3, parent=None):
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setFixedHeight(16 + rows * 22)
        self.setStyleSheet(f"""
            QPlainTextEdit {{ background: transparent; color: {FG}; border: 1px solid {BORDER}; border-radius: {RADIUS}px;
                padding: 6px 8px; font-size: 14px; }}
            QPlainTextEdit:focus {{ border-color: {RING}; }}""")


class Select(QComboBox):
    """shadcn <Select> trigger (h-8, rounded-lg, border-input) with a popover-like list."""

    def __init__(self, items: list[tuple[str, str]] = (), height: int = 32, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(height)
        self.setFont(font(14))
        for value, text in items:
            self.addItem(text, value)
        self.setStyleSheet(f"""
            QComboBox {{ background: transparent; color: {FG}; border: 1px solid {BORDER}; border-radius: {RADIUS}px;
                padding: 0 28px 0 10px; min-height: 0; }}
            QComboBox:hover {{ border-color: {mix(BORDER, FG, .12)}; }}
            QComboBox:focus {{ border-color: {RING}; }}
            QComboBox:disabled {{ color: {tint(FG, .5)}; }}
            QComboBox::drop-down {{ border: none; width: 26px; }}
            QComboBox::down-arrow {{ image: url(ui:chevron-down.svg); width: 12px; height: 12px; }}
            QComboBox QAbstractItemView {{ background: #ffffff; border: 1px solid {tint(FG, .1)}; border-radius: 8px;
                padding: 4px; outline: 0; selection-background-color: {MUTED}; selection-color: {FG}; }}
            QComboBox QAbstractItemView::item {{ min-height: 28px; padding: 0 6px; border-radius: 6px; }}""")

    def value(self):
        return self.currentData()

    def set_value(self, value):
        i = self.findData(value)
        if i >= 0:
            self.setCurrentIndex(i)


# ---- avatar --------------------------------------------------------------------------------
def initials_of(name: str | None, email: str | None = None) -> str:
    import re
    src = (name or email or "?").strip()
    parts = [p for p in re.split(r"[\s@._-]+", src) if p]
    return ((parts[0][0] if parts else "?") + (parts[1][0] if len(parts) > 1 else "")).upper()


class Avatar(QWidget):
    """Round photo, or the initials on primary/10 while there is none."""

    def __init__(self, size: int = 32, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._image: QImage | None = None
        self._initials = "?"
        self._dark = False

    def set_user(self, name: str | None, email: str | None, image: QImage | None = None, dark: bool = False):
        self._initials = initials_of(name, email)
        self._image = image if image is not None and not image.isNull() else None
        self._dark = dark
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        r = QRectF(0, 0, self.width(), self.height())
        path = QPainterPath()
        path.addEllipse(r)
        p.setClipPath(path)
        if self._image is not None:
            img = self._image.scaled(round(r.width() * dpr()), round(r.height() * dpr()),
                                     Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            p.drawImage(r, img, QRectF((img.width() - r.width() * dpr()) / 2, (img.height() - r.height() * dpr()) / 2,
                                       r.width() * dpr(), r.height() * dpr()))
        else:
            p.fillRect(r, QColor("#2a3c66" if self._dark else tint(PRIMARY, .1)))
            p.setPen(QColor("#ffffff" if self._dark else PRIMARY))
            p.setFont(font(max(9.0, self.height() * .36), 700))
            p.drawText(r, Qt.AlignCenter, self._initials)
        p.setClipping(False)
        p.setPen(QPen(QColor(0, 0, 0, 30), 1))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(r.adjusted(.5, .5, -.5, -.5))
        p.end()


# ---- meters, glow, title -------------------------------------------------------------------
class Bar(QWidget):
    """A thin rounded progress bar (Progress h-1 / h-1.5 / h-2)."""

    def __init__(self, height: int = 6, color: str = PRIMARY, track: str = MUTED, parent=None):
        super().__init__(parent)
        self.setFixedHeight(height)
        self._value, self._color, self._track, self._min = 0.0, color, track, 0.0

    def set(self, value: float, color: str | None = None, minimum: float = 0.0):
        self._value = max(0.0, min(1.0, value))
        self._min = minimum
        if color:
            self._color = color
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        rad = r.height() / 2
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(self._track))
        p.drawRoundedRect(r, rad, rad)
        w = r.width() * max(self._min, self._value)
        if w > 0:
            p.setBrush(QColor(self._color))
            p.drawRoundedRect(QRectF(0, 0, max(w, r.height()), r.height()), rad, rad)
        p.end()


def paint_hero_glow(p: QPainter, width: float, height: float):
    """.hero-glow: radial-gradient(60% 50% at 50% 0%, glow 16%, transparent 70%),
    radial-gradient(40% 40% at 85% 20%, glow 10%, transparent 70%)."""
    for cx, cy, rx, ry, color, alpha in ((.5, 0, .6, .5, GLOW_1, .16), (.85, .2, .4, .4, GLOW_2, .10)):
        p.save()
        center = QPointF(width * cx, height * cy)
        rxp, ryp = width * rx, height * ry
        p.translate(center)
        p.scale(1, ryp / rxp if rxp else 1)
        g = QRadialGradient(QPointF(0, 0), rxp)
        c0 = QColor(color)
        c0.setAlphaF(alpha)
        c1 = QColor(color)
        c1.setAlphaF(0)
        g.setColorAt(0, c0)
        g.setColorAt(.7, c1)
        g.setColorAt(1, c1)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(g))
        p.drawEllipse(QPointF(0, 0), rxp, rxp)
        p.restore()


class WebPage(QWidget):
    """A white page (the site's background) with the hero glow over its top ``glow`` px."""

    def __init__(self, glow: int = 0, parent=None):
        super().__init__(parent)
        self.glow = glow
        self.setAttribute(Qt.WA_StyledBackground, False)

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(BG))
        if self.glow:
            p.setRenderHint(QPainter.Antialiasing)
            p.setClipRect(QRect(0, 0, self.width(), self.glow))
            paint_hero_glow(p, self.width(), self.glow)
        p.end()


class GradientTitle(QWidget):
    """The hero's h1: bold, tight tracking, text fading from foreground to foreground/65."""

    def __init__(self, text: str, px: int = 60, small_px: int = 36, parent=None):
        super().__init__(parent)
        self.text, self.px, self.small_px = text, px, small_px
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._update_height()

    def _font(self) -> QFont:
        f = font(self.px if self.width() >= 640 or self.width() == 0 else self.small_px, 700)
        f.setLetterSpacing(QFont.PercentageSpacing, 97.5)
        return f

    def _update_height(self):
        self.setFixedHeight(round(QFontMetricsF(self._font()).height() * 1.1))

    def resizeEvent(self, e):
        self._update_height()
        super().resizeEvent(e)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        f = self._font()
        fm = QFontMetricsF(f)
        path = QPainterPath()
        w = fm.horizontalAdvance(self.text)
        base = (self.height() - fm.height()) / 2 + fm.ascent()
        path.addText(QPointF((self.width() - w) / 2, base), f, self.text)
        g = QLinearGradient(0, base - fm.ascent(), 0, base + fm.descent())
        g.setColorAt(0, QColor(FG))
        end = QColor(FG)
        end.setAlphaF(.65)
        g.setColorAt(1, end)
        p.fillPath(path, QBrush(g))
        p.end()


class Logo(QWidget):
    """components/Logo.tsx: the mark (assets/logo.png) and the wordmark "Boost" + blue "imise"
    over "PHOTO & WHATSAPP TOOLS"."""

    def __init__(self, compact: bool = False, mark: int = 32, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        img = QImage(str(ASSETS / "logo.png"))
        pm = QPixmap.fromImage(img.scaledToHeight(round(mark * max(2.0, dpr())), Qt.SmoothTransformation))
        pm.setDevicePixelRatio(max(2.0, dpr()))
        icon = QLabel()
        icon.setPixmap(pm)
        icon.setStyleSheet("background: transparent;")
        lay.addWidget(icon)
        if not compact:
            col = QVBoxLayout()
            col.setSpacing(2)
            name = QLabel(f'Boost<span style="color: {BLUE};">imise</span>')
            f = font(17, 700)
            f.setLetterSpacing(QFont.AbsoluteSpacing, -.4)
            name.setFont(f)
            name.setStyleSheet(f"color: {NAVY}; background: transparent;")
            col.addWidget(name)
            col.addWidget(label("PHOTO & WHATSAPP TOOLS", 10, 600, MUTED_FG, upper_spacing=.3))
            lay.addLayout(col)


class FullLogo(QLabel):
    """The full logo (assets/logo-full.png): the "B" mark over the "Boost" + blue "imise" wordmark, for
    light pages such as sign-in. height in px."""

    def __init__(self, height: int = 112, parent=None):
        super().__init__(parent)
        ratio = max(2.0, dpr())
        img = QImage(str(ASSETS / "logo-full.png"))
        pm = QPixmap.fromImage(img.scaledToHeight(round(height * ratio), Qt.SmoothTransformation))
        pm.setDevicePixelRatio(ratio)
        self.setPixmap(pm)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet("background: transparent;")


class ResponsiveGrid(QWidget):
    """Cards in as many columns as fit (min_width each, at most max_cols), like the site's
    sm:grid-cols-2 lg:grid-cols-4 grids."""

    def __init__(self, min_width: int = 220, max_cols: int = 4, gap: int = 16, parent=None,
                 breakpoints: tuple[int, ...] | None = None):
        super().__init__(parent)
        self.items: list[QWidget] = []
        self.min_width, self.max_cols, self.gap = min_width, max_cols, gap
        self.breakpoints = breakpoints
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(gap)
        self._cols = 0

    def add(self, w: QWidget):
        self.items.append(w)
        self._relayout(force=True)
        return w

    def clear(self):
        for w in self.items:
            self.grid.removeWidget(w)
            w.hide()
            w.deleteLater()
        self.items.clear()
        self._cols = 0

    def _cols_for(self, width: int) -> int:
        if self.breakpoints:  # columns at widths, e.g. (1, 2, 4) for 0 / 560 / 980 px
            cols = 1
            for need, n in zip((0, 560, 980), self.breakpoints):
                if width >= need:
                    cols = n
            return max(1, cols)
        return max(1, min(self.max_cols, (width + self.gap) // (self.min_width + self.gap)))

    def _relayout(self, force=False):
        cols = self._cols_for(self.width() or self.min_width * self.max_cols)
        if cols == self._cols and not force:
            return
        self._cols = cols
        for w in self.items:
            self.grid.removeWidget(w)
        for i, w in enumerate(self.items):
            self.grid.addWidget(w, i // cols, i % cols)
        for c in range(max(cols, self.grid.columnCount())):
            self.grid.setColumnStretch(c, 1 if c < cols else 0)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._relayout()


# ---- toasts (sonner, richColors, top-center) ------------------------------------------------
_TOAST = {  # bg, border, text, icon
    "normal": (CARD, BORDER, FG, None),
    "success": ("#ecfdf3", "#bffcd9", "#008a2e", "circle-check"),
    "info": ("#f0f8ff", "#dde7fd", "#0973dc", "info"),
    "warning": ("#fffcf0", "#fbeeb1", "#dc7609", "triangle-alert"),
    "error": ("#fff0f0", "#ffe0e1", "#e60000", "octagon-x"),
}


class _Toast(QFrame):
    closed = Signal(object)

    def __init__(self, text: str, kind: str, action: tuple[str, Callable] | None, duration: int, parent):
        super().__init__(parent)
        bg, border, fg, icon = _TOAST[kind]
        self.setObjectName("toast")
        self.setStyleSheet(f"QFrame#toast {{ background: {bg}; border: 1px solid {border}; border-radius: {RADIUS}px; }}")
        self.setFixedWidth(356)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(8)
        if icon:
            lay.addWidget(Icon(icon, fg, 16), 0, Qt.AlignVCenter)
        msg = label(text, 13, 600, fg, wrap=True)
        lay.addWidget(msg, 1)
        if action:
            btn = QPushButton(action[0])
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFont(font(12, 600))
            btn.setFixedHeight(24)
            btn.setStyleSheet(f"QPushButton {{ background: {FG}; color: #ffffff; border: none; border-radius: 4px; "
                              f"padding: 0 8px; min-height: 0; }} QPushButton:hover {{ background: {mix(FG, '#ffffff', .15)}; }}")
            btn.clicked.connect(lambda: (action[1](), self.dismiss()))
            lay.addWidget(btn, 0, Qt.AlignVCenter)
        shadow(self, blur=24, dy=4, alpha=.10)
        self._timer = QTimer(self, singleShot=True, interval=duration, timeout=self.dismiss)
        self._timer.start()
        self._closing = False

    def enterEvent(self, e):
        self._timer.stop()
        super().enterEvent(e)

    def leaveEvent(self, e):
        if not self._closing:
            self._timer.start(1500)
        super().leaveEvent(e)

    def dismiss(self):
        if self._closing:
            return
        self._closing = True
        self.closed.emit(self)


class Toaster(QWidget):
    """Holds the toasts at the top centre of a window, newest on top."""

    MAX = 4

    def __init__(self, window: QWidget):
        super().__init__(window)
        self.window_ = window
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(8)
        self.toasts: list[_Toast] = []
        self.setAttribute(Qt.WA_TranslucentBackground)
        window.installEventFilter(self)
        self.hide()

    def eventFilter(self, obj, e):
        if obj is self.window_ and e.type() == QEvent.Resize:
            self._place()
        return False

    def _place(self):
        self.adjustSize()
        w = self.sizeHint().width() or 356
        self.setGeometry((self.window_.width() - w) // 2, 24, w, self.sizeHint().height())
        self.raise_()

    def show_toast(self, text: str, kind: str = "normal", action=None, duration: int = 4000):
        t = _Toast(text, kind, action, duration, self)
        t.closed.connect(self._remove)
        self.toasts.insert(0, t)
        self.lay.insertWidget(0, t)
        while len(self.toasts) > self.MAX:
            self._remove(self.toasts[-1])
        self.show()
        self._place()

    def _remove(self, t: _Toast):
        if t in self.toasts:
            self.toasts.remove(t)
            self.lay.removeWidget(t)
            t.deleteLater()
        if not self.toasts:
            self.hide()
        else:
            QTimer.singleShot(0, self._place)


class _ToastAPI:
    """toast("..."), toast.success / error / warning / info("...", action=("Label", fn))."""

    def __init__(self):
        self.toaster: Toaster | None = None

    def install(self, window: QWidget):
        self.toaster = Toaster(window)

    def _show(self, text, kind, action=None, duration=None):
        if self.toaster is None:
            return
        try:
            self.toaster.show_toast(text, kind, action, duration or (6000 if kind == "error" else 4000))
        except RuntimeError:  # window closed
            self.toaster = None

    def __call__(self, text, action=None, duration=None):
        self._show(text, "normal", action, duration)

    def success(self, text, action=None, duration=None):
        self._show(text, "success", action, duration)

    def error(self, text, action=None, duration=None):
        self._show(text, "error", action, duration)

    def warning(self, text, action=None, duration=None):
        self._show(text, "warning", action, duration)

    def info(self, text, action=None, duration=None):
        self._show(text, "info", action, duration)


toast = _ToastAPI()


# ---- dialog --------------------------------------------------------------------------------
class _Dim(QWidget):
    """bg-black/10 + backdrop blur, approximated by a slightly darker veil."""

    def __init__(self, window: QWidget):
        super().__init__(window)
        self.setGeometry(window.rect())
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("background: rgba(0, 0, 0, 46);")
        self.show()
        self.raise_()


class WebDialog(QDialog):
    """shadcn <Dialog>: a rounded-xl card (p-4, ring navy/10, shadow) centred over the dimmed window.
    Put content in ``self.body``; ``add_footer()`` makes the grey footer strip with buttons."""

    def __init__(self, parent: QWidget | None, width: int = 448, closable: bool = True):
        win = parent.window() if parent is not None else None
        super().__init__(win)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._win = win
        self._dim: _Dim | None = None
        self._closable = closable
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        self.card = QFrame(objectName="dialogcard")
        self.card.setStyleSheet(f"QFrame#dialogcard {{ background: {CARD}; border: 1px solid {tint(FG, .1)}; "
                                f"border-radius: 14px; }}")
        self.card.setFixedWidth(width)
        shadow(self.card, blur=40, dy=12, alpha=.16)
        outer.addWidget(self.card)
        self.card_lay = QVBoxLayout(self.card)
        self.card_lay.setContentsMargins(0, 0, 0, 0)
        self.card_lay.setSpacing(0)
        self.body_widget = QWidget()
        self.body_widget.setStyleSheet("background: transparent;")
        self.body = QVBoxLayout(self.body_widget)
        self.body.setContentsMargins(16, 16, 16, 16)
        self.body.setSpacing(16)
        self.card_lay.addWidget(self.body_widget)
        if closable:
            self.close_btn = Button("", "x", "ghost-muted", "icon-sm", parent=self.card)
            self.close_btn.clicked.connect(self.reject)
            self.close_btn.raise_()

    def header(self, title: str, text: str = "", icon: str | None = None, icon_bg: str | None = None,
               icon_fg: str = PRIMARY, gradient: bool = False):
        col = QVBoxLayout()
        col.setSpacing(6)
        if icon:
            tile = QLabel()
            tile.setFixedSize(44, 44)
            tile.setAlignment(Qt.AlignCenter)
            bg = (f"qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {SKY}, stop:1 {BLUE})" if gradient
                  else icon_bg or tint(PRIMARY, .1))
            tile.setStyleSheet(f"background: {bg}; border-radius: 14px;")
            tile.setPixmap(icon_pixmap(icon, "#ffffff" if gradient else icon_fg, 20, max(2.0, dpr())))
            col.addWidget(tile)
            col.addSpacing(2)
        col.addWidget(label(title, 18, 700, FG, wrap=True))
        if text:
            self.description = label(text, 14, 400, MUTED_FG, wrap=True)
            col.addWidget(self.description)
        self.body.addLayout(col)

    def add_footer(self, buttons: list[QWidget], vertical: bool = False):
        foot = QFrame(objectName="dialogfoot")
        foot.setStyleSheet(f"QFrame#dialogfoot {{ background: {tint(MUTED, .5)}; border: none; "
                           f"border-top: 1px solid {BORDER}; border-bottom-left-radius: 14px; "
                           f"border-bottom-right-radius: 14px; }}")
        lay = QVBoxLayout(foot) if vertical else QHBoxLayout(foot)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(8)
        if not vertical:
            lay.addStretch(1)
        for b in buttons:
            lay.addWidget(b)
        self.card_lay.addWidget(foot)
        return foot

    def showEvent(self, e):
        if self._win is not None and self._dim is None:
            self._dim = _Dim(self._win)
        super().showEvent(e)
        QTimer.singleShot(0, self._center)

    def _center(self):
        self.adjustSize()
        if self._closable:
            self.close_btn.move(self.card.width() - 36, 8)
        if self._win is not None:
            g = self._win.geometry()
            self.move(g.center().x() - self.width() // 2, g.center().y() - self.height() // 2)

    def done(self, r):
        if self._dim is not None:
            self._dim.deleteLater()
            self._dim = None
        super().done(r)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape and not self._closable:
            return
        super().keyPressEvent(e)


def open_url(url: str):
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices
    QDesktopServices.openUrl(QUrl(url))


def h_row(*widgets, spacing: int = 8, margins=(0, 0, 0, 0), stretch_at: int | None = None) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setContentsMargins(*margins)
    row.setSpacing(spacing)
    for i, w in enumerate(widgets):
        if w is None:
            row.addStretch(1)
        elif isinstance(w, QWidget):
            row.addWidget(w, 1 if i == stretch_at else 0)
        else:
            row.addLayout(w)
    return row


def v_col(*widgets, spacing: int = 8, margins=(0, 0, 0, 0)) -> QVBoxLayout:
    col = QVBoxLayout()
    col.setContentsMargins(*margins)
    col.setSpacing(spacing)
    for w in widgets:
        if w is None:
            col.addStretch(1)
        elif isinstance(w, QWidget):
            col.addWidget(w)
        else:
            col.addLayout(w)
    return col


def wrap(layout, parent=None) -> QWidget:
    w = QWidget(parent)
    w.setStyleSheet("background: transparent;")
    w.setLayout(layout)
    return w


def clear_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget() is not None:
            item.widget().hide()
            item.widget().deleteLater()
        elif item.layout() is not None:
            clear_layout(item.layout())


def opacity(widget: QWidget, value: float):
    if value >= 1:
        widget.setGraphicsEffect(None)
        return
    eff = QGraphicsOpacityEffect(widget)
    eff.setOpacity(value)
    widget.setGraphicsEffect(eff)


def checkerboard(p: QPainter, rect: QRectF):
    """.checkerboard: muted background with 8% grey 20 px checks."""
    p.fillRect(rect, QColor(MUTED))
    c = QColor(128, 128, 128, 20)
    s = 10
    p.save()
    p.setClipRect(rect)
    y0, x0 = int(rect.top()), int(rect.left())
    for j, y in enumerate(range(y0, int(rect.bottom()) + 1, s)):
        for i, x in enumerate(range(x0, int(rect.right()) + 1, s)):
            if (i + j) % 2 == 0:
                p.fillRect(QRect(x, y, s, s), c)
    p.restore()


def cursor_pos() -> QCursor:
    return QCursor.pos()

