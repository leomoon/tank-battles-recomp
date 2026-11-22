"""Bounded cold-cache CMake/CRT check before the full packaged player build."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import tempfile
import time

from install_layout import Toolchain


def verify(toolchain: Toolchain, work: Path, timeout: float = 240) -> None:
    work = Path(work).resolve()
    work.mkdir(parents=True, exist_ok=True)
    # A new directory on every invocation prevents a warm compiler cache from
    # hiding a broken first link. Keep failures and their logs for diagnosis.
    probe = Path(tempfile.mkdtemp(prefix="compiler probe ", dir=work))
    source = probe / "source"
    source.mkdir()
    (source / "CMakeLists.txt").write_text('''cmake_minimum_required(VERSION 3.20)
project(CompilerProbe LANGUAGES C CXX)
add_executable(probe_c main.c)
add_executable(probe_cxx main.cpp)
target_compile_features(probe_cxx PRIVATE cxx_std_20)
find_package(Threads REQUIRED)
target_link_libraries(probe_cxx PRIVATE Threads::Threads)
''')
    (source / "main.c").write_text('''#include <stdlib.h>
int main(void) { int *p = malloc(sizeof(int)); if (!p) return 1;
*p = 42; int result = *p != 42; free(p); return result; }
''')
    (source / "main.cpp").write_text('''#include <string>
#include <thread>
#include <stdexcept>
int main() { std::string text;
std::thread worker([&] { text = "runtime ready"; }); worker.join();
try { throw std::runtime_error(text); }
catch (const std::exception &e) { return std::string(e.what()) != "runtime ready"; }
return 1; }
''')
    build = probe / "build"
    env = toolchain.environment(os.environ, probe / "cache")
    suffix = ".exe" if toolchain.target == "windows" else ""
    commands = [
        [str(toolchain.cmake), "-S", str(source), "-B", str(build), "-G", "Ninja",
         f"-DCMAKE_MAKE_PROGRAM={toolchain.ninja}",
         f"-DCMAKE_TOOLCHAIN_FILE={toolchain.toolchain_file}", "-DCMAKE_BUILD_TYPE=Release"],
        [str(toolchain.cmake), "--build", str(build), "--parallel", "2"],
        [str(build / ("probe_c" + suffix))],
        [str(build / ("probe_cxx" + suffix))],
    ]
    deadline = time.monotonic() + timeout
    log = probe / "compiler.log"
    with log.open("w", encoding="utf-8") as output:
        for command in commands:
            print(f"[verify] cold compiler check: {Path(command[0]).name} (log: {log})", flush=True)
            output.write(repr(command) + "\n")
            output.flush()
            process = subprocess.Popen(command, cwd=probe, env=env, stdin=subprocess.DEVNULL,
                                       stdout=output, stderr=subprocess.STDOUT,
                                       start_new_session=os.name != "nt")
            try:
                status = process.wait(timeout=max(0.01, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                if os.name == "nt":
                    try:
                        subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       creationflags=subprocess.CREATE_NO_WINDOW, timeout=10)
                    except (OSError, subprocess.TimeoutExpired):
                        pass
                    if process.poll() is None:
                        process.kill()
                else:
                    import signal
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                process.wait()
                raise TimeoutError(f"Cold compiler check exceeded {timeout}s; see {log}") from None
            if status:
                raise RuntimeError(f"Cold compiler check failed (exit {status}); see {log}\n"
                                   + log.read_text(errors="replace")[-4000:])
    print("PASS: cold-cache C/C++ configure, link, allocation, threads and exceptions", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--toolchain", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=240)
    args = parser.parse_args()
    verify(Toolchain(args.toolchain), args.work, args.timeout)
