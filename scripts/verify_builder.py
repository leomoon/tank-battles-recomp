"""Check a downloaded, extracted builder on a clean machine, without game files.

Run with the builder's own Python, ideally with networking disabled and PATH
limited to the operating system:

    <builder>/toolchain/python/bin/python3 -E -s -B <builder>/scripts/verify_builder.py --work DIR

It verifies the builder's files, translates a synthetic PPU program, builds a
player and launcher with only the packaged toolchain, assembles a player
folder, then starts the launcher offline (the synthetic title runs until the
graphics device is needed) and checks the account interface and window.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import port  # noqa: E402
import setup_engine  # noqa: E402
import player_folder  # noqa: E402
from install_layout import Layout, Toolchain  # noqa: E402
from toolchain_probe import verify as verify_toolchain  # noqa: E402
from synthetic_content import write_sfo  # noqa: E402


def synthetic_title(work: Path) -> Path:
    """A game-free PPU program installed where setup would put a title."""
    title = work / "workspace/game/dev_hdd0/game/NPUB30154"
    (title / "USRDIR").mkdir(parents=True, exist_ok=True)
    elf = title / "USRDIR/EBOOT.elf"
    subprocess.run([sys.executable, str(port.ROOT / "external/ps3recomp/tools/make_smoke_elf.py"), "--out", str(elf),
                    "--header", str(work / "smoke-header.h")], check=True, capture_output=True)
    write_sfo(title / "PARAM.SFO", title_id="NPUB30154", category="HG")
    return elf


def build(work: Path, elf: Path, output: Path) -> Path:
    import argparse as parsing
    config = port.LAYOUT.load_config("config.toml")
    args = parsing.Namespace(elf=str(elf), restart=False, build_dir=None, jobs=None, build_type="Release", backend="auto",
                             cmake="cmake", ninja="ninja", cmake_args=[], runtime_bundle=None, runtime_source=None,
                             toolchain=None, sdk=None, recomp_dir=None, analyze_only=False, import_only=False,
                             check=False)

    def synthetic(args, config, reporter):
        from setup_inputs import require_valid
        from setup_pipeline import player_executable, run_pipeline
        reporter.stage("inputs", "started")
        checks = port.setup_tools(args) + [setup_engine.check_resources(),
                                           setup_engine.check_space(port.LAYOUT.workspace, config)]
        args.jobs = setup_engine.bounded_jobs(None, setup_engine.COMPILE_JOB_MB)
        require_valid(checks)
        reporter.stage("inputs", "done")
        run_pipeline(args, config, port.resolve, port.prepare, port.configure, port.build, notify=reporter.stage)
        build_dir = port.resolve(config["paths"]["build"])
        reporter.stage("assemble", "started")
        sources = player_folder.build_outputs(build_dir, args.multi_config)
        sources.update(player_folder.content_files(port.resolve(config["paths"]["vfs_root"]),
                                                   port.resolve(config["paths"]["hdd0_root"]) / "home/00000001/exdata",
                                                   "NPUB30154"))
        player_folder.assemble(output, sources, {"source_commit": port.LAYOUT.source_commit()})
        reporter.stage("assemble", "done")
        return player_executable(build_dir, args)

    stream = io.StringIO()
    reporter = setup_engine.JsonReporter(setup_engine.log_path(port.LAYOUT.workspace), stream=stream)
    status = setup_engine.run(args, config, reporter, setup=synthetic)
    events = [line for line in stream.getvalue().splitlines() if '"stage"' in line or '"result"' in line]
    print("\n".join(events))
    if status:
        log_tail = reporter.log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-80:]
        print("\n".join(log_tail), file=sys.stderr)
        raise SystemExit(f"Packaged synthetic build failed (status {status}); see {reporter.log_path}")
    return output / player_folder.launcher_name()


def launch(launcher: Path, window_driver: str = "dummy") -> None:
    folder = launcher.parent
    environment = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE",
                                                    "LOCALAPPDATA", "APPDATA", "XDG_RUNTIME_DIR",
                                                    "DISPLAY", "XAUTHORITY", "WAYLAND_DISPLAY") if key in os.environ}
    environment.update(SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy", PATH=os.defpath)
    status = subprocess.run([str(launcher), "--account", "status"], capture_output=True,
                            encoding="utf-8", errors="replace", env=environment, timeout=60)
    if status.returncode or "active=" not in status.stdout:
        raise SystemExit(f"Launcher account interface failed: {status.stdout}{status.stderr}")
    shots = folder.parent / "launcher-screens"
    shots.mkdir(exist_ok=True)
    window = subprocess.run([str(launcher)], capture_output=True, encoding="utf-8", errors="replace", timeout=60,
                            env=dict(environment, SDL_VIDEODRIVER=window_driver, TB_LAUNCHER_UI_SMOKE=str(shots)))
    drawn = len(list(shots.glob("screen-*.bmp")))
    if window.returncode or drawn != 6:
        raise SystemExit(f"Launcher captured {drawn}/6 screens (exit {window.returncode}): {window.stdout}{window.stderr}")
    if f"[launcher] SDL video driver: {window_driver}" not in window.stderr:
        raise SystemExit(f"Launcher did not use the requested {window_driver} backend: {window.stderr}")
    print(f"[verify] launcher rendered all six screens using {window_driver}")
    started = time.monotonic()
    offline = subprocess.run([str(launcher), "--offline"], capture_output=True,
                             encoding="utf-8", errors="replace", timeout=120, env=environment)
    logs = sorted((folder / "logs").glob("session-*.log"))
    text = logs[-1].read_text(errors="replace") if logs else ""
    if "[milestone] first guest dispatch" not in text:
        raise SystemExit(f"The synthetic player did not run its translated code (exit {offline.returncode}):\n"
                         f"{text[-4000:] if logs else 'no session log'}\n{offline.stdout}{offline.stderr}")
    print(f"[verify] launcher started the player, which ran translated code ({time.monotonic() - started:.1f}s)")


def audit_player(launcher: Path, source: str) -> None:
    folder = launcher.parent
    manifest = player_folder.read_manifest(folder)
    expected = {
        launcher.name,
        "player/" + player_folder.executable_name("TankBattlesRecomp"),
        player_folder.TITLE + "/PARAM.SFO",
        player_folder.TITLE + "/USRDIR/EBOOT.elf",
    }
    actual = {path.relative_to(folder).as_posix() for path in folder.rglob("*") if path.is_file()}
    if not manifest or manifest.get("source_commit") != source:
        raise SystemExit("Installed player provenance does not match the builder")
    if set(manifest["files"]) != expected or actual != expected | {player_folder.MANIFEST}:
        raise SystemExit(f"Unexpected synthetic player contents: {sorted(actual)}")
    for name, digest in manifest["files"].items():
        if player_folder.sha256(folder / name) != digest:
            raise SystemExit(f"Installed player checksum mismatch: {name}")
    print(f"[verify] player source {source}, checked {len(expected)} installed files")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--work", required=True, help="Writable directory for the workspace and player folder")
    parser.add_argument("--window-driver", choices=("dummy", "wayland", "x11"), default="dummy",
                        help="SDL backend for the launcher window smoke check")
    args = parser.parse_args()
    work = Path(args.work).resolve()
    port.LAYOUT = Layout(port.ROOT, work / "workspace")
    if not port.LAYOUT.packaged:
        raise SystemExit("Run this from an extracted builder (no package.json found)")
    port.LAYOUT.verify()
    print(f"[verify] builder files verified ({len(port.LAYOUT.package['files'])}); source {port.LAYOUT.source_commit()}")
    verify_toolchain(Toolchain(port.LAYOUT.toolchain()), work)
    elf = synthetic_title(work)
    run = Path(tempfile.mkdtemp(prefix="player-check-", dir=work))
    launcher = build(work, elf, run / "Tank Battles test folder")
    audit_player(launcher, port.LAYOUT.source_commit())
    launch(launcher, args.window_driver)
    print("PASS: the downloaded builder built and started a synthetic player with only its own tools")
    return 0


if __name__ == "__main__":
    sys.exit(main())
