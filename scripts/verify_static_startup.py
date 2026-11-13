#!/usr/bin/env python3
"""Verify a bounded native startup has no observed unaccounted execution fallback."""
import argparse,json,os,re,resource,tomllib
from pathlib import Path
from port import ROOT,launch_environment,positive_seconds
from boot_session import run_session

def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--timeout',type=positive_seconds,default=6);args=parser.parse_args()
 resource.setrlimit(resource.RLIMIT_CORE,(0,0))
 config=tomllib.loads((ROOT/'config.toml').read_text());directory=ROOT/'analysis/static-execution';directory.mkdir(parents=True,exist_ok=True)
 env=launch_environment(config)
 for name in ('TB_EXECUTION_PROBE','TB_HLE_AUDIT_ONLY','TB_ASSET_PROBE','TB_ENTITLEMENT_PROBE','TB_OFFLINE_PROBE','TB_DIAGNOSTIC_STALL'):env.pop(name,None)
 env.update(TB_EXECUTION_MODE='strict',SDL_AUDIODRIVER='dummy',SDL_VIDEODRIVER='dummy')
 log=directory/'startup.log'
 status,result=run_session([str(ROOT/config['paths']['build']/'TankBattlesRecomp'),str(ROOT/config['game']['elf'])],cwd=ROOT,env=env,timeout=args.timeout,log_path=log,request_snapshot=True)
 text=log.read_text(errors='replace')
 counts={name:int(value,16) for name,value in re.findall(r'\[execution-count\] ([a-z-]+)=0x([0-9A-Fa-f]+)',text)}
 pairs=[int(value,16) for value in re.findall(r'\[execution-image-pair\] 0x([0-9A-Fa-f]+)',text)]
 inventory=json.loads((ROOT/config['paths']['build']/'generated_spu/inventory.json').read_text())
 registered={p['image_id']:p for p in inventory['programs']}
 observed=[dict(image_id=key>>32,overlay_id=key&0xffffffff) for key in pairs]
 matched=all(i['image_id'] in registered and (not i['overlay_id'] or i['overlay_id'] in registered) for i in observed)
 failure_kinds=('ppu-miss','hle-missing','spu-interpreter','spu-untranslated','ppu-recovery')
 passed=status==124 and all(k in counts and counts[k]==0 for k in failure_kinds) and bool(observed) and matched
 report=dict(passed=passed,scope='Bounded startup only; title/menu/campaign remain unverified.',counts=counts,images=observed,
  inventory_matches=matched,registered_programs=[dict(image_id=p['image_id'],sha256=p['sha256'],fingerprint=p['fingerprint']) for p in inventory['programs']],
  session=result,source_overlay=json.loads((ROOT/config['paths']['build']/'instrumented_sdk/manifest.json').read_text()))
 (directory/'verification.json').write_text(json.dumps(report,indent=2)+'\n')
 if not passed:raise SystemExit('Strict startup verification failed or incomplete; inspect analysis/static-execution/')
 print('PASS: bounded startup has no observed guest execution fallback or missing HLE; observed images match local static registrations.')
if __name__=='__main__':main()
