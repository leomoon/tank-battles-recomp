#!/usr/bin/env python3
"""Fetch, verify and stage the redistributable build toolchain for the standalone builders.

Every component is pinned in ci/toolchain/manifest.json with its download URL
and SHA-256. Nothing is used before its digest matches.

  python scripts/toolchain.py fetch --host linux --cache DIR [--sources]
  python scripts/toolchain.py stage --host linux --cache DIR --dest DIR
  python scripts/toolchain.py verify --dest DIR

`stage` lays out a self-contained toolchain for one host OS (and can stage
either OS from either OS, since nothing from the staged payload is executed):

  zig/            Zig (clang, lld, libc++, glibc stubs, mingw-w64), the only compiler
  cmake/          CMake
  ninja[.exe]     Ninja
  python/         standalone CPython with the lifter's wheels unpacked into site-packages
  cmake/zig-<target>.cmake   CMake toolchain files for each build target
  toolchain.json  identity: manifest digest and every component's version/digest

The compiler targets x86_64 Linux with a glibc 2.31 baseline and x86_64
Windows (mingw-w64 GNU ABI). Development tools installed on the machine are
never consulted by a staged toolchain.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tarfile
import tempfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "ci/toolchain/manifest.json"
HOSTS = ("linux", "windows")
# glibc 2.31: Ubuntu 20.04, Debian 11 and newer. Windows: x64 Windows 10 and newer.
TARGETS = {"linux": "x86_64-linux-gnu.2.31", "windows": "x86_64-windows-gnu"}


def load_manifest(path=MANIFEST):
    return json.loads(Path(path).read_text())


def manifest_digest(path=MANIFEST):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def entries(manifest, host, sources=False):
    """(name, url, sha256) for one host's tools and wheels, plus optional sources."""
    for name, tool in manifest["tools"].items():
        yield name, tool[host]["url"], tool[host]["sha256"]
    for name, wheel in manifest["wheels"].items():
        item = wheel.get(host) or wheel.get("any")
        yield f"wheel:{name}", item["url"], item["sha256"]
    if sources:
        for name, source in manifest["sources"].items():
            yield f"source:{name}", source["url"], source["sha256"]
        for name, item in manifest.get("packaging", {}).items():
            yield f"packaging:{name}", item["url"], item["sha256"]


def cached(cache, digest):
    return Path(cache) / digest[:2] / digest


def fetch_one(cache, url, digest):
    path = cached(cache, digest)
    if path.is_file() and sha256(path) == digest:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".download-")
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "tank-battles-toolchain"})
        with os.fdopen(descriptor, "wb") as stream, urllib.request.urlopen(request, timeout=120) as response:
            shutil.copyfileobj(response, stream, 1 << 20)
        actual = sha256(temporary)
        if actual != digest:
            raise ValueError(f"SHA-256 mismatch for {url}: expected {digest}, got {actual}")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return path


def fetch(cache, host, sources=False, manifest=None):
    manifest = manifest or load_manifest()
    paths = {}
    for name, url, digest in entries(manifest, host, sources):
        paths[name] = fetch_one(cache, url, digest)
        print(f"[toolchain] {name}: verified", flush=True)
    return paths


