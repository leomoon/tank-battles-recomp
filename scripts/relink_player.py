#!/usr/bin/env python3
"""Relink a Windows consumer player build against another runtime bundle.

  python scripts/relink_player.py --build-dir build-player --bundle path/to/runtime-bundle

For implementation comparisons of runtime-only changes: the title objects
(translated PPU/SPU code and the entry point) are reused as they are, and only
the final link is replayed with the selected bundle's archives. The bundle must
verify and carry the same PPU ABI as the one the build was configured with.
The relink is recorded in <build>/runtime-relink.json, which build_identity.py
uses for the measurement manifest instead of the configured TB_RUNTIME_ROOT.
"""
import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def cache_value(build, key):
    for line in (build / 'CMakeCache.txt').read_text(errors='replace').splitlines():
        if line.startswith(key + ':'):
            return line.split('=', 1)[1]
    return None


def read_log(path):
    data = path.read_bytes()
    encoding = 'utf-16' if data.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8'
    return data.decode(encoding, errors='replace')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--build-dir', required=True, type=Path)
    parser.add_argument('--bundle', required=True, type=Path)
    parser.add_argument('--configuration', default='Release')
    args = parser.parse_args()
    if sys.platform != 'win32':
        parser.error('Windows consumer builds only; Linux full-source builds relink with ninja')
    build, bundle = args.build_dir.resolve(), args.bundle.resolve()
    configured = cache_value(build, 'TB_RUNTIME_ROOT')
    if not configured:
        raise SystemExit('Not a runtime-consumer build: no TB_RUNTIME_ROOT in ' + str(build / 'CMakeCache.txt'))
    configured = Path(configured).resolve()
    new_manifest = json.loads((bundle / 'manifest.json').read_text())
    old_manifest = json.loads((configured / 'manifest.json').read_text())
    if new_manifest['abi_sha256'] != old_manifest['abi_sha256']:
        raise SystemExit('PPU ABI differs from the configured bundle; reconfigure instead of relinking')
    subprocess.run([sys.executable, str(bundle / 'tools/runtime_bundle.py'), 'verify', str(bundle),
                    '--source', new_manifest['source_commit']], check=True)
    logs = sorted((build / 'TankBattlesRecomp.dir' / args.configuration).rglob('lld-link.command.1.tlog'))
    if len(logs) != 1:
        raise SystemExit('Expected one lld-link command log, found %d' % len(logs))
    command = ' '.join(line for line in read_log(logs[0]).splitlines() if not line.startswith('^'))
    count = 0
    for before in {str(configured), configured.as_posix()}:
        command, n = re.subn(re.escape(before), lambda _: str(bundle), command, flags=re.IGNORECASE)
        count += n
    if not count:
        raise SystemExit('The recorded link command does not reference ' + str(configured))
    # MSBuild's command logs store paths upper-cased; replaying /OUT verbatim
    # would rename the executable to TANKBATTLESRECOMP.EXE. Use the real path.
    exe = build / args.configuration / 'TankBattlesRecomp.exe'
    command, n = re.subn(r'/OUT:("[^"]*"|\S+)', lambda _: '/OUT:"' + str(exe) + '"', command, count=1, flags=re.IGNORECASE)
    if n != 1:
        raise SystemExit('The recorded link command has no /OUT')
    sys.path.insert(0, str(ROOT / 'external/ps3recomp/tools'))
    from msvc_env import environ, find_vcvars
    linker = Path(find_vcvars()).parents[2] / 'Tools/Llvm/x64/bin/lld-link.exe'
    objects = {p: p.stat().st_mtime_ns for p in (build / 'TankBattlesRecomp.dir' / args.configuration).glob('*.obj')}
    subprocess.run('"' + str(linker) + '" ' + command, cwd=build, env=environ(), check=True)
    if any(p.stat().st_mtime_ns != stamp for p, stamp in objects.items()):
        raise SystemExit('Title objects changed during the relink')
    record = dict(bundle=str(bundle), source_commit=new_manifest['source_commit'],
                  manifest_sha256=hashlib.sha256((bundle / 'manifest.json').read_bytes()).hexdigest(),
                  executable_sha256=hashlib.sha256(exe.read_bytes()).hexdigest(), reused_objects=len(objects))
    (build / 'runtime-relink.json').write_text(json.dumps(record, indent=2) + '\n')
    print(f"Relinked {exe} against {bundle} ({len(objects)} title objects reused)")


if __name__ == '__main__':
    main()
