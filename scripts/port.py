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
import json

from boot_session import run_session
from install_layout import Layout, Toolchain

if sys.version_info < (3, 11):
    raise SystemExit("Python 3.11 or newer is required.")
import tomllib

ROOT = Path(__file__).resolve().parents[1]  # Read-only resources in an installed package.
LAYOUT = Layout(ROOT)


def resolve(value: str) -> Path:
    """User paths (game data, translations, builds, logs) live in the workspace."""
    return LAYOUT.resolve(value)


def execute(command, *, cwd, env=None):
    subprocess.run(command, cwd=cwd, env=env, check=True)


# Front ends (scripts/setup_engine.py) replace this to stream and own child processes.
EXECUTE = execute


def run(command: list[str], env=None) -> None:
    print("+ " + shlex.join(map(str, command)), flush=True)
    LAYOUT.workspace.mkdir(parents=True, exist_ok=True)
    EXECUTE([str(part) for part in command], cwd=LAYOUT.workspace, env=env)


def selected_runtime(args):
    """The runtime bundle and builder toolchain for this build, if any.

    An installed package always uses its own. A checkout uses them only when
    selected, and otherwise builds the runtime from source as before."""
    bundle = getattr(args, "runtime_bundle", None)
    bundle = resolve(bundle) if bundle else LAYOUT.runtime_bundle()
    chosen = getattr(args, "toolchain", None)
    chosen = resolve(chosen) if chosen else LAYOUT.toolchain()
    toolchain = Toolchain(chosen) if chosen else None
    if toolchain and not bundle:
        raise ValueError("--toolchain builds players from a runtime bundle; also pass --runtime-bundle")
    if toolchain and toolchain.target != ("windows" if os.name == "nt" else "linux"):
        raise ValueError(f"The selected toolchain runs on {toolchain.target}, not this system")
    # Only the Visual Studio route (Windows bundle without a builder toolchain) is multi-config.
    args.multi_config = bool(os.name == "nt" and bundle and not toolchain)
    # Their identities join setup's configuration signature.
    args.selected_runtime_bundle, args.selected_toolchain = bundle, toolchain.root if toolchain else None
    return bundle, toolchain


def build_environment(toolchain):
    return toolchain.environment(os.environ, LAYOUT.workspace / "cache/zig") if toolchain else None


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
    jobs = getattr(args, "lift_jobs", None) or args.jobs or config["recompiler"]["jobs"]
    run([sys.executable, str(sdk / "tools/ppu_lifter.py"), str(elf),
         "--functions", str(functions),
         "--hle-stubs", str(analysis / f"{elf.stem}.imports.json"),
         "--output", str(resolve(config["paths"]["recompiled"])),
         "--jobs", str(jobs), "--chunk-lines", str(config["recompiler"]["chunk_lines"])])
    from patch_network_events import patch
    patch(resolve(config["paths"]["recompiled"]))


def forget_moved_configuration(build: Path, source: Path) -> None:
    """A build directory configured from resources that have since moved cannot
    be reconfigured in place. Its translations stay; CMake starts over."""
    cache = build / "CMakeCache.txt"
    if not cache.is_file():
        return
    for line in cache.read_text(errors="replace").splitlines():
        if line.startswith("CMAKE_HOME_DIRECTORY:INTERNAL="):
            recorded = Path(line.split("=", 1)[1])
            if recorded.resolve() != source.resolve():
                print(f"[configure] resources moved from {recorded}; recreating the build configuration.", flush=True)
                cache.unlink()
                shutil.rmtree(build / "CMakeFiles", ignore_errors=True)
            return


