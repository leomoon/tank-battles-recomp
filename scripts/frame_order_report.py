#!/usr/bin/env python3
"""Find returns to an earlier exact pre-window image after an intervening image."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

def report(directory):
    directory=Path(directory);rows=[json.loads(l) for l in (directory/'frames.jsonl').read_text().splitlines()]
    if not rows or any(not r['complete'] for r in rows):raise ValueError('Missing or incomplete history')
    seen={};returns=[];previous=None;unique=Counter()
    for n,row in enumerate(rows):
        if row['index']!=n or n and row['present']!=rows[n-1]['present']+1:raise ValueError('Nonconsecutive history')
        digest=hashlib.sha256((directory/f'frame-{n:04d}.ppm').read_bytes()).hexdigest()
        unique[digest]+=1
        if digest!=previous and digest in seen:
            earlier=seen[digest]
            returns.append(dict(index=n,present=row['present'],buffer=row['buffer'],earlier_index=earlier,
                earlier_buffer=rows[earlier]['buffer'],age_seconds=row['elapsed_seconds']-rows[earlier]['elapsed_seconds'],
                intervening_frames=n-earlier-1))
        seen[digest]=n;previous=digest
    return dict(frames=len(rows),unique_images=len(unique),returns_to_earlier_image=returns,
        limitations='Exact-image recurrence is a candidate for stale/reordered presentation, not proof of simulation reversal. Static scenes, loops and deliberate animation can repeat. Use gameplay and the corresponding command/buffer trace to interpret it.')

def report_scanout(path):
    rows=[json.loads(line) for line in Path(path).read_text().splitlines()]
    presents=[r for r in rows if r.get('kind','present')=='present']
    if len(presents)<2:raise ValueError('Insufficient scanout records')
    if any(b['present']!=a['present']+1 or b['elapsed_ns']<a['elapsed_ns'] for a,b in zip(presents,presents[1:])):
        raise ValueError('Nonconsecutive scanout records')
    requests=[r for r in rows if r.get('kind')=='flip-request' and r['d']==0]
    backwards=[]
    for a,b in zip(presents,presents[1:]):
        if b['write_serial'] and b['write_serial']<a['write_serial']:
            backwards.append(dict(seconds=b['elapsed_ns']/1e9,present=b['present'],buffer=b['buffer'],
                previous_buffer=a['buffer'],write_serial=b['write_serial'],previous_write_serial=a['write_serial'],
                nearest_immediate_request_ms=min(abs(b['elapsed_ns']-r['elapsed_ns']) for r in requests)/1e6 if requests else None))
    return dict(presents=len(presents),elapsed_seconds=presents[-1]['elapsed_ns']/1e9,
                older_buffer_presentations=backwards,immediate_flip_requests=len(requests),
                duplicate_generations=sum(a['buffer']==b['buffer'] and a['write_serial']==b['write_serial'] for a,b in zip(presents,presents[1:])),
                limitations='Generation serials track backend surface writes. Older generations identify presentation ordering candidates, not CPU simulation rollback. Repeated completed-frame refreshes are counted separately and are not automatically defects.')

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('history',type=Path);p.add_argument('--scanout',action='store_true');p.add_argument('--output',required=True,type=Path);a=p.parse_args()
    r=report_scanout(a.history) if a.scanout else report(a.history)
    a.output.write_text(json.dumps(r,indent=2)+'\n')
    print(json.dumps({k:v for k,v in r.items() if not isinstance(v,(list,dict))}))
