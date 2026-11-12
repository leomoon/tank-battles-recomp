#!/usr/bin/env python3
"""Prepare, build, and run the port using locally supplied game data."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

if sys.version_info < (3, 11):
    raise SystemExit("Python 3.11 or newer is required.")
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def resolve(value: str) -> Path:
    path = Path(value).expanduser()
    return (ROOT / path).resolve() if not path.is_absolute() else path.resolve()


def run(command: list[str], **kwargs) -> None:
    print("+ " + shlex.join(map(str, command)), flush=True)
    subprocess.run(command, cwd=ROOT, check=True, **kwargs)


def sdk_path(args, config) -> Path:
    sdk = resolve(args.sdk or config["paths"]["sdk"])
    if not (sdk / "tools/ppu_loader.py").is_file():
        raise ValueError("Missing ps3recomp dependency. Run git submodule update --init --recursive, or pass --sdk /path/to/ps3recomp.")
    return sdk


def elf_path(args, config) -> Path:
    elf = resolve(args.elf or config["game"]["elf"])
    if not elf.is_file():
        raise ValueError(f"Missing decrypted game executable: {elf}. Supply your purchased game's decrypted dump or pass --elf /path/to/EBOOT.elf.")
    with elf.open("rb") as file:
        header = file.read(20)
    if header[:7] != b"\x7fELF\x02\x02\x01" or header[18:20] != b"\x00\x15":
        raise ValueError(f"Expected a decrypted ELF64 big-endian PPU executable, not an encrypted EBOOT.BIN: {elf}")
    return elf


def tool(value: str) -> str:
    executable = shutil.which(value)
    if not executable:
        raise ValueError(f"Required tool not found: {value}")
    return str(Path(executable).resolve())


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return number


def prepare(args, config) -> None:
    sdk, elf = sdk_path(args, config), elf_path(args, config)
    analysis = resolve(config["paths"]["analysis"])
    analysis.mkdir(parents=True, exist_ok=True)
    run([sys.executable, str(sdk / "tools/ppu_loader.py"), str(elf), "-o", str(analysis)])
    functions = analysis / "functions.json"
    run([sys.executable, str(sdk / "tools/find_functions.py"), str(elf),
         "--seed-json", str(analysis / f"{elf.stem}.functions.json"), "--output", str(functions)])
    # SPU scanning and lifting are handled by CMake's PORT_SPU_INPUTS pipeline.
    if args.analyze_only:
        return
    jobs = args.jobs or config["recompiler"]["jobs"]
    run([sys.executable, str(sdk / "tools/ppu_lifter.py"), str(elf),
         "--functions", str(functions),
         "--hle-stubs", str(analysis / f"{elf.stem}.imports.json"),
         "--output", str(resolve(config["paths"]["recompiled"])),
         "--jobs", str(jobs), "--chunk-lines", str(config["recompiler"]["chunk_lines"])])


def configure(args, config) -> None:
    sdk, elf = sdk_path(args, config), elf_path(args, config)
    build = resolve(args.build_dir or config["paths"]["build"])
    recomp = resolve(args.recomp_dir or config["paths"]["recompiled"])
    command = [tool(args.cmake), "-S", str(ROOT), "-B", str(build), "-G", "Ninja",
               f"-DCMAKE_MAKE_PROGRAM={tool(args.ninja)}",
               f"-DPS3RECOMP_DIR={sdk}", f"-DRECOMP_DIR={recomp}",
               f"-DPORT_SPU_INPUTS={elf}", f"-DCMAKE_BUILD_TYPE={args.build_type}"]
    if sys.platform.startswith("linux") and args.backend != "auto":
        command.append(f"-DPS3RECOMP_RSX_VULKAN={'ON' if args.backend == 'vulkan' else 'OFF'}")
    extra = args.cmake_args
    command.extend(extra[1:] if extra[:1] == ["--"] else extra)
    run(command)


def build(args, config) -> None:
    run([tool(args.cmake), "--build", str(resolve(args.build_dir or config["paths"]["build"])),
         "--target", "TankBattlesRecomp", "--parallel", str(args.jobs)])


def launch(args, config) -> None:
    elf = elf_path(args, config)
    build_dir = resolve(args.build_dir or config["paths"]["build"])
    executable = build_dir / ("TankBattlesRecomp.exe" if os.name == "nt" else "TankBattlesRecomp")
    if not executable.is_file():
        raise ValueError(f"Port executable not found: {executable}. Run configure and build first.")
    env = os.environ.copy()
    env.setdefault("PS3_TITLE", config["game"]["title"] + " Recomp")
    for key, setting in (("PS3_VFS_ROOT", "vfs_root"), ("PS3_HDD0_ROOT", "hdd0_root"),
                         ("PS3_HDD1_ROOT", "hdd1_root")):
        env.setdefault(key, str(resolve(config["paths"][setting])))
    # Cache files and saves belong in the local dump tree, never the source SDK.
    Path(env["PS3_HDD1_ROOT"]).mkdir(parents=True, exist_ok=True)
    run([str(executable), str(elf)], env=env)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.toml", help="TOML configuration, relative to repository root")
    commands = parser.add_subparsers(dest="command", required=True)
    prep = commands.add_parser("prepare", help="Analyze and translate your decrypted game executable")
    prep.add_argument("--sdk")
    prep.add_argument("--elf")
    prep.add_argument("--analyze-only", action="store_true")
    prep.add_argument("--jobs", type=positive_int)
    conf = commands.add_parser("configure", help="Configure the native build, including embedded SPU lifting")
    conf.add_argument("--sdk")
    conf.add_argument("--elf")
    conf.add_argument("--build-dir")
    conf.add_argument("--recomp-dir")
    conf.add_argument("--cmake", default="cmake")
    conf.add_argument("--ninja", default="ninja")
    conf.add_argument("--backend", choices=("auto", "vulkan", "null"), default="auto", help="Linux renderer; auto uses the CMake default or existing cache")
    conf.add_argument("--build-type", default="RelWithDebInfo")
    conf.add_argument("cmake_args", nargs=argparse.REMAINDER, help="Extra CMake arguments after --")
    compile_command = commands.add_parser("build", help="Compile the configured native executable")
    compile_command.add_argument("--build-dir")
    compile_command.add_argument("--cmake", default="cmake")
    compile_command.add_argument("--jobs", type=positive_int, default=2)
    launch_command = commands.add_parser("run", help="Run the experimental port with your local game dump")
    launch_command.add_argument("--elf")
    launch_command.add_argument("--build-dir")
    args = parser.parse_args()
    try:
        with resolve(args.config).open("rb") as file:
            config = tomllib.load(file)
        {"prepare": prepare, "configure": configure, "build": build, "run": launch}[args.command](args, config)
    except (OSError, ValueError, KeyError) as error:
        parser.exit(2, f"error: {error}\n")
    except subprocess.CalledProcessError as error:
        return error.returncode if error.returncode > 0 else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
