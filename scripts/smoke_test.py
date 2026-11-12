#!/usr/bin/env python3
"""Build and boot upstream's synthetic PPU title without any game data."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

from port import ROOT, positive_int, resolve, run, tool


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk", default="external/ps3recomp")
    parser.add_argument("--build-dir", default="build-smoke")
    parser.add_argument("--cmake", default="cmake")
    parser.add_argument("--ninja", default="ninja")
    parser.add_argument("--jobs", type=positive_int, default=2)
    args = parser.parse_args()
    try:
        sdk, build_dir = resolve(args.sdk), resolve(args.build_dir)
        source = sdk / "runtime/ppu/tests/smoke/smoke_recomp.c"
        if not source.is_file():
            raise ValueError("Missing ps3recomp submodule. Run git submodule update --init --recursive.")
        cmake, ninja = tool(args.cmake), tool(args.ninja)
        generated = build_dir / "synthetic"
        generated.mkdir(parents=True, exist_ok=True)
        image = generated / "smoke.elf"
        run([sys.executable, str(sdk / "tools/make_smoke_elf.py"),
             "--out", str(image), "--header", str(generated / "ppu_recomp.h")])
        shutil.copyfile(source, generated / "smoke_recomp.c")
        run([cmake, "-S", str(ROOT), "-B", str(build_dir), "-G", "Ninja",
             f"-DCMAKE_MAKE_PROGRAM={ninja}", f"-DPS3RECOMP_DIR={sdk}",
             f"-DRECOMP_DIR={generated}", "-DPORT_ENABLE_SPU=OFF",
             "-DPS3RECOMP_RSX_VULKAN=OFF", "-DCMAKE_BUILD_TYPE=RelWithDebInfo"])
        run([cmake, "--build", str(build_dir), "--target", "TankBattlesRecomp",
             "--parallel", str(args.jobs)])
        executable = build_dir / ("TankBattlesRecomp.exe" if os.name == "nt" else "TankBattlesRecomp")
        env = os.environ.copy()
        env.update(SDL_AUDIODRIVER="dummy", SDL_VIDEODRIVER="dummy", PS3_TITLE="Tank Battles scaffold smoke test")
        # Keep every writable mount in this test's ignored build directory.
        for name in ("PS3_VFS_ROOT", "PS3_HDD0_ROOT", "PS3_HDD1_ROOT"):
            env[name] = str(generated)
        result = subprocess.run([str(executable), str(image)], cwd=ROOT, env=env,
                                capture_output=True, text=True, timeout=60)
        log = result.stdout + result.stderr
        (build_dir / "smoke.log").write_text(log)
        print(log)
        # The guest sends status 39; upstream's atexit verifier returns 0 on success.
        if result.returncode != 0 or "[smoke] PASS" not in log:
            raise ValueError(f"Synthetic boot failed (exit {result.returncode}); see {build_dir / 'smoke.log'}")
        print("Synthetic boot passed. This does not establish Tank Battles compatibility.")
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        parser.exit(1, f"error: {error}\n")
    except subprocess.CalledProcessError as error:
        return error.returncode if error.returncode > 0 else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
