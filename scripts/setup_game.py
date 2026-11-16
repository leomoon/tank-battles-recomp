"""Import a purchased NPUB30154 package and prepare its executable locally."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile

from decrypt_self import Self, SELF_TYPES, check_elf, find_other_key, find_self_key, parse_keys
from prepare_entitlement import CONTENT_ID, prepare as prepare_unlock

TITLE_ID = "NPUB30154"
EBOOT_SHA256 = "ca48282696515e0c706b93acca0b1ae33a07a0d3ee60b5a737156524ff1c39e1"
ELF_SHA256 = {
    "b81cc67ed5cf306af601a22f8de003c06857dd71035f0dbf190e918e355e0538",  # RPCS3
    "31c0bba8b4cd6fc90a374b387c76e69c591081c0dc8fc8ee7b82e4fa3594c533",  # portable tool
}


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def sfo_title(path):
    data = Path(path).read_bytes()
    if len(data) < 20 or data[:4] != b"\0PSF":
        raise ValueError("Invalid PARAM.SFO")
    _, _, keys, values, count = struct.unpack_from("<5I", data)
    if count > 4096 or 20 + count * 16 > len(data):
        raise ValueError("Invalid PARAM.SFO entry table")
    for index in range(count):
        key, fmt, length, maximum, offset = struct.unpack_from("<HHIII", data, 20 + index * 16)
        end = data.find(b"\0", keys + key, values)
        if end < 0 or values + offset + length > len(data):
            raise ValueError("Invalid PARAM.SFO string bounds")
        if data[keys + key:end] == b"TITLE_ID":
            return data[values + offset:values + offset + length].rstrip(b"\0").decode("ascii")
    raise ValueError("PARAM.SFO has no TITLE_ID")


def validate_title(title):
    if sfo_title(title / "PARAM.SFO") != TITLE_ID:
        raise ValueError("This port requires Tank Battles NPUB30154")
    if digest(title / "USRDIR/EBOOT.BIN") != EBOOT_SHA256:
        raise ValueError("Unsupported EBOOT.BIN version: expected the original NPUB30154 executable")
    for name in ("assets_common.edat", "assets_images.edat", "assets_sound.edat", "assets_ps3.edat"):
        if not (title / "USRDIR" / name).is_file():
            raise ValueError("Incomplete game: missing " + name)


def source_keys(checkout, self_file):
    """Read only the needed key entries from an explicitly supplied local source.

    Nothing is downloaded, printed, or written to a key file. Normal users can
    instead supply the scetool-format file accepted by decrypt_self.py.
    """
    crypto = Path(checkout) / "rpcs3/Crypto"
    text = (crypto / "key_vault.cpp").read_text()
    pattern = r'0x([0-9a-fA-F]+),\s*KEY_NPDRM,\s*"([0-9a-fA-F]+)",\s*"([0-9a-fA-F]+)"'
    matches = [m for m in re.finditer(pattern, text) if int(m[1], 16) == self_file.key_revision]
    if self_file.self_type != 8 or len(matches) != 1:
        raise ValueError("No unique NPDRM key revision in the supplied RPCS3 source; use --keys")
    header = (crypto / "key_vault.h").read_text()
    def table(name):
        match = re.search(r"\b" + name + r"\[[^]]+\]\s*=\s*\{([^}]+)\}", header)
        if not match:
            raise ValueError("Missing NPDRM format table in supplied source")
        value = bytes(int(v, 16) for v in re.findall(r"0x([0-9a-fA-F]{2})\b", match[1]))
        if len(value) != 16:
            raise ValueError("Invalid NPDRM format-table size")
        return value
    return bytes.fromhex(matches[0][2]), bytes.fromhex(matches[0][3]), table("NP_KLIC_FREE"), table("NP_KLIC_KEY")


def decrypt_eboot(title, *, keys=None, rpcs3_source=None):
    validate_title(title)
    output = title / "USRDIR/EBOOT.elf"
    if output.is_file() and digest(output) in ELF_SHA256:
        return output
    sf = Self((title / "USRDIR/EBOOT.BIN").read_bytes())
    if sf.self_type != 8 or sf.npdrm_license() != 3:
        raise ValueError("Expected this title's free NPDRM executable; paid unlock is verified separately")
    if keys:
        entries = parse_keys(Path(keys).read_text())
        try:
            erk, riv = find_self_key(entries, sf.key_revision, SELF_TYPES[sf.self_type])
            free, wrapping = find_other_key(entries, "NP_klic_free"), find_other_key(entries, "NP_klic_key")
        except KeyError:
            raise ValueError("Key file needs revision-0001 NPDRM erk/riv, NP_klic_free and NP_klic_key entries") from None
    elif rpcs3_source:
        erk, riv, free, wrapping = source_keys(rpcs3_source, sf)
    else:
        raise ValueError("Decrypting EBOOT.BIN requires your scetool-format --keys FILE (or PS3_KEYS). An existing local RPCS3 source can also be passed with --rpcs3-source PATH. No keys are downloaded.")
    try:
        elf = sf.to_elf(erk, riv, log=lambda _: None, klic=free, klic_key=wrapping)
        valid = check_elf(elf, sf.e_entry, log=lambda _: None)
    except (ValueError, struct.error, IndexError):
        raise ValueError("SELF decryption failed; check the supplied key file and original dump") from None
    if not valid or hashlib.sha256(elf).hexdigest() not in ELF_SHA256:
        raise ValueError("Decrypted executable does not match the verified Tank Battles image")
    descriptor, temporary = tempfile.mkstemp(prefix=".EBOOT-", dir=output.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(elf)
        os.chmod(temporary, 0o644)
        os.replace(temporary, output)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return output


def package_title(package):
    with Path(package).open("rb") as stream:
        header = stream.read(0x80)
    if len(header) != 0x80 or header[:4] != b"\x7fPKG":
        raise ValueError("Not a PS3 PKG file")
    content = header[0x30:0x60].rstrip(b"\0").decode("ascii")
    if not re.fullmatch(r"[A-Z]{2}\d{4}-NPUB30154_00-[A-Z0-9_]{16}", content):
        raise ValueError("The PKG is not the supported Tank Battles NPUB30154 release")


def validate_unlock(path):
    data = Path(path).read_bytes()
    if len(data) != 352 or data[:4] != b"NPD\0" or struct.unpack_from(">I", data, 4)[0] != 1 or struct.unpack_from(">I", data, 8)[0] not in (2, 3) or struct.unpack_from(">I", data, 12)[0] != 1:
        raise ValueError("Unsupported unlock EDAT: expected the original version-1 license-2 or license-3 unlock")
    if data[16:64].rstrip(b"\0") != CONTENT_ID.encode():
        raise ValueError("Unlock EDAT belongs to a different title")
    return struct.unpack_from(">I", data, 8)[0]


def install_game(args, config, root, resolve):
    title = resolve(config["paths"]["vfs_root"])
    local = root / "game/input"
    package = resolve(args.pkg) if args.pkg else None
    source = resolve(args.game_dir) if args.game_dir else None
    if package is None and source is None and not (title / "USRDIR/EBOOT.BIN").is_file():
        packages = list(local.glob("*.pkg"))
        if len(packages) != 1:
            raise ValueError("Supply --pkg PATH, --game-dir PATH, or put one Tank Battles PKG in game/input/")
        package = packages[0]
    keys = resolve(args.keys) if args.keys else None
    if keys is None and (root / "game/keys").is_file():
        keys = root / "game/keys"
    checkout = resolve(args.rpcs3_source) if args.rpcs3_source else None
    if package or source:
        if package:
            package_title(package)
        else:
            validate_title(source)
            if source.resolve() == title.resolve() or source.resolve().is_relative_to(title.resolve()) or title.resolve().is_relative_to(source.resolve()):
                raise ValueError("--game-dir must be separate from the installed title")
        title.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".tank-battles-import-", dir=title.parent) as temporary:
            staging = Path(temporary) / TITLE_ID
            if package:
                subprocess.run([sys.executable, str(resolve(config["paths"]["sdk"]) / "tools/pkg_extract.py"), str(package), str(staging)], check=True)
            else:
                shutil.copytree(source, staging, ignore=shutil.ignore_patterns("*.elf", "*.ELF", "*.dec*", "*.unedat", "*.log", "*.rifkey"))
            # Validate and decrypt before replacing an existing installed title.
            decrypt_eboot(staging, keys=keys, rpcs3_source=checkout)
            previous = Path(temporary) / "previous"
            if title.exists():
                title.rename(previous)
            try:
                staging.rename(title)
            except OSError:
                if previous.exists():
                    previous.rename(title)
                raise
    elf = decrypt_eboot(title, keys=keys, rpcs3_source=checkout)
    expected = resolve(config["game"]["elf"])
    if elf.resolve() != expected:
        raise ValueError("Configured game.elf must be USRDIR/EBOOT.elf under paths.vfs_root")
    exdata = resolve(config["paths"]["hdd0_root"]) / "home/00000001/exdata"
    exdata.mkdir(parents=True, exist_ok=True)
    for option, suffix in ((args.unlock, ".edat"), (args.rap, ".rap")):
        candidates = list(local.glob("*" + suffix)) if not option else [resolve(option)]
        destination = exdata / (CONTENT_ID + suffix)
        if option or (not destination.exists() and len(candidates) == 1):
            if len(candidates) != 1 or not candidates[0].is_file():
                raise ValueError("Missing purchased unlock " + suffix)
            if suffix == ".rap" and candidates[0].stat().st_size != 16:
                raise ValueError("Purchased RAP must be exactly 16 bytes")
            if suffix == ".edat":
                validate_unlock(candidates[0])
            if candidates[0].resolve() != destination.resolve():
                shutil.copyfile(candidates[0], destination)
            destination.chmod(0o600 if suffix == ".rap" else 0o644)
    unlock = exdata / (CONTENT_ID + ".edat")
    if unlock.exists():
        license = validate_unlock(unlock)
        if license == 3:
            print("[setup] Original license-3 unlock installed; runtime authenticates it without a RAP.")
        elif (exdata / (CONTENT_ID + ".rap")).exists():
            prepare_unlock(exdata)
            print("[setup] Purchased unlock sidecar prepared; runtime will verify the unlock.")
        elif not Path(str(unlock) + ".rifkey").exists():
            raise ValueError("Purchased unlock EDAT supplied without its RAP. Add --rap PATH.")
    else:
        print("[setup] No purchased unlock supplied: the game will have trial access.")
    record = root / "game/setup.json"
    previous = json.loads(record.read_text()) if record.is_file() else {}
    manifest = {"title_id": TITLE_ID, "encrypted_eboot_sha256": digest(title / "USRDIR/EBOOT.BIN"), "elf_sha256": digest(elf), "package_sha256": digest(package) if package else (previous.get("package_sha256") if not source else None), "decryptor": "scripts/decrypt_self.py (MIT)", "decryptor_sha256": digest(Path(__file__).with_name("decrypt_self.py")), "unlock_present": unlock.exists(), "unlock_license_type": validate_unlock(unlock) if unlock.exists() else None}
    (root / "game/setup.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print("[setup] Original game imported and executable verified. Asset EDATs decrypt at first launch.")
    return elf
