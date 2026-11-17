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
from contextlib import contextmanager

from decrypt_self import Self, SELF_TYPES, check_elf, find_other_key, find_self_key, parse_keys
from prepare_entitlement import CONTENT_ID, prepare as prepare_unlock
from setup_inputs import HASHES, ELF_HASHES, digest, sfo_title, inspect_title, print_checks, require_valid, validate_elf

TITLE_ID = "NPUB30154"
EBOOT_SHA256 = HASHES["USRDIR/EBOOT.BIN"]
ELF_SHA256 = ELF_HASHES


def validate_title(title, allow_unknown=False):
    checks = inspect_title(title, allow_unknown)
    print_checks([item for item in checks if item["status"] != "ok"])
    require_valid(checks)


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


def decrypt_eboot(title, *, keys=None, rpcs3_source=None, manual_elf=None, rpcs3=None, allow_unknown=False):
    validate_title(title, allow_unknown)
    output = title / "USRDIR/EBOOT.elf"
    if manual_elf:
        validate_elf(manual_elf, allow_unknown, ELF_SHA256)
        elf = Path(manual_elf).read_bytes()
        if output.is_file() and digest(output) == hashlib.sha256(elf).hexdigest():
            return output
    elif output.is_file():
        try:
            validate_elf(output, allow_unknown, ELF_SHA256)
            return output
        except ValueError:
            if not (keys or rpcs3_source or rpcs3):
                raise
        elf = None
    else:
        elf = None
    if elf is None and rpcs3:
        elf = decrypt_with_rpcs3(title / "USRDIR/EBOOT.BIN", rpcs3)
    if elf is None:
        elf = portable_decrypt(title, keys, rpcs3_source)
    # Validate even explicitly accepted versions structurally before publication.
    from setup_inputs import elf_structure
    elf_structure(elf)
    if hashlib.sha256(elf).hexdigest() not in ELF_SHA256:
        if not allow_unknown:
            raise ValueError("Decrypted executable does not match the verified Tank Battles image")
        print("[warning] Untested decrypted ELF accepted explicitly; compatibility is not established.")
    descriptor, temporary = tempfile.mkstemp(prefix=".EBOOT-", dir=output.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(elf)
        os.chmod(temporary, 0o644)
        os.replace(temporary, output)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return output


def decrypt_with_rpcs3(encrypted, executable):
    """Run only an explicitly supplied binary against a disposable copy."""
    executable = Path(executable).resolve()
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise ValueError("--rpcs3 must point to your executable RPCS3 binary or AppImage")
    with tempfile.TemporaryDirectory(prefix="tank-battles-decrypt-") as directory:
        work = Path(directory)
        copied = work / "EBOOT.BIN"
        shutil.copyfile(encrypted, copied)
        env = os.environ.copy()
        env.update(XDG_CONFIG_HOME=str(work / "config"), XDG_CACHE_HOME=str(work / "cache"),
                   QT_QPA_PLATFORM="offscreen", APPIMAGE_EXTRACT_AND_RUN="1")
        try:
            # RPCS3 may print key material on failure. Do not forward its output.
            result = subprocess.run([str(executable), "--headless", "--decrypt", str(copied)],
                                    cwd=work, env=env, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
        except subprocess.TimeoutExpired:
            raise ValueError("RPCS3 decryption timed out. Decrypt manually and supply --elf PATH.") from None
        output = work / "EBOOT.elf"
        if result.returncode or not output.is_file():
            raise ValueError("RPCS3 did not produce EBOOT.elf. Decrypt manually and supply --elf PATH, or use --keys FILE.")
        return output.read_bytes()


def portable_decrypt(title, keys, rpcs3_source):
    try:
        sf = Self((title / "USRDIR/EBOOT.BIN").read_bytes())
        if sf.self_type != 8 or sf.npdrm_license() != 3:
            raise ValueError("Expected this title's free NPDRM executable; paid unlock is verified separately")
    except (struct.error, IndexError):
        raise ValueError("Invalid or truncated EBOOT.BIN SELF. Restore your original executable.") from None
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
        raise ValueError("Decrypting EBOOT.BIN requires --keys FILE (or PS3_KEYS/game/keys), --rpcs3-source PATH, --rpcs3 EXECUTABLE or a manually decrypted --elf FILE. No keys are downloaded.")
    try:
        elf = sf.to_elf(erk, riv, log=lambda _: None, klic=free, klic_key=wrapping)
        valid = check_elf(elf, sf.e_entry, log=lambda _: None)
    except (ValueError, struct.error, IndexError):
        raise ValueError("SELF decryption failed; check the supplied key file and original dump") from None
    if not valid:
        raise ValueError("SELF decryption failed; check the supplied key file and original dump")
    return elf


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


def read_record(path):
    try:
        result = json.loads(path.read_text())
        return result if isinstance(result, dict) else {}
    except (OSError, ValueError):
        return {}


def setup_options(args, config, root, resolve):
    data = resolve(config["paths"]["gamedata_root"])
    local = data / "input"
    options = {name: resolve(getattr(args, name)) if getattr(args, name, None) else None
               for name in ("pkg", "game_dir", "keys", "rpcs3_source", "elf", "rpcs3", "unlock", "rap")}
    if options["keys"] is None and (data / "keys").is_file():
        options["keys"] = data / "keys"
    title = resolve(config["paths"]["vfs_root"])
    options.update(title=title, data=data, local=local,
                   exdata=resolve(config["paths"]["hdd0_root"]) / "home/00000001/exdata")
    if not options["pkg"] and not options["game_dir"] and not (title / "USRDIR/EBOOT.BIN").is_file():
        packages = sorted(local.glob("*.[pP][kK][gG]"))
        if len(packages) == 1:
            options["pkg"] = packages[0]
        elif len(packages) > 1:
            raise ValueError("Multiple packages in game/input/. Select the original NPUB30154 package with --pkg PATH.")
    return options


@contextmanager
def input_title(options, config, resolve):
    """Extract a package only into disposable storage; inspection never installs."""
    package, source, title = (options[name] for name in ("pkg", "game_dir", "title"))
    if package:
        package_title(package)
        record = read_record(options["data"] / "setup.json")
        cached = (record.get("package_sha256") == digest(package)
                  and all(item["status"] == "ok" for item in inspect_title(title)))
        if cached:
            yield title
        else:
            with tempfile.TemporaryDirectory(prefix="tank-battles-package-") as temporary:
                extracted = Path(temporary) / TITLE_ID
                subprocess.run([sys.executable, str(resolve(config["paths"]["sdk"]) / "tools/pkg_extract.py"),
                                str(package), str(extracted)], check=True)
                yield extracted
    else:
        yield source or title


def inspect_setup(args, options, title):
    allow_unknown = getattr(args, "allow_unknown_version", False)
    checks = inspect_title(title, allow_unknown)

    def item(name, path, status, action):
        checks.append(dict(name=name, path=str(path), status=status, action=action))

    elf = options["elf"] or title / "USRDIR/EBOOT.elf"
    provider = next((name for name in ("rpcs3", "keys", "rpcs3_source") if options[name]), None)
    if elf.is_file():
        try:
            sha = validate_elf(elf, allow_unknown, ELF_SHA256)
            item("EBOOT.elf", elf, "ok" if sha in ELF_SHA256 else "warning", "" if sha in ELF_SHA256 else "Untested version accepted explicitly.")
        except (OSError, ValueError) as error:
            if provider and not options["elf"]:
                item("EBOOT.elf", elf, "ready", "Cached ELF is invalid; will regenerate a verified copy using --" + provider.replace("_", "-") + ".")
            else:
                item("EBOOT.elf", elf, "error", str(error))
    elif options["elf"]:
        item("EBOOT.elf", elf, "error", "Missing manual executable. Supply your decrypted EBOOT.elf with --elf FILE.")
    else:
        item("EBOOT.elf", elf, "ready" if provider else "error",
             "Will decrypt a copy using --" + provider.replace("_", "-") + "." if provider else
             "Supply --elf FILE, --keys FILE, --rpcs3 EXECUTABLE or --rpcs3-source PATH to decrypt the original EBOOT.BIN.")
    for name in ("keys", "rpcs3_source", "rpcs3"):
        path = options[name]
        if path and not (path.is_dir() if name == "rpcs3_source" else path.is_file()):
            item(name, path, "error", "Supply an existing --" + name.replace("_", "-") + " path.")
        elif name == "rpcs3" and path and not os.access(path, os.X_OK):
            item(name, path, "error", "The supplied RPCS3 file must be executable.")
    for name, suffix in (("unlock", ".edat"), ("rap", ".rap")):
        destination = options["exdata"] / (CONTENT_ID + suffix)
        selected = options[name]
        if not selected:
            if destination.is_file():
                selected = destination
            else:
                candidates = sorted(options["local"].glob("*" + suffix))
                if len(candidates) > 1:
                    item(name, options["local"], "error", "Multiple candidates. Select --" + name + " FILE explicitly.")
                elif candidates:
                    selected = candidates[0]
        options[name] = selected
        if selected:
            try:
                if name == "unlock":
                    options["license"] = validate_unlock(selected)
                elif selected.stat().st_size != 16:
                    raise ValueError("Purchased RAP must be exactly 16 bytes")
                item(name, selected, "ok", "")
            except (OSError, ValueError) as error:
                item(name, selected, "error", str(error))
        elif name == "unlock":
            item(name, destination, "warning", "Purchased unlock is missing. Add --unlock FILE or put it in game/input/; continuing gives trial access.")
        else:
            item(name, destination, "optional", "A RAP is required only for a license-2 unlock without a valid prepared sidecar.")
    if options.get("license") == 2 and not options["rap"]:
        sidecar = options["exdata"] / (CONTENT_ID + ".edat.rifkey")
        if not sidecar.is_file() or sidecar.stat().st_size != 16:
            item("license-2 RAP", sidecar, "error", "Provide --rap FILE (16 bytes) for your purchased license-2 unlock.")
    return checks


def check_game(args, config, root, resolve):
    options = setup_options(args, config, root, resolve)
    with input_title(options, config, resolve) as title:
        checks = inspect_setup(args, options, title)
        print_checks(checks)
        return checks


def install_game(args, config, root, resolve):
    options = setup_options(args, config, root, resolve)
    title, package = options["title"], options["pkg"]
    if resolve(config["game"]["elf"]) != (title / "USRDIR/EBOOT.elf").resolve():
        raise ValueError("Configured game.elf must be USRDIR/EBOOT.elf under paths.vfs_root")
    allow_unknown = getattr(args, "allow_unknown_version", False)
    kwargs = dict(keys=options["keys"], rpcs3_source=options["rpcs3_source"],
                  manual_elf=options["elf"], rpcs3=options["rpcs3"], allow_unknown=allow_unknown)
    with input_title(options, config, resolve) as source:
        checks = inspect_setup(args, options, source)
        print_checks(checks)
        require_valid(checks)
        if source.resolve() != title.resolve():
            if source.resolve().is_relative_to(title.resolve()) or title.resolve().is_relative_to(source.resolve()):
                raise ValueError("--game-dir must be separate from the installed title")
            title.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=".tank-battles-import-", dir=title.parent) as temporary:
                staging = Path(temporary) / TITLE_ID
                shutil.copytree(source, staging, ignore=shutil.ignore_patterns("*.dec*", "*.unedat", "*.log", "*.rifkey"))
                # Fully verify staged content before replacing an installed title.
                decrypt_eboot(staging, **kwargs)
                previous = Path(temporary) / "previous"
                if title.exists():
                    title.rename(previous)
                try:
                    staging.rename(title)
                except OSError:
                    if previous.exists():
                        previous.rename(title)
                    raise
    elf = decrypt_eboot(title, **kwargs)
    expected = resolve(config["game"]["elf"])
    if elf.resolve() != expected:
        raise ValueError("Configured game.elf must be USRDIR/EBOOT.elf under paths.vfs_root")
    exdata = options["exdata"]
    exdata.mkdir(parents=True, exist_ok=True)
    for option, suffix in ((options["unlock"], ".edat"), (options["rap"], ".rap")):
        destination = exdata / (CONTENT_ID + suffix)
        if option:
            if suffix == ".rap" and option.stat().st_size != 16:
                raise ValueError("Purchased RAP must be exactly 16 bytes")
            if suffix == ".edat":
                validate_unlock(option)
            if option.resolve() != destination.resolve():
                shutil.copyfile(option, destination)
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
    record = options["data"] / "setup.json"
    previous = read_record(record)
    manifest = {"title_id": TITLE_ID, "encrypted_eboot_sha256": digest(title / "USRDIR/EBOOT.BIN"), "elf_sha256": digest(elf), "input_sha256": {name: digest(title / name) for name in HASHES}, "package_sha256": digest(package) if package else (previous.get("package_sha256") if not options["game_dir"] else None), "decryption_provider": "manual ELF" if options["elf"] else "RPCS3 executable" if options["rpcs3"] else "portable decryptor or verified cached ELF", "decryptor_sha256": digest(Path(__file__).with_name("decrypt_self.py")), "unknown_version_accepted": allow_unknown, "unlock_present": unlock.exists(), "unlock_license_type": validate_unlock(unlock) if unlock.exists() else None}
    record.parent.mkdir(parents=True, exist_ok=True)
    from setup_pipeline import atomic_json
    atomic_json(record, manifest)
    print("[setup] Original game imported and executable verified. Asset EDATs decrypt at first launch.")
    return elf
