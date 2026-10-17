"""Pillow <-> Qt helpers shared by the photo tools."""
from __future__ import annotations

from PIL import Image
from PySide6.QtGui import QImage


def pil_to_qimage(im: Image.Image) -> QImage:
    """A QImage that owns a copy of the pixels. Safe to call on a worker thread."""
    if im.mode not in ("RGB", "RGBA"):
        im = im.convert("RGBA")
    if im.mode == "RGB":
        data = im.tobytes("raw", "RGB")
        qim = QImage(data, im.width, im.height, im.width * 3, QImage.Format_RGB888)
    else:
        data = im.tobytes("raw", "RGBA")
        qim = QImage(data, im.width, im.height, im.width * 4, QImage.Format_RGBA8888)
    return qim.copy()  # `data` is freed when this returns
