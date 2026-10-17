"""assets/logo.png -> assets/icon.ico (every size Windows asks for, each scaled down from the 1024 px
logo so small sizes stay sharp) and assets/icon.png (256 px, for the README / web). The drawing is
core/theme.py's icon_image, the same as the window's icon.

Run:  .venv\\Scripts\\python.exe packaging\\make_icon.py
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QGuiApplication

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
sys.path.insert(0, str(ROOT))
from core.theme import ICON_SIZES, icon_image  # noqa: E402


def render(size: int) -> Image.Image:
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    icon_image(size).save(buf, "PNG")
    return Image.open(io.BytesIO(bytes(buf.data()))).convert("RGBA")


def main(out_dir: Path = ASSETS) -> Path:
    _app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])  # noqa: F841 - QPainter needs one
    if not (ASSETS / "logo.png").is_file():
        raise SystemExit(f"Could not read {ASSETS / 'logo.png'}")
    images = [render(s) for s in ICON_SIZES]
    out_dir.mkdir(parents=True, exist_ok=True)
    ico = out_dir / "icon.ico"
    images[-1].save(ico, format="ICO", sizes=[(s, s) for s in ICON_SIZES], append_images=images[:-1])
    images[-1].save(out_dir / "icon.png")
    return ico


if __name__ == "__main__":
    print(main(Path(sys.argv[1]) if len(sys.argv) > 1 else ASSETS))
