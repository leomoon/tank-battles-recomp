#!/usr/bin/env python3
"""Summarize TB_PAGE_CENSUS runs: which threads read or write RSX surface memory.

  scripts/page_census_report.py <run dir or startup.log> [...] [--build-dir build-vulkan] [--json out.json]

Each "[page-census]" block in a startup log is a cumulative snapshot; the last
one in a log is that run's total. Code addresses inside the executable are
resolved with addr2line; addresses in shared libraries (memcpy) stay numeric.
"""
import argparse
import json
import re
import subprocess
from pathlib import Path

LINE = re.compile(r'^\[page-census\] who=(\S+) kind=(\w+) surface=(\d+):0x([0-9A-Fa-f]+) (\d+)x(\d+) (read|write) '
                  r'(inside|shared-page) rip=0x([0-9a-f]+) count=(\d+)$')


def last_snapshot(text):
    rows = []
    for line in text.splitlines():
        if line.startswith('[page-census] arms='):
            rows = []
        else:
            m = LINE.match(line)
            if m:
                who, kind, loc, off, w, h, access, where, rip, count = m.groups()
                rows.append(dict(who=who, kind=kind, location=int(loc), offset=int(off, 16), size=[int(w), int(h)],
                                 access=access, where=where, rip=int(rip, 16), count=int(count)))
    return rows


def symbol(binary, rip, cache={}):
    if rip > 1 << 40:
        return 'shared library'
    if (binary, rip) not in cache:
        try:
            out = subprocess.run(['addr2line', '-f', '-C', '-e', str(binary), hex(rip)], capture_output=True,
                                 text=True, timeout=30).stdout.splitlines()
            cache[(binary, rip)] = out[0] if out else '?'
        except (OSError, subprocess.TimeoutExpired):
            cache[(binary, rip)] = '?'
    return cache[(binary, rip)]


def summarize(paths, binary):
    runs = {}
    for path in paths:
        path = Path(path)
        log = path / 'startup.log' if path.is_dir() else path
        rows = last_snapshot(log.read_text(errors='replace'))
        for row in rows:
            row['function'] = symbol(binary, row['rip'])
            row['class'] = 'walker' if row['who'] == 'walker' else 'guest/host thread'
        runs[str(path)] = rows
    readers = [dict(run=run, **row) for run, rows in runs.items() for row in rows
               if row['class'] != 'walker' and row['access'] == 'read' and row['where'] == 'inside']
    return dict(runs=runs, non_walker_surface_readers=readers)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('paths', nargs='+')
    parser.add_argument('--build-dir', default='build-vulkan')
    parser.add_argument('--json')
    args = parser.parse_args()
    result = summarize(args.paths, Path(args.build_dir) / 'TankBattlesRecomp')
    for run, rows in result['runs'].items():
        print(run)
        for r in sorted(rows, key=lambda r: (r['class'], -r['count'])):
            print(f"  {r['who']:28s} {r['access']:5s} {r['where']:11s} {r['kind']} {r['location']}:0x{r['offset']:08X} "
                  f"{r['size'][0]}x{r['size'][1]:<5} {r['count']:6d}  {r['function']}")
    print(f"non-walker reads inside surfaces: {len(result['non_walker_surface_readers'])}")
    if args.json:
        Path(args.json).write_text(json.dumps(result, indent=2) + '\n')


if __name__ == '__main__':
    main()
