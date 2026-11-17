"""Run the synthetic boot title with isolated mounts and no GPU requirement."""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--runner',required=True);parser.add_argument('--data',required=True)
    args=parser.parse_args();data=Path(args.data).resolve()
    with tempfile.TemporaryDirectory(prefix='tb-runtime-smoke-') as directory:
        root=Path(directory)
        env={key:value for key,value in os.environ.items() if not key.startswith(('TB_','PS3_','PS3RECOMP_','GCM_','PAD_'))}
        env.update(TB_SESSION_CAPTURED='1',TB_SETTINGS_FILE='',TB_EXECUTION_MODE='strict',PS3_PARAM_SFO=str(data/'PARAM.SFO'),PS3_VFS_ROOT=str(data),PS3_HDD0_ROOT=str(root/'dev_hdd0'),PS3_HDD1_ROOT=str(root/'dev_hdd1'),PS3_GAMEDATA_ROOT=str(root),PS3_DEV_FLASH=str(root/'dev_flash'),SDL_VIDEODRIVER='dummy',SDL_AUDIODRIVER='dummy',PS3_VERBOSE='0',RSX_ENGINE='0')
        result=subprocess.run([str(Path(args.runner).resolve()),str(data/'smoke.elf')],env=env,capture_output=True,text=True,timeout=30)
        print(result.stdout+result.stderr)
        text=result.stdout+result.stderr
        if '[smoke] static SPU PASS' not in text or '[smoke] PASS' not in text or '[smoke] FAIL' in text or result.returncode!=0:
            raise SystemExit('Synthetic boot failed: '+str(result.returncode))

if __name__=='__main__':main()
