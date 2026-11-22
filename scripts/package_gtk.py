"""Stage the pinned Windows GTK4 runtime from MSYS2's x64 UCRT packages."""
from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tarfile

from toolchain import fetch_one, sha256

MANIFEST = Path(__file__).resolve().parents[1] / "ci/toolchain/windows-gtk4.json"
MIRROR = "https://repo.msys2.org/mingw/"


def packages() -> dict:
    return json.loads(MANIFEST.read_text())["packages"]


def notices() -> list[str]:
    lines = ["", "Windows GTK4 desktop runtime (unmodified MSYS2 UCRT64 binaries):"]
    for name, package in packages().items():
        licence = ", ".join(package["license"]).replace("spdx:", "")
        lines.append(f"  {name} {package['version']} ({licence})")
        lines.append(f"    Corresponding source: {MIRROR}sources/{package['source']}")
    lines += ["Licence texts are in gtk/share/licenses/. Additional source archives are in gtk/sources/."]
    return lines


def copy_package(archive: Path, target: Path, package: dict) -> None:
    dlls = set(package["dlls"])
    data = tuple(package.get("data", ()))
    aliases = []
    with subprocess.Popen(["zstd", "-dc", str(archive)], stdout=subprocess.PIPE) as zstd:
        with tarfile.open(fileobj=zstd.stdout, mode="r|") as bundle:
            for member in bundle:
                parts = PurePosixPath(member.name).parts
                if parts[:1] != ("ucrt64",) or ".." in parts:
                    continue
                relative = PurePosixPath(*parts[1:])
                name = relative.as_posix()
                if relative.parent == PurePosixPath("bin") and relative.name.lower() in dlls:
                    if member.islnk():
                        source = PurePosixPath(member.linkname)
                        if source.parent != PurePosixPath("ucrt64/bin") or source.name.lower() not in package["dlls"]:
                            raise ValueError(f"Invalid Windows GTK DLL hard link in {package['file']}")
                        aliases.append((relative, source.name))
                    elif not member.isfile():
                        raise ValueError(f"Unexpected Windows GTK DLL entry {member.name}")
                    dlls.remove(relative.name.lower())
                    if member.islnk():
                        continue
                elif not member.isfile() or not name.startswith(("share/licenses/", *data)):
                    continue
                output = target / name
                output.parent.mkdir(parents=True, exist_ok=True)
                with bundle.extractfile(member) as source, output.open("xb") as destination:
                    shutil.copyfileobj(source, destination)
        # The tar end marker can precede its final padding.
        while zstd.stdout.read(1 << 20):
            pass
        if zstd.wait():
            raise ValueError(f"Failed to decompress {archive}")
    if dlls:
        raise ValueError(f"GTK package {package['file']} is missing {', '.join(sorted(dlls))}")
    for name, source in aliases:
        shutil.copyfile(target / "bin" / source, target / name)


def stage(target: Path, cache: Path) -> dict[str, str]:
    """Copy only needed DLLs, data, licences and corresponding sources."""
    target.mkdir(parents=True)
    for package in packages().values():
        archive = fetch_one(cache, MIRROR + "ucrt64/" + package["file"], package["sha256"])
        copy_package(archive, target, package)
        if digest := package.get("source_sha256"):
            source = fetch_one(cache, MIRROR + "sources/" + package["source"], digest)
            output = target / "sources" / package["source"]
            output.parent.mkdir(exist_ok=True)
            shutil.copyfile(source, output)
    schemas = target / "share/glib-2.0/schemas"
    subprocess.run(["glib-compile-schemas", "--strict", str(schemas)], check=True)
    shutil.copyfile(MANIFEST, target / "manifest.json")
    return {path.relative_to(target.parent).as_posix(): sha256(path)
            for path in sorted(target.rglob("*")) if path.is_file()}
