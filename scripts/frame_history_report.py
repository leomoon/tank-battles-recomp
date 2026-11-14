#!/usr/bin/env python3
"""Validate bounded native frame sequences and describe temporal changes."""
import argparse
import hashlib
import json
from pathlib import Path
import re
from statistics import mean


def read_frame(path):
    header, pixels = path.read_bytes().split(b'\n255\n', 1)
    magic, w, h = header.split()
    w, h = int(w), int(h)
    if magic != b'P6' or len(pixels) != w*h*3:
        raise ValueError('Incomplete native frame: '+str(path))
    return w, h, pixels


def report(run, region=None, allow_failed=False):
    run = Path(run)
    session = json.loads((run/'session.json').read_text())
    accepted = (not session.get('video') or session['video'].get('passed')) and session.get('strict_passed') and not session['session'].get('interrupted') and session['session']['launcher_exit_status'] in (0,124)
    if not accepted and not allow_failed:
        raise ValueError('Failed or interrupted session is diagnostic only')
    rows = [json.loads(line) for line in (run/'history/frames.jsonl').read_text().splitlines()]
    if len(rows)<2:
        raise ValueError('Need at least two consecutive frames')
    changes, frames, previous = [], [], None
    dimensions = None
    for index,row in enumerate(rows):
        if not row['complete'] or row['index']!=index:
            raise ValueError('Incomplete or missing frame record')
        if index and (row['present']!=rows[index-1]['present']+1 or row['elapsed_seconds']<rows[index-1]['elapsed_seconds']):
            raise ValueError('Frame sequence is not consecutive')
        w,h,pixels = read_frame(run/'history'/f'frame-{index:04}.ppm')
        if dimensions and dimensions!=(w,h):
            raise ValueError('Frame dimensions changed')
        if (row['width'],row['height'])!=(w,h):
            raise ValueError('Frame metadata dimensions disagree')
        dimensions=(w,h)
        x,y,rw,rh=region if region else (0,0,w,h)
        if min(x,y)<0 or min(rw,rh)<=0 or x+rw>w or y+rh>h:
            raise ValueError('Region is outside the frame')
        # Fixed spatial sample, no screenshot decoding libraries needed.
        sample=[pixels[(yy*w+xx)*3:(yy*w+xx)*3+3] for yy in range(y,y+rh,4) for xx in range(x,x+rw,4)]
        if previous is not None:
            changed=sum(max(abs(a[c]-b[c]) for c in range(3))>24 for a,b in zip(previous,sample))
            changes.append(changed/len(sample))
        previous=sample
        frames.append(dict(**row,pixel_sha256=hashlib.sha256(pixels).hexdigest()))
    log=(run/'startup.log').read_text(errors='replace')
    failures=[line for line in log.splitlines() if re.search(r'\[(?:rsx engine|RSX vulkan|rsx shader|GCM2D|NV3089|NV0039)\].*(?:unsupported|unhandled|skipp|failed| -> fail|unmapped|invalid|not applied|rejected)',line,re.I)]
    return dict(run=str(run),accepted_for_verification=bool(accepted) and not failures,frames=frames,region=region,
                sampled_change_fraction=dict(min=min(changes),max=max(changes),mean=mean(changes)),renderer_failures=failures[:64],renderer_failure_count=len(failures),
                limitations='Changes describe motion, not a flicker pass/fail threshold. Compare the same settled scene and reference animation. Failure checks cover emitted diagnostics; silent unimplemented GPU paths are not proven absent. Readback/capture cadence is not windowed FPS or simulation speed.')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('run',type=Path);p.add_argument('--region',type=int,nargs=4,metavar=('X','Y','W','H'))
    p.add_argument('--allow-failed',action='store_true');p.add_argument('--output',type=Path)
    a=p.parse_args();r=report(a.run,a.region,a.allow_failed)
    out=a.output or a.run/'history/report.json';out.write_text(json.dumps(r,indent=2)+'\n')
    print(json.dumps({k:r[k] for k in ('accepted_for_verification','sampled_change_fraction','renderer_failure_count')},indent=2))
    if not r['accepted_for_verification'] and not a.allow_failed:raise SystemExit(1)


if __name__=='__main__':main()