def configure(args, config) -> None:
    sdk, elf = sdk_path(args, config), elf_path(args, config)
    build = resolve(args.build_dir or config["paths"]["build"])
    recomp = resolve(args.recomp_dir or config["paths"]["recompiled"])
    bundle, toolchain = selected_runtime(args)
    environment = None
    if bundle:
        manifest = json.loads((bundle / "manifest.json").read_text())
        identity = LAYOUT.source_commit() if not args.runtime_source else None
        expected = args.runtime_source or identity
        if manifest.get("source_commit") != expected:
            raise ValueError(f"Runtime bundle source mismatch: selected {expected}, bundle is {manifest.get('source_commit')}")
        if args.runtime_source and not LAYOUT.packaged:
            print(f"[runtime] Explicitly selected CI source {expected}.", flush=True)
        run([sys.executable, str(bundle / "tools/runtime_bundle.py"), "verify", str(bundle), "--source", expected])
        abi = build / "runtime-abi-check"
        run([sys.executable, str(ROOT / "scripts/runtime_abi.py"), "--sdk", str(sdk),
             "--output", str(abi), "--check-title-header", str(recomp / "ppu_recomp.h")])
        if json.loads((abi / "abi.json").read_text())["header_sha256"] != manifest.get("abi_sha256"):
            raise ValueError("Local lifter/title ABI differs from the selected runtime bundle")
        forget_moved_configuration(build, ROOT / "cmake/player-consumer")
        common = ["-S", str(ROOT / "cmake/player-consumer"), "-B", str(build), f"-DTB_PORT_ROOT={ROOT}",
                  f"-DTB_RUNTIME_ROOT={bundle}", f"-DTB_RUNTIME_EXPECTED_SOURCE={expected}",
                  f"-DPS3RECOMP_DIR={sdk}", f"-DRECOMP_DIR={recomp}",
                  f"-DPORT_SPU_INPUTS={elf}", f"-DPython3_EXECUTABLE={sys.executable}"]
        if toolchain:
            command = [str(toolchain.cmake), *common, "-G", "Ninja", f"-DCMAKE_MAKE_PROGRAM={toolchain.ninja}",
                       f"-DCMAKE_TOOLCHAIN_FILE={toolchain.toolchain_file}", f"-DCMAKE_BUILD_TYPE={args.build_type}"]
            environment = build_environment(toolchain)
            if toolchain.target == "windows":
                print("[configure] Checking the bundled Zig compiler. Its first link can take longer while the cache is populated.",
                      flush=True)
        elif os.name == "nt":
            command = [tool(args.cmake), *common, "-A", "x64", "-T", "ClangCL", "-DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreaded"]
        else:
            command = [tool(args.cmake), *common, "-G", "Ninja", f"-DCMAKE_MAKE_PROGRAM={tool(args.ninja)}",
                       f"-DCMAKE_BUILD_TYPE={args.build_type}"]
    else:
        forget_moved_configuration(build, ROOT)
        command = [tool(args.cmake), "-S", str(ROOT), "-B", str(build), "-G", "Ninja",
                   f"-DCMAKE_MAKE_PROGRAM={tool(args.ninja)}",
               f"-DPS3RECOMP_DIR={sdk}", f"-DRECOMP_DIR={recomp}",
               f"-DPORT_SPU_INPUTS={elf}", f"-DCMAKE_BUILD_TYPE={args.build_type}"]
        if sys.platform.startswith("linux") and args.backend != "auto":
            command.append(f"-DPS3RECOMP_RSX_VULKAN={'ON' if args.backend == 'vulkan' else 'OFF'}")
    extra = args.cmake_args
    command.extend(extra[1:] if extra[:1] == ["--"] else extra)
    run(command, env=environment)


def build(args, config) -> None:
    _, toolchain = selected_runtime(args)
    cmake = str(toolchain.cmake) if toolchain else tool(args.cmake)
    command = [cmake, "--build", str(resolve(args.build_dir or config["paths"]["build"])),
               "--target", "TankBattlesRecomp", "PlayTankBattles", "--parallel", str(args.jobs)]
    if args.multi_config:
        command[3:3] = ["--config", getattr(args, "build_type", "Release")]
    run(command, env=build_environment(toolchain))


def setup(args, config) -> int:
    """The console front end of scripts/setup_engine.py."""
    from setup_engine import ConsoleReporter, log_path, run
    return run(args, config, ConsoleReporter(log_path(LAYOUT.workspace)))