def safe_extract(archive, dest, strip_top=True):
    """Extract tar/zip into dest, refusing absolute paths and parent escapes."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    name = str(archive)
    with tempfile.TemporaryDirectory(dir=dest.parent, prefix=".extract-") as temporary:
        temporary = Path(temporary)
        if zipfile.is_zipfile(name):
            with zipfile.ZipFile(name) as z:
                for member in z.infolist():
                    target = (temporary / member.filename).resolve()
                    if not target.is_relative_to(temporary.resolve()):
                        raise ValueError(f"Unsafe path in {archive}: {member.filename}")
                    z.extract(member, temporary)
                    mode = member.external_attr >> 16
                    if mode & 0o111 and target.is_file():
                        target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        else:
            with tarfile.open(name) as t:
                t.extractall(temporary, filter="data")
        top = list(temporary.iterdir())
        source = top[0] if strip_top and len(top) == 1 and top[0].is_dir() else temporary
        for item in source.iterdir():
            target = dest / item.name
            if target.exists():
                shutil.rmtree(target) if target.is_dir() else target.unlink()
            shutil.move(str(item), target)


def site_packages(python_root, host):
    if host == "windows":
        return python_root / "Lib/site-packages"
    found = sorted((python_root / "lib").glob("python3.*/site-packages"))
    if not found:
        raise ValueError("Standalone Python has no site-packages directory")
    return found[0]


def install_wheel(wheel, site):
    """A wheel is a zip laid out for site-packages; unpack it without running Python."""
    with zipfile.ZipFile(wheel) as z:
        for member in z.infolist():
            target = (site / member.filename).resolve()
            if not target.is_relative_to(site.resolve()):
                raise ValueError(f"Unsafe path in wheel {wheel}: {member.filename}")
        z.extractall(site)


TOOLCHAIN_FILE = """# Generated by scripts/toolchain.py: compile with the staged Zig for {target}.
# Nothing from the machine's own compilers, linkers or SDKs is used.
get_filename_component(TB_TOOLCHAIN_ROOT "${{CMAKE_CURRENT_LIST_DIR}}/.." ABSOLUTE)
set(CMAKE_SYSTEM_NAME {system})
set(CMAKE_SYSTEM_PROCESSOR x86_64)
set(TB_ZIG_TARGET "{target}")
set(TB_TOOLCHAIN "zig-{zig_version}")
set(CMAKE_C_COMPILER {c_compiler})
set(CMAKE_CXX_COMPILER {cxx_compiler})
set(CMAKE_AR "${{TB_TOOLCHAIN_ROOT}}/bin/zig-ar{wrapper}" CACHE FILEPATH "" FORCE)
set(CMAKE_RANLIB "${{TB_TOOLCHAIN_ROOT}}/bin/zig-ranlib{wrapper}" CACHE FILEPATH "" FORCE)
set(CMAKE_RC_COMPILER "${{TB_TOOLCHAIN_ROOT}}/bin/zig-rc{wrapper}" CACHE FILEPATH "" FORCE)
# Zig turns __DATE__/__TIME__ into errors for reproducibility; the pinned SDK
# stamps its shader translator with them, so keep it a warning. Zig also emits
# DWARF by default, which more than quadruples the shipped static libraries.
set(CMAKE_C_FLAGS_INIT "-Wno-error=date-time -g0")
set(CMAKE_CXX_FLAGS_INIT "-Wno-error=date-time -g0")
set(CMAKE_C_COMPILER_TARGET "{target}")
set(CMAKE_CXX_COMPILER_TARGET "{target}")
set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_PACKAGE ONLY)
"""


def cmake_toolchain(host, target_os, zig_version):
    system = "Windows" if target_os == "windows" else "Linux"
    if host == "windows":
        # Ninja can invoke the executable directly, including paths that cmd.exe would parse again.
        driver = '"${TB_TOOLCHAIN_ROOT}/zig/zig.exe"'
        c_compiler, cxx_compiler = f"{driver} cc", f"{driver} c++"
    else:
        c_compiler = '"${TB_TOOLCHAIN_ROOT}/bin/zig-cc"'
        cxx_compiler = '"${TB_TOOLCHAIN_ROOT}/bin/zig-c++"'
    return TOOLCHAIN_FILE.format(target=TARGETS[target_os], system=system, zig_version=zig_version,
                                 wrapper=".cmd" if host == "windows" else "",
                                 c_compiler=c_compiler, cxx_compiler=cxx_compiler)


def write_wrappers(bin_dir, host):
    """zig-cc/zig-c++/zig-ar/zig-ranlib/zig-rc front ends for build systems that want one program per tool."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    tools = {"zig-cc": "cc", "zig-c++": "c++", "zig-ar": "ar", "zig-ranlib": "ranlib", "zig-rc": "rc"}
    for name, sub in tools.items():
        if host == "windows":
            (bin_dir / f"{name}.cmd").write_text(f'@"%~dp0..\\zig\\zig.exe" {sub} %*\r\n')
        else:
            path = bin_dir / name
            # Shell parameter expansion, not dirname: the wrappers must run with
            # PATH limited to the staged toolchain.
            body = f'exec "${{0%/*}}/../zig/zig" {sub} "$@"\n'
            if sub in ("cc", "c++"):
                # Upstream CMake (SPIRV-Tools) adds -static to every MinGW link,
                # DLLs included. Zig links its C++ runtime statically anyway and
                # treats "-shared -static" as a static image, so drop -static
                # only when -shared is also present.
                body = ('shared=0\nfor a in "$@"; do [ "$a" = -shared ] && shared=1; done\n'
                        'if [ $shared = 1 ]; then\n'
                        '  for a in "$@"; do shift; [ "$a" = -static ] || set -- "$@" "$a"; done\n'
                        'fi\n' + body)
            path.write_text('#!/bin/sh\n' + body)
            path.chmod(0o755)


