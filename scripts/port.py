#!/usr/bin/env python3
"""Prepare, build, and run the port using locally supplied game data."""

from __future__ import annotations

import argparse
from datetime import datetime
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

from boot_session import run_session

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
        raise ValueError("Missing ps3recomp source. Restore external/ps3recomp from this repository, or pass --sdk /path/to/ps3recomp.")
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


def positive_seconds(value: str) -> float:
    import math
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be finite and greater than zero")
    return number


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


def setup(args, config) -> None:
    from setup_game import check_game
    from setup_inputs import print_checks, require_valid
    if args.check:
        checks = check_game(args, config, ROOT, resolve)
        if not args.import_only:
            tools = setup_tools(args)
            print_checks(tools)
            checks.extend(tools)
        require_valid(checks)
        print("[setup] Check passed. Purchased inputs were not modified.")
        return
    from setup_pipeline import setup_lock
    with setup_lock(resolve(config["paths"]["gamedata_root"])):
        setup_install(args, config)


def setup_install(args, config):
    from setup_game import install_game
    from setup_inputs import print_checks, require_valid
    if not args.import_only:
        tools = setup_tools(args)
        print_checks(tools)
        require_valid(tools)
    args.elf = str(install_game(args, config, ROOT, resolve))
    if args.import_only:
        return
    # Reuse the same translation and build paths as the individual commands.
    args.sdk = None
    args.analyze_only = False
    args.recomp_dir = None
    args.cmake_args = []
    args.jobs = args.jobs or config["recompiler"]["jobs"]
    from setup_pipeline import run_pipeline
    run_pipeline(args, config, resolve, prepare, configure, build)
    suffix = " --build-dir " + shlex.quote(args.build_dir) if args.build_dir else ""
    print("[setup] Ready. Launch with: python scripts/port.py run" + suffix)


def setup_tools(args):
    checks = []
    for name, executable, action in (("CMake", args.cmake, "Install CMake and Ninja; see README Linux build dependencies."),
                                     ("Ninja", args.ninja, "Install Ninja; see README Linux build dependencies."),
                                     ("C++ compiler", os.environ.get("CXX", "c++"), "Install a C17/C++20 compiler and the Linux development packages listed in README.")):
        found = shutil.which(executable)
        checks.append(dict(name=name, path=found or executable, status="ok" if found else "error", action="" if found else action))
    return checks


def launch_environment(config, paramsfo=None, mounts=None):
    env = os.environ.copy()
    if mounts:
        env.update(mounts)
    env.setdefault("PS3_TITLE", config["game"]["title"] + " Recomp")
    for key, setting in (("PS3_VFS_ROOT", "vfs_root"), ("PS3_HDD0_ROOT", "hdd0_root"),
                         ("PS3_HDD1_ROOT", "hdd1_root"), ("PS3_GAMEDATA_ROOT", "gamedata_root"),
                         ("PS3_DEV_FLASH", "flash_root")):
        env.setdefault(key, str(resolve(config["paths"][setting])))
    # Cache files and saves belong in the local dump tree, never the source SDK.
    Path(env["PS3_HDD1_ROOT"]).mkdir(parents=True, exist_ok=True)
    sfo = resolve(paramsfo or env.get("PS3_PARAM_SFO") or config["game"]["paramsfo"])
    if not sfo.is_file():
        raise ValueError(f"Missing game metadata: {sfo}")
    env["PS3_PARAM_SFO"] = str(sfo)
    env.setdefault("TB_EXPECTED_TITLE_ID", config["game"]["title_id"])
    env.setdefault("PS3_STDIO_UNBUFFERED", "1")
    env.setdefault("PS3_VERBOSE", "0")
    return env


