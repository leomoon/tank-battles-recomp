"""Assemble and verify the read-only, game-free resources of a builder package.

The resource tree mirrors the checkout layout the setup scripts already use
(scripts/, cmake/, external/ps3recomp/, config.toml), plus a verified runtime
bundle in runtime/ and a staged toolchain in toolchain/. package.json records
the source commit and ABI so an installed builder needs no Git, and lists every
resource file with its SHA-256 so setup rejects a modified package.

    python scripts/package_resources.py assemble --output DIR \\
        --runtime-bundle BUNDLE --toolchain STAGED [--source COMMIT]
    python scripts/package_resources.py verify DIR
"""
from __future__ import annotations

import argparse
import ast
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from install_layout import PACKAGE_FILE, PACKAGE_FORMAT, Layout, sha256  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
# Entry points the packaged route runs; their local imports are followed.
SCRIPT_ENTRIES = ("port.py", "runtime_abi.py", "patch_network_events.py", "toolchain.py",
                  "package_resources.py", "setup_engine.py", "player_folder.py", "builder_app.py",
                  "verify_builder.py")
SCRIPT_DATA = ("rap_format.json",)
TOP_FILES = ("config.toml", "LICENSE", "runtime_entry.cpp", "ci/toolchain/manifest.json")
CMAKE_FILES = ("cmake/TitleCompileFlags.cmake", "cmake/PlayerLauncher.cmake", "cmake/player-consumer/CMakeLists.txt")
SDK = "external/ps3recomp"
SDK_DIRS = ("tools", "cmake", "include", "runtime", "libs")
SDK_FILES = ("LICENSE", "requirements.txt")
SKIP_PARTS = {"__pycache__", "tests", ".git"}
SKIP_SUFFIXES = {".pyc", ".pyo"}
# Purchased content, keys, saves, credentials and generated title code never ship.
FORBIDDEN_SUFFIXES = {".pkg", ".elf", ".self", ".fself", ".sprx", ".prx", ".edat", ".sdat", ".rap", ".rif",
                      ".key", ".keys", ".sfo", ".pup", ".iso", ".trp", ".pem", ".sav", ".profile", ".o", ".obj"}
FORBIDDEN_PARTS = {"game", "recompiled", "generated_spu", "dev_hdd0", "dev_hdd1", "analysis", "keys"}


def script_closure(scripts: Path) -> list[str]:
    local = {path.stem for path in scripts.glob("*.py")}
    pending = [name for name in SCRIPT_ENTRIES if (scripts / name).is_file()]
    seen: set[str] = set()
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        tree = ast.parse((scripts / name).read_text(), filename=name)
        for node in ast.walk(tree):
            modules = []
            if isinstance(node, ast.Import):
                modules = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                modules = [node.module.split(".")[0]]
            pending += [module + ".py" for module in modules if module in local]
    return sorted(seen)


def resource_files(root: Path = ROOT) -> list[str]:
    names = [f"scripts/{name}" for name in script_closure(root / "scripts")]
    names += [f"scripts/{name}" for name in SCRIPT_DATA]
    names += [name for name in TOP_FILES if (root / name).is_file()]
    names += [path.relative_to(root).as_posix() for path in sorted(root.glob("*.h"))]
    names += list(CMAKE_FILES)
    names += [path.relative_to(root).as_posix() for path in sorted((root / "launcher").glob("*")) if path.is_file()]
    names += [f"{SDK}/{name}" for name in SDK_FILES if (root / SDK / name).is_file()]
    for directory in SDK_DIRS:
        for path in sorted((root / SDK / directory).rglob("*")):
            relative = path.relative_to(root)
            if path.is_file() and not SKIP_PARTS & set(relative.parts) and path.suffix not in SKIP_SUFFIXES:
                names.append(relative.as_posix())
    return names