def setup_tools(args):
    _, toolchain = selected_runtime(args)
    if toolchain:
        # The packaged route needs nothing installed besides the Vulkan driver.
        from toolchain import verify
        try:
            identity = verify(toolchain.root)
            return [dict(name="Builder toolchain", path=str(toolchain.root), status="ok", action="",
                         detail=identity.get("zig"))]
        except (OSError, ValueError) as error:
            return [dict(name="Builder toolchain", path=str(toolchain.root), status="error",
                         action=f"Download the builder again ({error}).")]
    checks = []
    requirements = [("CMake", args.cmake, "Install CMake; see README build dependencies.")]
    if not args.multi_config:
        requirements.append(("Ninja", args.ninja, "Install Ninja; see README build dependencies."))
    compiler = "clang-cl" if os.name == "nt" else os.environ.get("CXX", "c++")
    requirements.append(("C++ compiler", compiler, "Install Visual Studio Build Tools with the ClangCL component." if os.name == "nt"
                         else "Install a C17/C++20 compiler and the Linux development packages listed in README."))
    for name, executable, action in requirements:
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


def player_executable(args, config) -> Path:
    build_dir = resolve(args.build_dir or config["paths"]["build"])
    executable = build_dir / ("TankBattlesRecomp.exe" if os.name == "nt" else "TankBattlesRecomp")
    if os.name == "nt" and not executable.is_file():
        executable = build_dir / "Release/TankBattlesRecomp.exe"
    if not executable.is_file():
        raise ValueError(f"Port executable not found: {executable}. Run configure and build first.")
    return executable


def rpcn_account(args, config) -> int:
    flag = "--rpcn-create-account" if args.action == "create" else "--rpcn-check"
    return subprocess.run([str(player_executable(args, config)), flag, str(resolve(args.profile))],
                          cwd=LAYOUT.workspace).returncode


