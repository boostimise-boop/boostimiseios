"""Builds the Boostimise installer:  dist\\Boostimise-Setup-<version>.exe

  1. assets/logo.png -> assets/icon.ico                 (make_icon.py)
  2. PyInstaller     -> dist/Boostimise/Boostimise.exe  (Boostimise.spec: the exe + everything it needs)
  3. Boostimise.exe --selftest                          (every tool opens, every library loads)
  4. Inno Setup      -> dist/Boostimise-Setup-<ver>.exe (installer.iss)

Name, publisher and version come from core/theme.py. Run build.bat, or:
    .venv\\Scripts\\python.exe packaging\\build.py
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BUILD = ROOT / "build"
DIST = ROOT / "dist"

VERSION_INFO = """\
VSVersionInfo(
  ffi=FixedFileInfo(filevers={v4}, prodvers={v4}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', {publisher!r}),
      StringStruct('FileDescription', {name!r}),
      StringStruct('FileVersion', {version!r}),
      StringStruct('InternalName', {name!r}),
      StringStruct('LegalCopyright', {copyright!r}),
      StringStruct('OriginalFilename', {exe!r}),
      StringStruct('ProductName', {name!r}),
      StringStruct('ProductVersion', {version!r})])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def app_info() -> dict[str, str]:
    """APP_NAME, APP_PUBLISHER, APP_VERSION and APP_ID from core/theme.py (read as text: no Qt)."""
    text = (ROOT / "core" / "theme.py").read_text(encoding="utf-8")
    info = {}
    for key in ("name", "publisher", "version", "id"):
        match = re.search(rf'^APP_{key.upper()}\s*=\s*"([^"]+)"', text, re.M)
        if not match:
            sys.exit(f"APP_{key.upper()} = \"...\" not found in core/theme.py")
        info[key] = match.group(1)
    return info


def step(text: str):
    print(f"\n=== {text}", flush=True)


def run(cmd: list, **kwargs):
    print(">", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], check=True, **kwargs)


def write_version_info(info: dict[str, str]) -> Path:
    parts = [int(p) for p in re.findall(r"\d+", info["version"])[:4]]
    v4 = tuple(parts + [0] * (4 - len(parts)))
    path = BUILD / "version_info.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(VERSION_INFO.format(
        v4=v4, exe=f"{info['name']}.exe", copyright=f"© {date.today().year} {info['publisher']}",
        **info), encoding="utf-8")
    return path


def self_test(exe: Path):
    """Run the packaged app's self-test with a throw-away data folder."""
    data = BUILD / "selftest-data"
    shutil.rmtree(data, ignore_errors=True)
    report = BUILD / "selftest.txt"
    report.unlink(missing_ok=True)
    env = dict(os.environ, COVERDESK_DATA=str(data))
    try:
        code = subprocess.run([str(exe), "--selftest", str(report)], env=env, timeout=600).returncode
    except subprocess.TimeoutExpired:
        sys.exit("The self-test did not finish within 10 minutes.")
    print(report.read_text(encoding="utf-8") if report.exists() else "(no report written)")
    if code != 0:
        sys.exit(f"The packaged app failed its self-test (exit code {code}), see above. "
                 f"The app's log: {data / 'app.log'}")


def find_iscc() -> Path | None:
    found = shutil.which("iscc")
    candidates = [Path(found)] if found else []
    for base in (os.environ.get("LOCALAPPDATA", "") + r"\Programs", os.environ.get("ProgramFiles(x86)", ""),
                 os.environ.get("ProgramFiles", "")):
        if base:
            candidates += sorted(Path(base).glob("Inno Setup */ISCC.exe"), reverse=True)
    return next((c for c in candidates if c.is_file()), None)


def main():
    info = app_info()
    name, version = info["name"], info["version"]
    print(f"Building {name} {version} ({info['publisher']})")

    step("Icon")
    sys.path.insert(0, str(HERE))
    import make_icon

    print(make_icon.main())

    step("PyInstaller")
    write_version_info(info)
    run([sys.executable, "-m", "PyInstaller", HERE / "Boostimise.spec", "--noconfirm", "--clean",
         "--distpath", DIST, "--workpath", BUILD / "pyinstaller"], cwd=ROOT)
    exe = DIST / name / f"{name}.exe"
    size = sum(f.stat().st_size for f in exe.parent.rglob("*") if f.is_file())
    print(f"{exe}  ({size / 1e6:.0f} MB in the folder)")

    step("Self-test of the packaged app")
    self_test(exe)

    step("Installer")
    iscc = find_iscc()
    if not iscc:
        sys.exit("Inno Setup 6 is not installed, so no installer was made. Install it from "
                 "https://jrsoftware.org/isdl.php (or: winget install JRSoftware.InnoSetup) and run "
                 f"build.bat again. The app itself is ready in {exe.parent}.")
    run([iscc, "/Qp", f"/DAppName={name}", f"/DAppVersion={version}", f"/DAppPublisher={info['publisher']}",
         f"/DAppUserModelId={info['id']}", f"/DSourceDir={exe.parent}", f"/DOutputDir={DIST}",
         f"/DIconFile={ROOT / 'assets' / 'icon.ico'}", HERE / "installer.iss"])
    setup = DIST / f"{name}-Setup-{version}.exe"
    print(f"\nDone: {setup}  ({setup.stat().st_size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
