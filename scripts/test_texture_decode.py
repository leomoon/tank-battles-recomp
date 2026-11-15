#!/usr/bin/env python3
"""Build the game-free decoder comparison on Linux or native Windows."""
import argparse,os,subprocess
from pathlib import Path
from port import ROOT,resolve
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--build-dir',default='build-tests/texture-decode');p.add_argument('--benchmark',action='store_true');a=p.parse_args()
out=resolve(a.build_dir)
subprocess.run(['cmake','-S',str(ROOT/'tests/texture_decode'),'-B',str(out),'-DCMAKE_BUILD_TYPE=Release'],check=True)
subprocess.run(['cmake','--build',str(out),'--config','Release','--parallel','2'],check=True)
env={k:v for k,v in os.environ.items() if not k.startswith('TB_')}
subprocess.run(['ctest','--test-dir',str(out),'-C','Release','--output-on-failure'],env=env,check=True)
if a.benchmark:
 exe=out/('Release/decode_specialized.exe' if os.name=='nt' else 'decode_specialized')
 subprocess.run([str(exe),'--benchmark'],env=env,check=True)
