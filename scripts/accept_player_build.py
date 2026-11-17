#!/usr/bin/env python3
"""Run the hardware-dependent Release gates from a Linux desktop terminal."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from port import ROOT, resolve
from gameplay_report import summarize


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir',default='build-player')
    parser.add_argument('--output',required=True,help='New directory under analysis/performance/')
    args=parser.parse_args()
    output=resolve(args.output);build=resolve(args.build_dir)
    if not output.is_relative_to(ROOT/'analysis/performance') or output.exists():
        parser.error('Use a new output under analysis/performance/')
    executable=build/'TankBattlesRecomp'
    if not executable.is_file():parser.error('Build the player executable first')
    if 'CMAKE_BUILD_TYPE:STRING=Release' not in (build/'CMakeCache.txt').read_text():parser.error('This gate requires Release')
    output.mkdir(parents=True)
    identity=hashlib.sha256(executable.read_bytes()).hexdigest()
    declaration=dict(executable_sha256=identity,minimum_game_fps=59.85,maximum_mean_ms=16.71,
                     maximum_p95_ms=18,maximum_p99_ms=25,minimum_seconds=60,
                     scene='default Battle, neutral player one, host audio, overlay off')
    (output/'declaration.json').write_text(json.dumps(declaration,indent=2)+'\n')
    with (output/'vulkan-fixtures.log').open('w') as log:
        gpu=subprocess.run([sys.executable,str(ROOT/'scripts/test_vulkan_draw_groups.py')],stdout=log,stderr=subprocess.STDOUT)
    if gpu.returncode:
        print('GPU fixture failed; inspect '+str(output/'vulkan-fixtures.log'))
        return gpu.returncode if gpu.returncode>0 else 1
    subprocess.run([sys.executable,str(ROOT/'scripts/script_gameplay.py'),'--output',str(output/'battle'),
                    '--build-dir',str(build),'--scene','battle'],check=True)
    report=summarize(output/'battle');manifest=report['manifest'];cadence=report['cadence']['completed_frame']
    checks=dict(release=manifest['build_type']=='Release',clean_baseline=report['baseline_eligible'],
                same_executable=manifest['executable_sha256']==identity,overlay_off=manifest['overlay'] is False,
                strict=manifest['strict_passed'],host_audio=bool(manifest['actual_audio']) and 'driver=dummy' not in str(manifest['actual_audio']),
                fps=cadence['fps']>=declaration['minimum_game_fps'],mean=cadence['mean_ms']<=declaration['maximum_mean_ms'],
                p95=cadence['p95_ms']<=declaration['maximum_p95_ms'],p99=cadence['p99_ms']<=declaration['maximum_p99_ms'])
    result=dict(passed=all(checks.values()),checks=checks,cadence=cadence,declaration=declaration)
    (output/'acceptance.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
    return 0 if result['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
