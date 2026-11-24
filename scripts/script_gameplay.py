#!/usr/bin/env python3
"""Drive a labeled benchmark route; capture scene proof only outside steady timing."""
import argparse,json,re,subprocess,time,sys,os
import hashlib
from pathlib import Path
from port import ROOT,resolve
if os.name!='nt':
 from verify_vulkan_rendering import request_close


def invalid_ending_scene(text):
 return any(word in text.upper() for word in ('MISSION FAILED','DEBRIEF','RESULTS','KILLS:','RANK','SELECT AN OPTION','CONTROLS:','RULES:','VICTORY','DEFEAT','GAME OVER','WINS'))


def live_battle_timer(text):
 return any(0<=int(value)<=120 for value in re.findall(r'^\s*(\d{1,3})\s*$',text,re.M))


def interval_end_ns(manifest, timing_row):
 # perf_counter matches the producer's QPC clock on Windows (monotonic used
 # GetTickCount64 on Python 3.11). Linux uses CLOCK_MONOTONIC on both sides.
 return timing_row['monotonic_ns']-timing_row['elapsed_ns']+round(manifest['interval_seconds'][1]*1e9)


def windows_hud_countdown(ocr, size):
 """Use only one localized OCR word inside the live timer, never HUD scores."""
 width,height=size
 if width<=0 or height<=0:return None
 values=[]
 for line in ocr.get('lines',[]):
  for word in line.get('words',[]):
   text=word.get('text','').strip()
   if not re.fullmatch(r'[0-9]{1,3}',text) or not 0<=int(text)<=120:continue
   x,y=word['x']*1280/width,word['y']*720/height
   right=x+word['width']*1280/width;bottom=y+word['height']*720/height
   if 590<=x<right<=690 and 72<=y<bottom<=106:values.append(text)
 return values[0] if len(values)==1 else None


def default_battle_map(text):
 # Recognize the selected map as well as the heading; OCR often loses the
 # heading's animated letters. This does not establish live gameplay.
 normalized=' '.join(text.upper().split())
 return bool(re.search(r'FIELD [O0]F WAR [I1]',normalized) and
             ('CONFIRM' in normalized or 'ELECT MAP' in normalized) and 'BACK' in normalized)


def boundary_countdown(out,label,text,allow_manual,process,expected_overlay=False):
 if not allow_manual:return text
 image=out/(label+'.png');digest=hashlib.sha256(image.read_bytes()).hexdigest()
 request=out/(label+'-review-request.json')
 request.write_text(json.dumps(dict(image=image.name,image_sha256=digest,required='Review live HUD and countdown, then write <label>-review.json with image_sha256, live_gameplay=true, countdown (0..120), reviewer and overlay_visible.')))
 review=out/(label+'-review.json');deadline=time.perf_counter()+120
 while process.poll() is None and time.perf_counter()<deadline:
  if review.exists():
   try:answer=json.loads(review.read_text())
   except (PermissionError,json.JSONDecodeError):
    # A Windows writer can briefly hold an exclusive handle or publish a partial JSON.
    time.sleep(.2);continue
   if answer.get('image_sha256')!=digest or answer.get('live_gameplay') is not True or not answer.get('reviewer') or type(answer.get('countdown')) is not int or not 0<=answer['countdown']<=120 or type(answer.get('overlay_visible')) is not bool or answer['overlay_visible']!=expected_overlay:
    raise ValueError('Invalid/stale manual boundary review')
   return str(answer['countdown'])
  time.sleep(.2)
 raise RuntimeError('Boundary review not supplied: '+str(request))


