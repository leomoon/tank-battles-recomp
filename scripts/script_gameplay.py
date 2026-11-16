#!/usr/bin/env python3
"""Drive a labeled benchmark route; capture scene proof only outside steady timing."""
import argparse,json,re,subprocess,time,sys
from pathlib import Path
from port import ROOT,resolve
from verify_vulkan_rendering import request_close


def invalid_ending_scene(text):
 return any(word in text.upper() for word in ('MISSION FAILED','DEBRIEF','RESULTS','KILLS:','RANK','SELECT AN OPTION','CONTROLS:','RULES:','VICTORY','DEFEAT','GAME OVER','WINS'))


def live_battle_timer(text):
 return any(0<=int(value)<=120 for value in re.findall(r'^\s*(\d{1,3})\s*$',text,re.M))


def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True);parser.add_argument('--scene',choices=['battle','campaign'],default='battle');parser.add_argument('--reference-template');parser.add_argument('--appimage');parser.add_argument('--repeat',type=int,default=1);parser.add_argument('--scopes',action='store_true');parser.add_argument('--overlay',action='store_true');parser.add_argument('--correctness-video',action='store_true');parser.add_argument('--build-dir',default='build-vulkan');parser.add_argument('--submit-mode',choices=['serial','grouped'],default='grouped');parser.add_argument('--profile-seconds',type=int,default=60,help='10..60 seconds; shorter diagnostic intervals require --scopes');parser.add_argument('--decode-profile',action='store_true');parser.add_argument('--texture-decode-mode',choices=['reference','specialized'],default='specialized');parser.add_argument('--depth-gpu',action='store_true',help='Experimental depth conversion diagnostic');a=parser.parse_args()
 if not 10<=a.profile_seconds<=60 or a.profile_seconds!=60 and (not a.scopes or a.reference_template):parser.error('Shorter intervals require a native --scopes diagnostic and 10..60 seconds')
 out=resolve(a.output)
 if not out.is_relative_to(ROOT/'analysis/performance') or out.exists():parser.error('Use a new output inside analysis/performance/')
 if not 1<=a.repeat<=5:parser.error('--repeat must be 1..5')
 if a.reference_template and not a.appimage:parser.error('--reference-template requires --appimage')
 if a.repeat>1:
  out.mkdir(parents=True,exist_ok=False)
  outcomes=[]
  for index in range(a.repeat):
   command=[sys.executable,__file__,'--output',str(out/f'run-{index+1}'),'--scene',a.scene,'--build-dir',a.build_dir,'--submit-mode',a.submit_mode,'--texture-decode-mode',a.texture_decode_mode]
   if a.reference_template:command+=['--reference-template',a.reference_template,'--appimage',a.appimage]
   if a.depth_gpu:command+=['--depth-gpu']
   if a.decode_profile:command+=['--decode-profile']
   if a.correctness_video:command+=['--correctness-video']
   if a.scopes:command+=['--scopes','--profile-seconds',str(a.profile_seconds)]
   if a.overlay:command+=['--overlay']
   result=subprocess.run(command);outcomes.append(result.returncode);(out/'outcomes.json').write_text(json.dumps(outcomes))
  raise SystemExit(0 if not any(outcomes) else 1)
 pad=out.parent/(out.name+'-pad.txt');pad.parent.mkdir(parents=True,exist_ok=True);pad.write_text('')
 if a.reference_template:
  if not a.appimage:parser.error('--reference-template requires --appimage')
  cmd=[sys.executable,str(ROOT/'scripts/measure_reference.py'),'--output',str(out),'--scene',a.scene,'--template',a.reference_template,'--appimage',a.appimage,'--timeout','260']
 else:
  cmd=[sys.executable,str(ROOT/'scripts/measure_gameplay.py'),'--output',str(out),'--scene',a.scene,'--profile',str(ROOT/'analysis/performance/profiles'/str(out.relative_to(ROOT/'analysis/performance')).replace('/','-')),'--pin-window','--scripted-input',str(pad),'--timeout','260']
  cmd+=['--build-dir',a.build_dir,'--submit-mode',a.submit_mode,'--texture-decode-mode',a.texture_decode_mode]
  if a.depth_gpu:cmd+=['--depth-gpu']
  if a.decode_profile:cmd+=['--decode-profile']
  if a.correctness_video:cmd+=['--correctness-video']
  if a.scopes:cmd+=['--scopes','--profile-seconds',str(a.profile_seconds)]
  if a.overlay:cmd+=['--overlay']
 process=subprocess.Popen(cmd);start=time.monotonic();events=[];window=None;video=None;video_log=None
 def event(label,**fields):
  if not out.is_dir():return
  events.append(dict(seconds=time.monotonic()-start,label=label,**fields));(out/'route.json').write_text(json.dumps(events,indent=2)+'\n')
 def press(mask,label,reports=2):
  if a.reference_template:
   key={0x4000:'z',0x2000:'x',0x40:'Down'}[mask]
   subprocess.run(['xdotool','windowfocus',window],check=True,capture_output=True)
   subprocess.run(['xdotool','keydown','--window',window,key],check=True,capture_output=True);time.sleep(.15)
   subprocess.run(['xdotool','keyup','--window',window,key],check=True,capture_output=True)
  else:pad.write_text(f'{mask} {reports}\n')
  event(label,mask=mask,reports=reports);time.sleep(3)
 def capture(label):
  path=out/(label+'.png')
  subprocess.run(['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-f','x11grab','-window_id',window,'-i',__import__('os').environ['DISPLAY'],'-frames:v','1','-y',str(path)],check=True,timeout=10)
  normalized=out/'ocr.png';subprocess.run(['magick',str(path),'-resize','1280x720!','-resize','300%','-colorspace','gray','-negate','-threshold','40%','-morphology','Erode','Disk:2',str(normalized)],check=True,capture_output=True)
  text=subprocess.run(['tesseract',str(normalized),'stdout','--psm','11'],capture_output=True,text=True).stdout.upper()
  for box in ['280x40+500+356','280x40+500+252']:
   crop=out/'button.png';subprocess.run(['magick',str(path),'-resize','1280x720!','-crop',box,'-resize','300%','-colorspace','gray','-negate','-threshold','40%','-morphology','Erode','Disk:2',str(crop)],check=True,capture_output=True)
   text+='\n'+subprocess.run(['tesseract',str(crop),'stdout','--psm','7'],capture_output=True,text=True).stdout.upper()
  timer=out/(label+'-timer.png')
  subprocess.run(['magick',str(path),'-resize','1280x720!','-crop','100x34+590+72','-fx','(r>0.65 && g>0.35 && b<0.4)?0:1','-resize','400%',str(timer)],check=True,capture_output=True)
  timer_text=subprocess.run(['tesseract',str(timer),'stdout','--psm','7','-c','tessedit_char_whitelist=0123456789'],capture_output=True,text=True).stdout
  (out/(label+'-timer.txt')).write_text(timer_text)
  return text
 def wait(label,words,timeout=65):
  deadline=time.monotonic()+timeout;text=''
  while process.poll() is None and time.monotonic()<deadline:
   text=capture('navigation')
   if a.reference_template and ('AUTO LOAD' in text or 'HDD ACCESS' in text):press(0x4000,'autosave notice');continue
   if a.reference_template and 'NOT SIGNED' in text:press(0x2000,'offline notice');continue
   if all(w in ' '.join(text.split()) for w in words):
    (out/(label+'.png')).write_bytes((out/'navigation.png').read_bytes());event(label,ocr=text);return
   time.sleep(2)
  raise RuntimeError('Did not reach '+label+': '+text)
 try:
  while process.poll() is None and not (out/'window-prepared.json').exists():time.sleep(.5)
  if process.poll() is not None:raise RuntimeError('Measurement failed before window ready')
  if a.reference_template:
   pid=json.loads((out/'window-prepared.json').read_text())['verified']['pid']
   found=subprocess.check_output(['xdotool','search','--pid',str(pid),'--name','Tank Battles'],text=True).split()
  else:found=subprocess.check_output(['xdotool','search','--name','^'+re.escape('Tank Battles performance '+out.name+(' [VIDEO DIAGNOSTIC]' if a.correctness_video else ' [PROFILE DIAGNOSTIC]' if a.scopes or a.decode_profile else ''))+'$'],text=True).split()
  assert len(found)==1;window=found[0]
  if a.correctness_video:
   video_log=(out/'video.log').open('w')
   video=subprocess.Popen(['ffmpeg','-nostdin','-hide_banner','-loglevel','warning','-f','x11grab','-framerate','60','-draw_mouse','0','-window_id',window,'-i',__import__('os').environ['DISPLAY'],'-an','-c:v','libx264','-preset','ultrafast','-crf','18','-threads','2','-fps_mode','passthrough',str(out/'window.mkv')],stdout=video_log,stderr=subprocess.STDOUT)
   event('correctness-video-start',fps=60)
  wait('title',['PRESS']);press(0x4000,'continue title');wait('main-menu',['ONLINE'])
  if a.scene=='battle':
   press(0x4000,'Battle');wait('map',['COUNTRY MAPS']);press(0x4000,'default map');time.sleep(5);press(0x4000,'default mode');time.sleep(5);press(0x4000,'default players');wait('briefing',['RULES'],85)
  else:
   press(0x40,'Campaign');press(0x4000,'open Campaign');wait('players',['PLAYER']);press(0x4000,'Single Player');wait('chapter',['CHAPTER']);press(0x4000,'Mission 1');time.sleep(5);press(0x4000,'default camouflage');wait('briefing',['CONTROLS'],85)
  press(0x4000,'continue briefing');time.sleep(7)
  before=capture('gameplay-before')
  if any(w in before for w in ['BRIEFING','RRIEFING','CONTROLS:','RULES:','SELECT MAP','MISSION FAILED','VICTORY','DEFEAT','GAME OVER','WINS','DEBRIEF']):raise RuntimeError('Still not live gameplay')
  event('live-gameplay-boundary',ocr=before)
  (out/'start.json').write_text(json.dumps(dict(live_gameplay_verified=True,source='scripted route plus retained live HUD boundary screenshot',scene=a.scene,input='scripted navigation; player one neutral during timed interval',boundary_image='gameplay-before.png')))
  while process.poll() is None:
   m=json.loads((out/'measurement.json').read_text())
   if m['interval_seconds']:break
   time.sleep(.2)
  pad.write_text('')
  origin=time.monotonic()
  while time.monotonic()-origin<a.profile_seconds+9 and process.poll() is None:time.sleep(.5)
  pad.write_text('');after=capture('gameplay-after');event('end-gameplay-boundary',ocr=after)
  if invalid_ending_scene(after) or a.scene=='battle' and not live_battle_timer((out/'gameplay-after-timer.txt').read_text()):
   event('invalid-ending-scene',reason='gameplay did not span full requested interval');(out/'invalid-scene.json').write_text(json.dumps(dict(reason='ending scene was not live gameplay')))
  (out/'scene-evidence.json').write_text(json.dumps(dict(before=before,after=after,scripted=True,frames_outside_interval=True),indent=2)+'\n')
  import os,signal
  if not a.reference_template:os.kill(int((out/'pid').read_text()),signal.SIGUSR1);time.sleep(.3)
  if a.reference_template:(out/'stop.json').write_text('{}')
  else:request_close(window)
  process.wait(timeout=20)
 except BaseException as error:
  event('route-failure',error=str(error))
  if window:
   try:request_close(window)
   except Exception:pass
  try:process.wait(timeout=20)
  except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=10)
  raise
 finally:
  pad.write_text('')
  if video is not None:
   import signal
   if video.poll() is None:video.send_signal(signal.SIGINT)
   video.wait(timeout=20)
   video_log.close()
 raise SystemExit(process.returncode)

if __name__=='__main__':main()