def audit(names) -> None:
    for name in names:
        parts = Path(name).parts
        if Path(name).suffix.lower() in FORBIDDEN_SUFFIXES or FORBIDDEN_PARTS & {p.lower() for p in parts[:1]}:
            raise ValueError(f"Refusing to package non-redistributable file: {name}")


def checkout_commit() -> str:
    status = subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT, text=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if status.strip():
        print(f"[package] warning: uncommitted changes; resources are labelled {commit} but differ from it", flush=True)
    return commit


def copy_tree(source: Path, destination: Path, link: bool = False) -> None:
    if link:   # Tests only: reference large verified trees instead of copying them.
        destination.symlink_to(Path(source).resolve(), target_is_directory=True)
    else:
        shutil.copytree(source, destination, symlinks=False)


def assemble(output: Path, runtime: Path | None, toolchain: Path | None, source: str | None,
             link: bool = False) -> dict:
    output = Path(output).resolve()
    if output.exists():
        raise ValueError(f"Destination already exists; use a fresh directory: {output}")
    names = resource_files()
    audit(names)
    source = source or checkout_commit()
    package = dict(format=PACKAGE_FORMAT, kind="tank-battles-builder-resources", source_commit=source,
                   files={})
    output.mkdir(parents=True)
    for name in names:
        destination = output / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, destination)
        package["files"][name] = sha256(destination)
    if runtime:
        from runtime_bundle import verify as verify_bundle
        manifest = verify_bundle(Path(runtime), source=source)
        copy_tree(Path(runtime), output / "runtime", link)
        package.update(runtime="runtime", abi_sha256=manifest["abi_sha256"],
                       target=manifest["build"].get("target") or manifest["build"].get("system"))
    if toolchain:
        from toolchain import verify as verify_toolchain
        identity = verify_toolchain(Path(toolchain))
        copy_tree(Path(toolchain), output / "toolchain", link)
        package.update(toolchain="toolchain", toolchain_identity=identity)
    (output / PACKAGE_FILE).write_text(json.dumps(package, indent=2, sort_keys=True) + "\n")
    verify(output)
    return package


def verify(root: Path) -> dict:
    root = Path(root).resolve()
    layout = Layout(root, workspace=os.environ.get("TB_WORKSPACE") or root.parent / ".verify-workspace")
    if not layout.packaged:
        raise ValueError(f"No {PACKAGE_FILE} in {root}")
    layout.verify()
    audit(layout.package["files"])
    if layout.runtime_bundle():
        from runtime_bundle import verify as verify_bundle
        manifest = verify_bundle(layout.runtime_bundle(), source=layout.package["source_commit"])
        if manifest["abi_sha256"] != layout.package["abi_sha256"]:
            raise ValueError("Packaged runtime ABI differs from package.json")
    if layout.toolchain():
        from toolchain import verify as verify_toolchain
        verify_toolchain(layout.toolchain())
    return layout.package


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("assemble")
    build.add_argument("--output", required=True)
    build.add_argument("--runtime-bundle")
    build.add_argument("--toolchain")
    build.add_argument("--source", help="Source commit (default: this checkout's HEAD)")
    build.add_argument("--link-large", action="store_true", help=argparse.SUPPRESS)
    check = commands.add_parser("verify")
    check.add_argument("root")
    commands.add_parser("list", help="Print the resource file list")
    args = parser.parse_args()
    try:
        if args.command == "assemble":
            package = assemble(Path(args.output), args.runtime_bundle and Path(args.runtime_bundle),
                               args.toolchain and Path(args.toolchain), args.source, args.link_large)
            print(f"[package] {len(package['files'])} resource files; source {package['source_commit']}")
        elif args.command == "verify":
            package = verify(Path(args.root))
            print(f"[package] verified {len(package['files'])} resource files; source {package['source_commit']}")
        else:
            print("\n".join(resource_files()))
    except (OSError, ValueError) as error:
        parser.exit(2, f"error: {error}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