def launch(args, config) -> int:
    elf = elf_path(args, config)
    build_dir = resolve(args.build_dir or config["paths"]["build"])
    executable = build_dir / ("TankBattlesRecomp.exe" if os.name == "nt" else "TankBattlesRecomp")
    if not executable.is_file():
        raise ValueError(f"Port executable not found: {executable}. Run configure and build first.")
    env = launch_environment(config, args.paramsfo)
    log_path = resolve(args.log) if args.log else None
    if args.log and not log_path.is_relative_to((ROOT / "analysis").resolve()):
        raise ValueError("--log must be inside the repository's analysis/ directory")
    if not args.console and log_path is None:
        directory = Path(env["TB_LOG_DIR"]).expanduser() if env.get("TB_LOG_DIR") else Path(env.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "tank-battles-recomp/logs"
        log_path = directory / (datetime.now().strftime("session-%Y%m%d-%H%M%S-%f") + f"-{os.getpid()}.log")
    if log_path:
        print(f"[session] log: {log_path}", flush=True)
    if args.console:
        env["TB_LOG_CONSOLE"] = "1"
    status, _ = run_session([str(executable), str(elf)], cwd=ROOT, env=env,
                            timeout=args.timeout, log_path=log_path, request_snapshot=True)
    return status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.toml", help="TOML configuration, relative to repository root")
    commands = parser.add_subparsers(dest="command", required=True)
    install = commands.add_parser("setup", help="Import your original game, decrypt, translate, configure and build")
    sources = install.add_mutually_exclusive_group()
    sources.add_argument("--pkg", help="Original purchased Tank Battles NPUB30154 PKG")
    sources.add_argument("--game-dir", help="Original installed NPUB30154 folder containing EBOOT.BIN")
    install.add_argument("--unlock", help="Purchased full-game unlock EDAT")
    install.add_argument("--rap", help="Your purchased unlock's 16-byte RAP (license-2 EDAT only)")
    install.add_argument("--keys", default=os.environ.get("PS3_KEYS"), help="Your scetool-format SELF keys; defaults to PS3_KEYS or game/keys")
    install.add_argument("--rpcs3-source", help="Optional existing local RPCS3 source as the SELF key provider")
    install.add_argument("--rpcs3", help="Your RPCS3 executable/AppImage; decrypts only a temporary copy of EBOOT.BIN")
    install.add_argument("--elf", help="Manually decrypted EBOOT.elf, validated and copied into the installed game")
    install.add_argument("--check", action="store_true", help="Report all missing/mismatched inputs and actions without installing or building")
    install.add_argument("--allow-unknown-version", action="store_true", help="Explicitly accept untested hashes with warnings; never bypass file/title/ELF structure checks")
    install.add_argument("--restart", action="store_true", help="Regenerate translations before the incremental native build; keeps purchased inputs")
    install.add_argument("--import-only", action="store_true", help="Import, decrypt and prepare the unlock without translation or compilation")
    install.add_argument("--build-dir")
    install.add_argument("--jobs", type=positive_int)
    install.add_argument("--cmake", default="cmake")
    install.add_argument("--ninja", default="ninja")
    install.add_argument("--backend", choices=("auto", "vulkan", "null"), default="auto")
    install.add_argument("--build-type", default="Release")
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
    conf.add_argument("--build-type", default="Release")
    conf.add_argument("cmake_args", nargs=argparse.REMAINDER, help="Extra CMake arguments after --")
    compile_command = commands.add_parser("build", help="Compile the configured native executable")
    compile_command.add_argument("--build-dir")
    compile_command.add_argument("--cmake", default="cmake")
    compile_command.add_argument("--jobs", type=positive_int, default=2)
    launch_command = commands.add_parser("run", help="Run the experimental port with your local game dump")
    launch_command.add_argument("--elf")
    launch_command.add_argument("--paramsfo", help="Local PARAM.SFO (defaults to config.toml)")
    launch_command.add_argument("--build-dir")
    launch_command.add_argument("--timeout", type=positive_seconds, help="Maximum run time in seconds; timeout exits with status 124")
    logging = launch_command.add_mutually_exclusive_group()
    logging.add_argument("--log", help="Diagnostic log under analysis/; default: per-user state directory")
    logging.add_argument("--console", action="store_true", help="Print game output to the terminal instead of an automatic log")
    args = parser.parse_args()
    try:
        with resolve(args.config).open("rb") as file:
            config = tomllib.load(file)
        result = {"setup": setup, "prepare": prepare, "configure": configure, "build": build, "run": launch}[args.command](args, config)
        return result if isinstance(result, int) else 0
    except (OSError, ValueError, KeyError) as error:
        parser.exit(2, f"error: {error}\n")
    except ModuleNotFoundError as error:
        parser.exit(2, f"error: Missing Python dependency {error.name}. Activate your venv and run: python -m pip install -r external/ps3recomp/requirements.txt cmake ninja\n")
    except subprocess.CalledProcessError as error:
        return error.returncode if error.returncode > 0 else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