def launch(args, config) -> int:
    elf = elf_path(args, config)
    executable = player_executable(args, config)
    env = launch_environment(config, args.paramsfo)
    log_path = resolve(args.log) if args.log else None
    if args.log and not log_path.is_relative_to(resolve("analysis")):
        raise ValueError("--log must be inside the workspace's analysis/ directory")
    if not args.console and log_path is None:
        directory = Path(env["TB_LOG_DIR"]).expanduser() if env.get("TB_LOG_DIR") else Path(env.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "tank-battles-recomp/logs"
        log_path = directory / (datetime.now().strftime("session-%Y%m%d-%H%M%S-%f") + f"-{os.getpid()}.log")
    if log_path:
        print(f"[session] log: {log_path}", flush=True)
    if args.console:
        env["TB_LOG_CONSOLE"] = "1"
    command = [str(executable), str(elf)]
    if args.rpcn_profile:
        command.extend(["--rpcn-profile", str(resolve(args.rpcn_profile))])
    LAYOUT.workspace.mkdir(parents=True, exist_ok=True)
    status, _ = run_session(command, cwd=LAYOUT.workspace, env=env,
                            timeout=args.timeout, log_path=log_path, request_snapshot=True,
                            shutdown_grace=5 if args.rpcn_profile else 2)
    return status


def add_setup_arguments(parser) -> None:
    """Shared by `port.py setup` and scripts/setup_engine.py."""
    sources = parser.add_mutually_exclusive_group()
    sources.add_argument("--pkg", help="Original purchased Tank Battles NPUB30154 PKG")
    sources.add_argument("--game-dir", help="Original installed NPUB30154 folder containing EBOOT.BIN")
    parser.add_argument("--unlock", help="Purchased full-game unlock EDAT")
    parser.add_argument("--rap", help="Your purchased unlock's 16-byte RAP (license-2 EDAT only)")
    parser.add_argument("--keys", default=os.environ.get("PS3_KEYS"), help="Your scetool-format SELF keys; defaults to PS3_KEYS or game/keys")
    parser.add_argument("--rpcs3-source", help="Optional existing local RPCS3 source as the SELF key provider")
    parser.add_argument("--rpcs3", help="Your RPCS3 executable/AppImage; decrypts only a temporary copy of EBOOT.BIN")
    parser.add_argument("--elf", help="Manually decrypted EBOOT.elf, validated and copied into the installed game")
    parser.add_argument("--check", action="store_true", help="Report all missing/mismatched inputs and actions without installing or building")
    parser.add_argument("--allow-unknown-version", action="store_true", help="Explicitly accept untested hashes with warnings; never bypass file/title/ELF structure checks")
    parser.add_argument("--restart", action="store_true", help="Regenerate translations before the incremental native build; keeps purchased inputs")
    parser.add_argument("--import-only", action="store_true", help="Import, decrypt and prepare the unlock without translation or compilation")
    parser.add_argument("--build-dir")
    parser.add_argument("--runtime-bundle", help="Verified game-free runtime bundle matching these scripts")
    parser.add_argument("--toolchain", help="Staged builder toolchain (scripts/toolchain.py stage); builds with only its tools")
    parser.add_argument("--runtime-source", help="Exact CI source commit recorded by the selected runtime bundle")
    parser.add_argument("--jobs", type=positive_int)
    parser.add_argument("--cmake", default="cmake")
    parser.add_argument("--ninja", default="ninja")
    parser.add_argument("--backend", choices=("auto", "vulkan", "null"), default="auto")
    parser.add_argument("--build-type", default="Release")
    parser.add_argument("--output", help="Movable player folder to create or update (default for an installed builder: ~/Games/Tank Battles Recomp)")
    parser.add_argument("--migrate-saves-from", help="Copy saves and settings from an older game folder into a new player folder, without overwriting")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.toml", help="TOML configuration; relative paths look in the workspace, then the resources")
    parser.add_argument("--workspace", help="Writable directory for game data, translations, builds and logs (default: the checkout, or a per-user folder for an installed builder)")
    commands = parser.add_subparsers(dest="command", required=True)
    install = commands.add_parser("setup", help="Import your original game, decrypt, translate, configure and build")
    add_setup_arguments(install)
    prep = commands.add_parser("prepare", help="Analyze and translate your decrypted game executable")
    prep.add_argument("--sdk")
    prep.add_argument("--elf")
    prep.add_argument("--analyze-only", action="store_true")
    prep.add_argument("--jobs", type=positive_int)
    conf = commands.add_parser("configure", help="Configure the native build, including embedded SPU lifting")
    conf.add_argument("--sdk")
    conf.add_argument("--elf")
    conf.add_argument("--build-dir")
    conf.add_argument("--runtime-bundle", help="Verified game-free runtime bundle matching these scripts")
    conf.add_argument("--toolchain", help="Staged builder toolchain (scripts/toolchain.py stage); builds with only its tools")
    conf.add_argument("--runtime-source", help="Exact CI source commit recorded by the selected runtime bundle")
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
    compile_command.add_argument("--runtime-bundle")
    compile_command.add_argument("--toolchain")
    compile_command.add_argument("--build-type", default="Release")
    launch_command = commands.add_parser("run", help="Run the experimental port with your local game dump")
    launch_command.add_argument("--elf")
    launch_command.add_argument("--paramsfo", help="Local PARAM.SFO (defaults to config.toml)")
    launch_command.add_argument("--build-dir")
    launch_command.add_argument("--rpcn-profile", help="Opt in to RPCN using a private profile file")
    launch_command.add_argument("--timeout", type=positive_seconds, help="Maximum run time in seconds; timeout exits with status 124")
    logging = launch_command.add_mutually_exclusive_group()
    logging.add_argument("--log", help="Diagnostic log under analysis/; default: per-user state directory")
    logging.add_argument("--console", action="store_true", help="Print game output to the terminal instead of an automatic log")
    rpcn_command = commands.add_parser("rpcn", help="Create or check an account on a private RPCN server")
    rpcn_command.add_argument("action", choices=["create", "check"])
    rpcn_command.add_argument("profile", help="Private RPCN profile file")
    rpcn_command.add_argument("--build-dir")
    args = parser.parse_args()
    global LAYOUT
    try:
        LAYOUT = Layout(ROOT, args.workspace)
        LAYOUT.verify()
        config = LAYOUT.load_config(args.config)
        result = {"setup": setup, "prepare": prepare, "configure": configure, "build": build,
                  "run": launch, "rpcn": rpcn_account}[args.command](args, config)
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
