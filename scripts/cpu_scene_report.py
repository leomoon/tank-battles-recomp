"""Summarize diagnostic instruction samples and raw mailbox counters by scene."""
import argparse
import collections
import json
import re
from pathlib import Path

from cpu_sample_report import report


MBOX = re.compile(
    r"\[mbox-profile\] bucket=(\d+) guest_tid=(\d+) spu=(\d+) "
    r"immediate=(\d+) full=(\d+) recovered=(\d+)"
)


def classify(symbol):
    if any(name in symbol for name in ("spu_raw_reg_", "func_001EE524(", "raw_spu_profile_mbox")):
        return "raw SPU MMIO or mailbox submit function"
    if symbol.startswith(("vm_read", "vm_write", "vm_raw_", "vm_mm_")):
        return "guest memory helpers"
    if any(name in symbol for name in ("indirect_call", "drain_trampoline", "execution_event")):
        return "indirect dispatch and execution tracking"
    if symbol.startswith(("func_", "sub_")):
        return "translated guest functions"
    return "other runtime and host code"


def mailbox_counts(log, begin, end):
    counts = collections.Counter()
    captured_buckets = 0
    for match in MBOX.finditer(log):
        bucket, guest_tid, _spu, immediate, full, recovered = map(int, match.groups())
        start = bucket * 100_000_000
        if guest_tid != 1 or start < begin or start + 100_000_000 > end:
            continue
        counts.update(immediate=immediate, full=full, recovered=recovered)
        captured_buckets += 1
    return dict(counts, captured_buckets=captured_buckets, reads=sum(counts.values()))


def summarize(directory, symbols=None):
    resource = json.loads((directory / "host-resource-report.json").read_text())
    log = (directory / "startup.log").read_text(errors="replace")
    main = int((directory / "pid").read_text())
    mixer_names = re.findall(r'sys_ppu_thread_create tid=(\d+) name="([^"]*[Mm]ixer[^"]*)"', log)
    host_ids = dict(re.findall(r'\[THREAD (\d+)\] host thread started, .*?hosttid=(\d+)', log))
    roles = {main: "main PPU", **{int(host_ids[tid]): name for tid, name in mixer_names if tid in host_ids}}
    results = {}
    sampled = (directory / "cpu-samples.csv").is_file()
    for scene, data in resource["scenes"].items():
        interval = data["start_ns"], data["end_ns"]
        stats = {"mailbox": mailbox_counts(log, *interval), "cores": data["cores"]}
        if sampled:
            profile = report(directory, interval, limit=None, player_symbols=symbols)
            counts = collections.Counter()
            if not profile["samples"]:
                raise ValueError(f"No instruction samples for {scene}")
            for row in profile["hotspots"]:
                if row["symbol"].startswith("spu_") and "_spu_func_" in row["symbol"]:
                    roles.setdefault(row["tid"], "raw SPU")
                if row["tid"] == main:
                    counts[classify(row["symbol"])] += row["count"]
            fps = data.get("completed_fps")
            seconds = (interval[1] - interval[0]) / 1e9
            stats.update(
                profile=profile,
                main_samples_by_category=dict(counts),
                main_cpu_ms_per_completed_frame={
                    key: 1000 * val / (99 * seconds * fps)
                    for key, val in counts.items()
                } if fps else {},
                sampled_user_cores=profile["samples"] / (99 * seconds),
            )
        results[scene] = stats
        for thread in data["threads"]:
            if thread["name"].startswith("tb-"):
                roles.setdefault(thread["tid"], thread["name"])
    return dict(thread_roles=roles, scenes=results)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--player-symbols", type=Path)
    args = parser.parse_args()
    result = summarize(args.run, args.player_symbols)
    (args.run / "cpu-attribution.json").write_text(json.dumps(result, indent=2) + "\n")
