#!/usr/bin/env python3
"""Resolve user-space CPU samples by mapped module and bounded symbol range."""
import argparse,bisect,collections,json,subprocess
from pathlib import Path


def report(directory, interval=None, limit=40, player_symbols=None):
 directory=Path(directory)
 if interval is None:
  m=json.loads((directory/'measurement.json').read_text());b,e=m['interval_seconds']
  with (directory/'gameplay.jsonl').open() as stream:
   timing=json.loads(stream.readline())
  origin=timing['monotonic_ns']-timing['elapsed_ns']
  interval=origin+round(b*1e9),origin+round(e*1e9)
 begin,end=interval
 maps=[]
 for line in (directory/'cpu-sample-maps.txt').read_text().splitlines():
  fields=line.split(maxsplit=5)
  if len(fields)<6 or 'x' not in fields[1]:continue
  lo,hi=(int(v,16) for v in fields[0].split('-'));maps.append((lo,hi,int(fields[2],16),fields[5]))
 cache={};counts=collections.Counter();threads=collections.Counter();unmapped=0
 def resolve(ip):
  for lo,hi,offset,path in maps:
   if not lo<=ip<hi:continue
   if path not in cache:
    symbols=[];dynamic=True
    try:
     symbol_file=player_symbols if player_symbols and Path(path).name=='TankBattlesRecomp' else path
     with open(symbol_file,'rb') as f:header=f.read(18)
     dynamic=int.from_bytes(header[16:18],'little' if header[5]==1 else 'big')==3
     result=subprocess.run(['nm','-S','-n','-C','--defined-only',symbol_file],capture_output=True,text=True)
     if result.returncode or not result.stdout:result=subprocess.run(['nm','-D','-S','-n','-C','--defined-only',symbol_file],capture_output=True,text=True)
     for row in result.stdout.splitlines():
      values=row.split(maxsplit=3)
      if len(values)==4 and values[2] in ('t','T','w','W'):
       try:symbols.append((int(values[0],16),int(values[1],16),values[3]))
       except ValueError:pass
    except (FileNotFoundError,PermissionError):pass
    symbols.sort();cache[path]=(dynamic,symbols,[s[0] for s in symbols])
   dynamic,symbols,starts=cache[path];relative=ip-(lo-offset) if dynamic else ip;i=bisect.bisect_right(starts,relative)-1
   symbol='unresolved +0x%x'%relative
   if i>=0 and symbols[i][0]<=relative<symbols[i][0]+symbols[i][1]:symbol=symbols[i][2]
   return Path(path).name,symbol
  return '[unmapped]','0x%x'%ip
 for line in (directory/'cpu-samples.csv').read_text().splitlines():
  stamp,tid,ip=line.split(',')
  if not begin<=int(stamp)<end:continue
  module,symbol=resolve(int(ip,16));counts[(int(tid),module,symbol)]+=1;threads[int(tid)]+=1;unmapped+=module=='[unmapped]'
 result=dict(samples=sum(counts.values()),thread_samples=dict(threads),unmapped=unmapped,hotspots=[dict(tid=t,module=mod,symbol=s,count=n) for (t,mod,s),n in counts.most_common(limit)],scope='99 Hz user CPU samples, not wall-time or GPU utilization. Blocking waits/kernel time are excluded. A busy polling SPU core is not automatically the critical rendering path. Stripped/private library symbols remain unresolved.')
 return result

if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('directory',type=Path);a=p.parse_args();r=report(a.directory);(a.directory/'cpu-report.json').write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r,indent=2))
