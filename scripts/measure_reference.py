#!/usr/bin/env python3
"""Sample RPCS3's own FPS title in a copied profile; no video during the interval."""
import argparse,hashlib,json,os,re,shutil,signal,subprocess,time
from pathlib import Path
from diagnostic_logs import open_log,normalize_log_permissions,normalize_log_file
from port import ROOT,resolve


def summarize_reference(manifest, invalid_scene=False):
 if invalid_scene:raise ValueError('Ending scene was not live gameplay')
 if manifest.get('phase')!='gameplay' or not manifest.get('scene_evidence',{}).get('live_gameplay_verified'):raise ValueError('No verified live gameplay')
 interval=manifest.get('interval_seconds')
 if not interval or interval[1]-interval[0]<59.999999:raise ValueError('Need a complete 60-second interval')
 begin,end=interval;rows=[r for r in manifest['rows'] if begin<=r['seconds']<=end]
 if not rows or rows[0]['seconds']>begin+1.5 or rows[-1]['seconds']<end-1.5:raise ValueError('Incomplete reference coverage')
 if any(not r['visible'] or r['fps'] is None or r['window_size']!=[1280,720] for r in rows):raise ValueError('Hidden, resized or unmeasured reference interval')
 return dict(samples=len(rows),mean_reported_fps=sum(r['fps'] for r in rows)/len(rows),minimum_reported_fps=min(r['fps'] for r in rows),maximum_reported_fps=max(r['fps'] for r in rows),scope=manifest['fps_source'])


def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True);parser.add_argument('--template',required=True);parser.add_argument('--appimage',required=True);parser.add_argument('--scene',required=True,choices=['battle','campaign']);parser.add_argument('--timeout',type=float,default=300);a=parser.parse_args()
 out=resolve(a.output);template=resolve(a.template);app=resolve(a.appimage)
 if not out.is_relative_to(ROOT/'analysis/performance') or out.exists():parser.error('Use new output inside analysis/performance/')
 out.mkdir(parents=True)
 shutil.copytree(template/'config/rpcs3',out/'config/rpcs3',symlinks=True,ignore=shutil.ignore_patterns('dev_hdd0','dev_hdd1','captures','savestates'))
 if (template/'cache/rpcs3').exists():shutil.copytree(template/'cache/rpcs3',out/'cache/rpcs3',ignore=shutil.ignore_patterns('RPCS3.log*'))
 cfg=out/'config/rpcs3';hdd=cfg/'dev_hdd0';(hdd/'game').mkdir(parents=True);content=ROOT/'game/dev_hdd0/game/NPUB30154';(hdd/'game/NPUB30154').symlink_to(content,target_is_directory=True)
 source=template/'config/rpcs3/dev_hdd0/home/00000001/exdata';target=hdd/'home/00000001/exdata';target.mkdir(parents=True)
 for path in source.iterdir():
  if path.is_file() and not path.is_symlink():shutil.copyfile(path,target/path.name)
 config=cfg/'config.yml';text=config.read_text();text=re.sub(r'(?m)^  Window Title Format:.*$', '  Window Title Format: "FPS: %F | %R | %V | %T [%t]"',text);config.write_text(text)
 env=dict(os.environ,XDG_CONFIG_HOME=str(out/'config'),XDG_CACHE_HOME=str(out/'cache'),QT_QPA_PLATFORM='xcb')
 manifest=dict(scene=a.scene,profile='independent copied RPCS3 config/licenses; fresh save',config_template=str(template),appimage=str(app),appimage_sha256=hashlib.sha256(app.read_bytes()).hexdigest(),config=str(config),config_sha256=hashlib.sha256(config.read_bytes()).hexdigest(),gpu=subprocess.check_output(['nvidia-smi','--query-gpu=name,driver_version','--format=csv,noheader'],text=True).strip(),input_source='scripted keyboard navigation; no input during timed interval',fps_source='RPCS3 formatted title FPS, sampled once/second; not exact per-frame timestamps',phase='unverified',interval_seconds=None,rows=[])
 start=time.monotonic();process=None;window=None;prepared=False
 try:
  with open_log(out/'launcher.log') as stream:
   process=subprocess.Popen([str(app),'--appimage-extract-and-run','--no-gui',str(content/'USRDIR/EBOOT.BIN')],env=env,stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
   while process.poll() is None and time.monotonic()-start<a.timeout and not (out/'stop.json').exists():
    for name in ('RPCS3.log','RPCS3.log.gz'):normalize_log_file(out/'cache/rpcs3'/name)
    clients=json.loads(subprocess.check_output(['hyprctl','-j','clients'],text=True));owned=[]
    for c in clients:
     pid=c.get('pid',0)
     try:
      if c.get('xwayland') and str(out/'config').encode() in Path('/proc',str(pid),'environ').read_bytes() and 'Tank Battles' in c.get('title',''):owned.append(c)
     except (PermissionError,FileNotFoundError):pass
    if len(owned)==1:
     c=owned[0];address='address:'+c['address']
     if not prepared:
      if c.get('grouped'):op='hl.dsp.window.move({ window = "'+address+'", out_of_group = true })'
      elif not c.get('floating'):op='hl.dsp.window.float({ window = "'+address+'", action = "toggle" })'
      elif not c.get('pinned'):op='hl.dsp.window.pin({ window = "'+address+'" })'
      elif c.get('size')!=[1280,720]:op='hl.dsp.window.resize({ window = "'+address+'", x = 1280, y = 720 })'
      else:
       op='hl.dsp.focus({ window = "'+address+'" })';prepared=True
       (out/'window-prepared.json').write_text(json.dumps(dict(verified=c),indent=2)+'\n')
      subprocess.run(['hyprctl','dispatch',op],check=True,capture_output=True)
     fps=re.search(r'FPS:\s*([0-9.]+)',c.get('title',''))
     manifest['rows'].append(dict(seconds=time.monotonic()-start,title=c.get('title'),fps=float(fps[1]) if fps else None,visible=bool(c.get('visible')) and not c.get('hidden',False),window_size=c.get('size')))
    marker=out/'start.json'
    if manifest['interval_seconds'] is None and marker.exists():
     scene=json.loads(marker.read_text());boundary=time.monotonic()-start;manifest.update(phase='gameplay',scene_evidence=scene,interval_seconds=[boundary+5,boundary+65])
    (out/'measurement.json').write_text(json.dumps(manifest,indent=2)+'\n');time.sleep(1)
 finally:
  if process and process.poll() is None:
   os.killpg(process.pid,signal.SIGTERM)
   try:process.wait(timeout=8)
   except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait()
  normalize_log_permissions(out)
 try:report=summarize_reference(manifest,(out/'invalid-scene.json').exists())
 except ValueError as error:raise SystemExit(str(error))
 (out/'report.json').write_text(json.dumps(report,indent=2)+'\n')

if __name__=='__main__':main()
