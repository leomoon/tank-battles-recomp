"""Where read-only resources end and the user's writable workspace begins.

A source checkout is both: the scripts, SDK and CMake files sit next to the
game data, translations and build directory, as before. An installed builder
package is read-only. It carries the same scripts/SDK/CMake files plus a
verified runtime bundle and staged toolchain, identified by `package.json`
instead of Git, and every path the user's build writes resolves inside a
separate workspace directory.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

RESOURCES = Path(__file__).resolve().parents[1]
PACKAGE_FILE = "package.json"
PACKAGE_FORMAT = 1
# Config entries that name packaged resources rather than user state.
RESOURCE_SETTINGS = ("sdk",)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def default_workspace() -> Path:
    """Per-user workspace for an installed package used without --workspace."""
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local")
        return base / "TankBattlesRecomp/workspace"
    base = os.environ.get("XDG_DATA_HOME")
    base = Path(base) if base and Path(base).is_absolute() else Path.home() / ".local/share"
    return base / "tank-battles-recomp/workspace"


class Layout:
    def __init__(self, resources: Path = RESOURCES, workspace: str | Path | None = None):
        self.resources = Path(resources).resolve()
        package = self.resources / PACKAGE_FILE
        self.package = json.loads(package.read_text()) if package.is_file() else None
        if self.package is not None and self.package.get("format") != PACKAGE_FORMAT:
            raise ValueError(f"Unsupported package format in {package}; use the matching builder scripts")
        chosen = workspace or os.environ.get("TB_WORKSPACE")
        if chosen:
            self.workspace = Path(chosen).expanduser().resolve()
        elif self.package is not None:
            self.workspace = default_workspace().resolve()
        else:
            self.workspace = self.resources
        if self.package is not None and self.workspace.is_relative_to(self.resources):
            raise ValueError("The workspace must be outside the installed builder resources, which are read-only")

    @property
    def packaged(self) -> bool:
        return self.package is not None

    def resource(self, value: str | Path) -> Path:
        path = Path(value).expanduser()
        return path.resolve() if path.is_absolute() else (self.resources / path).resolve()

    def resolve(self, value: str | Path) -> Path:
        """User paths: relative entries live in the workspace."""
        path = Path(value).expanduser()
        return path.resolve() if path.is_absolute() else (self.workspace / path).resolve()

    def load_config(self, value: str | Path) -> dict:
        import tomllib
        # Explicit config files may live anywhere; the default ships with the resources.
        path = Path(value).expanduser()
        candidate = path if path.is_absolute() else self.workspace / path
        if not candidate.is_file():
            candidate = self.resource(path)
        with candidate.open("rb") as stream:
            config = tomllib.load(stream)
        for name in RESOURCE_SETTINGS:
            config["paths"][name] = str(self.resource(config["paths"][name]))
        return config

    def source_commit(self) -> str:
        if self.packaged:
            return self.package["source_commit"]
        try:
            return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.resources, text=True,
                                           stderr=subprocess.DEVNULL).strip()
        except (OSError, subprocess.CalledProcessError):
            raise ValueError("This checkout has no Git metadata. Pass --runtime-source with the runtime bundle's source commit.") from None

    def runtime_bundle(self) -> Path | None:
        return self.resources / self.package["runtime"] if self.packaged and self.package.get("runtime") else None

    def toolchain(self) -> Path | None:
        return self.resources / self.package["toolchain"] if self.packaged and self.package.get("toolchain") else None

    def verify(self) -> None:
        """Reject a modified or incomplete package before any work is done."""
        if not self.packaged:
            return
        bad = [name for name, digest in self.package["files"].items()
               if not (self.resources / name).is_file() or sha256(self.resources / name) != digest]
        if bad:
            raise ValueError(f"Builder resources are damaged or modified ({len(bad)} files, e.g. {bad[0]}). Download the builder again.")


class Toolchain:
    """A staged scripts/toolchain.py tree: CMake, Ninja, Python and Zig."""

    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        identity = self.root / "toolchain.json"
        if not identity.is_file():
            raise ValueError(f"Not a staged builder toolchain: {self.root}")
        self.identity = json.loads(identity.read_text())
        windows = self.identity["host"] == "windows"
        suffix = ".exe" if windows else ""
        self.cmake = self.root / "cmake/bin" / ("cmake" + suffix)
        self.ninja = self.root / ("ninja" + suffix)
        self.python = self.root / "python" / ("python.exe" if windows else "bin/python3")
        self.target = self.identity["host"]
        self.toolchain_file = self.root / "cmake" / f"zig-{self.target}.cmake"
        missing = [p for p in (self.cmake, self.ninja, self.toolchain_file) if not p.is_file()]
        if missing:
            raise ValueError(f"Incomplete builder toolchain, missing {missing[0]}")

    def environment(self, base: dict, cache: Path) -> dict:
        """Only staged tools are reachable, so no installed compiler or library can satisfy the build."""
        cache.mkdir(parents=True, exist_ok=True)
        keep = {k: base[k] for k in ("SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP", "TMPDIR", "HOME",
                                     "USERPROFILE", "LOCALAPPDATA", "LANG", "LC_ALL") if k in base}
        path = [self.root / "bin", self.root / "cmake/bin", Path(sys.executable).parent]
        if os.name == "nt":
            system = Path(base.get("SYSTEMROOT", r"C:\Windows"))
            path += [system / "System32", system]
        return dict(keep, PATH=os.pathsep.join(map(str, path)),
                    ZIG_GLOBAL_CACHE_DIR=str(cache), ZIG_LOCAL_CACHE_DIR=str(cache))
