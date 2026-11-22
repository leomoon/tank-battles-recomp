"""Package the standalone builders: Builder.AppImage and Builder.exe.

Both carry the same game-free resources (scripts/package_resources.py:
setup scripts, SDK, a verified runtime bundle and the staged toolchain), plus
NOTICES.txt and the Windows GTK4 runtime and third-party sources.

    package_builder.py resources --target linux --runtime-bundle B --toolchain T --cache C --output R [--source COMMIT]
    package_builder.py appdir   --resources R --output AppDir
    package_builder.py appimage --appdir AppDir --runtime type2-runtime --output Builder.AppImage   (needs mksquashfs)
    package_builder.py stub     --toolchain T --output BuilderStub.exe                              (Linux-hosted Zig)
    package_builder.py exe      --resources R --stub BuilderStub.exe --output Builder.exe

The AppImage starts AppRun, which runs the staged Python with the builder
window. `Builder.AppImage --appimage-extract` unpacks it where FUSE is not
available; run squashfs-root/AppRun afterwards. Builder.exe verifies and
unpacks its payload with Windows' tar.exe on first start (`--extract DIR`
chooses the place).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import zipfile
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parent))
import package_resources  # noqa: E402
import package_gtk  # noqa: E402
import toolchain as toolchains  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
# Resource timestamps must predate the consumer's generated Ninja files, even
# when the packaging runner's clock is ahead of the user's machine.
RESOURCE_EPOCH = 946684800  # 2000-01-01 UTC, also representable in ZIP/DOS time.
APPRUN = """#!/bin/sh
# Tank Battles builder: the staged Python runs the builder window.
# The AppImage runtime sets APPDIR; an extracted squashfs-root uses its own path.
HERE="${APPDIR:-$(cd "${0%/*}" && pwd -P)}"
exec "$HERE/toolchain/python/bin/python3" -E -s -B "$HERE/scripts/builder_app.py" "$@"
"""
DESKTOP = """[Desktop Entry]
Type=Application
Name=Tank Battles Builder
Comment=Build Tank Battles for this computer from your purchased game
Exec=AppRun
Icon=tank-battles-builder
Categories=Game;
Terminal=false
"""
MAGIC = b"TBBUILD1"


def notices(target: str) -> str:
    manifest = toolchains.load_manifest()
    lines = ["Tank Battles builder: third-party components", "",
             "This builder contains no game content. It compiles your own copy of the game on your computer.", "",
             "Build tools:"]
    for name, tool in manifest["tools"].items():
        lines.append(f"  {name} {tool['version']} - {tool['licence']} - {tool[target]['url']}")
    lines += ["", "Python packages (lifter):"]
    for name, wheel in manifest["wheels"].items():
        lines.append(f"  {name} {wheel['version']}")
    lines += ["", "Libraries linked into players (licence texts in runtime/licences/):"]
    for name, source in manifest["sources"].items():
        if target in source.get("targets", ["linux", "windows"]):
            lines.append(f"  {name} {source['version']} - {source['licence']} - {source['url']}")
    if target == "windows":
        lines += package_gtk.notices()
        lines += ["", "GNU libiconv is LGPL-2.1-or-later and is linked statically into Windows players.",
                  "Its exact source is included in sources/. Players are linked on your computer from",
                  "object files and libraries, so you can relink them against a modified libiconv."]
    lines += ["", "Each tool's own licence files are inside toolchain/ next to it.", ""]
    return "\n".join(lines)


def resources(args) -> None:
    output = Path(args.output).resolve()
    package = package_resources.assemble(output, Path(args.runtime_bundle), Path(args.toolchain), args.source)
    (output / "NOTICES.txt").write_text(notices(args.target))
    if args.target == "windows":
        package["files"].update(package_gtk.stage(output / "gtk", Path(args.cache)))
        package["windows_gtk4_manifest_sha256"] = package["files"]["gtk/manifest.json"]
        (output / "package.json").write_text(json.dumps(package, indent=2, sort_keys=True) + "\n")
        package_resources.verify(output)
        source = toolchains.load_manifest()["sources"]["libiconv"]
        archive = toolchains.cached(Path(args.cache), source["sha256"])
        if not archive.is_file():
            raise SystemExit(f"libiconv source is not in the cache {args.cache}; run toolchain.py fetch --sources")
        (output / "sources").mkdir()
        shutil.copy2(archive, output / "sources" / Path(source["url"]).name)
    print(f"[builder] resources ready in {output}")


def png(size: int = 256) -> bytes:
    """An original, simple tank icon, drawn here so no artwork file is needed."""
    pixels = bytearray()
    for y in range(size):
        pixels.append(0)
        for x in range(size):
            u, v = x / size, y / size
            color = (24, 38, 54, 255)
            if 0.18 < u < 0.82 and 0.52 < v < 0.72:
                color = (92, 128, 70, 255)                        # hull
            if 0.32 < u < 0.64 and 0.38 < v < 0.54:
                color = (110, 150, 84, 255)                       # turret
            if 0.62 < u < 0.92 and 0.43 < v < 0.48:
                color = (110, 150, 84, 255)                       # barrel
            if 0.16 < u < 0.84 and 0.70 < v < 0.80:
                color = (40, 44, 48, 255)                         # tracks
            if (0.06 > abs(v - 0.15) and 0.06 > abs(u - 0.5)):
                color = (255, 199, 102, 255)                      # star
            pixels.extend(color)
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(bytes(pixels), 9)) + chunk(b"IEND", b""))


def appdir(args) -> None:
    source, output = Path(args.resources).resolve(), Path(args.output).resolve()
    if output.exists():
        raise SystemExit(f"{output} exists; use a fresh directory")
    shutil.copytree(source, output, symlinks=False)
    (output / "AppRun").write_text(APPRUN)
    (output / "AppRun").chmod(0o755)
    (output / "tank-battles-builder.desktop").write_text(DESKTOP)
    (output / "tank-battles-builder.png").write_bytes(png())
    (output / ".DirIcon").write_bytes(png())
    print(f"[builder] AppDir ready in {output}")


def appimage(args) -> None:
    mksquashfs = shutil.which("mksquashfs")
    if not mksquashfs:
        raise SystemExit("mksquashfs (squashfs-tools) is needed to create the AppImage")
    runtime = Path(args.runtime).read_bytes()
    expected = toolchains.load_manifest()["packaging"]["appimage_runtime"]["sha256"]
    if hashlib.sha256(runtime).hexdigest() != expected:
        raise SystemExit("The AppImage runtime does not match the pinned digest")
    output = Path(args.output).resolve()
    image = output.with_suffix(".squashfs")
    subprocess.run([mksquashfs, args.appdir, str(image), "-root-owned", "-noappend", "-comp", "zstd",
                    "-Xcompression-level", "19", "-b", "1M", "-all-time", str(RESOURCE_EPOCH), "-quiet"], check=True)
    with output.open("wb") as stream:
        stream.write(runtime)
        stream.write(image.read_bytes())
    image.unlink()
    output.chmod(0o755)
    print(f"[builder] {output} ({output.stat().st_size >> 20} MB)")


def stub(args) -> None:
    tc = Path(args.toolchain).resolve()
    command = [str(tc / "bin/zig-c++"), "-target", "x86_64-windows-gnu", "-std=c++20", "-O2", "-g0",
               "-DUNICODE", "-D_UNICODE", "-DNOMINMAX", str(ROOT / "builder/windows_stub.cpp"),
               "-o", str(Path(args.output).resolve()), "-lbcrypt", "-lshell32", "-luser32", "-Wl,--subsystem,windows"]
    env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(Path(args.output).resolve().parent / ".zig-cache"),
               ZIG_LOCAL_CACHE_DIR=str(Path(args.output).resolve().parent / ".zig-cache"))
    subprocess.run(command, check=True, env=env)
    print(f"[builder] stub {args.output}")


def exe(args) -> None:
    source, output = Path(args.resources).resolve(), Path(args.output).resolve()
    payload = output.with_suffix(".payload.zip")
    with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(source.rglob("*")):
            if path.is_file() and not path.is_symlink():
                info = zipfile.ZipInfo(path.relative_to(source).as_posix(), (2000, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = (path.stat().st_mode & 0xFFFF) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                with path.open("rb") as contents, archive.open(info, "w") as packed:
                    shutil.copyfileobj(contents, packed, 1 << 20)
    digest = hashlib.sha256()
    with payload.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    stub_bytes = Path(args.stub).read_bytes()
    with output.open("wb") as stream:
        stream.write(stub_bytes)
        with payload.open("rb") as body:
            shutil.copyfileobj(body, stream, 1 << 20)
        stream.write(struct.pack("<QQ", len(stub_bytes), payload.stat().st_size) + digest.digest() + MAGIC)
    payload.unlink()
    print(f"[builder] {output} ({output.stat().st_size >> 20} MB)")


def read_exe_payload(path: Path) -> tuple[int, int, bytes]:
    """(offset, size, sha256) of Builder.exe's payload; raises if malformed."""
    data_size = path.stat().st_size
    with path.open("rb") as stream:
        stream.seek(data_size - 56)
        offset, size = struct.unpack("<QQ", stream.read(16))
        digest, magic = stream.read(32), stream.read(8)
    if magic != MAGIC or offset + size + 56 != data_size:
        raise ValueError(f"{path} has no builder payload")
    return offset, size, digest


def provenance(args) -> None:
    """SHA256SUMS and provenance.json for the release candidates."""
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    sums, artifacts = [], []
    for artifact, resources_dir in zip(args.artifact, args.resources):
        artifact, resources_dir = Path(artifact), Path(resources_dir)
        digest = toolchains.sha256(artifact)
        package = json.loads((resources_dir / "package.json").read_text())
        runtime = json.loads((resources_dir / "runtime/manifest.json").read_text())
        sums.append(f"{digest}  {artifact.name}")
        artifacts.append(dict(name=artifact.name, sha256=digest, bytes=artifact.stat().st_size,
                              source_commit=package["source_commit"], abi_sha256=package.get("abi_sha256"),
                              target=package.get("target"), toolchain=package.get("toolchain_identity"),
                              runtime_build=runtime.get("build"), resource_files=len(package["files"])))
        if gtk_manifest := package.get("windows_gtk4_manifest_sha256"):
            artifacts[-1]["windows_gtk4_manifest_sha256"] = gtk_manifest
    record = dict(source_commit=args.source, toolchain_manifest_sha256=toolchains.manifest_digest(),
                  workflow_run=os.environ.get("GITHUB_SERVER_URL", "") and
                  f"{os.environ['GITHUB_SERVER_URL']}/{os.environ.get('GITHUB_REPOSITORY')}/actions/runs/{os.environ.get('GITHUB_RUN_ID')}",
                  artifacts=artifacts)
    mismatched = [a["name"] for a in artifacts if args.source and a["source_commit"] != args.source]
    if mismatched:
        raise SystemExit(f"Artifacts from another source: {mismatched}")
    (output / "SHA256SUMS").write_text("\n".join(sums) + "\n")
    (output / "provenance.json").write_text(json.dumps(record, indent=2) + "\n")
    (output / "NOTICES-linux.txt").write_text(notices("linux"))
    (output / "NOTICES-windows.txt").write_text(notices("windows"))
    print(f"[builder] provenance for {len(artifacts)} artifacts in {output}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("resources")
    command.add_argument("--target", choices=("linux", "windows"), required=True)
    for name in ("--runtime-bundle", "--toolchain", "--cache", "--output"):
        command.add_argument(name, required=True)
    command.add_argument("--source")
    command = commands.add_parser("appdir")
    command.add_argument("--resources", required=True)
    command.add_argument("--output", required=True)
    command = commands.add_parser("appimage")
    for name in ("--appdir", "--runtime", "--output"):
        command.add_argument(name, required=True)
    command = commands.add_parser("stub")
    command.add_argument("--toolchain", required=True)
    command.add_argument("--output", required=True)
    command = commands.add_parser("exe")
    for name in ("--resources", "--stub", "--output"):
        command.add_argument(name, required=True)
    command = commands.add_parser("provenance")
    command.add_argument("--artifact", action="append", required=True)
    command.add_argument("--resources", action="append", required=True, help="The resources each artifact was made from, in order")
    command.add_argument("--source", required=True)
    command.add_argument("--output", required=True)
    args = parser.parse_args()
    {"provenance": provenance, "resources": resources, "appdir": appdir, "appimage": appimage, "stub": stub, "exe": exe}[args.command](args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
