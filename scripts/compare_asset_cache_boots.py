#!/usr/bin/env python3
"""Run owned warm/fresh/interrupted Vulkan profiles with independent cache trees."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib
from port import ROOT, resolve, positive_seconds

ASSETS = ('assets_common.edat', 'assets_images.edat', 'assets_ps3.edat', 'assets_sound.edat')


def sha(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f,'sha256').hexdigest()


def prepare_profile(source, destination, mode):
    if destination.exists():
        raise ValueError('Preserve previous profile; choose a new output directory')
    # Copy, never hardlink or symlink writable title/cache contents.
    shutil.copytree(source, destination, symlinks=False,
                    ignore=lambda directory,names: [n for n in names if n.endswith(('.dec','.complete','.lock','.unedat')) or '.tmp-' in n or '.verify-' in n])
    if mode == 'warm':
        for path in (source/'USRDIR').glob('*.dec'):
            shutil.copy2(path, destination/'USRDIR'/path.name)
            marker=Path(str(path)+'.complete')
            if marker.exists():shutil.copy2(marker,destination/'USRDIR'/marker.name)
    elif mode == 'interrupted':
        for name in ASSETS:
            cache=destination/'USRDIR'/(name+'.dec')
            cache.write_bytes(b'Interrupted cache output from original synthetic fixture.\n')
            stamp=(destination/'USRDIR'/name).stat().st_mtime+60
            os.utime(cache,(stamp,stamp))
            Path(str(cache)+'.tmp-abandoned').write_bytes(b'Unpublished temporary output.\n')


def check_caches(title, references):
    result=[]
    for name in ASSETS:
        encrypted=title/'USRDIR'/name;cache=Path(str(encrypted)+'.dec')
        with encrypted.open('rb') as f:f.seek(0x88);expected=int.from_bytes(f.read(8),'big')
        reference=references/(name+'.unedat')
        match=cache.exists() and cache.stat().st_size==expected and sha(cache)==sha(reference)
        result.append(dict(asset=name,expected_bytes=expected,reference_sha256=sha(reference),
                           passed=match,completion_record=Path(str(cache)+'.complete').is_file()))
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,help='Unique directory under analysis/resources/')
    parser.add_argument('--build-dir',default='build-vulkan')
    parser.add_argument('--timeout',type=positive_seconds,default=35)
    parser.add_argument('--modes',nargs='+',choices=['warm','fresh','interrupted'],default=['warm','fresh','interrupted'])
    parser.add_argument('--references',default='game/reference-rpcs3/assets')
    parser.add_argument('--scenario',choices=['title','battle','campaign'],default='title',help='Battle/campaign require manual navigation, not an inferred elapsed-key route')
    parser.add_argument('--prepare-only',action='store_true')
    args=parser.parse_args()
    output=resolve(args.output)
    if not output.is_relative_to(ROOT/'analysis/resources') or output.exists():parser.error('Use a new analysis/resources/ directory')
    config=tomllib.loads((ROOT/'config.toml').read_text());source=resolve(config['paths']['vfs_root']);references=resolve(args.references)
    for name in ASSETS:
        if not (references/(name+'.unedat')).is_file():parser.error('Missing independent reference '+name)
    output.mkdir(parents=True)
    originals={str(p):sha(p) for p in (source/'USRDIR').iterdir() if p.is_file() and p.name.endswith(('.edat','.dec','.complete'))}
    results=[]
    for mode in args.modes:
        title=output/mode/'title';prepare_profile(source,title,mode)
        before={p.name:p.stat().st_mtime_ns for p in (title/'USRDIR').glob('*.dec')}
        if args.prepare_only:
            results.append(dict(mode=mode,title=str(title),prepared=True));continue
        run=ROOT/'analysis/rendering'/output.name/mode
        print('Starting '+mode+' '+args.scenario+'; '+('leave at title' if args.scenario=='title' else 'navigate manually to requested scene'),flush=True)
        command=[sys.executable,str(ROOT/'scripts/capture_rendering.py'),'--output',str(run),'--build-dir',args.build_dir,'--content-root',str(title),'--timeout',str(args.timeout),'--capture','--textures','--resources']
        if args.scenario=='title':command.append('--no-gamepad')
        child=subprocess.run(command,cwd=ROOT)
        session=json.loads((run/'session.json').read_text()) if (run/'session.json').exists() else {}
        assets=check_caches(title,references)
        results.append(dict(mode=mode,title=str(title),scenario_requested=args.scenario,scenario_confirmed=False,
            launcher=child.returncode,strict_passed=session.get('strict_passed',False),assets=assets,capture=str(run),
            previous_cache_mtimes=before,final_cache_mtimes={p.name:p.stat().st_mtime_ns for p in (title/'USRDIR').glob('*.dec')}))
        if child.returncode:break
    intact=all(Path(p).is_file() and sha(Path(p))==digest for p,digest in originals.items())
    report=dict(profiles=results,originals_intact=intact,original_hashes=originals,prepared_only=args.prepare_only,
                note='Scenario labels are requests; confirm reached screens from captures/user actions. Warm legacy caches without completion records regenerate once.')
    (output/'profiles.json').write_text(json.dumps(report,indent=2)+'\n')
    if not intact or (not args.prepare_only and any(r['launcher'] or not r['strict_passed'] or not all(a['passed'] and a['completion_record'] for a in r['assets']) for r in results)):
        raise SystemExit('Profile verification failed; inspect profiles.json')


if __name__=='__main__':main()
