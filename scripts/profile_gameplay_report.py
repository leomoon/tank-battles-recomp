#!/usr/bin/env python3
"""Correlate inclusive CPU scopes, sparse GPU timings and CPU samples with gameplay."""
import argparse,json,statistics
from pathlib import Path
from cpu_sample_report import report as cpu_report


def report(directory):
 directory=Path(directory);m=json.loads((directory/'measurement.json').read_text());b,e=m['interval_seconds']
 gameplay=[json.loads(l) for l in (directory/'gameplay.jsonl').read_text().splitlines()];origin=gameplay[0]['monotonic_ns']-gameplay[0]['elapsed_ns']
 scopes=[json.loads(l) for l in (directory/'scopes.jsonl').read_text().splitlines()]
 first_present=next(t for row in gameplay for t in row['events'][2])
 # The first scope report is called immediately after the first successful
 # present timing event. This correlates the legacy scope clock without
 # pretending its relative origin is identical to the gameplay clock.
 scope_origin=origin+first_present-scopes[0]['elapsed_ns']
 selected=[r for r in scopes if origin+b*1e9<=scope_origin+r['elapsed_ns']<=origin+e*1e9]
 if len(selected)<2:raise ValueError('Insufficient aligned scope interval')
 first,last=selected[0],selected[-1];seconds=(last['elapsed_ns']-first['elapsed_ns'])/1e9;times={}
 for name,end in last['timings'].items():
  start=first['timings'][name];n=end['n']-start['n'];ns=end['ns']-start['ns'];times[name]=dict(calls=n,wall_seconds=ns/1e9,mean_ms=ns/n/1e6 if n else None)
 gpu=[]
 if (directory/'gpu-timing.jsonl').exists():
  for line in (directory/'gpu-timing.jsonl').read_text().splitlines():
   row=json.loads(line)
   if origin+b*1e9<=row['monotonic_ns']<origin+e*1e9:gpu.append(row['gpu_ns']/1e6)
 gpu.sort();cpu=cpu_report(directory) if (directory/'cpu-samples.csv').exists() else None
 return dict(scope_window_seconds=seconds,scopes=times,gpu_samples=len(gpu),gpu_mean_ms=statistics.mean(gpu) if gpu else None,gpu_p95_ms=gpu[min(len(gpu)-1,int(.95*len(gpu)))] if gpu else None,cpu_samples=cpu,
             limitations=['CPU scopes are inclusive overlapping wall times; do not sum them.','Legacy CPU scope-clock alignment uses paired first successful present hooks; the interval has startup hook-order/logging uncertainty, not nanosecond cross-clock accuracy.','GPU timing sparsely samples every 256th serialized general command, not whole guest GPU frames or window-present work. Fixed-period samples may alias recurring passes and do not establish total GPU utilization.','User CPU samples exclude blocking/kernel time; a busy SPU polling core is not automatically the rendering critical path.'])

if __name__=='__main__':
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('directory',type=Path);a=parser.parse_args();r=report(a.directory);(a.directory/'profile-report.json').write_text(json.dumps(r,indent=2)+'\n');print(json.dumps({k:v for k,v in r.items() if k!='cpu_samples'},indent=2))
