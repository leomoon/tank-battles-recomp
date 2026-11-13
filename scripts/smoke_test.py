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
from boot_session import run_session
from synthetic_content import write_sfo


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
        write_sfo(generated / "PARAM.SFO")
        run([sys.executable, str(sdk / "tools/make_smoke_elf.py"),
             "--out", str(image), "--header", str(generated / "ppu_recomp.h")])
        shutil.copyfile(source, generated / "smoke_recomp.c")
        run([cmake, "-S", str(ROOT), "-B", str(build_dir), "-G", "Ninja",
             f"-DCMAKE_MAKE_PROGRAM={ninja}", f"-DPS3RECOMP_DIR={sdk}",
             f"-DRECOMP_DIR={generated}", "-DPORT_ENABLE_SPU=OFF",
             "-DPS3RECOMP_RSX_VULKAN=OFF", "-DCMAKE_BUILD_TYPE=RelWithDebInfo",
             f"-DTANK_BATTLES_DIAGNOSTIC_TESTS={'ON' if sys.platform.startswith('linux') else 'OFF'}"])
        run([cmake, "--build", str(build_dir), "--target", "TankBattlesRecomp",
             "--parallel", str(args.jobs)])
        executable = build_dir / ("TankBattlesRecomp.exe" if os.name == "nt" else "TankBattlesRecomp")
        env = os.environ.copy()
        env.pop("TB_DIAGNOSTIC_STALL", None)
        env.pop("TB_EXPECTED_TITLE_ID", None)
        env["PS3_PARAM_SFO"] = str(generated / "PARAM.SFO")
        env["PS3_GAMEDATA_ROOT"] = str(generated)
        env.update(SDL_AUDIODRIVER="dummy", SDL_VIDEODRIVER="dummy", PS3_TITLE="Tank Battles scaffold smoke test")
        # Keep every writable mount in this test's ignored build directory.
        for name in ("PS3_VFS_ROOT", "PS3_HDD0_ROOT", "PS3_HDD1_ROOT"):
            env[name] = str(generated)
        status, result = run_session([str(executable), str(image)], cwd=ROOT, env=env,
                                     timeout=60, log_path=build_dir / "smoke.log")
        log = (build_dir / "smoke.log").read_text(errors="replace")
        print(log)
        if status != 0 or "[smoke] PASS" not in log:
            raise ValueError(f"Synthetic boot failed; see {build_dir / 'smoke.log'}")
        for marker in ("ELF loaded", "function table registered", "HLE initialized",
                       "filesystem registered", "first guest dispatch", "first guest frame presented"):
            if f"[milestone] {marker}" not in log:
                raise ValueError(f"Missing boot milestone: {marker}")
        if sys.platform.startswith("linux"):
            if "[override-test] PASS" not in log:
                raise ValueError("Registered override was not reached or failed TOC preservation")
            stall_env = dict(env, TB_DIAGNOSTIC_STALL="1")
            status, result = run_session([str(executable), str(image)], cwd=ROOT, env=stall_env,
                                         timeout=1, log_path=build_dir / "stall.log", request_snapshot=True)
            stall_log = (build_dir / "stall.log").read_text(errors="replace")
            if status != 124 or not result["timed_out"] or "reason=snapshot requested" not in stall_log:
                raise ValueError("Stall timeout or Linux diagnostic snapshot failed")
            for marker in ("milestone=diagnostic test stall", "cia=0x00010200", "hle_nid=0x1CF98800"):
                if marker not in stall_log:
                    raise ValueError(f"Missing timeout evidence: {marker}")
            status, _ = run_session([str(executable), str(generated / "missing.elf")], cwd=ROOT,
                                    env=env, timeout=5, log_path=build_dir / "load-failure.log")
            failure = (build_dir / "load-failure.log").read_text(errors="replace")
            if status != 1 or "reason=ELF load failed" not in failure:
                raise ValueError("ELF startup-failure diagnostics failed")
            print("Override, timeout snapshot, and startup-failure checks passed.")
        print("Synthetic boot passed. This does not establish Tank Battles compatibility.")
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        parser.exit(1, f"error: {error}\n")
    except subprocess.CalledProcessError as error:
        return error.returncode if error.returncode > 0 else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
