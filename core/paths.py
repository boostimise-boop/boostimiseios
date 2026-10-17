"""Where the app's code and the user's data live.

Code is in tools/<tool>/, everything a tool writes (settings, logs, browser logins, history)
is in data/<tool>/, so the code folders can be replaced without losing anything.

Installed from the setup (a PyInstaller exe) the program folder may be read-only and is replaced
on every update, so the data goes to %LOCALAPPDATA%\\CoverDesk instead (the app's name up to 2.1;
the folder keeps it so updating never loses anyone's logins). The COVERDESK_DATA environment
variable overrides both (the build's self-test uses it to leave real data alone).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # inside the installed app: its _internal folder
ASSETS = ROOT / "assets"
FROZEN = bool(getattr(sys, "frozen", False))
# Folder name is fixed on purpose: renaming the app must not lose everyone's logins.
DATA_DIR = Path(os.environ.get("COVERDESK_DATA") or (
    Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "CoverDesk" if FROZEN else ROOT / "data"))


def data_dir(name: str) -> Path:
    """data/<name>, created on first use."""
    path = DATA_DIR / name
    path.mkdir(parents=True, exist_ok=True)
    return path
