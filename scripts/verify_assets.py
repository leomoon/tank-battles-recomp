#!/usr/bin/env python3
"""Compare runtime asset IO to independently decrypted local references."""
from __future__ import annotations
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import subprocess
import sys
import tomllib
import uuid
from port import ROOT, resolve, launch_environment

ASSETS = ("assets_common.edat", "assets_images.edat", "assets_ps3.edat", "assets_sound.edat")
def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--references", default="game/reference-rpcs3/assets",
                        help="Independent RPCS3 .unedat files; never use runtime .dec files as their own reference")
    parser.add_argument("--key-log", required=True, help="Local RPCS3 log with this purchased title's asset KLIC; key stays in memory/stdin")
    args = parser.parse_args()
    config = tomllib.loads((ROOT / "config.toml").read_text())
    source = resolve(config["game"]["elf"]).parent
    references = resolve(args.references)
    raw = resolve(args.key_log).read_bytes()
    text = (gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw).decode(errors="replace")
    candidates = set(re.findall(r"KLIC(?:=|:)\s*(?:0x)?([0-9a-fA-F]{32})", text))
    if len(candidates) != 1:
        parser.error("Expected one distinct asset-key candidate; key values are not printed.")
    key = bytes.fromhex(next(iter(candidates)))
    text = raw = None
    candidates.clear()
    directory = ROOT / "analysis/step5"
    directory.mkdir(parents=True, exist_ok=True)
    manifest = directory / "assets.manifest"
    for name in ASSETS:
        if not (references / (name + ".unedat")).is_file():
            parser.error(f"Missing independent reference: {name}.unedat")
    manifest.write_text("".join(f"/dev_hdd0/game/{config['game']['title_id']}/USRDIR/{name}\t{references / (name+'.unedat')}\n"
                                for name in ASSETS))
    env = launch_environment(config)
    for name in ("TB_HLE_AUDIT_ONLY", "TB_HLE_INVENTORY", "TB_IMPORT_PROBE", "TB_DIAGNOSTIC_STALL"):
        env.pop(name, None)
    env.update(TB_ASSET_PROBE=str(manifest), TB_PATH_PROBE_TOKEN=uuid.uuid4().hex)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    command = [str(resolve(config["paths"]["build"]) / "TankBattlesRecomp"), str(resolve(config["game"]["elf"]))]
    result = subprocess.run(command, cwd=ROOT, env=env, input=key, capture_output=True, timeout=60)
    key = None
    log = (result.stdout + result.stderr).decode(errors="replace")
    log = re.sub(r"(?i)(?:0x)?[a-f0-9]{32,}", "[REDACTED_HEX]", log)
    (directory / "asset-probe.log").write_text(log)
    if result.returncode or "[asset-probe] ALL PASS files=4" not in log:
        raise SystemExit("Native asset probe failed; inspect analysis/step5/asset-probe.log")
    first_mtimes = {(source / (name+".dec")): (source / (name+".dec")).stat().st_mtime_ns for name in ASSETS}
    # Re-prime with the same transient key and verify stable-cache reuse in a new process.
    key_text = resolve(args.key_log).read_bytes()
    if key_text[:2] == b"\x1f\x8b": key_text = gzip.decompress(key_text)
    key = bytes.fromhex(re.findall(rb"KLIC(?:=|:)\s*(?:0x)?([0-9a-fA-F]{32})", key_text)[0].decode())
    key_text = None
    repeat = subprocess.run(command, cwd=ROOT, env=env, input=key, capture_output=True, timeout=60)
    key = None
    if repeat.returncode:
        raise SystemExit("Native repeated-cache probe failed")
    checks = []
    for name in ASSETS:
        cache = source / (name + ".dec")
        reference = references / (name + ".unedat")
        expected = int.from_bytes((source / name).read_bytes()[0x88:0x90], "big")
        match = sha(cache) == sha(reference)
        reused = cache.stat().st_mtime_ns == first_mtimes[cache]
        if not (match and reused and cache.stat().st_size == reference.stat().st_size == expected):
            raise SystemExit(f"Asset size, reference hash, or cache reuse check failed: {name}")
        checks.append(dict(asset=name, plaintext_size=expected, sha256=sha(reference),
                           full_reference_match=match, cache_reused=reused))
    (directory / "asset-verification.json").write_text(json.dumps(dict(
        assets=checks, native_layers=["PPU HLE", "raw syscalls", "generic C file API"],
        operations=["open","fstat","seek","read","EOF","close"],
        writable_paths=["cache","HDD user data"], key_material_recorded=False), indent=2) + "\n")
    print("PASS: all four assets match independent references; reads/seeks/sizes/EOF passed in all three layers.")
    print("PASS: cache reuse and local cache/user-data write/read mappings.")

if __name__ == "__main__":
    main()
