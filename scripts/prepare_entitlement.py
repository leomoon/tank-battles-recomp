#!/usr/bin/env python3
"""Derive a local SDK RIF sidecar from the user's purchased RAP, without logging keys."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tomllib
from Crypto.Cipher import AES
from port import ROOT, resolve
from private_files import publish_private, restrict_private

CONTENT_ID = "UP2006-NPUB30154_00-TANKBATTLES00KEY"
TABLE_SHA256 = "07784509a2360eeec07ee4bd130fbdfd9d510133a16619b5ee8bc0ba9377b7e7"


def validate_tables(tables):
    if set(tables) != {"RAP_KEY", "RAP_PBOX", "RAP_E1", "RAP_E2"} or any(len(value) != 16 for value in tables.values()):
        raise ValueError("Invalid RAP format-table sizes or names")
    encoded = json.dumps({key: value.hex() for key, value in tables.items()}, sort_keys=True, separators=(",", ":")).encode()
    if hashlib.sha256(encoded).hexdigest() != TABLE_SHA256:
        raise ValueError("RAP format tables differ from the pinned, tested public specification")
    return tables

def platform_tables(checkout=None):
    if checkout is None:
        data = json.loads(Path(__file__).with_name("rap_format.json").read_text())
        tables = {name: bytes.fromhex(value) for name, value in data["tables"].items()}
        return validate_tables(tables)
    # An explicit source checkout remains available for independent verification.
    header = (Path(checkout) / "rpcs3/Crypto/key_vault.h").read_text()
    tables = {}
    for name in ("RAP_KEY", "RAP_PBOX", "RAP_E1", "RAP_E2"):
        match = re.search(r"\b" + name + r"\[[^]]+\]\s*=\s*\{([^}]+)\}", header)
        if not match:
            raise ValueError("RPCS3 format tables not found")
        values = bytes(int(v, 16) for v in re.findall(r"0x([0-9a-fA-F]{2})\b", match[1]))
        if len(values) != 16:
            raise ValueError("Unexpected RPCS3 format-table length")
        tables[name] = values
    return validate_tables(tables)

def derive_rif(rap, tables):
    if len(rap) != 16:
        raise ValueError("Purchased RAP must be exactly 16 bytes")
    validate_tables(tables)
    value = bytearray(AES.new(tables["RAP_KEY"], AES.MODE_ECB).decrypt(rap))
    permutation = tables["RAP_PBOX"]
    if sorted(permutation) != list(range(16)):
        raise ValueError("Invalid format permutation")
    for _ in range(5):
        value = bytearray(a ^ b for a, b in zip(value, tables["RAP_E1"]))
        for position in range(15, 0, -1):
            value[permutation[position]] ^= value[permutation[position - 1]]
        borrow = 0
        for index in permutation:
            before = (value[index] - borrow) & 255
            subtraction = tables["RAP_E2"][index]
            if not (borrow and before == 255):
                borrow = int(before < subtraction)
            value[index] = (before - subtraction) & 255
    return bytes(value)

def prepare(directory, checkout=None):
    directory = Path(directory)
    edat = directory / (CONTENT_ID + ".edat")
    rap = directory / (CONTENT_ID + ".rap")
    if not edat.is_file():
        raise ValueError("Purchased unlock EDAT is missing")
    rif = derive_rif(rap.read_bytes(), platform_tables(checkout))
    output = Path(str(edat) + ".rifkey")
    if not output.exists() and not output.is_symlink():
        try:
            publish_private(output, rif)
        except FileExistsError:
            pass  # A concurrent preparer published first. Compare it below.
    restrict_private(output)
    if output.read_bytes() != rif:
        raise ValueError("Existing derived sidecar differs; inspect your local entitlement files")
    return output

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rpcs3-source", help="Optional local RPCS3 source checkout to verify against instead of bundled public format tables")
    parser.add_argument("--exdata", help="Local purchased unlock directory; defaults to the configured HDD0 profile")
    args = parser.parse_args()
    try:
        config = tomllib.loads((ROOT / "config.toml").read_text())
        directory = resolve(args.exdata) if args.exdata else resolve(os.environ.get("PS3_HDD0_ROOT") or config["paths"]["hdd0_root"]) / "home/00000001/exdata"
        from setup_game import validate_unlock
        if validate_unlock(directory / (CONTENT_ID + ".edat")) == 3:
            print("Original license-3 unlock needs no RAP or sidecar; the runtime will authenticate it.")
        else:
            prepare(directory, resolve(args.rpcs3_source) if args.rpcs3_source else None)
            print("Prepared private unlock RIF sidecar; the runtime will authenticate the purchased EDAT.")
    except (OSError, ValueError) as error:
        parser.exit(1, f"error: {error}\n")

if __name__ == "__main__":
    main()
