"""Freeze a private player and verified runtime bundle under ignored analysis/."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile

from host_inventory import capture
from runtime_bundle import verify as verify_bundle

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def hashes(root):
    return {p.relative_to(root).as_posix(): sha(p) for p in sorted(Path(root).rglob('*')) if p.is_file()}


def copy_checked(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    before = sha(source)
    shutil.copy2(source, target)
    if sha(target) != before or sha(source) != before:
        raise ValueError('File changed during freeze: '+str(source))
    return before


def cache_values(build):
    values = {}
    for line in (Path(build)/'CMakeCache.txt').read_text(encoding='utf-8').splitlines():
        if '=' in line and not line.startswith(('#', '//')):
            key, value = line.split('=', 1)
            values[key.split(':', 1)[0]] = value
    return values


def executable(build, configuration='Release'):
    build = Path(build)
    for path in (build/configuration/'TankBattlesRecomp.exe', build/'TankBattlesRecomp.exe', build/'TankBattlesRecomp'):
        if path.is_file():
            return path
    raise ValueError('No player executable in '+str(build))


def freeze(build, output, runtime=None, configuration='Release'):
    build, output = Path(build).resolve(), Path(output).resolve()
    if not output.is_relative_to(ROOT/'analysis') or output.exists():
        raise ValueError('Use a new directory under ignored analysis/')
    exe = executable(build, configuration)
    cache = cache_values(build)
    runtime = Path(runtime or cache.get('TB_RUNTIME_ROOT', '')).resolve() if runtime or cache.get('TB_RUNTIME_ROOT') else None
    bundle = verify_bundle(runtime) if runtime else None
    output.mkdir(parents=True)
    record = dict(schema=1, created_utc=datetime.now(timezone.utc).isoformat(),
                  source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                  working_tree=subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True),
                  build_directory=str(build), configuration=configuration,
                  executable='player/'+exe.name, executable_sha256=copy_checked(exe, output/'player'/exe.name),
                  runtime_source_commit=bundle['source_commit'] if bundle else None,
                  expected_runtime_source=cache.get('TB_RUNTIME_EXPECTED_SOURCE'),
                  provenance_note='Executable/archive bytes are frozen as found; checkout commit does not attest that all objects were rebuilt from it.',
                  environment={k:v for k,v in os.environ.items() if k.startswith(('TB_', 'PAD_', 'SDL_', 'VK_DRAW_', 'GCM_')) and not any(s in k.upper() for s in ('KEY','TOKEN','PASSWORD','SECRET'))})
    copy_checked(build/'CMakeCache.txt', output/'build/CMakeCache.txt')
    for pattern in ('**/*.command.*.tlog', '*.vcxproj', 'compile_commands.json', 'commands.txt', 'instrumented_sdk/manifest.json'):
        for source in build.glob(pattern):
            if source.is_file():
                copy_checked(source, output/'build'/source.relative_to(build))
    if runtime:
        shutil.copytree(runtime, output/'runtime')
        verify_bundle(output/'runtime')
    generated = {}
    for name, source in [('ppu', ROOT/'recompiled'), ('spu', build/'generated_spu')]:
        if source.exists():
            generated[name] = hashes(source)
    record['generated_files'] = generated
    record['private_objects'] = {p.relative_to(build).as_posix(): sha(p) for p in sorted(build.rglob('*.obj'))}
    # Keep source inputs and the old compile commands locally; no source archive is uploaded.
    tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().strip('\0').split('\0')
    record['source_files'] = {name:sha(ROOT/name) for name in tracked if (ROOT/name).is_file()}
    with tarfile.open(output/'source.tar', 'w') as archive:
        for name in record['source_files']:
            archive.add(ROOT/name, arcname=name, recursive=False)
    record['sdk_tree'] = subprocess.check_output(['git', 'rev-parse', 'HEAD:external/ps3recomp'], cwd=ROOT, text=True).strip()
    for source in (ROOT/'config.toml', ROOT/'game/settings.ini'):
        if source.exists():
            copy_checked(source, output/'settings'/source.name)
    home = ROOT/'game/dev_hdd0/home'
    if home.exists():
        # Local-only recovery copy. Private sidecars retain protected ACLs on Windows.
        shutil.copytree(home, output/'private-home')
        from private_files import restrict_private
        for p in (output/'private-home').rglob('*.rifkey'):
            restrict_private(p)
        record['original_home_hashes'] = hashes(home)
        if hashes(output/'private-home') != record['original_home_hashes']:
            raise ValueError('Private profile snapshot mismatch')
    capture(output/'host')
    record['files'] = hashes(output)
    (output/'baseline.json').write_text(json.dumps(record, indent=2)+'\n', encoding='utf-8')
    verify(output)
    return record


def verify(output):
    output = Path(output).resolve()
    record = json.loads((output/'baseline.json').read_text(encoding='utf-8'))
    for name, digest in record['files'].items():
        path = output/name
        if not path.resolve().is_relative_to(output) or not path.is_file() or sha(path) != digest:
            raise ValueError('Frozen baseline file mismatch: '+name)
    if sha(output/record['executable']) != record['executable_sha256']:
        raise ValueError('Executable identity mismatch')
    if (output/'runtime').exists():
        verify_bundle(output/'runtime')
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir', type=Path, default=ROOT/'build-player')
    parser.add_argument('--runtime', type=Path)
    parser.add_argument('--configuration', default='Release')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    result = verify(args.output) if args.verify else freeze(args.build_dir, args.output, args.runtime, args.configuration)
    print(json.dumps({k:result[k] for k in ('source_commit','runtime_source_commit','executable_sha256','provenance_note')}, indent=2))


if __name__ == '__main__':
    main()