def stage(cache, dest, host, manifest=None):
    manifest = manifest or load_manifest()
    dest = Path(dest)
    paths = fetch(cache, host, manifest=manifest)
    dest.mkdir(parents=True, exist_ok=True)
    safe_extract(paths["zig"], dest / "zig")
    safe_extract(paths["cmake"], dest / "cmake")
    safe_extract(paths["ninja"], dest / "ninja-dist", strip_top=False)
    ninja = "ninja.exe" if host == "windows" else "ninja"
    shutil.move(str(dest / "ninja-dist" / ninja), dest / ninja)
    shutil.rmtree(dest / "ninja-dist")
    safe_extract(paths["python"], dest / "python")
    site = site_packages(dest / "python", host)
    for name in manifest["wheels"]:
        install_wheel(paths[f"wheel:{name}"], site)
    write_wrappers(dest / "bin", host)
    zig_version = manifest["tools"]["zig"]["version"]
    for target_os in TARGETS:
        (dest / "cmake" / f"zig-{target_os}.cmake").write_text(cmake_toolchain(host, target_os, zig_version))
    identity = {
        "schema": 1,
        "host": host,
        "manifest_sha256": manifest_digest(),
        "targets": TARGETS,
        "components": {name: {"version": tool["version"], "sha256": tool[host]["sha256"]}
                       for name, tool in manifest["tools"].items()},
        "wheels": {name: (wheel.get(host) or wheel["any"])["sha256"] for name, wheel in manifest["wheels"].items()},
    }
    (dest / "toolchain.json").write_text(json.dumps(identity, indent=2) + "\n")
    print(f"[toolchain] staged {host} toolchain in {dest}", flush=True)
    return identity


def verify(dest):
    dest = Path(dest)
    identity = json.loads((dest / "toolchain.json").read_text())
    host = identity["host"]
    exe = ".exe" if host == "windows" else ""
    required = [dest / "zig" / f"zig{exe}", dest / "cmake/bin" / f"cmake{exe}", dest / f"ninja{exe}",
                dest / "python" / ("python.exe" if host == "windows" else "bin/python3")]
    required += [dest / "cmake" / f"zig-{t}.cmake" for t in TARGETS]
    missing = [str(p) for p in required if not p.exists()]
    if missing:
        raise ValueError("Incomplete staged toolchain: " + ", ".join(missing))
    return identity


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("fetch", "stage"):
        command = commands.add_parser(name)
        command.add_argument("--host", choices=HOSTS, default="windows" if os.name == "nt" else "linux")
        command.add_argument("--cache", required=True)
        if name == "fetch":
            command.add_argument("--sources", action="store_true", help="Also fetch the runtime dependency sources")
        else:
            command.add_argument("--dest", required=True)
    check = commands.add_parser("verify")
    check.add_argument("--dest", required=True)
    args = parser.parse_args()
    try:
        if args.command == "fetch":
            fetch(args.cache, args.host, args.sources)
        elif args.command == "stage":
            stage(args.cache, args.dest, args.host)
        else:
            print(json.dumps(verify(args.dest), indent=2))
    except (OSError, ValueError) as error:
        parser.exit(2, f"error: {error}\n")


if __name__ == "__main__":
    main()
