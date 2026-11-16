#!/usr/bin/env python3
"""Normalize bounded Linux depth/transfer costs to completed gameplay frames."""
import argparse
import json
import re
from pathlib import Path

def report(directory):
    directory=Path(directory)
    m=json.loads((directory/'measurement.json').read_text())
    if not m['strict_passed'] or m['phase']!='gameplay' or not m['live_gameplay_verified']:
        raise ValueError('Need strict-clean verified gameplay')
    if not m['diagnostics'].get('texture_decode_profile'):
        raise ValueError('Need explicit depth diagnostics')
    if m.get('renderer_failures') or (directory/'invalid-scene.json').exists():
        raise ValueError('Failed renderer or ending scene')
    rows=[json.loads(line) for line in (directory/'gameplay.jsonl').read_text().splitlines()]
    origin=rows[0]['monotonic_ns']-rows[0]['elapsed_ns']
    begin,end=[origin+round(seconds*1e9) for seconds in m['interval_seconds']]
    completed=[origin+t for row in rows for t in row['events'][1]]
    pattern=r'\[depth-cost\] path=(\S+) frame=(\d+) calls=(\d+) requested_bytes=(\d+) inclusive_ns=(\d+) monotonic_ns=(\d+)'
    samples={}
    for match in re.finditer(pattern,(directory/'startup.log').read_text(errors='replace')):
        path,frame,calls,size,cost,stamp=match.groups()
        samples.setdefault(path,[]).append(dict(frame=int(frame),calls=int(calls),requested_bytes=int(size),inclusive_ns=int(cost),stamp=int(stamp)))
    if not samples:raise ValueError('Need timestamped depth costs; old power-of-two logs cannot be aligned exactly')
    out={}
    for path in ('depth-to-guest','depth-as-color','surface-guest-read','nv0039-copy'):
        selected=[s for s in samples.get(path,[]) if begin<=s['stamp']<end]
        if len(selected)<2:
            out[path]=dict(observed_anywhere_in_capture=path in samples,aligned_window_available=False)
            continue
        first,last=selected[0],selected[-1]
        frames=sum(first['stamp']<=t<last['stamp'] for t in completed)
        if not frames:raise ValueError('No completed frames in aligned counter window')
        delta={k:last[k]-first[k] for k in ('calls','requested_bytes','inclusive_ns')}
        if any(v<0 for v in delta.values()):raise ValueError('Nonmonotonic cumulative costs')
        out[path]=dict(observed_anywhere_in_capture=True,aligned_window_available=True,seconds=(last['stamp']-first['stamp'])/1e9,completed_frames=frames,**delta,ms_per_completed_frame=delta['inclusive_ns']/frames/1e6,requested_bytes_per_completed_frame=delta['requested_bytes']/frames)
    return dict(paths=out,limitations=['Diagnostic inclusive wall time, not CPU cycles or clean throughput. Nested paths overlap; do not sum them.','Each path uses its own first/last cumulative snapshots strictly inside the verified live interval.','Requested bytes include cache hits and rejected calls; they are not actual GPU traffic.','An absent path means no logged calls in this bounded capture, not global PPU/SPU read coverage.'])

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path)
    args=parser.parse_args()
    result=report(args.directory)
    (args.directory/'depth-cost-report.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result['paths'],indent=2))
