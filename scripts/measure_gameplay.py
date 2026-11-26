#!/usr/bin/env python3
"""Measure verified gameplay; use online-pair / online-pair-review for paired peers.

See docs/ONLINE_FPS_BASELINES.md for paired capture and visibility review.
"""
import argparse,hashlib,json,os,re,subprocess,sys,time,tomllib
from pathlib import Path
from boot_session import run_session
from port import ROOT,resolve,launch_environment
from user_profiles import open_profile
from diagnostic_logs import open_log
from gameplay_report import summarize,renderer_failures,FrameProgress
from build_identity import source_identity, measurement_build, require_runtime_source
from host_inventory import inventory


def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',required=True);p.add_argument('--scene',required=True,choices=['battle','campaign']);p.add_argument('--profile',required=True);p.add_argument('--resume',action='store_true');p.add_argument('--scripted-input',help='Explicit PAD_FILE path for labeled scripted navigation/gameplay');p.add_argument('--scopes',action='store_true');p.add_argument('--overlay',action='store_true');p.add_argument('--pin-window',action='store_true',help='Float/pin only this test window across workspace switches');p.add_argument('--timeout',type=float,default=300);p.add_argument('--correctness-video',action='store_true');p.add_argument('--build-dir',default='build-vulkan');p.add_argument('--submit-mode',choices=['serial','grouped'],default='grouped');p.add_argument('--profile-seconds',type=int,default=60,help='10..60 seconds, shorter intervals require --scopes and cannot be clean baselines');p.add_argument('--decode-profile',action='store_true');p.add_argument('--texture-decode-mode',choices=['reference','specialized'],default='specialized');p.add_argument('--cpu-depth',action='store_true',help='Reference CPU depth conversion (TB_DEPTH_GPU=0); GPU conversion is the default');p.add_argument("--color-readback-cache",action="store_true");p.add_argument("--fence-poll",action="store_true");p.add_argument("--content-root",help="Isolated purchased title copy inside analysis/");p.add_argument("--lifecycle-stats",action="store_true");p.add_argument("--cpu-samples",action="store_true",help="Windows diagnostic: sample the main guest, walker and present threads during the interval");p.add_argument("--draw-remap-cache",action="store_true");p.add_argument("--draw-verify",action="store_true");p.add_argument("--quick",action="store_true",help="Short clean iteration interval; never acceptance evidence");p.add_argument("--frame-budget",action="store_true",help="Record the per-frame walker/guest timeline inside the interval");p.add_argument("--label-sync",action="store_true",help="Reference mode: wait for the GPU before every label/reference publication");p.add_argument("--vk-serial",action="store_true",help="Reference mode: submit and wait for every draw batch");p.add_argument("--page-census",action="store_true",help="Diagnostic: record CPU accesses to RSX surface memory");p.add_argument("--cpu-resolve",action="store_true",help="Reference mode: NV3089 surface blits through guest memory");p.add_argument("--vblank-hz",default="60",help="Vblank beat; 62.5 is the old 16 ms frame clock")
 p.add_argument('--mailbox-profile',action='store_true',help='Linux raw SPU mailbox diagnostic')
 p.add_argument('--vertex-profile',action='store_true',help='Linux vertex preparation cost diagnostic')
 p.add_argument('--raw-spu-idle',choices=['0','1'],help='Compare original raw-SPU polling (0) with bounded idle waits (1)')
 p.add_argument('--cpu-accounting',action='store_true',help='Windows read-only OS CPU counters for every process thread; no suspension or timeline hooks')
 p.add_argument('--render-scale',type=int,choices=[50,75,100,150,200,300],default=100)
 p.add_argument('--expected-runtime-source',help='Require and verify this runtime commit before launching the game')
 p.add_argument('--rsx-trace',action='store_true',help='Heavy diagnostic: retain three command/state frames at gameplay entry')
 a=p.parse_args()
 if a.mailbox_profile and sys.platform!='linux':p.error('--mailbox-profile requires Linux')
 if a.vertex_profile and sys.platform!='linux':p.error('--vertex-profile requires Linux')
 if a.cpu_accounting and os.name!='nt':p.error('--cpu-accounting requires Windows')
 if not 10<=a.profile_seconds<=60 or a.profile_seconds!=60 and not (a.scopes or a.quick):p.error('Shorter profile intervals require --scopes or --quick and 10..60 seconds')
 build=resolve(a.build_dir)
 out=resolve(a.output)
 if not out.is_relative_to(ROOT/'analysis/performance') or out.exists():p.error('Use a new output inside analysis/performance/')
 if not resolve(a.profile).is_relative_to(ROOT/'analysis'):p.error('Measurement profiles must be inside ignored analysis/')
 if a.scripted_input and (not resolve(a.scripted_input).is_relative_to(ROOT/'analysis/performance') or resolve(a.scripted_input).suffix not in ('.txt','.pad')):p.error('Use a .txt/.pad scripted-input file inside analysis/performance/')
 out.mkdir(parents=True);config=tomllib.loads((ROOT/'config.toml').read_text());source=resolve(config['paths']['hdd0_root']);content=source/'game'/config['game']['title_id']
 if a.content_root:
  content=resolve(a.content_root)
  if not content.is_relative_to(ROOT/'analysis') or not (content/'USRDIR/EBOOT.elf').is_file():p.error('Content copy must be a title directory inside analysis/')
 metadata,lock=open_profile(resolve(a.profile),source,content,config['game']['title_id'],resume=a.resume)
 env=launch_environment(config,str(content/'PARAM.SFO'),mounts=metadata['mounts'])
 for k in list(env):
  if k.startswith(('TB_','PAD_','PS3RECOMP_VK_','RSX_ENGINE_','VK_DRAW_','PS3_FTRACE')):env.pop(k)
 env.pop('SPU_RAW_MBOX_PROFILE',None)
 if a.mailbox_profile:env['SPU_RAW_MBOX_PROFILE']='1'
 if a.vertex_profile:env['TB_DEPTH_PATH_TRACE']='1'
 (out/'settings.ini').write_text('overlay = '+('1' if a.overlay else '0')+'\n')
 env.update(TB_RENDER_SCALE=str(a.render_scale),TB_FXAA='0',PS3RECOMP_FULLSCREEN='0')
 title='Tank Battles performance '+out.name+(' [VIDEO DIAGNOSTIC]' if a.correctness_video else ' [PROFILE DIAGNOSTIC]' if a.scopes or a.decode_profile else '')
 env.update(TB_SETTINGS_FILE=str(out/'settings.ini'),TB_EXECUTION_MODE='strict',TB_GAMEPLAY_TIMING=str(out/'gameplay.jsonl'),TB_FPS_OVERLAY='1' if a.overlay else '0',PS3_TITLE=title,SDL_VIDEODRIVER='windows' if os.name=='nt' else 'x11',PS3RECOMP_VK_WINDOW='1',PS3RECOMP_VK_GUEST_PROGRAMS='1',VK_DRAW_BATCH='0',PS3_VERBOSE='0',TB_VK_SUBMIT_MODE=a.submit_mode,TB_TEXTURE_DECODE_MODE=a.texture_decode_mode)
 if env.get('SDL_AUDIODRIVER')=='dummy':env.pop('SDL_AUDIODRIVER')
 if a.correctness_video:env['TB_FRAME_ORDER_TRACE']=str(out/'scanout-order.jsonl')
 if a.decode_profile:env.update(TB_TEXTURE_DECODE_PROFILE=str(out/'texture-decode.jsonl'),TB_DEPTH_PATH_TRACE='1')
 # Benchmark defaults deliberately select the reference implementation.
 if a.lifecycle_stats:env['TB_LIFECYCLE_STATS']='1'
 env['TB_DEPTH_GPU']='0' if a.cpu_depth else '1'
 env['TB_COLOR_READBACK_CACHE']='1' if a.color_readback_cache else '0'
 env['TB_VK_FENCE_POLL']='1' if a.fence_poll else '0'
 env['TB_DRAW_REMAP_CACHE']='1' if a.draw_remap_cache else '0'
 env['TB_LABEL_SYNC']='1' if a.label_sync else '0'
 env['TB_VK_ASYNC']='0' if a.vk_serial else '1'
 if a.page_census:env['TB_PAGE_CENSUS']='1'
 env['TB_GPU_RESOLVE']='0' if a.cpu_resolve else '1'
 env['TB_VBLANK_HZ']=a.vblank_hz
 if a.raw_spu_idle is not None:env['TB_RAW_SPU_IDLE']=a.raw_spu_idle
 if a.draw_verify:env['TB_DRAW_REMAP_VERIFY']='1'
 if a.frame_budget:env.update(TB_FRAME_BUDGET_TRIGGER=str(out/'frame-budget.trigger'),TB_FRAME_BUDGET=str(out/'frame-budget.trace'),TB_FRAME_BUDGET_DELAY='5',TB_FRAME_BUDGET_SECONDS=str(a.profile_seconds))
 if a.scopes:env.update(TB_RENDER_PROFILE=str(out/'scopes.jsonl'),TB_RENDER_PROFILE_MAX_REPORTS='600')
 env['TB_DIAGNOSTIC_TRIGGER']=str(out/'diagnostic.trigger')
 if a.rsx_trace:
  (out/'rsx').mkdir()
  env.update(TB_RSX_TRACE=str(out/'rsx'),TB_DEPTH_PATH_TRACE='1')
 if a.scripted_input:
  env.update(PAD_FILE=str(resolve(a.scripted_input)),PAD_NO_KEYBOARD='1',SDL_GAMECONTROLLER_IGNORE_DEVICES_EXCEPT='0x0000/0x0000')
 inspected=measurement_build(build)
 if a.expected_runtime_source:require_runtime_source(inspected,a.expected_runtime_source)
 frozen=json.loads((build/'sources.json').read_text()) if (build/'sources.json').exists() else None
 flags=inspected['effective_flags']
 host=inventory();(out/'host.json').write_text(json.dumps(host,indent=2)+'\n')
 gpu=host.get('hardware',{}).get('gpu',host.get('graphics',{}))
 exe=inspected['executable'];manifest=dict(scene=a.scene,phase='unverified',live_gameplay_verified=False,interval_seconds=None,visible_entire_interval=False,strict_passed=False,guest_resolution=[1280,720],internal_surfaces=[],window_size=None,gpu=gpu,driver=('win32' if os.name=='nt' else 'x11')+'/Vulkan',audio=env.get('SDL_AUDIODRIVER','host-default'),input_source='scripted navigation, neutral player one; physical/keyboard excluded' if a.scripted_input else 'physical controller/keyboard; no synthetic PAD inputs',sdk_verbose=False,submission_mode=a.submit_mode,texture_decode_mode=a.texture_decode_mode,fence_wait_mode="poll" if a.fence_poll else "blocking",label_publication="gpu-complete" if a.label_sync else "processed",batch_submission="serial" if a.vk_serial else "async",surface_blits="guest-memory" if a.cpu_resolve else "gpu",vblank_hz=a.vblank_hz,draw_remap_mode="cached" if a.draw_remap_cache else "reference",color_readback_mode="cached" if a.color_readback_cache else "reference",depth_conversion="reference" if a.cpu_depth else "gpu",build_directory=str(build),source_inputs_sha256=hashlib.sha256(json.dumps(frozen['files'],sort_keys=True).encode()).hexdigest() if frozen else source_identity(),build_type=inspected['build_type'],compiler=inspected['compiler'],effective_flags=flags,diagnostics=dict(video=a.correctness_video,screenshots=False,textures=False,resources=False,input_trace=False,wait_trace=False,passes=False,scopes=a.scopes,frame_budget=a.frame_budget,page_census=a.page_census,texture_decode_profile=a.decode_profile,draw_verify=a.draw_verify,lifecycle_stats=a.lifecycle_stats,cpu_samples=a.cpu_samples,vertex_profile=a.vertex_profile),overlay=a.overlay,quick_bench=a.quick,source_commit=frozen['commit'] if frozen else subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),executable_sha256=hashlib.sha256(exe.read_bytes()).hexdigest(),sdk_manifest_sha256=inspected['sdk_manifest_sha256'],runtime_source_commit=inspected['runtime_source_commit'],runtime_build=inspected['runtime_build'],profile=metadata,clock_origin='TB_GAMEPLAY_TIMING first event; marker and observation clocks mapped by current telemetry timestamp')
 manifest.update(render_scale=a.render_scale,window_mode='windowed',fxaa=False,raw_spu_idle=a.raw_spu_idle or 'runtime-default')
 manifest['diagnostics']['cpu_accounting']=a.cpu_accounting
 manifest['title_pgo']=inspected['title_pgo']
 manifest['diagnostics']['pgo_training']=inspected['title_pgo']=='generate'
 manifest['host_clock']=dict(name='perf_counter',implementation=time.get_clock_info('perf_counter').implementation,
  resolution_seconds=time.get_clock_info('perf_counter').resolution,
  reason='QueryPerformanceCounter matches tb_host_now on Windows; CLOCK_MONOTONIC on Linux.')
 if a.rsx_trace:manifest['diagnostics'].update(resources=True,rsx_trace=True)
 if a.expected_runtime_source:manifest['expected_runtime_source']=a.expected_runtime_source
 if a.mailbox_profile:manifest['diagnostics']['mailbox_profile']=True
 manifest['launch_environment']={k:v for k,v in env.items() if k.startswith(('TB_','PAD_','PS3RECOMP_VK_','SDL_','VK_DRAW_'))}
 manifest['telemetry_unavailable']=['CPU stack/context-switch sampling on Windows'] if os.name=='nt' else []
 (out/'measurement.json').write_text(json.dumps(manifest,indent=2)+'\n')
 import threading
 stop=threading.Event();observations=[];child=None;marker=None;sampler=None;sample_streams=[];resources=None
 if a.scopes and os.name!='nt':
  subprocess.run(['cc','-O2',str(ROOT/'scripts/sample_gameplay_cpu.c'),'-o',str(out/'cpu-sampler')],check=True)
 def on_start(process):
  nonlocal child,resources;child=process;(out/'pid').write_text(str(process.pid))
  if os.name=='nt' and (a.scopes or a.frame_budget or a.cpu_accounting):
   from windows_resources import WindowsResources
   try:resources=WindowsResources(process.pid,all_threads=True)
   except OSError as error:(out/'resources-unavailable.json').write_text(json.dumps(dict(error=str(error))))
 desktop=None
 if os.name=='nt':
  from windows_gameplay import WindowsDesktop
  desktop=WindowsDesktop()
 def watch():
  nonlocal marker,sampler
  progress=FrameProgress()
  while not stop.wait(1):
   t=None;path=out/'gameplay.jsonl'
   if path.exists():
    try:
     last=json.loads(path.read_text().splitlines()[-1]);t=(time.perf_counter_ns()-(last['monotonic_ns']-last['elapsed_ns']))/1e9
    except (IndexError,json.JSONDecodeError):continue
   if t is None:continue
   interval=manifest.get('interval_seconds')
   stalled=progress.observe_record(last,time.perf_counter_ns(),
                            bool(interval and interval[0]<=t<=interval[1]))
   if stalled:
    (out/'progress-stall.json').write_text(json.dumps(stalled,indent=2)+'\n')
    (out/'invalid-scene.json').write_text(json.dumps(stalled,indent=2)+'\n')
    (out/'diagnostic.trigger').write_text('snapshot')
    if desktop:
     window=desktop.find(child.pid,title)
     if window and window.get('visible'):
      desktop.capture(window['window'],child.pid,out/'progress-stall.png')
    return
   if os.name=='nt':
    if child is None:continue
    c=desktop.find(child.pid,title)
    if a.pin_window and c and not (out/'window-prepared.json').exists():
     c=desktop.prepare(c['window'],child.pid)
     if c.get('visible') and c.get('size')==[1280,720]:
      prepared=out/'window-prepared.tmp'
      prepared.write_text(json.dumps(dict(verified=c,operations=['resize client to 1280x720, foreground'])))
      prepared.replace(out/'window-prepared.json')
     else:
      (out/'window-pending.json').write_text(json.dumps(c))
   else:
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
     elif c.get('fullscreen'):
      operations=['hl.dsp.window.fullscreen({ window = "'+prefix+'", mode = "'+('maximized' if c['fullscreen']==2 else 'fullscreen')+'", action = "unset" })']
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
   try:
    saved_overlay=re.search(r'(?m)^overlay\s*=\s*([01])\s*$',(out/'settings.ini').read_text())
    observations[-1]['overlay_consistent']=bool(saved_overlay and int(saved_overlay[1])==int(a.overlay))
   except OSError:observations[-1]['overlay_consistent']=False
   if resources:
    try:observations[-1]['resources']=resources.sample()
    except OSError as error:observations[-1]['resources']=dict(status='unavailable',reason=str(error))
   with (out/'observations.jsonl').open('a') as f:f.write(json.dumps(observations[-1])+'\n')
   control=out/'start.json'
   if marker is None and control.exists():
    marker=json.loads(control.read_text())
    if a.frame_budget and child:(out/'frame-budget.trigger').write_text('arm')
    if a.rsx_trace:(out/'rsx/request').write_text('gameplay')
    manifest.update(phase='gameplay',live_gameplay_verified=bool(marker.get('live_gameplay_verified')),interval_seconds=[t+5,t+5+a.profile_seconds],scene_evidence=marker,window_size=c.get('size'));(out/'measurement.json').write_text(json.dumps(manifest,indent=2)+'\n')
    if a.cpu_samples and child and os.name=='nt':
     # Suspend-and-sample at ~1 kHz from 5 s after the marker for the interval.
     sample_streams.append(open_log(out/'cpu-samples.log','w'))
     sampler=subprocess.Popen([sys.executable,str(ROOT/'scripts/sample_windows_cpu.py'),'--pid',str(child.pid),'--seconds',str(a.profile_seconds),'--delay','5','--output',str(out/'cpu-samples.json'),'--map',str(exe.with_suffix('.map'))],stdout=sample_streams[0],stderr=subprocess.STDOUT)
    if a.scopes and child and os.name!='nt':
     try:(out/'cpu-sample-maps.txt').write_text(Path('/proc',str(child.pid),'maps').read_text())
     except FileNotFoundError:pass
     sample_streams.extend([open_log(out/'cpu-samples.csv','w'),open_log(out/'cpu-samples.log','w')])
     sampler=subprocess.Popen([str(out/'cpu-sampler'),str(child.pid),str(5+a.profile_seconds)],stdout=sample_streams[0],stderr=sample_streams[1])

 def guarded_watch():
  try:watch()
  except Exception as error:
   (out/'observer-error.json').write_text(json.dumps(dict(error=str(error))))
 worker=threading.Thread(target=guarded_watch,daemon=True);worker.start()
 try:status,session=run_session([str(exe),str(content/'USRDIR/EBOOT.elf')],cwd=ROOT,env=env,timeout=a.timeout,log_path=out/'startup.log',request_snapshot=True,on_start=on_start)
 finally:
  stop.set();worker.join(timeout=5);lock.close()
  if resources and not worker.is_alive():resources.close()
  if sampler is not None:
   if sampler.poll() is None:sampler.terminate()
   sampler.wait(timeout=5)
  for stream in sample_streams:stream.close()
 log=(out/'startup.log').read_text(errors='replace');counts={k:int(v,16) for k,v in re.findall(r'\[execution-count\] ([a-z-]+)=0x([0-9a-fA-F]+)',log)}
 manifest.update(actual_audio=re.findall(r'\[gameplay-audio\] (.*)',log),swapchain_sizes=sorted(set(re.findall(r'window swapchain (\d+x\d+)',log))),session=session,renderer_failures=renderer_failures(log),strict_counts=counts,strict_passed=all(counts.get(k)==0 for k in ['ppu-miss','hle-missing','spu-interpreter','spu-untranslated','ppu-recovery']),internal_surfaces=sorted(set(re.findall(r'\[gameplay-surface\] (.*)',log))))
 if marker and marker.get('visual_boundaries'):
  from script_gameplay import validate_visual_boundaries
  try:
   validate_visual_boundaries(out,a.overlay,require_receipt=True)
   manifest['live_gameplay_verified']=True
   manifest['scene_evidence']=dict(marker,pending_review=False,live_gameplay_verified=True)
  except (OSError,ValueError) as error:
   manifest['live_gameplay_verified']=False
   (out/'invalid-scene.json').write_text(json.dumps(dict(reason=str(error))))
 if (out/'invalid-scene.json').exists() or (out/'observer-error.json').exists():manifest['live_gameplay_verified']=False
 manifest['effective_runtime']=dict(device=re.findall(r'using device \d+: (.*)',log),present_mode=re.findall(r'window present mode (.*)',log),batch_submission=re.findall(r'draw batches: (.*)',log),depth_gpu_active='exact Z16 GPU conversion active' in log,nv3089_gpu_exact=True if 'NV3089 GPU point sampling matches' in log else False if 'NV3089 GPU point sampling differs' in log else None)
 actual=manifest['effective_runtime']['batch_submission']
 if actual:manifest['batch_submission']='async' if 'asynchronous' in actual[-1] else 'serial'
 manifest['render_scale_observations']=dict(status='unavailable',reason='Clean run or runtime without scale telemetry')
 if a.frame_budget and (out/'frame-budget.trace').is_file():
  from frame_budget import load,render_scale_observations
  manifest['render_scale_observations']=render_scale_observations(load(out/'frame-budget.trace')[2])
 if marker:
  begin,end=manifest['interval_seconds'];rows=[r for r in observations if begin<=r['seconds']<=end];manifest['visible_entire_interval']=bool(rows) and rows[-1]['seconds']>=end-1.5 and all(r['visible'] and r['window_size']==manifest['window_size'] for r in rows)
  manifest['overlay_consistent']=bool(rows) and all(r.get('overlay_consistent') for r in rows)
 (out/'measurement.json').write_text(json.dumps(manifest,indent=2)+'\n')
 if resources and marker:
  from windows_cpu_report import analyze
  (out/'windows-cpu.json').write_text(json.dumps(analyze(out),indent=2)+'\n')
 if marker and a.rsx_trace:
  completed=sorted(path.name for path in (out/'rsx').glob('*.done'))
  accepted=manifest['strict_passed'] and manifest['live_gameplay_verified'] and manifest['visible_entire_interval'] and manifest['overlay_consistent'] and not manifest['renderer_failures'] and len(completed)==3
  (out/'diagnostic-report.json').write_text(json.dumps(dict(kind='heavy RSX resource evidence',
   baseline_eligible=False,complete=accepted,completed_scopes=completed),indent=2)+'\n')
  if not accepted:raise SystemExit('Incomplete or invalid RSX resource diagnostic')
 elif marker and a.correctness_video:
  if not manifest["strict_passed"]:raise SystemExit("Strict failure in correctness video")
 elif marker:
  report=summarize(out,minimum_seconds=a.profile_seconds);(out/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(report['cadence'])
 else:raise SystemExit('No verified live-gameplay start marker; diagnostic only')

if __name__=='__main__':
 if len(sys.argv)>1 and sys.argv[1]=='online-pair':
  from online_gameplay import capture
  capture(sys.argv[2:])
 elif len(sys.argv)>1 and sys.argv[1]=='online-pair-review':
  from online_gameplay import review
  review(sys.argv[2:])
 else:main()
