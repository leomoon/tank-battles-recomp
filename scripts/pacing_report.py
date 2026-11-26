"""Summarize TB_PACING_TRACE without mixing guest and host clock domains."""
import argparse,json,statistics,struct,bisect,re
from pathlib import Path


def frame_accounting(events, spans, base_ns):
 """Exclusive elapsed-time accounting, never add waits from different threads."""
 from frame_budget import Union, summary, frame_dependencies
 # Use ns throughout. Trace text timestamps are milliseconds relative to base.
 absolute=[(k,t,a,b,base_ns+round(x*1e6),base_ns+round(y*1e6)) for k,t,a,b,x,y in spans]
 starts=sorted(s[4] for s in absolute if s[0]==8)
 if not events:return dict(frames=0)
 lo,hi=events[0]['ns'],events[-1]['ns']
 waits=[];pending={}
 for e in events:
  if e['kind']==1:pending[e['object']]=e['ns']
  elif e['kind']==2:
   begin=pending.pop(e['object'],None)
   if begin is not None:waits.append((begin,e['ns']))
  elif e['kind']==9:pending.pop(e['object'],None)
 pacing=Union(waits)
 semaphore_spans=[(s[4],s[5]) for s in absolute if s[0]==37 and s[1]==1]
 semaphore=Union(semaphore_spans)
 combined=Union(waits+semaphore_spans)
 frames=[]
 for a,b in zip(starts,starts[1:]):
  if not lo<=a<b<=hi:continue
  pw,sw,union=pacing.within(a,b),semaphore.within(a,b),combined.within(a,b)
  overlap=pw+sw-union
  exits=[e for e in events if e['kind']==2 and a<=e['ns']<b]
  resets=[e for e in events if e['kind']==9 and a<=e['ns']<b]
  frames.append(dict(start_ns=a,end_ns=b,frame_ms=(b-a)/1e6,
      pacing_only_ms=(pw-overlap)/1e6,semaphore_only_ms=(sw-overlap)/1e6,
      overlapping_wait_ms=overlap/1e6,remaining_ms=((b-a)-union)/1e6,
      pacing_exits=len(exits),pacing_resets=len(resets),
      corrections_us=[e['correction_u32'] for e in exits],
      nominal_intervals_us=[struct.unpack('!f',struct.pack('!I',e['interval_bits']))[0]*1e6 for e in exits],
      flags=[e['flags'] for e in exits],
      previous_guest_us=[e.get('previous_guest_us') for e in exits],
      recovery_requests=sum(e['kind']==11 and a<=e['ns']<b for e in events),
      invalidations=sum(e['kind']==14 and a<=e['ns']<b for e in events)))
 keys=('frame_ms','pacing_only_ms','semaphore_only_ms','overlapping_wait_ms','remaining_ms')
 return dict(frames=len(frames),exclusive_ms={key:summary([f[key] for f in frames]) for key in keys},
     zero_correction_frames=summary([f['frame_ms'] for f in frames if f['corrections_us'] and not any(f['corrections_us'])]),
     positive_correction_frames=summary([f['frame_ms'] for f in frames if any(f['corrections_us'])]),
     reset_count=sum(f['pacing_resets'] for f in frames),exit_count=sum(f['pacing_exits'] for f in frames),
     reset_groups={str(n):dict(count=len(group),frame_ms=summary([f['frame_ms'] for f in group]))
                   for n in sorted({f['pacing_resets'] for f in frames})
                   if (group:=[f for f in frames if f['pacing_resets']==n])},
     flip_dependencies=frame_dependencies(spans),timeline=frames,
     limitations=['Pacing span starts at first waiting comparison, not function entry.',
                  'Remaining wall time includes guest work, scheduling and waits not classified here; it is not CPU execution time.',
                  'Semaphore spans are main guest thread only; callback spans are reported separately and not added.',
                  'Network frame counters and pacer resets do not establish simulation update count.'])


