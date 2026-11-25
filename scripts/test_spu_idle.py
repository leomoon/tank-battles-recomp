#!/usr/bin/env python3
"""Linux standalone idle-wait and raw-worker stop tests; CTest also covers Windows wake races."""
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SDK = ROOT / "external/ps3recomp"

with tempfile.TemporaryDirectory(prefix="tank-battles-spu-idle-") as directory:
    executable = Path(directory) / "test"
    subprocess.run(["cc", "-std=gnu17", "-O2", "-UNDEBUG",
                    "-I" + str(SDK / "include"),
                    "-I" + str(SDK / "runtime/spu"),
                    "-I" + str(SDK / "runtime/platform"),
                    str(ROOT / "tests/test_spu_idle.c"),
                    str(SDK / "runtime/platform/win32_compat.c"),
                    "-pthread", "-o", str(executable)], check=True)
    subprocess.run([str(executable)], check=True, timeout=15)
    spu = SDK / "runtime/spu"
    sources = ["spu_channels.c", "spu_drain.c", "spu_workload.c", "spu_lockstep.c",
               "spu_coherency.c", "spurs_policy.c", "spurs_job.c", "spu_tsp_weak.c",
               "spu_vm_pagemap.c", "spurs_policy_blob_weak.c", "spu_interp.c"]
    subprocess.run(["cc", "-std=gnu17", "-O1", "-UNDEBUG",
                    "-I" + str(SDK / "include"), "-I" + str(spu),
                    "-I" + str(SDK / "runtime/platform"),
                    str(ROOT / "tests/test_spu_idle_stop.c"),
                    *[str(spu / name) for name in sources],
                    str(SDK / "runtime/platform/win32_compat.c"),
                    "-pthread", "-lm", "-o", str(executable)], check=True)
    subprocess.run([str(executable)], check=True, timeout=30, cwd=directory)
print("SPU idle identity, work slots, mailbox, stop, event and wait races passed")