def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True);parser.add_argument('--scene',choices=['battle','campaign'],default='battle');parser.add_argument('--reference-template');parser.add_argument('--appimage');parser.add_argument('--repeat',type=int,default=1);parser.add_argument('--scopes',action='store_true');parser.add_argument('--overlay',action='store_true');parser.add_argument('--correctness-video',action='store_true');parser.add_argument('--build-dir',default='build-vulkan');parser.add_argument('--submit-mode',choices=['serial','grouped'],default='grouped');parser.add_argument('--profile-seconds',type=int,help='Gameplay interval in seconds (default 60, or 25 for host resources)');parser.add_argument('--decode-profile',action='store_true');parser.add_argument('--texture-decode-mode',choices=['reference','specialized'],default='specialized');parser.add_argument('--cpu-depth',action='store_true',help='Reference CPU depth conversion (TB_DEPTH_GPU=0); GPU conversion is the default');parser.add_argument("--color-readback-cache",action="store_true");parser.add_argument("--fence-poll",action="store_true");parser.add_argument("--draw-remap-cache",action="store_true");parser.add_argument("--draw-verify",action="store_true");parser.add_argument("--quick",action="store_true",help="Short clean iteration interval; never acceptance evidence");parser.add_argument("--frame-budget",action="store_true");parser.add_argument("--label-sync",action="store_true");parser.add_argument("--vk-serial",action="store_true");parser.add_argument("--page-census",action="store_true");parser.add_argument("--cpu-resolve",action="store_true");parser.add_argument("--vblank-hz",default="60");parser.add_argument("--soak-minutes",type=float,default=0,help="Keep the session running after live gameplay, cycling rounds and sampling resources; then close normally");parser.add_argument('--manual-boundaries',action='store_true',help='Allow explicit image-hash-bound review when countdown OCR fails');parser.add_argument('--cpu-samples',action='store_true',help='Windows diagnostic thread sampling during the interval');parser.add_argument('--pending-review',action='store_true',help='Timing diagnostic only: when countdown OCR fails, continue and retain the boundary images for later human review; never baseline-eligible')
 parser.add_argument('--host-resources',action='store_true',help='Sample Linux CPU and sensors across Battle and its menus')
 parser.add_argument('--mailbox-profile',action='store_true',help='Record Linux raw SPU mailbox status counters by scene')
 parser.add_argument('--linux-symbols',action='store_true',help='Diagnostic user-space CPU samples across the Linux resource route')
 parser.add_argument('--vertex-profile',action='store_true',help='Diagnostic vertex preparation costs across the native Linux resource route')
 parser.add_argument('--render-scale',type=int,choices=[100,150,200,300],default=100)
 parser.add_argument('--expected-runtime-source',help='Require this runtime commit before a native measurement launches')
 parser.add_argument('--rsx-trace',action='store_true',help='Heavy diagnostic: three gameplay RSX state frames; never clean baseline evidence')
 a=parser.parse_args()
 if (a.linux_symbols or a.mailbox_profile or a.vertex_profile) and (not a.host_resources or a.reference_template):
  parser.error('CPU attribution requires the native --host-resources route')
 if a.host_resources and a.reference_template and a.correctness_video:
  parser.error('Resource-route video capture requires the native player')
 if a.host_resources and (sys.platform!='linux' or a.scene!='battle' or a.vblank_hz!='60' or
    any((a.scopes,a.decode_profile,a.cpu_samples,a.frame_budget,a.page_census,a.soak_minutes,
         a.pending_review,a.manual_boundaries))):
  parser.error('--host-resources requires Linux Battle at 60 Hz and compatible diagnostic options')
 if a.profile_seconds is None:
  a.profile_seconds=25 if a.host_resources else 60
 if a.host_resources and not a.reference_template:
  a.quick=True
 if a.pending_review and a.manual_boundaries:parser.error('--pending-review and --manual-boundaries are exclusive')
 if os.name=='nt' and (a.reference_template or a.correctness_video or a.soak_minutes):parser.error('Windows reference/video/soak adapters are not implemented yet; no fallback evidence is recorded')
 if not 10<=a.profile_seconds<=60 or a.profile_seconds!=60 and not a.host_resources and (not (a.scopes or a.quick) or a.reference_template):parser.error('Shorter intervals require a native --scopes or --quick run and 10..60 seconds')
 out=resolve(a.output)
 if not out.is_relative_to(ROOT/'analysis/performance') or out.exists():parser.error('Use a new output inside analysis/performance/')
 if not 1<=a.repeat<=5:parser.error('--repeat must be 1..5')
 if a.reference_template and not a.appimage:parser.error('--reference-template requires --appimage')
 if a.reference_template and a.render_scale!=100:parser.error('--render-scale applies only to the native player')
 if a.reference_template and a.expected_runtime_source:parser.error('--expected-runtime-source applies only to the native player')
 if a.repeat>1:
  out.mkdir(parents=True,exist_ok=False)
  outcomes=[]
  for index in range(a.repeat):
   command=[sys.executable,__file__,'--output',str(out/f'run-{index+1}'),'--scene',a.scene,'--build-dir',a.build_dir,'--submit-mode',a.submit_mode,'--texture-decode-mode',a.texture_decode_mode]
   command+=['--render-scale',str(a.render_scale)]
   if a.expected_runtime_source:command+=['--expected-runtime-source',a.expected_runtime_source]
   if a.rsx_trace:command+=['--rsx-trace']
   if a.reference_template:command+=['--reference-template',a.reference_template,'--appimage',a.appimage]
   if a.manual_boundaries:command+=['--manual-boundaries']
   if a.pending_review:command+=['--pending-review']
   if a.cpu_depth:command+=['--cpu-depth']
   if a.color_readback_cache:command+=['--color-readback-cache']
   if a.fence_poll:command+=['--fence-poll']
   if a.label_sync:command+=['--label-sync']
   if a.vk_serial:command+=['--vk-serial']
   if a.page_census:command+=['--page-census']
   if a.cpu_resolve:command+=['--cpu-resolve']
   command+=['--vblank-hz',a.vblank_hz]
   if a.draw_remap_cache:command+=['--draw-remap-cache']
   if a.draw_verify:command+=['--draw-verify']
   if a.decode_profile:command+=['--decode-profile']
   if a.correctness_video:command+=['--correctness-video']
   if a.scopes:command+=['--scopes']
   if a.cpu_samples:command+=['--cpu-samples']
   if a.quick:command+=['--quick']
   if a.frame_budget:command+=['--frame-budget']
   command+=['--profile-seconds',str(a.profile_seconds)]
   if a.host_resources:command+=['--host-resources']
   if a.linux_symbols:command+=['--linux-symbols']
   if a.mailbox_profile:command+=['--mailbox-profile']
   if a.vertex_profile:command+=['--vertex-profile']
   if a.overlay:command+=['--overlay']
   result=subprocess.run(command);outcomes.append(result.returncode);(out/'outcomes.json').write_text(json.dumps(outcomes))
  raise SystemExit(0 if not any(outcomes) else 1)
 pad=out.parent/(out.name+'-pad.txt');pad.parent.mkdir(parents=True,exist_ok=True);pad.write_text('')
 if a.reference_template:
  if not a.appimage:parser.error('--reference-template requires --appimage')
  cmd=[sys.executable,str(ROOT/'scripts/measure_reference.py'),'--output',str(out),'--scene',a.scene,'--template',a.reference_template,'--appimage',a.appimage,'--timeout','260']
  if a.host_resources:cmd+=['--resource-seconds',str(a.profile_seconds)]
 else:
  cmd=[sys.executable,str(ROOT/'scripts/measure_gameplay.py'),'--output',str(out),'--scene',a.scene,'--profile',str(ROOT/'analysis/performance/profiles'/out.relative_to(ROOT/'analysis/performance').as_posix().replace('/','-')),'--pin-window','--scripted-input',str(pad),'--timeout',str(int(260+210*a.host_resources+60*a.soak_minutes+(240 if a.manual_boundaries else 0)))]
  cmd+=['--build-dir',a.build_dir,'--submit-mode',a.submit_mode,'--texture-decode-mode',a.texture_decode_mode]
  cmd+=['--render-scale',str(a.render_scale)]
  if a.expected_runtime_source:cmd+=['--expected-runtime-source',a.expected_runtime_source]
  if a.rsx_trace:cmd+=['--rsx-trace']
  if a.cpu_depth:cmd+=['--cpu-depth']
  if a.soak_minutes:cmd+=['--lifecycle-stats']
  if a.color_readback_cache:cmd+=['--color-readback-cache']
  if a.fence_poll:cmd+=['--fence-poll']
  if a.label_sync:cmd+=['--label-sync']
  if a.vk_serial:cmd+=['--vk-serial']
  if a.page_census:cmd+=['--page-census']
  if a.cpu_resolve:cmd+=['--cpu-resolve']
  cmd+=['--vblank-hz',a.vblank_hz]
  if a.draw_remap_cache:cmd+=['--draw-remap-cache']
  if a.draw_verify:cmd+=['--draw-verify']
  if a.decode_profile:cmd+=['--decode-profile']
  if a.correctness_video:cmd+=['--correctness-video']
  if a.scopes:cmd+=['--scopes']
  if a.cpu_samples:cmd+=['--cpu-samples']
  if a.quick:cmd+=['--quick']
  if a.frame_budget:cmd+=['--frame-budget']
  cmd+=['--profile-seconds',str(a.profile_seconds)]
  if a.overlay:cmd+=['--overlay']
  if a.mailbox_profile:cmd+=['--mailbox-profile']
  if a.vertex_profile:cmd+=['--vertex-profile']
 desktop=None
 if os.name=='nt':
  from windows_gameplay import WindowsDesktop
  desktop=WindowsDesktop()
 process=subprocess.Popen(cmd);start=time.perf_counter();events=[];window=None;video=None;video_log=None;resources=None;capture_ns=None;cpu_sampler=None
 def event(label,**fields):
  if not out.is_dir():return
  stamp=time.perf_counter_ns()
  row=dict(seconds=stamp/1e9-start,monotonic_ns=stamp,label=label,**fields)
  if a.vertex_profile and label in ('resource-start','resource-end'):
   log=out/'startup.log'
   offset=log.stat().st_size
   (out/'diagnostic.trigger').write_text('snapshot')
   deadline=time.perf_counter()+5
   while process.poll() is None and time.perf_counter()<deadline:
    with log.open('rb') as stream:
     stream.seek(offset)
     text=stream.read().decode(errors='replace')
    counts=dict(re.findall(r'\[vulkan-count\] ([a-z-]+)=0x([0-9a-fA-F]+)',text))
    if '[diagnostic] reason=snapshot requested' in text and all(
       key in counts for key in ('guest-draw','pipeline','texture-upload','window-present')):
     row['vulkan_counts']={key:int(value,16) for key,value in counts.items()}
     row['vulkan_counts_observed_ns']=time.perf_counter_ns()
     break
    time.sleep(.01)
   else:raise RuntimeError('Vertex diagnostic snapshot was not recorded')
  events.append(row)
  (out/'route.json').write_text(json.dumps(events,indent=2)+'\n')
 def press(mask,label,reports=2):
  if a.reference_template:
   key={0x4000:'z',0x2000:'x',0x40:'Down'}[mask]
   subprocess.run(['xdotool','windowfocus',window],check=True,capture_output=True)
   subprocess.run(['xdotool','keydown','--window',window,key],check=True,capture_output=True);time.sleep(.15)
   subprocess.run(['xdotool','keyup','--window',window,key],check=True,capture_output=True)
  else:pad.write_text(f'{mask} {reports}\n')
  event(label,mask=mask,reports=reports);time.sleep(3)
 def capture(label):
  nonlocal capture_ns
  path=out/(label+'.png')
  capture_ns=time.perf_counter_ns()
  if desktop:
   pid=int((out/'pid').read_text());captured=desktop.capture(int(window),pid,path,out/(label+'-timer.png'))
   (out/(label+'-capture.json')).write_text(json.dumps(captured))
   result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(ROOT/'scripts/windows_ocr.ps1'),'-ImagePath',str(path)],capture_output=True,text=True,encoding='utf-8',creationflags=subprocess.CREATE_NO_WINDOW,check=True,timeout=25)
   ocr=json.loads(result.stdout);(out/(label+'-ocr.json')).write_text(json.dumps(ocr,indent=2))
   timer_result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(ROOT/'scripts/windows_ocr.ps1'),'-ImagePath',str(out/(label+'-timer.png'))],capture_output=True,text=True,encoding='utf-8',creationflags=subprocess.CREATE_NO_WINDOW,check=True,timeout=25)
   timer=json.loads(timer_result.stdout)['text']
   timer_source='masked crop OCR'
   if not live_battle_timer(timer):
    localized=windows_hud_countdown(ocr,captured['size'])
    if localized is not None:timer=localized;timer_source='full-frame OCR localized to timer bounds'
   (out/(label+'-timer-source.json')).write_text(json.dumps(dict(source=timer_source,text=timer)))
   if label.startswith('gameplay-'):timer=boundary_countdown(out,label,timer,a.manual_boundaries,process,a.overlay)
   (out/(label+'-timer.txt')).write_text(timer)
   return ocr['text'].upper()
  # X11 can return BadMatch while a resized client settles. Require a fresh
  # image so a failed capture cannot reuse the previous scene's evidence.
  for _ in range(3):
   path.unlink(missing_ok=True)
   capture_ns=time.perf_counter_ns()
   captured=subprocess.run(['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-f','x11grab','-window_id',window,'-i',os.environ['DISPLAY'],'-frames:v','1','-y',str(path)],capture_output=True,timeout=10)
   if captured.returncode==0 and path.is_file() and path.stat().st_size:break
   time.sleep(.2)
  else:raise RuntimeError(f'No fresh capture for {label}: {captured.stderr.decode(errors="replace").strip()}')
  normalized=out/'ocr.png';subprocess.run(['magick',str(path),'-resize','1280x720!','-resize','300%','-colorspace','gray','-negate','-threshold','40%','-morphology','Erode','Disk:2',str(normalized)],check=True,capture_output=True)
  text=subprocess.run(['tesseract',str(normalized),'stdout','--psm','11'],capture_output=True,text=True).stdout.upper()
  if label == 'navigation':
   # The selected map's gold text survives higher-resolution rendering better
   # as a single line than the full-screen thresholded OCR used for menus.
   selected_map=subprocess.check_output(['magick',str(path),'-resize','1280x720!',
       '-crop','340x38+460+418','+repage','-resize','300%','png:-'])
   text+='\n'+subprocess.run(['tesseract','stdin','stdout','--psm','7'],
       input=selected_map,capture_output=True,check=True).stdout.decode().upper()
  for box in ['280x40+500+356','280x40+500+252']:
   crop=out/'button.png';subprocess.run(['magick',str(path),'-resize','1280x720!','-crop',box,'-resize','300%','-colorspace','gray','-negate','-threshold','40%','-morphology','Erode','Disk:2',str(crop)],check=True,capture_output=True)
   text+='\n'+subprocess.run(['tesseract',str(crop),'stdout','--psm','7'],capture_output=True,text=True).stdout.upper()
  if a.host_resources and label not in ('navigation','round-watch'):
   check_overlay(path)
  if a.host_resources and label.startswith('level-select-'):
   heading=subprocess.check_output(['magick',str(path),'-resize','1280x720!','-crop',
       '180x26+250+100','+repage','-resize','400%','-colorspace','gray','-negate','-threshold','40%','png:-'])
   text+='\n'+subprocess.run(['tesseract','stdin','stdout','--psm','7'],
       input=heading,capture_output=True,check=True).stdout.decode().upper()
  timer=out/(label+'-timer.png')
  subprocess.run(['magick',str(path),'-resize','1280x720!','-crop','60x34+610+72','+repage','-fx','(r>0.65 && g>0.35 && b<0.4)?0:1','-resize','400%',str(timer)],check=True,capture_output=True)
  timer_text=subprocess.run(['tesseract',str(timer),'stdout','--psm','7','-c','tessedit_char_whitelist=0123456789'],capture_output=True,text=True).stdout
  (out/(label+'-timer.txt')).write_text(timer_text)
  return text
 def check_overlay(path):
  from host_resource_report import overlay_visible

  visible=overlay_visible(path)
  expected=a.overlay and not a.reference_template
  event('overlay-check',image=path.name,visible=visible)
  if visible!=expected:
   raise RuntimeError(f'Overlay state at {path.name} was {visible}, expected {expected}')
  if not a.reference_template:
   settings=(out/'settings.ini').read_text()
   if not re.search(rf'(?m)^overlay\s*=\s*{int(expected)}\s*$',settings):
    raise RuntimeError('The overlay setting changed during the resource run')

 def hold_scene(label, verify):
  def boundary(side):
   for _ in range(3):
    if verify(capture(f'{label}-{side}')):return
   raise RuntimeError(f'Could not verify {label} at {side}')

  boundary('before')
  event('resource-start',scene=label)
  time.sleep(12)
  event('resource-end',scene=label)
  boundary('after')

 def is_debrief(text):
  return 'DEBRIEF' in text or ('KILLS:' in text and 'RANK' in text)

 def close_owned_window():
  # X11 can reuse the numeric window id after a child exits. Never close a
  # newer session's window from an older route's exception cleanup.
  try:
   expected=int((out/'pid').read_text())
   if desktop:
    desktop.close(int(window),expected);return
   owner=int(subprocess.check_output(['xdotool','getwindowpid',window],text=True,stderr=subprocess.DEVNULL))
   if owner==expected:request_close(window)
  except (FileNotFoundError,ValueError,subprocess.CalledProcessError):pass
 def wait(label,words,timeout=65):
  deadline=time.perf_counter()+timeout;text=''
  while process.poll() is None and time.perf_counter()<deadline:
   text=capture('navigation')
   if a.reference_template and ('AUTO LOAD' in text or 'HDD ACCESS' in text):press(0x4000,'autosave notice');continue
   if a.reference_template and 'NOT SIGNED' in text:press(0x2000,'offline notice');continue
   if all(w in ' '.join(text.split()) for w in words) or label=='title' and desktop and 'BUTTON' in text or label=='map' and default_battle_map(text):
    (out/(label+'.png')).write_bytes((out/'navigation.png').read_bytes());event(label,ocr=text);return
   time.sleep(2)
  raise RuntimeError('Did not reach '+label+': '+text)
 try:
  while process.poll() is None and not (out/'window-prepared.json').exists():time.sleep(.5)
  if process.poll() is not None:raise RuntimeError('Measurement failed before window ready')
  if desktop:
   found=[str(json.loads((out/'window-prepared.json').read_text())['verified']['window'])]
  elif a.reference_template:
   pid=json.loads((out/'window-prepared.json').read_text())['verified']['pid']
   found=subprocess.check_output(['xdotool','search','--pid',str(pid),'--name','Tank Battles'],text=True).split()
  else:found=subprocess.check_output(['xdotool','search','--name','^'+re.escape('Tank Battles performance '+out.name+(' [VIDEO DIAGNOSTIC]' if a.correctness_video else ' [PROFILE DIAGNOSTIC]' if a.scopes or a.decode_profile else ''))+'$'],text=True).split()
  assert len(found)==1;window=found[0]
  if a.host_resources:
   from measure_host_resources import ResourceRecorder
   pid=int(subprocess.check_output(['xdotool','getwindowpid',window],text=True))
   resources=ResourceRecorder(out,pid)
   if a.linux_symbols:
    from diagnostic_logs import open_log

    sampler=out/'cpu-sampler'
    subprocess.run(['cc','-O2',str(ROOT/'scripts/sample_gameplay_cpu.c'),'-o',str(sampler)],check=True)
    (out/'cpu-sample-maps.txt').write_text(Path('/proc',str(pid),'maps').read_text())
    with open_log(out/'cpu-samples.csv','w') as samples, open_log(out/'cpu-samples.log','w') as sampler_log:
     cpu_sampler=subprocess.Popen([str(sampler),str(pid),'480'],stdout=samples,stderr=sampler_log)

  if a.correctness_video:
   video_log=(out/'video.log').open('w')
   video=subprocess.Popen(['ffmpeg','-nostdin','-hide_banner','-loglevel','warning','-f','x11grab','-framerate','60','-draw_mouse','0','-window_id',window,'-i',__import__('os').environ['DISPLAY'],'-an','-c:v','libx264','-preset','ultrafast','-crf','18','-threads','2','-fps_mode','passthrough',str(out/'window.mkv')],stdout=video_log,stderr=subprocess.STDOUT)
   event('correctness-video-start',fps=60)
  wait('title',['PRESS'])
  if a.host_resources:event('resource-start',scene='title-to-menu')
  press(0x4000,'continue title');wait('main-menu',['ONLINE'])
  if a.host_resources:
   event('resource-end',scene='title-to-menu',boundary_capture_ns=capture_ns)
   hold_scene('main-menu',lambda text:'ONLINE' in text)
  if a.scene=='battle':
   press(0x4000,'Battle');wait('map',['COUNTRY MAPS'])
   if a.host_resources:hold_scene('level-select',lambda text:default_battle_map(text) or
      any(label in ' '.join(text.split()) for label in ('COUNTRY MAPS','SELECT MAP')))
   press(0x4000,'default map');time.sleep(5);press(0x4000,'default mode');time.sleep(5);press(0x4000,'default players');wait('briefing',['RULES'],85)
   if a.host_resources:hold_scene('briefing',lambda text:'RULES' in text)
  else:
   press(0x40,'Campaign');press(0x4000,'open Campaign');wait('players',['PLAYER']);press(0x4000,'Single Player');wait('chapter',['CHAPTER']);press(0x4000,'Mission 1');time.sleep(5);press(0x4000,'default camouflage');wait('briefing',['CONTROLS'],85)
  # The telemetry observer supplies the five-second gameplay warm-up after
  # this boundary. Avoid spending another seven seconds of a short Battle
  # round before arming that warm-up.
  press(0x4000,'continue briefing');time.sleep(2)
  before=capture('gameplay-before')
  # OCR can fail on one animated/stylized digit. Retry fresh owned-window
  # captures before arming telemetry, retaining every rejected boundary.
  if a.scene=='battle' and not a.pending_review and not a.manual_boundaries:
   for retry in range(2):
    if live_battle_timer((out/'gameplay-before-timer.txt').read_text()):break
    for evidence in out.glob('gameplay-before*'):
     if evidence.is_file():(out/('countdown-retry-'+str(retry+1)+'-'+evidence.name)).write_bytes(evidence.read_bytes())
    time.sleep(1)
    before=capture('gameplay-before')
  if any(w in before for w in ['BRIEFING','RRIEFING','CONTROLS:','RULES:','SELECT MAP','MISSION FAILED','VICTORY','DEFEAT','GAME OVER','WINS','DEBRIEF']):raise RuntimeError('Still not live gameplay')
  unread=a.scene=='battle' and not live_battle_timer((out/'gameplay-before-timer.txt').read_text())
  if unread and not a.pending_review:raise RuntimeError('No readable live Battle countdown at start')
  event('live-gameplay-boundary',ocr=before,pending_review=unread)
  (out/'start.json').write_text(json.dumps(dict(live_gameplay_verified=not unread,pending_review=unread,source='scripted route plus retained live HUD boundary screenshot',scene=a.scene,input='scripted navigation; player one neutral during timed interval',boundary_image='gameplay-before.png')))
  while process.poll() is None:
   m=json.loads((out/'measurement.json').read_text())
   if m['interval_seconds']:break
   time.sleep(.2)
  pad.write_text('')
  with (out/'gameplay.jsonl').open() as timing:deadline=interval_end_ns(m,json.loads(timing.readline()))
  event('timing-deadline',deadline_monotonic_ns=deadline)
  # Capture at the actual interval end, not several seconds later: a round
  # ending in that extra delay used to invalidate otherwise complete intervals.
  while time.perf_counter_ns()<deadline+200_000_000 and process.poll() is None:time.sleep(.1)
  pad.write_text('');after=capture('gameplay-after');event('end-gameplay-boundary',ocr=after)
  if invalid_ending_scene(after) or a.scene=='battle' and not a.pending_review and not live_battle_timer((out/'gameplay-after-timer.txt').read_text()):
   event('invalid-ending-scene',reason='gameplay did not span full requested interval');(out/'invalid-scene.json').write_text(json.dumps(dict(reason='ending scene was not live gameplay')))
  if a.host_resources:
   last_live=None if (out/'invalid-scene.json').exists() else capture_ns
   if last_live:
    (out/'round-last-live.png').write_bytes((out/'gameplay-after.png').read_bytes())
   watch_deadline=time.perf_counter()+200
   current=after
   while not is_debrief(current) and time.perf_counter()<watch_deadline and process.poll() is None:
    time.sleep(2)
    current=capture('round-watch')
    if not invalid_ending_scene(current) and live_battle_timer((out/'round-watch-timer.txt').read_text()):
     last_live=capture_ns
     (out/'round-last-live.png').write_bytes((out/'round-watch.png').read_bytes())
   if is_debrief(current):
    event('round-end-observed',last_live_ns=last_live,first_debrief_ns=capture_ns)
    hold_scene('debriefing',is_debrief)
   else:
    event('debriefing-unobserved',last_live_ns=last_live)
  (out/'scene-evidence.json').write_text(json.dumps(dict(before=before,after=after,scripted=True,frames_outside_interval=True),indent=2)+'\n')
  if desktop:
   countdown={}
   for label in ('gameplay-before','gameplay-after'):
    digits=re.findall(r'^\s*(\d{1,3})\s*$',(out/(label+'-timer.txt')).read_text(),re.M)
    countdown[label]=dict(value=int(digits[0]) if len(digits)==1 else None,capture=json.loads((out/(label+'-capture.json')).read_text())['capture_monotonic_ns'])
   first,last=countdown['gameplay-before'],countdown['gameplay-after']
   elapsed=(last['capture']-first['capture'])/1e9
   progression=(first['value']-last['value'])/elapsed if first['value'] is not None and last['value'] is not None and elapsed>0 else None
   (out/'countdown.json').write_text(json.dumps(dict(boundaries=countdown,wall_seconds=elapsed,game_seconds_per_wall_second=progression,method='image-hash-bound visual review' if a.manual_boundaries else 'Windows OCR',limitations='Integer boundary samples; no RPCS3 reference captured on this host.'),indent=2))
  if a.soak_minutes:
   # Long session: Confirm every 20 s cycles rounds/results; resources sampled every 10 s.
   pid=int((out/'pid').read_text());soak_begin=time.perf_counter();soak_end=soak_begin+60*a.soak_minutes;last_press=0;samples=[]
   while time.perf_counter()<soak_end and process.poll() is None:
    now=time.perf_counter()
    if now-last_press>=20:
     text=capture('soak-latest');event('soak-scene',ocr=text)
     control=out/'soak-control.json'
     action=json.loads(control.read_text()) if control.exists() else {'mask':0x4000}
     pad.write_text(str(action.get('mask',0x4000))+' 2\n');last_press=now;event('soak-input',mask=action.get('mask',0x4000))
     if control.exists():control.unlink()
    try:
     status=dict(l.split(':',1) for l in Path(f'/proc/{pid}/status').read_text().splitlines() if ':' in l)
     fds=len(list(Path(f'/proc/{pid}/fd').iterdir()))
     gpu=subprocess.run(['nvidia-smi','--query-compute-apps=pid,used_memory','--format=csv,noheader,nounits'],capture_output=True,text=True).stdout
     gpu_rows=[x for x in gpu.splitlines() if x.split(',')[0].strip()==str(pid)]
     gpu_mb=sum(int(x.split(',')[1]) for x in gpu_rows) if gpu_rows else None
     samples.append(dict(seconds=round(now-origin,1),rss_kb=int(status['VmRSS'].split()[0]),threads=int(status['Threads']),fds=fds,gpu_mb=gpu_mb))
     (out/'soak.json').write_text(json.dumps(samples,indent=1)+'\n')
    except (FileNotFoundError,KeyError,ValueError):pass
    time.sleep(10)
   event('soak-end',samples=len(samples),elapsed_seconds=time.perf_counter()-soak_begin,duration_passed=time.perf_counter()>=soak_end)
   capture('soak-final')
  import signal
  guest_alive=desktop.owner(int(window))==int((out/'pid').read_text()) if desktop else Path('/proc', (out/'pid').read_text().strip()).exists() if not a.reference_template else True
  if not a.reference_template and guest_alive:
   try:
    log=out/'startup.log'
    offset=log.stat().st_size
    (out/'diagnostic.trigger').write_text('snapshot')
    deadline=time.perf_counter()+5
    while process.poll() is None and time.perf_counter()<deadline:
     with log.open('rb') as stream:
      stream.seek(offset)
      snapshot=stream.read().decode(errors='replace')
     if '[execution-count] ppu-recovery=' in snapshot:break
     time.sleep(.1)
    else:raise RuntimeError('Final strict-execution snapshot was not recorded')
   except ProcessLookupError:guest_alive=False
  if a.reference_template:(out/'stop.json').write_text('{}')
  elif guest_alive:close_owned_window()
  close_requested=time.perf_counter()
  process.wait(timeout=20)
  event('closed',seconds_to_exit=round(time.perf_counter()-close_requested,2))
 except BaseException as error:
  event('route-failure',error=str(error))
  if a.reference_template and out.exists():
   (out/'stop.json').write_text('{}')
  elif window:
   try:close_owned_window()
   except Exception:pass
  try:process.wait(timeout=20)
  except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=10)
  raise
 finally:
  pad.write_text('')
  if cpu_sampler and cpu_sampler.poll() is None:
   cpu_sampler.terminate()
   cpu_sampler.wait(timeout=5)
  if resources:resources.close()
  if video is not None:
   import signal
   if video.poll() is None:video.send_signal(signal.SIGINT)
   video.wait(timeout=20)
   video_log.close()
 if a.host_resources and (process.returncode==0 or (out/'invalid-scene.json').exists()):
  from host_resource_report import summarize_run
  if a.linux_symbols and (out/'cpu-samples.csv').stat().st_size==0:
   raise RuntimeError('Linux sampler produced no samples. See cpu-samples.log.')
  result=summarize_run(out, reference=bool(a.reference_template))
  result['cpu_diagnostics']=dict(linux_symbols=a.linux_symbols,mailbox_profile=a.mailbox_profile,vertex_profile=a.vertex_profile,video=a.correctness_video)
  (out/'host-resource-report.json').write_text(json.dumps(result,indent=2)+'\n')
 raise SystemExit(process.returncode)

if __name__=='__main__':main()
