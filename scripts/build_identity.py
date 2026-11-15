"""Record reproducible executable/build inputs for explicit implementation comparisons."""
import hashlib
import json
import subprocess
import shutil
import tarfile
from pathlib import Path
from port import ROOT

BUILD_FIELDS = ('executable_sha256', 'sdk_manifest_sha256', 'source_commit', 'source_inputs_sha256', 'effective_flags', 'compiler', 'build_type', 'submission_mode')


def source_identity():
    names = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().strip('\0').split('\0')
    for folder,pattern in [('sdk_overrides','*.inc'),('scripts','*.py'),('tests','*.inc')]:
        names += [str(p.relative_to(ROOT)) for p in (ROOT/folder).glob(pattern)]
    hashes = {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in sorted(set(names)) if (ROOT/name).is_file()}
    return hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()


def validate_candidate_plan(reports, plan):
    """A plan names exact identities; scene/audio/quality checks remain unconditional."""
    if not plan.get('reason') or set(plan.get('variants', {})) != {'baseline', 'candidate'}:
        raise ValueError('Candidate plan needs a reason and exact baseline/candidate identities')
    for role, identity in plan['variants'].items():
        if set(identity) != set(BUILD_FIELDS):
            raise ValueError('Incomplete '+role+' build identity')
    observed = set()
    for report in reports:
        manifest = report['manifest']
        identity = {k: manifest.get(k) for k in BUILD_FIELDS}
        matches = [role for role, expected in plan['variants'].items() if identity == expected]
        if len(matches) != 1:
            raise ValueError('Run does not match exactly one declared build identity')
        observed.add(matches[0])
    if observed != {'baseline', 'candidate'}:
        raise ValueError('Need both baseline and candidate runs')
    return BUILD_FIELDS


def freeze_build(build, output):
    """Preserve a measured build and all source inputs without changing it."""
    build, output = Path(build).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError('Freeze output must be new')
    output.mkdir(parents=True)
    for name in ('TankBattlesRecomp', 'CMakeCache.txt', 'instrumented_sdk/manifest.json'):
        target=output/name
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(build/name,target)
    (output/'commands.txt').write_text(subprocess.check_output(['ninja','-C',str(build),'-t','commands'],text=True))
    names=subprocess.check_output(['git','ls-files','-z'],cwd=ROOT).decode().strip('\0').split('\0')
    for folder, pattern in [('sdk_overrides','*.inc'),('scripts','*.py'),('tests','*.inc')]:
        names += [str(p.relative_to(ROOT)) for p in (ROOT/folder).glob(pattern)]
    files={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in sorted(set(names)) if (ROOT/name).is_file()}
    with tarfile.open(output/'source.tar','w') as archive:
        for name in files:
            archive.add(ROOT/name,arcname=name)
    metadata=dict(commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),files=files,executable_sha256=hashlib.sha256((output/'TankBattlesRecomp').read_bytes()).hexdigest())
    (output/'sources.json').write_text(json.dumps(metadata,indent=2)+'\n')
    return metadata


if __name__ == '__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    print(freeze_build(args.build_dir,args.output)['executable_sha256'])
