# PyInstaller recipe: dist\Boostimise\Boostimise.exe plus its _internal folder (one folder, not one
# file: a 500 MB one-file exe would unpack itself on every start). Run build.bat, which makes the
# icon and version info this reads, then tests the result and wraps it in the installer.
import importlib.util
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

sys.path.insert(0, SPECPATH)
from build import BUILD, ROOT, app_info  # noqa: E402

NAME = app_info()["name"]

# The sidebar imports each tool's page by name (tools/__init__.py), which PyInstaller can't see.
tool_modules = [".".join(p.relative_to(ROOT).with_suffix("").parts).removesuffix(".__init__")
                for p in (ROOT / "tools").rglob("*.py")] + ["core.selftest"]
# Phone-number metadata is loaded per country at run time (the mobile-number form).
phone_modules = collect_submodules("phonenumbers")

# OpenVINO loads its device plugins (CPU / GPU / NPU) and model readers at run time. The bundled
# model is OpenVINO IR; ONNX is the download fallback. The TF / PyTorch / Paddle readers aren't used.
ov_libs = Path(importlib.util.find_spec("openvino").submodule_search_locations[0]) / "libs"
ov_skip = ("tensorflow", "pytorch", "paddle", "jax", "gguf")
ov_binaries = [(str(p), "openvino/libs") for p in ov_libs.glob("*.dll") if not any(s in p.name for s in ov_skip)]

a = Analysis(
    [str(ROOT / "app.py")],
    pathex=[str(ROOT)],
    binaries=ov_binaries,
    datas=[
        (str(ROOT / "assets" / "logo.png"), "assets"),  # the logo (core/theme.py)
        (str(ROOT / "assets" / "logo-full.png"), "assets"),  # logo with the wordmark (sign-in pages)
        (str(ROOT / "assets" / "*.svg"), "assets"),  # the style sheet's glyphs
        (str(ROOT / "assets" / "flags" / "*.svg"), "assets/flags"),  # country flags of the mobile-number form
        (str(ROOT / "assets" / "phone_models.tsv"), "assets"),  # model numbers -> names (model_detect/model_codes.py)
        (str(ROOT / "tools" / "whatsapp_fetch" / "*.js"), "tools/whatsapp_fetch"),
        (str(ROOT / "tools" / "photo_match" / "models"), "models"),  # matcher.RESOURCE_DIR is _MEIPASS
        (str(ov_libs / "cache.json"), "openvino/libs"),
        *collect_data_files("rapidocr_onnxruntime"),  # config.yaml and the OCR models
    ],
    hiddenimports=tool_modules + phone_modules + ["PySide6.QtWebSockets"],
    excludes=["tkinter", "matplotlib", "IPython", "pytest", "torch", "tensorflow", "paddle", "jax"],
    noarchive=False,
)
# ~50 MB nobody uses: OpenCV's video decoder (OCR reads still photos) and Qt's software OpenGL
# (the window is plain widgets, drawn without OpenGL).
unused = ("opencv_videoio_ffmpeg", "opengl32sw.dll")
a.binaries = [b for b in a.binaries if not any(u in b[0] for u in unused)]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=NAME,
    icon=str(ROOT / "assets" / "icon.ico"),
    version=str(BUILD / "version_info.txt"),
    console=False,
    upx=False,  # UPX-packed Qt DLLs break, and antivirus programs distrust them
)
coll = COLLECT(exe, a.binaries, a.datas, name=NAME, upx=False)
