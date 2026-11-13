#!/usr/bin/env python3
"""Derive a local SDK RIF sidecar from the user's purchased RAP, without logging keys."""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import re
import sys
import tomllib
from Crypto.Cipher import AES
from port import ROOT, resolve

CONTENT_ID = "UP2006-NPUB30154_00-TANKBATTLES00KEY"

def platform_tables(checkout):
    # Public format constants remain in the user's RPCS3 source checkout.
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
    return tables

def derive_rif(rap, tables):
    if len(rap) != 16:
        raise ValueError("Purchased RAP must be exactly 16 bytes")
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

def prepare(directory, checkout):
    directory = Path(directory)
    edat = directory / (CONTENT_ID + ".edat")
    rap = directory / (CONTENT_ID + ".rap")
    if not edat.is_file():
        raise ValueError("Purchased unlock EDAT is missing")
    rif = derive_rif(rap.read_bytes(), platform_tables(checkout))
    output = Path(str(edat) + ".rifkey")
    if output.exists():
        if output.read_bytes() != rif:
            raise ValueError("Existing derived sidecar differs; inspect your local entitlement files")
        output.chmod(0o600)
        return output
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(rif)
    return output

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rpcs3-source", default="../rpcs3", help="Local RPCS3 source checkout containing public format tables")
    parser.add_argument("--exdata", help="Local purchased unlock directory; defaults to the configured HDD0 profile")
    args = parser.parse_args()
    try:
        config = tomllib.loads((ROOT / "config.toml").read_text())
        directory = resolve(args.exdata) if args.exdata else resolve(os.environ.get("PS3_HDD0_ROOT") or config["paths"]["hdd0_root"]) / "home/00000001/exdata"
        prepare(directory, resolve(args.rpcs3_source))
        print("Prepared local unlock RIF sidecar; private key bytes were not logged.")
    except (OSError, ValueError) as error:
        parser.exit(1, f"error: {error}\n")

if __name__ == "__main__":
    main()
