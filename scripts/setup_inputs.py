"""Read-only inspection of user-supplied Tank Battles files."""
import hashlib
from pathlib import Path
import struct

TITLE_ID = "NPUB30154"
HASHES = {
    "PARAM.SFO": "2857058f5131a73c916acc9301dfc3892abaa31755c92732006c31d6fa9849fc",
    "USRDIR/EBOOT.BIN": "ca48282696515e0c706b93acca0b1ae33a07a0d3ee60b5a737156524ff1c39e1",
    "USRDIR/assets_common.edat": "e1b58219e7c5e2b7296bb4e3e47475b00f6d4dfe9fa5cbc0d4dc8dad34105cf9",
    "USRDIR/assets_images.edat": "ce9f2835ef97c9d5c7de2efad4e7d3f09e5d9011bfe546bd06a506917be38537",
    "USRDIR/assets_ps3.edat": "80c5f4df23b521366da739d0c8f9361db7d4eef3c5779b531ab2881f621722ef",
    "USRDIR/assets_sound.edat": "ca8aaab8f378f9e865b8221bfb252da3f901284a71a1b8c8206db7cdcedf4eba",
}
ELF_HASHES = {
    "b81cc67ed5cf306af601a22f8de003c06857dd71035f0dbf190e918e355e0538",
    "31c0bba8b4cd6fc90a374b387c76e69c591081c0dc8fc8ee7b82e4fa3594c533",
}


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def sfo_title(path):
    data = Path(path).read_bytes()
    if len(data) < 20 or data[:4] != b"\0PSF":
        raise ValueError("Invalid PARAM.SFO: supply the original installed metadata")
    _, _, keys, values, count = struct.unpack_from("<5I", data)
    if not 20 <= keys <= values <= len(data) or count > 4096 or 20 + count * 16 > keys:
        raise ValueError("Invalid PARAM.SFO entry table")
    for index in range(count):
        key, fmt, length, maximum, offset = struct.unpack_from("<HHIII", data, 20 + index * 16)
        end = data.find(b"\0", keys + key, values)
        if end < 0 or length > maximum or values + offset + length > len(data):
            raise ValueError("Invalid PARAM.SFO string bounds")
        if data[keys + key:end] == b"TITLE_ID":
            return data[values + offset:values + offset + length].rstrip(b"\0").decode("ascii")
    raise ValueError("PARAM.SFO has no TITLE_ID")


def elf_structure(data):
    """Reject SELF files, wrong architectures and truncated/unmapped images."""
    if len(data) < 64 or data[:7] != b"\x7fELF\x02\x02\x01" or data[18:20] != b"\x00\x15":
        raise ValueError("Expected a decrypted ELF64 big-endian PPU executable; EBOOT.BIN is not a decrypted ELF")
    entry, phoff = struct.unpack_from(">QQ", data, 24)
    stride, count = struct.unpack_from(">HH", data, 54)
    if stride < 56 or not 0 < count <= 128 or phoff + stride * count > len(data):
        raise ValueError("Invalid or truncated ELF program-header table")
    loads = []
    for index in range(count):
        kind, flags, offset, address, physical, size, memory, align = struct.unpack_from(">II6Q", data, phoff + index * stride)
        if kind != 1:
            continue
        if size > memory or offset + size > len(data) or address + memory > 0x100000000:
            raise ValueError("Invalid or truncated ELF load segment")
        if size:
            loads.append((address, address + size, offset))
    segment = next(((start, offset) for start, end, offset in loads if start <= entry and entry + 8 <= end), None)
    if segment is None:
        raise ValueError("ELF entry OPD is not inside a populated load segment")
    start, offset = segment
    code, toc = struct.unpack_from(">II", data, offset + entry - start)
    if not all(any(lo <= address < hi for lo, hi, _ in loads) for address in (code, toc)):
        raise ValueError("ELF entry OPD points outside the loaded image")


def self_structure(data):
    if len(data) < 112 or data[:4] != b"SCE\0":
        raise ValueError("EBOOT.BIN must be the original encrypted SELF; supply a decrypted file using --elf")
    _, version, revision, kind, metadata, header_size, data_size = struct.unpack_from(">4sIHHIQQ", data)
    if kind != 1 or not metadata + 0x60 <= header_size <= len(data):
        raise ValueError("Invalid or truncated EBOOT.BIN SELF metadata bounds")
    _, app, elf, programs, sections, segments, firmware, control, control_size, _ = struct.unpack_from(">10Q", data, 32)
    if app + 24 > header_size or elf + 64 > header_size or control + control_size > header_size:
        raise ValueError("Invalid or truncated EBOOT.BIN SELF header offsets")
    if data[elf:elf + 7] != b"\x7fELF\x02\x02\x01" or data[elf + 18:elf + 20] != b"\x00\x15":
        raise ValueError("EBOOT.BIN does not contain a big-endian PPU executable")
    stride, count = struct.unpack_from(">HH", data, elf + 54)
    if stride != 56 or not 0 < count <= 128 or programs + stride * count > header_size:
        raise ValueError("Invalid or truncated EBOOT.BIN program headers")


def inspect_title(title, allow_unknown=False):
    checks = []
    for name, expected in HASHES.items():
        path = Path(title) / name
        item = dict(path=str(path), name=name, status="ok", action="", expected_sha256=expected)
        if not path.is_file():
            item.update(status="error", action="Restore " + name + " from your original NPUB30154 package or installed dump.")
        else:
            try:
                if name == "PARAM.SFO" and sfo_title(path) != TITLE_ID:
                    raise ValueError("Wrong title ID: provide NPUB30154, not another release")
                with path.open("rb") as stream:
                    magic = stream.read(4)
                if name.endswith("EBOOT.BIN"):
                    self_structure(path.read_bytes())
                if name.endswith(".edat") and magic != b"NPD\0":
                    raise ValueError("Restore the original encrypted asset EDAT")
                item["sha256"] = digest(path)
                if item["sha256"] != expected:
                    item.update(status="warning" if allow_unknown else "error",
                                action="Untested version: restore the tested original, or use --allow-unknown-version explicitly. Compatibility is not established.")
            except (OSError, ValueError, struct.error) as error:
                item.update(status="error", action=str(error))
        checks.append(item)
    return checks


def print_checks(checks):
    for item in checks:
        print(f'[{item["status"]}] {item["name"]}: {item["path"]}')
        if item.get("action"):
            print("  " + item["action"])


def require_valid(checks):
    errors = [item for item in checks if item["status"] == "error"]
    if errors:
        raise ValueError("\n".join(item["name"] + ": " + item["action"] for item in errors))


def validate_elf(path, allow_unknown=False, accepted=None):
    data = Path(path).read_bytes()
    elf_structure(data)
    sha = hashlib.sha256(data).hexdigest()
    if sha not in (ELF_HASHES if accepted is None else accepted):
        if not allow_unknown:
            raise ValueError("Untested EBOOT.elf hash: supply the tested decrypted executable, or use --allow-unknown-version explicitly")
        print("[warning] Untested decrypted ELF accepted explicitly; compatibility is not established.")
    return sha
