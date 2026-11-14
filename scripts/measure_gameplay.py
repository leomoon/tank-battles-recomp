#!/usr/bin/env python3
"""Run one isolated visible gameplay measurement; operator marks a verified live scene."""
import argparse,hashlib,json,os,re,subprocess,time,tomllib
from pathlib import Path
from boot_session import run_session
from port import ROOT,resolve,launch_environment
from user_profiles import open_profile
from diagnostic_logs import open_log
from gameplay_report import summarize


def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',required=True);p.add_argument('--scene',required=True,choices=['battle','campaign']);p.add_argument('--profile',required=True);p.add_argument('--resume',action='store_true');p.add_argument('--scripted-input',help='Explicit PAD_FILE path for labeled scripted navigation/gameplay');p.add_argument('--scopes',action='store_true');p.add_argument('--overlay',action='store_true');p.add_argument('--pin-window',action='store_true',help='Float/pin only this test window across workspace switches');p.add_argument('--timeout',type=float,default=300);a=p.parse_args()
 out=resolve(a.output)
 if not out.is_relative_to(ROOT/'analysis/performance') or out.exists():p.error('Use a new output inside analysis/performance/')
 if not resolve(a.profile).is_relative_to(ROOT/'analysis'):p.error('Measurement profiles must be inside ignored analysis/')
 if a.scripted_input and (not resolve(a.scripted_input).is_relative_to(ROOT/'analysis/performance') or resolve(a.scripted_input).suffix not in ('.txt','.pad')):p.error('Use a .txt/.pad scripted-input file inside analysis/performance/')
 out.mkdir(parents=True);config=tomllib.loads((ROOT/'config.toml').read_text());source=resolve(config['paths']['hdd0_root']);content=source/'game'/config['game']['title_id']
 metadata,lock=open_profile(resolve(a.profile),source,content,config['game']['title_id'],resume=a.resume)
 env=launch_environment(config,str(content/'PARAM.SFO'),mounts=metadata['mounts'])
 for k in list(env):
  if k.startswith(('TB_','PAD_','PS3RECOMP_VK_','RSX_ENGINE_','VK_DRAW_','PS3_FTRACE')):env.pop(k)
 title='Tank Battles performance '+out.name
 env.update(TB_EXECUTION_MODE='strict',TB_GAMEPLAY_TIMING=str(out/'gameplay.jsonl'),TB_FPS_OVERLAY='1' if a.overlay else '0',PS3_TITLE=title,SDL_VIDEODRIVER='x11',PS3RECOMP_VK_WINDOW='1',PS3RECOMP_VK_GUEST_PROGRAMS='1',VK_DRAW_BATCH='0',PS3_VERBOSE='0')
 if env.get('SDL_AUDIODRIVER')=='dummy':env.pop('SDL_AUDIODRIVER')
 if a.scopes:env.update(TB_RENDER_PROFILE=str(out/'scopes.jsonl'),TB_RENDER_PROFILE_MAX_REPORTS='600',TB_GPU_TIMING=str(out/'gpu-timing.jsonl'))
 if a.scripted_input:
  env.update(PAD_FILE=str(resolve(a.scripted_input)),PAD_NO_KEYBOARD='1',SDL_GAMECONTROLLER_IGNORE_DEVICES_EXCEPT='0x0000/0x0000')
 commands=subprocess.check_output(['ninja','-C','build-vulkan','-t','commands'],text=True)
 flags={}
 for kind,needle in [('ppu','ppu_recomp_'),('spu','spu_recomp.c.o'),('runtime','rendering_profile.cpp.o')]:
  matches=[l for l in commands.splitlines() if needle in l and ' -c ' in l];flags[kind]=matches[0] if matches else None
 gpu=subprocess.check_output(['nvidia-smi','--query-gpu=name,driver_version','--format=csv,noheader'],text=True).strip()
 exe=ROOT/'build-vulkan/TankBattlesRecomp';manifest=dict(scene=a.scene,phase='unverified',live_gameplay_verified=False,interval_seconds=None,visible_entire_interval=False,strict_passed=False,guest_resolution=[1280,720],internal_surfaces=[],window_size=None,gpu=gpu,driver='x11/Vulkan',audio=env.get('SDL_AUDIODRIVER','host-default'),input_source='scripted navigation, neutral player one; physical/keyboard excluded' if a.scripted_input else 'physical controller/keyboard; no synthetic PAD inputs',sdk_verbose=False,build_type=re.search(r'^CMAKE_BUILD_TYPE:STRING=(.*)$',(ROOT/'build-vulkan/CMakeCache.txt').read_text(),re.M)[1],compiler=subprocess.check_output(['c++','--version'],text=True).splitlines()[0],effective_flags=flags,diagnostics=dict(video=False,screenshots=False,textures=False,resources=False,input_trace=False,wait_trace=False,passes=False,scopes=a.scopes),overlay=a.overlay,source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),executable_sha256=hashlib.sha256(exe.read_bytes()).hexdigest(),sdk_manifest_sha256=hashlib.sha256((ROOT/'build-vulkan/instrumented_sdk/manifest.json').read_bytes()).hexdigest(),profile=metadata,clock_origin='TB_GAMEPLAY_TIMING first event; marker and observation clocks mapped by current telemetry timestamp')
 (out/'measurement.json').write_text(json.dumps(manifest,indent=2)+'\n')
 import threading
 stop=threading.Event();observations=[];child=None;marker=None;sampler=None;sample_streams=[]
 if a.scopes:
  subprocess.run(['cc','-O2',str(ROOT/'scripts/sample_gameplay_cpu.c'),'-o',str(out/'cpu-sampler')],check=True)
 def on_start(process):
  nonlocal child;child=process;(out/'pid').write_text(str(process.pid))
 def watch():
  nonlocal marker,sampler
  while not stop.wait(1):
   t=None;path=out/'gameplay.jsonl'
   if path.exists():
    try:
     last=json.loads(path.read_text().splitlines()[-1]);t=(time.monotonic_ns()-(last['monotonic_ns']-last['elapsed_ns']))/1e9
    except (IndexError,json.JSONDecodeError):continue
   if t is None:continue
   response=subprocess.run(['hyprctl','-j','clients'],capture_output=True,text=True)
   clients=json.loads(response.stdout) if response.returncode==0 else []
   found=[c for c in clients if c.get('title')==title]
   c=found[0] if len(found)==1 else {}
   if a.pin_window and c and not (out/'window-prepared.json').exists():
    address=c['address']
    if not re.fullmatch(r'0x[0-9a-fA-F]+',address):raise ValueError('Unexpected window address')
    prefix='address:'+address
    if c.get('grouped'):
     operations=['hl.dsp.window.move({ window = "'+prefix+'", out_of_group = true })']
    elif not c.get('floating'):
     operations=['hl.dsp.window.float({ window = "'+prefix+'", action = "toggle" })']
    elif not c.get('pinned'):
     operations=['hl.dsp.window.pin({ window = "'+prefix+'" })']
    elif c.get('size')!=[1280,720]:
     operations=['hl.dsp.window.resize({ window = "'+prefix+'", x = 1280, y = 720 })']
    else:
     operations=['hl.dsp.window.alter_zorder({ window = "'+prefix+'", mode = "top" })','hl.dsp.window.tag({ window = "'+prefix+'", tag = "+pop" })','hl.dsp.focus({ window = "'+prefix+'" })']
     (out/'window-prepared.json').write_text(json.dumps(dict(verified=c,operations=operations),indent=2))
    for operation in operations:subprocess.run(['hyprctl','dispatch',operation],check=True,capture_output=True)
   threads=[]
   if child:
    for task in Path('/proc',str(child.pid),'task').glob('*'):
     try:
      rest=(task/'stat').read_text().rsplit(')',1)[1].split();threads.append(dict(tid=int(task.name),comm=(task/'comm').read_text().strip(),cpu_ticks=int(rest[11])+int(rest[12])))
     except FileNotFoundError:pass
   observations.append(dict(seconds=t,visible=bool(c.get('visible')) and not c.get('hidden',False),window_size=c.get('size'),pinned=c.get('pinned'),floating=c.get('floating'),hidden=c.get('hidden'),workspace=c.get('workspace'),monitor=c.get('monitor'),threads=threads))
   with (out/'observations.jsonl').open('a') as f:f.write(json.dumps(observations[-1])+'\n')
   control=out/'start.json'
   if marker is None and control.exists():
    marker=json.loads(control.read_text());manifest.update(phase='gameplay',live_gameplay_verified=bool(marker.get('live_gameplay_verified')),interval_seconds=[t+5,t+65],scene_evidence=marker,window_size=c.get('size'));(out/'measurement.json').write_text(json.dumps(manifest,indent=2)+'\n')
    if a.scopes and child:
     try:(out/'cpu-sample-maps.txt').write_text(Path('/proc',str(child.pid),'maps').read_text())
     except FileNotFoundError:pass
     sample_streams.extend([open_log(out/'cpu-samples.csv','w'),open_log(out/'cpu-samples.log','w')])
     sampler=subprocess.Popen([str(out/'cpu-sampler'),str(child.pid),'65'],stdout=sample_streams[0],stderr=sample_streams[1])

 worker=threading.Thread(target=watch,daemon=True);worker.start()
 try:status,session=run_session([str(exe),str(content/'USRDIR/EBOOT.elf')],cwd=ROOT,env=env,timeout=a.timeout,log_path=out/'startup.log',request_snapshot=True,on_start=on_start)
 finally:
  stop.set();worker.join(timeout=5);lock.close()
  if sampler is not None:
   if sampler.poll() is None:sampler.terminate()
   sampler.wait(timeout=5)
  for stream in sample_streams:stream.close()
 log=(out/'startup.log').read_text(errors='replace');counts={k:int(v,16) for k,v in re.findall(r'\[execution-count\] ([a-z-]+)=0x([0-9a-fA-F]+)',log)}
 manifest.update(actual_audio=re.findall(r'\[gameplay-audio\] (.*)',log),swapchain_sizes=sorted(set(re.findall(r'window swapchain (\d+x\d+)',log))),session=session,renderer_failures=[l for l in log.splitlines() if re.search(r'\[(?:rsx engine|RSX vulkan)\].*(?:failed|unsupported|unhandled|skipped)',l,re.I)],strict_counts=counts,strict_passed=all(counts.get(k)==0 for k in ['ppu-miss','hle-missing','spu-interpreter','spu-untranslated','ppu-recovery']),internal_surfaces=sorted(set(re.findall(r'\[gameplay-surface\] (.*)',log))))
 if (out/'invalid-scene.json').exists():manifest['live_gameplay_verified']=False
 if marker:
  begin,end=manifest['interval_seconds'];rows=[r for r in observations if begin<=r['seconds']<=end];manifest['visible_entire_interval']=bool(rows) and rows[-1]['seconds']>=end-1.5 and all(r['visible'] and r['window_size']==manifest['window_size'] for r in rows)
 (out/'measurement.json').write_text(json.dumps(manifest,indent=2)+'\n')
 if marker:
  report=summarize(out);(out/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(report['cadence'])
 else:raise SystemExit('No verified live-gameplay start marker; diagnostic only')

if __name__=='__main__':main()