def report(path, frame_budget=None, startup_log=None):
 rows=[json.loads(l) for l in Path(path).read_text().splitlines()]
 header=rows[0];events=rows[1:]
 if header.get('schema')!=1 or header.get('clock')!='host_monotonic_ns':raise ValueError('Unsupported pacing schema')
 if header['events']!=len(events):raise ValueError('Incomplete pacing trace')
 if any(not header['start_ns']<=e['ns']<=header['end_ns'] for e in events):raise ValueError('Event outside trace window')
 # Concurrent producer timestamps may arrive in buffer order rather than time order.
 events.sort(key=lambda e:e['ns'])
 def stats(values):
  if not values:return None
  v=sorted(values)
  return dict(count=len(v),mean=statistics.mean(v),min=v[0],p95=v[min(len(v)-1,int(.95*len(v)))],max=v[-1])
 corrections=[e for e in events if e['kind']==3]
 decisions=[e for e in events if e['kind'] in (1,2)]
 result=dict(header=header,correction_writes=len(corrections),correction_us=stats([e['correction_u32'] for e in corrections]),
             deadline_lateness_guest_us=stats([e['observed_float_us']-e['deadline_guest_us'] for e in decisions if e['kind']==2 and e['observed_float_us'] is not None and e['deadline_guest_us'] is not None]),
             poll_counts=stats([e['polls'] for e in decisions if e['kind']==2]),
             pacing_intervals_seconds=sorted({struct.unpack('!f',struct.pack('!I',e['interval_bits']))[0] for e in decisions}),
             scope='Host ns aligns with frame-budget/gameplay telemetry. Guest us is title-relative. tx/rx are local completed socket-call sequences, not wire sequence IDs or remote timestamps.')
 for kind,name in [(4,'sendto'),(5,'recvfrom')]:
  io=[e for e in events if e['kind']==kind]
  result[name]=dict(calls=len(io),failed=sum(e['result_u32']>=0x80000000 for e in io),
                   duration_host_ms=stats([(e['ns']-e['io_begin_ns'])/1e6 for e in io if e['io_begin_ns']]))
 if frame_budget:
  if startup_log is None:raise ValueError('Frame correlation requires startup log clock mapping')
  mappings=re.findall(r'\[frame-budget\] base_monotonic_ns=(\d+)',Path(startup_log).read_text(errors='replace'))
  if len(mappings)!=1:raise ValueError('Missing or ambiguous frame-budget clock mapping')
  base=int(mappings[0]);flips=[];frame_dropped=None
  for line in Path(frame_budget).read_text().splitlines():
   if line.startswith('#'):
    found=re.search(r'dropped=(\d+)',line)
    if found:frame_dropped=int(found[1])
   elif line and not line.startswith('T '):
    parts=line.split()
    if int(parts[0])==8:flips.append(base+round(float(parts[4])*1000))
  flips.sort();lags=[];within=0
  for e in events:
   i=bisect.bisect_right(flips,e['ns'])-1
   if i>=0 and i+1<len(flips):
    within+=1
    if e['kind']==2:lags.append((e['ns']-flips[i])/1e6)
  if not within:raise ValueError('No pacing/frame-budget overlap')
  result['frame_correlation']=dict(base_monotonic_ns=base,flips=len(flips),dropped=frame_dropped,
      events_inside_complete_frames=within,pacing_exit_since_previous_flip_ms=stats(lags))
  from frame_budget import load
  _,_,spans=load(frame_budget)
  result['frame_accounting']=frame_accounting(events,spans,base)
 receives=[e['ns'] for e in events if e['kind']==5 and e['result_u32']<0x80000000]
 after_receive=[]
 for e in corrections:
  i=bisect.bisect_right(receives,e['ns'])-1
  if i>=0:after_receive.append((e['ns']-receives[i])/1e6)
 result['correction_since_latest_local_receive_ms']=stats(after_receive)
 result['lossless']=header['dropped']==0
 result['usable_timeline']=bool(header.get('complete') and result['lossless'] and (not frame_budget or frame_dropped==0))
 if frame_budget:result['frame_accounting']['usable_timeline']=result['usable_timeline']
 return result

if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('trace');p.add_argument('--json',type=Path);p.add_argument('--frame-budget',type=Path);p.add_argument('--startup-log',type=Path);a=p.parse_args()
 r=report(a.trace,a.frame_budget,a.startup_log);text=json.dumps(r,indent=2,allow_nan=False)+'\n'
 if a.json:a.json.write_text(text)
 print(text,end='')
