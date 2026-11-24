#!/usr/bin/env python3
"""Exercise the shared display preferences without SDL, a game or a server."""
from pathlib import Path
import subprocess
import tempfile
ROOT = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix="tb-display-settings-") as directory:
    root = Path(directory)
    exe = root / "display-settings"
    subprocess.run(["c++", "-std=c++20", "-O2", "-I" + str(ROOT),
                    str(ROOT / "tests/display_settings.cpp"), str(ROOT / "player_settings.cpp"),
                    "-pthread", "-o", str(exe)], check=True)
    subprocess.run([str(exe), str(root)], check=True)
