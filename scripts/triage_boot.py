#!/usr/bin/env python3
"""Compare bounded offline boot profiles; write evidence under analysis/step4."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from port import ROOT, positive_seconds

def summarize(log):
    text = log.read_text(errors="replace")
    lines = text.splitlines()
    failed = [x for x in lines if re.search(r"open FAILED|open.*failed|UNRESOLVED|unresolved NID", x, re.I)]
    milestones = re.findall(r"\[milestone\] (.+)", text)
    spins = re.findall(r"PPU spinning on spu(\d+) Mbox_Stat: spu pc=(0x[0-9A-Fa-f]+).*?out=(\d+) in=(\d+) lr=(0x[0-9A-Fa-f]+)", text)
    returns = re.findall(r"\[port-hle\] return nid=0x2ECD48ED r3=(0x[0-9A-Fa-f]+)", text)
    return dict(first_reported_problem=failed[0] if failed else None,
                last_milestone=milestones[-1] if milestones else None,
                unresolved_nids=sorted(set(x.upper() for x in re.findall(r"unresolved NID (0x[0-9A-Fa-f]+)", text, re.I))),
                purchase_verification_returns=returns,
                mailbox_waits=[dict(spu=int(s), pc=p, out=int(o), inbound=int(i), ppu_lr=lr)
                               for s,p,o,i,lr in sorted(set(spins))],
                mailbox_spin_messages=len(spins),
                semaphore_waits=text.count("[WAIT] semaphore_wait"),
                asset_common_opened="[fs] open '/dev_bdvd/PS3_GAME/USRDIR/assets_common.edat'" in text,
                asset_common_read_as_zip="magic=504B0304" in text)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=positive_seconds, default=3)
    args = parser.parse_args()
    output = ROOT / "analysis/step4"
    output.mkdir(parents=True, exist_ok=True)
    profiles = [("default-a", False, "ok"), ("default-b", False, "ok"),
                ("corrected-a", True, "ok"), ("corrected-b", True, "ok"),
                ("corrected-fail", True, "fail")]
    results = []
    for name, corrected, unresolved in profiles:
        env = os.environ.copy()
        for key in ("PS3_VFS_ROOT", "PS3_HDD0_ROOT", "PS3_HDD1_ROOT", "PS3_NET_ONLINE",
                    "TB_HLE_AUDIT_ONLY", "TB_HLE_INVENTORY", "TB_IMPORT_PROBE", "TB_DIAGNOSTIC_STALL"):
            env.pop(key, None)
        env.update(PS3_HLE_TRACE="1000", PS3_HLE_UNRESOLVED=unresolved,
                   TB_TRACE_NID="0x2ECD48ED", SPU_RAW_DBG="1", SDL_AUDIODRIVER="dummy")
        if corrected:
            env["PS3_VFS_ROOT"] = str(ROOT / "game/dev_hdd0/game/NPUB30154")
        log = output / f"{name}.log"
        command = [sys.executable, str(ROOT / "scripts/port.py"), "run",
                   "--timeout", str(args.timeout), "--log", str(log)]
        result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True,
                                text=True, timeout=args.timeout + 10)
        entry = dict(profile=name, corrected_vfs_root=corrected,
                     unresolved_mode=unresolved, launcher_exit_status=result.returncode,
                     process=json.loads(Path(str(log) + ".result.json").read_text()),
                     observations=summarize(log))
        results.append(entry)
        print(name, json.dumps(entry["observations"]), flush=True)
    (output / "boot-comparison.json").write_text(json.dumps(results, indent=2) + "\n")
    if not all(x["launcher_exit_status"] == 124 for x in results):
        raise SystemExit("A boot profile did not time out as expected; inspect its result rather than assuming a stall.")
    print("Saved profile evidence; no runtime behavior or persistent mount configuration was changed.")

if __name__ == "__main__":
    main()
