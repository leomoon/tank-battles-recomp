"""Assemble the movable private player folder from a finished setup.

    <output>/
      Play Tank Battles[.exe]     launcher (launcher/play_tank_battles.cpp)
      player/TankBattlesRecomp    the locally built executable
      player/manifest.json        what this folder's builds own, with SHA-256
      game/                       installed content, plus saves and settings.ini
      online/, logs/              optional RPCN profiles and session logs

Only files listed in the manifest are replaced or removed by a later build.
Saves, settings (including the F4 FXAA choice), online profiles and logs are
never touched. Replacements are staged first, so an interrupted update leaves
the previous files usable and the next build completes it. Compilers,
translations and build caches stay in the workspace.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import tempfile

from install_layout import sha256

MANIFEST = "player/manifest.json"
FORMAT = 1
TITLE = "game/dev_hdd0/game/NPUB30154"
EXDATA = "game/dev_hdd0/home/00000001/exdata"
# Decrypted asset caches are rebuilt at first launch; locks are transient.
CACHE_SUFFIXES = (".dec", ".dec.complete", ".dec.lock", ".lock")
# User state an explicit migration may copy from an older game folder.
MIGRATED = ("dev_hdd0/home/00000001/savedata", "dev_hdd0/home/00000001/trophy", "settings.ini")


def executable_name(name: str) -> str:
    return name + (".exe" if os.name == "nt" else "")


def launcher_name() -> str:
    return executable_name("Play Tank Battles")


def build_outputs(build_dir: Path, multi_config: bool) -> dict[str, Path]:
    build_dir = Path(build_dir) / ("Release" if multi_config else "")
    outputs = {f"player/{executable_name('TankBattlesRecomp')}": build_dir / executable_name("TankBattlesRecomp"),
               launcher_name(): build_dir / executable_name("PlayTankBattles")}
    missing = [str(path) for path in outputs.values() if not path.is_file()]
    if missing:
        raise ValueError(f"The build did not produce {missing[0]}; run setup again.")
    return outputs


def content_files(title: Path, exdata: Path, title_id: str) -> dict[str, Path]:
    if not (title / "USRDIR/EBOOT.elf").is_file() or not (title / "PARAM.SFO").is_file():
        raise ValueError(f"The imported game is incomplete in {title}; run setup again.")
    files = {}
    for path in sorted(title.rglob("*")):
        if path.is_file() and not path.name.endswith(CACHE_SUFFIXES):
            files[f"{TITLE}/{path.relative_to(title).as_posix()}"] = path
    if exdata.is_dir():
        for path in sorted(exdata.iterdir()):
            if path.is_file() and title_id in path.name and not path.name.endswith(CACHE_SUFFIXES):
                files[f"{EXDATA}/{path.name}"] = path
    return files


def read_manifest(output: Path) -> dict | None:
    path = output / MANIFEST
    if not path.is_file():
        return None
    manifest = json.loads(path.read_text())
    if manifest.get("format") != FORMAT:
        raise ValueError(f"{output} was made by a different builder version; choose a new folder")
    return manifest


def check_output(output: Path) -> dict | None:
    """Refuse to mix the player into a folder that holds anything else."""
    manifest = read_manifest(output) if output.exists() else None
    if manifest is None and output.exists() and any(output.iterdir()):
        raise ValueError(f"{output} is not empty and is not a Tank Battles player folder. Choose a new or empty folder.")
    return manifest


def migrate(source_game: Path, output: Path, log=print) -> list[str]:
    """Copy saves/settings from an older game folder, never overwriting anything."""
    copied = []
    for relative in MIGRATED:
        source, target = Path(source_game) / relative, output / "game" / relative
        if not source.exists() or target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)
        copied.append(relative)
        log(f"[player] Copied {relative} from {source_game}")
    return copied


def assemble(output: Path, sources: dict[str, Path], identity: dict, log=print) -> dict:
    output = Path(output).resolve()
    previous = check_output(output)
    output.mkdir(parents=True, exist_ok=True)
    files = {}
    with tempfile.TemporaryDirectory(prefix=".staging-", dir=output) as temporary:
        staging = Path(temporary)
        changed = []
        for name, source in sources.items():
            digest = sha256(source)
            target = output / name
            files[name] = digest
            if previous and previous["files"].get(name) == digest and target.is_file() and sha256(target) == digest:
                continue   # Unchanged content is not copied again.
            staged = staging / name
            staged.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, staged)
            if sha256(staged) != digest:
                raise ValueError(f"{source} changed while the player folder was being assembled; run setup again.")
            changed.append(name)
        # Everything is staged before the first replacement.
        for name in changed:
            target = output / name
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staging / name, target)
    for name in (previous or {}).get("files", {}):
        if name not in files:
            (output / name).unlink(missing_ok=True)
    for directory in ("online", "logs", "game/dev_hdd1"):
        (output / directory).mkdir(parents=True, exist_ok=True)
    manifest = dict(format=FORMAT, kind="tank-battles-player", built_utc=datetime.now(timezone.utc).isoformat(),
                    files=files, **identity)
    path = output / MANIFEST
    temporary = path.with_name(".manifest.json.tmp")
    temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)
    log(f"[player] {'Updated' if previous else 'Created'} {output}")
    return manifest


def default_output() -> Path:
    base = Path(os.environ.get("USERPROFILE") or Path.home()) if os.name == "nt" else Path.home()
    return base / "Games" / "Tank Battles Recomp"
