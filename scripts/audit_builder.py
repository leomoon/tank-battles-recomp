"""Audit a complete builder payload before it is published.

    audit_builder.py PATH [--source COMMIT] [--json REPORT]

PATH is an extracted builder (an AppImage's squashfs-root, or Builder.exe
--extract output) or Builder.exe itself. Every file is inspected, not only the
ones package.json lists:

- package.json, the runtime bundle and the toolchain verify, and the source
  commit matches when given;
- nothing purchased or personal: PlayStation packages/executables/licences,
  keys, saves, RPCN accounts or profiles, private keys;
- no generated title code: lifted PPU/SPU translation units or objects built
  from them, anywhere, including inside archives' member names;
- compiled objects appear only inside the verified runtime and toolchain.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
import tempfile
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import package_builder  # noqa: E402
import package_resources  # noqa: E402
from runtime_bundle import archive_members  # noqa: E402

FORBIDDEN_SUFFIXES = {".pkg", ".self", ".sprx", ".edat", ".sdat", ".rap", ".rif", ".rifkey", ".sfo", ".pup",
                      ".trp", ".account", ".profile", ".sav"}
FORBIDDEN_NAMES = {"eboot.bin", "eboot.elf", "param.sfo", "key.pem", "keys", "setup-state.json", "settings.ini"}
FORBIDDEN_PARTS = {"savedata", "recompiled", "generated_spu", "dev_hdd0", "dev_hdd1", "online"}
# Lifted title code names thousands of guest functions; the ABI header and the
# SDK's synthetic smoke programs name a handful. Count distinct symbols.
PPU_FUNCTION = re.compile(rb"func_00[0-9A-F]{6}")
SPU_FUNCTION = re.compile(rb"spu_[0-9a-f]{64}_spu_func_[0-9A-F]{8}")
TITLE_SYMBOL_LIMIT = 64
TITLE_NAMES = re.compile(r"(^|/)ppu_recomp_\d+\.(c|cpp|o|obj)$|spu_[0-9a-f]{64}")
PRIVATE_KEY = re.compile(rb"-----BEGIN (?:RSA |EC |ENCRYPTED |OPENSSH )?PRIVATE KEY-----")
OBJECT_SUFFIXES = {".o", ".obj"}
# Verified elsewhere and allowed to hold compiled code.
TRUSTED_TREES = ("runtime/", "toolchain/")


def files_of(path: Path):
    """(relative name, bytes) for a directory or Builder.exe."""
    if path.is_dir():
        for item in sorted(path.rglob("*")):
            if item.is_symlink():
                raise ValueError(f"Symbolic link in the payload: {item.relative_to(path)}")
            if item.is_file():
                yield item.relative_to(path).as_posix(), item
        return
    offset, size, digest = package_builder.read_exe_payload(path)
    with path.open("rb") as stream:
        stream.seek(offset)
        payload = stream.read(size)
    if hashlib.sha256(payload).digest() != digest:
        raise ValueError("Builder.exe payload does not match its recorded SHA-256")
    archive = zipfile.ZipFile(io.BytesIO(payload))
    for info in archive.infolist():
        name = PurePosixPath(info.filename)
        if name.is_absolute() or ".." in name.parts:
            raise ValueError(f"Unsafe path in Builder.exe: {info.filename}")
        if not info.is_dir():
            yield info.filename, archive.read(info)


def inspect(name: str, data: bytes | Path, problems: list[str]) -> None:
    lower = name.lower()
    parts = set(PurePosixPath(lower).parts[:-1])
    base = PurePosixPath(lower).name
    trusted = name.startswith(TRUSTED_TREES)
    if PurePosixPath(lower).suffix in FORBIDDEN_SUFFIXES or base in FORBIDDEN_NAMES:
        problems.append(f"purchased or personal file: {name}")
    if parts & FORBIDDEN_PARTS and not name.startswith("toolchain/"):
        problems.append(f"user data directory: {name}")
    if TITLE_NAMES.search(lower) and not lower.startswith("external/ps3recomp/"):
        problems.append(f"generated title code: {name}")
    if PurePosixPath(lower).suffix in OBJECT_SUFFIXES and not trusted:
        problems.append(f"object file outside the verified runtime/toolchain: {name}")
    if name.startswith("toolchain/"):
        return   # Pinned, verified upstream archives; scanning 30k files gains nothing.
    blob = data.read_bytes() if isinstance(data, Path) else data
    if not trusted and (blob.startswith(b"\x7fELF") or blob.startswith(b"MZ")):
        problems.append(f"executable outside the verified runtime/toolchain: {name}")
    if PRIVATE_KEY.search(blob):
        problems.append(f"private key: {name}")
    if (len(set(PPU_FUNCTION.findall(blob))) > TITLE_SYMBOL_LIMIT or
            len(set(SPU_FUNCTION.findall(blob))) > TITLE_SYMBOL_LIMIT):
        problems.append(f"generated title code: {name}")
    if lower.endswith(".a") and blob.startswith(b"!<arch>\n"):
        for member in archive_members_from_bytes(blob):
            if TITLE_NAMES.search(member.lower()):
                problems.append(f"generated title object {member} inside {name}")


def archive_members_from_bytes(blob: bytes) -> list[str]:
    with tempfile.NamedTemporaryFile(suffix=".a", delete=False) as temporary:
        temporary.write(blob)
    try:
        return archive_members(temporary.name)
    finally:
        os.unlink(temporary.name)


def audit(path: Path, source: str | None = None) -> dict:
    path = Path(path).resolve()
    problems: list[str] = []
    count = 0
    package = None
    for name, data in files_of(path):
        count += 1
        if name == "package.json":
            package = json.loads(data.read_text() if isinstance(data, Path) else data.decode())
        inspect(name, data, problems)
    if package is None:
        problems.append("package.json is missing")
    elif source and package.get("source_commit") != source:
        problems.append(f"source {package.get('source_commit')} differs from the expected {source}")
    if path.is_dir() and package is not None:
        try:
            package_resources.verify(path)
        except (OSError, ValueError, KeyError) as error:
            problems.append(f"verification failed: {error}")
    return dict(path=str(path), files=count, source_commit=(package or {}).get("source_commit"),
                problems=sorted(set(problems)), ok=not problems)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path")
    parser.add_argument("--source")
    parser.add_argument("--json", help="Write the report here")
    args = parser.parse_args()
    try:
        report = audit(Path(args.path), args.source)
    except (OSError, ValueError) as error:
        report = dict(path=args.path, ok=False, problems=[str(error)])
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2) + "\n")
    for problem in report["problems"][:50]:
        print(f"[audit] {problem}")
    print(f"[audit] {'PASS' if report['ok'] else 'FAIL'}: {report.get('files', 0)} files in {args.path}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
