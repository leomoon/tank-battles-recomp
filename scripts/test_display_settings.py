#!/usr/bin/env python3
"""Exercise the shared display preferences without SDL, a game or a server."""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
ROOT = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix="tb-display-settings-") as directory:
    root = Path(directory)
    exe = root / ("display-settings.exe" if os.name == "nt" else "display-settings")
    if os.name == "nt":
        sys.path.insert(0, str(ROOT / "external/ps3recomp/tools"))
        from msvc_env import environ, find_vcvars
        environment = environ()
        compiler = shutil.which("clang-cl", path=environment["PATH"])
        if not compiler:
            compiler = str(Path(find_vcvars()).parents[2] / "Tools/Llvm/x64/bin/clang-cl.exe")
        subprocess.run([compiler, "/std:c++20", "/EHsc", "/O2", "/I" + str(ROOT),
                        str(ROOT / "tests/display_settings.cpp"), str(ROOT / "player_settings.cpp"),
                        "/Fe:" + str(exe)], cwd=root, env=environment, check=True)
    else:
        subprocess.run(["c++", "-std=c++20", "-O2", "-I" + str(ROOT),
                        str(ROOT / "tests/display_settings.cpp"), str(ROOT / "player_settings.cpp"),
                        "-pthread", "-o", str(exe)], check=True)
    subprocess.run([str(exe), str(root)], check=True)
