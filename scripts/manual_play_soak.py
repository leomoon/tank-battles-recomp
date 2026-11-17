#!/usr/bin/env python3
"""Thirty-minute physical-input play session with private saves and resource evidence."""
import argparse,json,subprocess,sys,time
from pathlib import Path
from port import ROOT,resolve
from verify_vulkan_rendering import request_close

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',required=True);p.add_argument('--content-root',required=True);p.add_argument('--minutes',type=float,default=30);a=p.parse_args();out=resolve(a.output)
 if a.minutes<30:p.error('Acceptance soak requires at least 30 minutes')
 command=[sys.executable,str(ROOT/'scripts/measure_gameplay.py'),'--output',str(out),'--profile',str(ROOT/'analysis/performance/profiles'/out.name),'--scene','battle','--content-root',a.content_root,'--overlay','--pin-window','--lifecycle-stats','--frame-budget','--timeout',str(int(a.minutes*60+300))]
 proc=subprocess.Popen(command);samples=[];start=None;window=None;pid=None
 def close_owned():
  if window is None or pid is None:return
  try:
   owner=int(subprocess.check_output(['xdotool','getwindowpid',window],text=True,stderr=subprocess.DEVNULL))
   if owner==pid:request_close(window)
  except (ValueError,subprocess.CalledProcessError):pass
 try:
  while proc.poll() is None:
   if (out/'window-prepared.json').exists() and start is None:
    start=time.monotonic();info=json.loads((out/'window-prepared.json').read_text())['verified'];pid=info['pid']
    windows=subprocess.check_output(['xdotool','search','--all','--pid',str(pid),'--name','^Tank Battles performance'],text=True).split();assert len(windows)==1;window=windows[0]
    (out/'soak-start.json').write_text(json.dumps(dict(monotonic=start,pid=pid,window=window,physical_input=True,required_seconds=a.minutes*60)))
   if start is not None:
    elapsed=time.monotonic()-start
    status=dict(l.split(':',1) for l in Path(f'/proc/{pid}/status').read_text().splitlines() if ':' in l)
    samples.append(dict(seconds=elapsed,rss_kb=int(status['VmRSS'].split()[0]),threads=int(status['Threads']),fds=len(list(Path(f'/proc/{pid}/fd').iterdir()))))
    (out/'soak.json').write_text(json.dumps(samples,indent=1)+'\n')
    if elapsed>=a.minutes*60:close_owned();break
   time.sleep(10)
  proc.wait(timeout=30)
  session=json.loads((out/'startup.log.result.json').read_text())
  log=(out/'startup.log').read_text(errors='replace')
  summary=dict(elapsed_seconds=samples[-1]['seconds'] if samples else 0,samples=len(samples),normal_exit=session.get('child_returncode')==0 and not session.get('timed_out'),render_workers_joined=log.count('[shutdown] joined render worker'),join_timeout='render worker join timed out' in log,measurement_exit=proc.returncode)
  summary['duration_passed']=summary['elapsed_seconds']>=a.minutes*60
  (out/'soak-summary.json').write_text(json.dumps(summary,indent=2));print(summary)
 finally:
  if proc.poll() is None:
   if window:close_owned()
   proc.wait(timeout=30)
if __name__=='__main__':main()
