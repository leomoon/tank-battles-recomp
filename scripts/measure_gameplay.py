#!/usr/bin/env python3
"""Run one isolated visible gameplay measurement; operator marks a verified live scene."""
import argparse,hashlib,json,os,re,subprocess,time,tomllib
from pathlib import Path
from boot_session import run_session
from port import ROOT,resolve,launch_environment
from user_profiles import open_profile
from diagnostic_logs import open_log
from gameplay_report import summarize,renderer_failures
from build_identity import source_identity


def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',required=True);p.add_argument('--scene',required=True,choices=['battle','campaign']);p.add_argument('--profile',required=True);p.add_argument('--resume',action='store_true');p.add_argument('--scripted-input',help='Explicit PAD_FILE path for labeled scripted navigation/gameplay');p.add_argument('--scopes',action='store_true');p.add_argument('--overlay',action='store_true');p.add_argument('--pin-window',action='store_true',help='Float/pin only this test window across workspace switches');p.add_argument('--timeout',type=float,default=300);p.add_argument('--correctness-video',action='store_true');p.add_argument('--build-dir',default='build-vulkan');p.add_argument('--submit-mode',choices=['serial','grouped'],default='grouped');p.add_argument('--profile-seconds',type=int,default=60,help='10..60 seconds, shorter intervals require --scopes and cannot be clean baselines');p.add_argument('--decode-profile',action='store_true');p.add_argument('--texture-decode-mode',choices=['reference','specialized'],default='specialized');p.add_argument('--depth-gpu',action='store_true',help='Select exact GPU depth conversion (the Linux production path) for a declared implementation comparison');p.add_argument("--color-readback-cache",action="store_true");p.add_argument("--fence-poll",action="store_true");p.add_argument("--draw-remap-cache",action="store_true");p.add_argument("--draw-verify",action="store_true");p.add_argument("--quick",action="store_true",help="Short clean iteration interval; never acceptance evidence");p.add_argument("--frame-budget",action="store_true",help="Record the per-frame walker/guest timeline inside the interval");p.add_argument("--label-sync",action="store_true",help="Reference mode: wait for the GPU before every label/reference publication");p.add_argument("--vk-serial",action="store_true",help="Reference mode: submit and wait for every draw batch");p.add_argument("--page-census",action="store_true",help="Diagnostic: record CPU accesses to RSX surface memory");p.add_argument("--cpu-resolve",action="store_true",help="Reference mode: NV3089 surface blits through guest memory");a=p.parse_args()
 if not 10<=a.profile_seconds<=60 or a.profile_seconds!=60 and not (a.scopes or a.quick):p.error('Shorter profile intervals require --scopes or --quick and 10..60 seconds')
 build=resolve(a.build_dir)
 out=resolve(a.output)
 if not out.is_relative_to(ROOT/'analysis/performance') or out.exists():p.error('Use a new output inside analysis/performance/')
 if not resolve(a.profile).is_relative_to(ROOT/'analysis'):p.error('Measurement profiles must be inside ignored analysis/')
 if a.scripted_input and (not resolve(a.scripted_input).is_relative_to(ROOT/'analysis/performance') or resolve(a.scripted_input).suffix not in ('.txt','.pad')):p.error('Use a .txt/.pad scripted-input file inside analysis/performance/')
 out.mkdir(parents=True);config=tomllib.loads((ROOT/'config.toml').read_text());source=resolve(config['paths']['hdd0_root']);content=source/'game'/config['game']['title_id']
 metadata,lock=open_profile(resolve(a.profile),source,content,config['game']['title_id'],resume=a.resume)
 env=launch_environment(config,str(content/'PARAM.SFO'),mounts=metadata['mounts'])
 for k in list(env):
  if k.startswith(('TB_','PAD_','PS3RECOMP_VK_','RSX_ENGINE_','VK_DRAW_','PS3_FTRACE')):env.pop(k)
 title='Tank Battles performance '+out.name+(' [VIDEO DIAGNOSTIC]' if a.correctness_video else ' [PROFILE DIAGNOSTIC]' if a.scopes or a.decode_profile else '')
 env.update(TB_EXECUTION_MODE='strict',TB_GAMEPLAY_TIMING=str(out/'gameplay.jsonl'),TB_FPS_OVERLAY='1' if a.overlay else '0',PS3_TITLE=title,SDL_VIDEODRIVER='x11',PS3RECOMP_VK_WINDOW='1',PS3RECOMP_VK_GUEST_PROGRAMS='1',VK_DRAW_BATCH='0',PS3_VERBOSE='0',TB_VK_SUBMIT_MODE=a.submit_mode,TB_TEXTURE_DECODE_MODE=a.texture_decode_mode)
 if env.get('SDL_AUDIODRIVER')=='dummy':env.pop('SDL_AUDIODRIVER')
 if a.correctness_video:env['TB_FRAME_ORDER_TRACE']=str(out/'scanout-order.jsonl')
 if a.decode_profile:env.update(TB_TEXTURE_DECODE_PROFILE=str(out/'texture-decode.jsonl'),TB_DEPTH_PATH_TRACE='1')
 # Benchmark defaults deliberately select the reference implementation.
 env['TB_DEPTH_GPU']='1' if a.depth_gpu else '0'
 env['TB_COLOR_READBACK_CACHE']='1' if a.color_readback_cache else '0'
 env['TB_VK_FENCE_POLL']='1' if a.fence_poll else '0'
 env['TB_DRAW_REMAP_CACHE']='1' if a.draw_remap_cache else '0'
 env['TB_LABEL_SYNC']='1' if a.label_sync else '0'
 env['TB_VK_ASYNC']='0' if a.vk_serial else '1'
 if a.page_census:env['TB_PAGE_CENSUS']='1'
 env['TB_GPU_RESOLVE']='0' if a.cpu_resolve else '1'
 if a.draw_verify:env['TB_DRAW_REMAP_VERIFY']='1'
 if a.frame_budget:env.update(TB_FRAME_BUDGET=str(out/'frame-budget.trace'),TB_FRAME_BUDGET_DELAY='5',TB_FRAME_BUDGET_SECONDS=str(a.profile_seconds))
 if a.scopes:env.update(TB_RENDER_PROFILE=str(out/'scopes.jsonl'),TB_RENDER_PROFILE_MAX_REPORTS='600',TB_GPU_TIMING=str(out/'gpu-timing.jsonl'))
 if a.scripted_input:
  env.update(PAD_FILE=str(resolve(a.scripted_input)),PAD_NO_KEYBOARD='1',SDL_GAMECONTROLLER_IGNORE_DEVICES_EXCEPT='0x0000/0x0000')
 commands=(build/'commands.txt').read_text() if (build/'commands.txt').exists() else subprocess.check_output(['ninja','-C',str(build),'-t','commands'],text=True)
 frozen=json.loads((build/'sources.json').read_text()) if (build/'sources.json').exists() else None
 flags={}
 for kind,needle in [('ppu','ppu_recomp_'),('spu','spu_recomp.c.o'),('runtime','rendering_profile.cpp.o')]:
  matches=[l for l in commands.splitlines() if needle in l and ' -c ' in l];flags[kind]=matches[0] if matches else None
 gpu=subprocess.check_output(['nvidia-smi','--query-gpu=name,driver_version','--format=csv,noheader'],text=True).strip()
 exe=build/'TankBattlesRecomp';manifest=dict(scene=a.scene,phase='unverified',live_gameplay_verified=False,interval_seconds=None,visible_entire_interval=False,strict_passed=False,guest_resolution=[1280,720],internal_surfaces=[],window_size=None,gpu=gpu,driver='x11/Vulkan',audio=env.get('SDL_AUDIODRIVER','host-default'),input_source='scripted navigation, neutral player one; physical/keyboard excluded' if a.scripted_input else 'physical controller/keyboard; no synthetic PAD inputs',sdk_verbose=False,submission_mode=a.submit_mode,texture_decode_mode=a.texture_decode_mode,fence_wait_mode="poll" if a.fence_poll else "blocking",label_publication="gpu-complete" if a.label_sync else "processed",batch_submission="serial" if a.vk_serial else "async",surface_blits="guest-memory" if a.cpu_resolve else "gpu",draw_remap_mode="cached" if a.draw_remap_cache else "reference",color_readback_mode="cached" if a.color_readback_cache else "reference",depth_conversion="gpu" if a.depth_gpu else "reference",build_directory=str(build),source_inputs_sha256=hashlib.sha256(json.dumps(frozen['files'],sort_keys=True).encode()).hexdigest() if frozen else source_identity(),build_type=re.search(r'^CMAKE_BUILD_TYPE:STRING=(.*)$',(build/'CMakeCache.txt').read_text(),re.M)[1],compiler=subprocess.check_output(['c++','--version'],text=True).splitlines()[0],effective_flags=flags,diagnostics=dict(video=a.correctness_video,screenshots=False,textures=False,resources=False,input_trace=False,wait_trace=False,passes=False,scopes=a.scopes,frame_budget=a.frame_budget,page_census=a.page_census,texture_decode_profile=a.decode_profile,draw_verify=a.draw_verify),overlay=a.overlay,quick_bench=a.quick,source_commit=frozen['commit'] if frozen else subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),executable_sha256=hashlib.sha256(exe.read_bytes()).hexdigest(),sdk_manifest_sha256=hashlib.sha256((build/'instrumented_sdk/manifest.json').read_bytes()).hexdigest(),profile=metadata,clock_origin='TB_GAMEPLAY_TIMING first event; marker and observation clocks mapped by current telemetry timestamp')
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
    monitors=json.loads(subprocess.check_output(['hyprctl','-j','monitors'],text=True))
    monitor=next((m for m in monitors if m['id']==c.get('monitor')),None)
    position=None
    if monitor:
     scale=monitor.get('scale',1);mx,my=monitor['x'],monitor['y'];mw,mh=monitor['width']/scale,monitor['height']/scale
     x,y=c['at'];w,h=c['size'];position=[round(min(max(x,mx+8),mx+mw-w-8)),round(min(max(y,my+8),my+mh-h-8))]
    if c.get('grouped'):
     operations=['hl.dsp.window.move({ window = "'+prefix+'", out_of_group = true })']
    elif not c.get('floating'):
     operations=['hl.dsp.window.float({ window = "'+prefix+'", action = "toggle" })']
    elif not c.get('pinned'):
     operations=['hl.dsp.window.pin({ window = "'+prefix+'" })']
    elif c.get('size')!=[1280,720]:
     operations=['hl.dsp.window.resize({ window = "'+prefix+'", x = 1280, y = 720 })']
    elif position and position!=c['at']:
     operations=[f'hl.dsp.window.move({{ window = "{prefix}", x = {position[0]}, y = {position[1]} }})']
    else:
     operations=['hl.dsp.window.alter_zorder({ window = "'+prefix+'", mode = "top" })','hl.dsp.focus({ window = "'+prefix+'" })']
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
    marker=json.loads(control.read_text())
    if a.frame_budget and child:os.kill(child.pid,__import__('signal').SIGUSR2)
    manifest.update(phase='gameplay',live_gameplay_verified=bool(marker.get('live_gameplay_verified')),interval_seconds=[t+5,t+5+a.profile_seconds],scene_evidence=marker,window_size=c.get('size'));(out/'measurement.json').write_text(json.dumps(manifest,indent=2)+'\n')
    if a.scopes and child:
     try:(out/'cpu-sample-maps.txt').write_text(Path('/proc',str(child.pid),'maps').read_text())
     except FileNotFoundError:pass
     sample_streams.extend([open_log(out/'cpu-samples.csv','w'),open_log(out/'cpu-samples.log','w')])
     sampler=subprocess.Popen([str(out/'cpu-sampler'),str(child.pid),str(5+a.profile_seconds)],stdout=sample_streams[0],stderr=sample_streams[1])

 worker=threading.Thread(target=watch,daemon=True);worker.start()
 try:status,session=run_session([str(exe),str(content/'USRDIR/EBOOT.elf')],cwd=ROOT,env=env,timeout=a.timeout,log_path=out/'startup.log',request_snapshot=True,on_start=on_start)
 finally:
  stop.set();worker.join(timeout=5);lock.close()
  if sampler is not None:
   if sampler.poll() is None:sampler.terminate()
   sampler.wait(timeout=5)
  for stream in sample_streams:stream.close()
 log=(out/'startup.log').read_text(errors='replace');counts={k:int(v,16) for k,v in re.findall(r'\[execution-count\] ([a-z-]+)=0x([0-9a-fA-F]+)',log)}
 manifest.update(actual_audio=re.findall(r'\[gameplay-audio\] (.*)',log),swapchain_sizes=sorted(set(re.findall(r'window swapchain (\d+x\d+)',log))),session=session,renderer_failures=renderer_failures(log),strict_counts=counts,strict_passed=all(counts.get(k)==0 for k in ['ppu-miss','hle-missing','spu-interpreter','spu-untranslated','ppu-recovery']),internal_surfaces=sorted(set(re.findall(r'\[gameplay-surface\] (.*)',log))))
 if (out/'invalid-scene.json').exists():manifest['live_gameplay_verified']=False
 if marker:
  begin,end=manifest['interval_seconds'];rows=[r for r in observations if begin<=r['seconds']<=end];manifest['visible_entire_interval']=bool(rows) and rows[-1]['seconds']>=end-1.5 and all(r['visible'] and r['window_size']==manifest['window_size'] for r in rows)
 (out/'measurement.json').write_text(json.dumps(manifest,indent=2)+'\n')
 if marker and a.correctness_video:
  if not manifest["strict_passed"]:raise SystemExit("Strict failure in correctness video")
 elif marker:
  report=summarize(out,minimum_seconds=a.profile_seconds);(out/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(report['cadence'])
 else:raise SystemExit('No verified live-gameplay start marker; diagnostic only')

if __name__=='__main__':main()
