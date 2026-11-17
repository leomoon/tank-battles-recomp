#!/usr/bin/env python3
"""Measure a clean native build and each compiler job without changing flags."""
import argparse
import json
import os
from pathlib import Path
import resource
import shutil
import subprocess
import sys
import time


def compiler_job(directory, command):
    started = time.monotonic()
    result = subprocess.run(command)
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    source = command[command.index('-c') + 1] if '-c' in command else None
    record = dict(command=command, source=source, seconds=time.monotonic()-started,
                  peak_rss_kib=usage.ru_maxrss, returncode=result.returncode)
    (Path(directory)/f'compiler-{os.getpid()}.json').write_text(json.dumps(record, indent=2)+'\n')
    return result.returncode


def main():
    if len(sys.argv)>1 and sys.argv[1]=='--compiler-job':
        return compiler_job(sys.argv[2], sys.argv[3:])
    from port import ROOT, resolve, positive_int
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir', required=True)
    parser.add_argument('--build-type', choices=('Release','RelWithDebInfo'), required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--jobs', type=positive_int, default=4)
    args=parser.parse_args()
    build, output=resolve(args.build_dir),resolve(args.output)
    if not output.is_relative_to(ROOT/'analysis') or output.exists():
        parser.error('Use a new output directory under analysis/')
    if (build/'build.ninja').exists():
        parser.error('Measurement requires a fresh build directory')
    output.mkdir(parents=True)
    launcher=';'.join((sys.executable,str(Path(__file__).resolve()),'--compiler-job',str(output)))
    started=time.monotonic()
    command=[shutil.which('cmake'),'-S',str(ROOT),'-B',str(build),'-G','Ninja',
             '-DCMAKE_BUILD_TYPE='+args.build_type,'-DPython3_EXECUTABLE='+sys.executable,
             '-DPORT_SPU_INPUTS='+str(ROOT/'game/dev_hdd0/game/NPUB30154/USRDIR/EBOOT.elf'),
             '-DCMAKE_C_COMPILER_LAUNCHER='+launcher,'-DCMAKE_CXX_COMPILER_LAUNCHER='+launcher]
    with (output/'configure.log').open('w') as stream:
        configure=subprocess.run(command,stdout=stream,stderr=subprocess.STDOUT)
    configured=time.monotonic()
    status=configure.returncode
    if not status:
        with (output/'build.log').open('w') as stream:
            status=subprocess.run([shutil.which('cmake'),'--build',str(build),'--target','TankBattlesRecomp','--parallel',str(args.jobs)],stdout=stream,stderr=subprocess.STDOUT).returncode
    ended=time.monotonic()
    records=[json.loads(p.read_text()) for p in output.glob('compiler-*.json')]
    ppu=[r for r in records if r['source'] and '/ppu_recomp_' in r['source']]
    executable=build/'TankBattlesRecomp'
    report=dict(build_type=args.build_type,jobs=args.jobs,clean_build=True,configure_seconds=configured-started,
                build_seconds=ended-configured,total_seconds=ended-started,returncode=status,
                compiler_jobs=len(records),peak_job_rss_kib=max((r['peak_rss_kib'] for r in records),default=0),
                ppu_jobs=len(ppu),peak_ppu_job_rss_kib=max((r['peak_rss_kib'] for r in ppu),default=0),
                executable_bytes=executable.stat().st_size if executable.exists() else None,
                memory_method='Linux getrusage(RUSAGE_CHILDREN) per compiler invocation; per-job peaks, not simultaneous whole-build RAM')
    if not status:
        report['executable_description']=subprocess.check_output(['file',str(executable)],text=True).strip()
        sections=subprocess.check_output(['readelf','-S',str(executable)],text=True)
        report['debug_info_present']='.debug_info' in sections
        report['static_symbols_present']='.symtab' in sections
        report['compile_commands']=str(output/'commands.txt')
        (output/'commands.txt').write_text(subprocess.check_output(['ninja','-C',str(build),'-t','commands'],text=True))
    (output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2),flush=True)
    return status


if __name__=='__main__':
    raise SystemExit(main())
