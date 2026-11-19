"""Record reproducible executable/build inputs for explicit implementation comparisons."""
import hashlib
import json
import subprocess
import shutil
import tarfile
from pathlib import Path
from port import ROOT


def measurement_build(build, configuration='Release'):
    """Inspect Ninja or Visual Studio builds without assuming the active shell compiler."""
    from freeze_performance_baseline import cache_values, executable, sha
    build=Path(build); cache=cache_values(build)
    if (build/'commands.txt').exists():
        commands=(build/'commands.txt').read_text()
    elif (build/'build.ninja').exists():
        commands=subprocess.check_output(['ninja','-C',str(build),'-t','commands'],text=True)
    else:
        logs=sorted(build.glob('**/'+configuration+'/**/*.command.*.tlog'))
        commands='\n'.join(read_command_log(p) for p in logs)
    flags={}
    for kind,needle in [('ppu','ppu_recomp_'),('spu','spu_recomp'),('runtime','rendering_profile')]:
        lines=commands.splitlines()
        matches=[line for i,line in enumerate(lines) if needle in line.lower() or i and needle in lines[i-1].lower()]
        flags[kind]='\n'.join(matches[:4]) or None
    runtime=Path(cache['TB_RUNTIME_ROOT']) if cache.get('TB_RUNTIME_ROOT') else None
    # relink_player.py replays the link against another bundle; trust that
    # record only while it still describes the executable on disk.
    relink=build/'runtime-relink.json'
    if runtime and relink.is_file():
        record=json.loads(relink.read_text())
        if hashlib.sha256(executable(build,configuration).read_bytes()).hexdigest()==record.get('executable_sha256'):
            runtime=Path(record['bundle'])
    manifest=runtime/'manifest.json' if runtime else build/'instrumented_sdk/manifest.json'
    if not manifest.is_file():
        raise ValueError('Missing runtime/source manifest: '+str(manifest))
    bundle=json.loads(manifest.read_text()) if runtime else None
    compiler_files=sorted(build.glob('CMakeFiles/*/CMakeCXXCompiler.cmake'))
    compiler_text=compiler_files[-1].read_text() if compiler_files else ''
    import re
    compiler=' '.join(re.findall(r'set\(CMAKE_CXX_COMPILER_(?:ID|VERSION) "([^"]+)"',compiler_text))
    return dict(executable=executable(build,configuration),effective_flags=flags,
                build_type=cache.get('CMAKE_BUILD_TYPE') or configuration,
                compiler=compiler or (bundle['build']['compiler_id']+' '+bundle['build']['compiler_version'] if bundle else 'unavailable'),
                sdk_manifest_sha256=sha(manifest),runtime_source_commit=bundle.get('source_commit') if bundle else None,
                runtime_manifest=str(manifest),runtime_build=bundle.get('build') if bundle else None)


def read_command_log(path):
    data=Path(path).read_bytes()
    encoding='utf-16' if data.startswith((b'\xff\xfe',b'\xfe\xff')) else 'utf-16-le' if b'\0' in data[:100] else 'utf-8'
    return data.decode(encoding,errors='replace')

BUILD_FIELDS = ('executable_sha256', 'sdk_manifest_sha256', 'source_commit', 'source_inputs_sha256', 'effective_flags', 'compiler', 'build_type', 'submission_mode')


def source_identity():
    names = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().strip('\0').split('\0')
    for folder,pattern in [('','*.cpp'),('','*.c'),('','*.h'),('sdk_overrides','*.inc'),('sdk_overrides','*.h'),('scripts','*.py'),('shaders','*.comp'),('shaders','*.inc'),('tests','*.inc'),('tests','*.cpp'),('tests/texture_decode','CMakeLists.txt'),('licenses','*.txt')]:
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
    for folder, pattern in [('','*.cpp'),('','*.c'),('','*.h'),('sdk_overrides','*.inc'),('sdk_overrides','*.h'),('scripts','*.py'),('shaders','*.comp'),('shaders','*.inc'),('tests','*.inc'),('tests','*.cpp'),('tests/texture_decode','CMakeLists.txt'),('licenses','*.txt')]:
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
