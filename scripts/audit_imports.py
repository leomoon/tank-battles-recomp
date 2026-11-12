#!/usr/bin/env python3
"""Report actual HLE registrations separately from implementation behavior."""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import json
import os
from pathlib import Path
import re
import sys
from boot_session import run_session
from port import ROOT, resolve

def read_inventory(path):
    records = defaultdict(lambda: defaultdict(list))
    probes = {}
    for line in path.read_text().splitlines():
        kind, nid, name = line.split("\t", 2)
        nid = int(nid, 16)
        if kind == "probe":
            probes[nid] = bool(int(name))
        else:
            records[nid][kind].append(name)
    return records, probes

def classify(item, registrations, probes, lookup, optional_names):
    nid = int(item["nid"], 16)
    record = registrations.get(nid, {})
    match = lookup(nid)
    name = match[1] if match else None
    context = record.get("context", [])
    generic = record.get("generic", [])
    override = record.get("override", [])
    fast = record.get("fast", [])
    present = probes.get(nid, False) or bool(fast or override)
    category = "registered" if present else "missing_handler"
    if not present and nid in optional_names:
        category = "absent_in_selected_profile"
    behavior = "not_verified"
    notes = []
    library = item.get("library", item.get("lib"))
    if library == "cellSail" and present:
        behavior = "module_has_known_limitations"
        notes.append("cellSail.c implements lifecycle scaffolding but no actual media playback; individual semantics need runtime validation.")
    if library in ("sceNp", "sceNp2", "sceNpTus", "cellSysutilAvc2") and present:
        behavior = "module_has_known_limitations"
        notes.append("Local/offline emulation or stub behavior; registration does not establish PSN/voice service compatibility.")
    if any(n.startswith(("net", "sys_net_bnet_")) for n in context):
        behavior = "offline_model"
        notes.append("Selected context handler provides offline socket behavior; optional host sockets require PS3_NET_ONLINE.")
    if present and not (context or generic or fast or override):
        notes.append("Runtime lookup found a handler not attributed by linker registration capture.")
    effective = ("port_override" if override else
                 "fast_then_context" if fast and context else
                 "fast_then_generic" if fast and generic else
                 "fast" if fast else "context" if context else "generic" if generic else
                 "unattributed" if present else None)
    return dict(library=library, nid=f"0x{nid:08X}", stub=item.get("stub"),
                resolved_name=name, name_resolved=name is not None,
                generic_registrations=generic, context_registrations=context,
                fast_registrations=fast, port_override_aliases=override,
                runtime_has_handler=probes.get(nid), effective_dispatch=effective,
                status=category, implementation_behavior=behavior,
                optional_network_name=optional_names.get(nid), notes=notes)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--imports", default="analysis/EBOOT.imports.json")
    parser.add_argument("--elf", default="game/dev_hdd0/game/NPUB30154/USRDIR/EBOOT.elf")
    parser.add_argument("--build-dir", default="build")
    parser.add_argument("--output", default="analysis/step4/import-audit.json")
    args = parser.parse_args()
    if not sys.platform.startswith("linux"):
        parser.error("Runtime registration capture currently requires Linux")
    sdk = ROOT / "external/ps3recomp"
    sys.path.insert(0, str(sdk / "tools"))
    from nid_database import get_default_db, compute_nid
    items = json.loads(resolve(args.imports).read_text())
    output = resolve(args.output)
    if not output.is_relative_to((ROOT / "analysis").resolve()):
        parser.error("--output must be under analysis/")
    output.parent.mkdir(parents=True, exist_ok=True)
    probe = output.with_suffix(".probes.txt")
    inventory = output.with_suffix(".registrations.tsv")
    probe.write_text("".join(f"{int(x['nid'],16):08X}\n" for x in items))
    env = os.environ.copy()
    env.pop("PS3_NET_ONLINE", None)
    env.pop("TB_DIAGNOSTIC_STALL", None)
    env.update(TB_HLE_AUDIT_ONLY="1", TB_IMPORT_PROBE=str(probe), TB_HLE_INVENTORY=str(inventory))
    status, _ = run_session([str(resolve(args.build_dir) / "TankBattlesRecomp"), str(resolve(args.elf))],
                            cwd=ROOT, env=env, timeout=10,
                            log_path=output.with_suffix(".startup.log"))
    if status:
        raise SystemExit("Registration-only startup failed; inspect the startup log")
    records, probes = read_inventory(inventory)
    network = (sdk / "libs/network/sysNet.c").read_text()
    table = network[network.index("void ps3_net_host_register"):]
    optional = {compute_nid(name): name for name in re.findall(r'\{\s*"([^"]+)"\s*,', table)}
    db = get_default_db()
    entries = [classify(x, records, probes, db.lookup_nid, optional) for x in items]
    result = dict(profile="offline (PS3_NET_ONLINE unset)", imports=entries,
                  counts=dict(Counter(x["status"] for x in entries)),
                  unresolved_names=sum(not x["name_resolved"] for x in entries),
                  evidence=dict(inventory=str(inventory.relative_to(ROOT)),
                                probes=str(probe.relative_to(ROOT))),
                  limitations=["Registration presence is not behavioral compatibility.",
                               "Fast handlers may decline and fall through to context/generic dispatch.",
                               "Absent entries can still reach hardcoded/PRX paths; verify observed call traces.",
                               "Limited module classifications come from current SDK sources, not gameplay validation."])
    output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(dict(counts=result["counts"], unresolved_names=result["unresolved_names"])))
    print(output)
if __name__ == "__main__":
    main()
