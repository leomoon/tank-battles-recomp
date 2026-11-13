"""Compare isolated owned cold/warm asset-cache profiles without altering originals."""
import argparse,json,re,shutil,tomllib,resource
from pathlib import Path
from port import ROOT,launch_environment
from boot_session import run_session

def main():
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument("--prefix",default="asset-cache-boot",help="Fresh fixture/report prefix, such as asset-cache-repeat")
 args=parser.parse_args()
 if not re.fullmatch(r"[a-z][a-z0-9-]*",args.prefix):parser.error("Prefix must start with a lowercase letter and contain only lowercase letters, digits, and hyphens")
 resource.setrlimit(resource.RLIMIT_CORE,(0,0))
 config=tomllib.loads((ROOT/'config.toml').read_text());out=ROOT/'analysis'/args.prefix;out.mkdir(parents=True,exist_ok=True)
 results=[]
 for mode in ('warm','cold'):
  base=ROOT/f'game/{args.prefix}-{mode}';title=base/'dev_hdd0/game/NPUB30154'
  if base.exists():raise SystemExit(f'Profile exists; preserve it and choose fresh names: {base}')
  shutil.copytree(ROOT/'game/dev_hdd0/game/NPUB30154',title,ignore=shutil.ignore_patterns('*.dec','*.unedat','*.verify-*'))
  exdata=base/'dev_hdd0/home/00000001/exdata';exdata.mkdir(parents=True)
  for p in (ROOT/'game/dev_hdd0/home/00000001/exdata').iterdir():
   if p.suffix in ('.edat','.rap','.rifkey'):shutil.copy2(p,exdata/p.name)
  if mode=='warm':
   for p in (ROOT/'game/dev_hdd0/game/NPUB30154/USRDIR').glob('*.dec'):shutil.copy2(p,title/'USRDIR'/p.name)
  env=launch_environment(config, str(title/'PARAM.SFO'))
  env.update(PS3_VFS_ROOT=str(title),PS3_HDD0_ROOT=str(base/'dev_hdd0'),PS3_HDD1_ROOT=str(base/'dev_hdd1'),PS3_GAMEDATA_ROOT=str(base),PS3_DEV_FLASH=str(base/'dev_flash'),SPU_RAW_DBG='1',PS3_HLE_TRACE='1000',SDL_AUDIODRIVER='dummy',SDL_VIDEODRIVER='dummy')
  command=[str(ROOT/'build/TankBattlesRecomp'),str(title/'USRDIR/EBOOT.elf')]
  status,result=run_session(command,cwd=ROOT,env=env,timeout=40 if mode=='cold' else 4,log_path=out/f'{mode}.log',request_snapshot=True)
  results.append(dict(mode=mode,status=status,result=result))
 (out/'profiles.json').write_text(json.dumps(results,indent=2)+'\n')
if __name__=='__main__':main()
