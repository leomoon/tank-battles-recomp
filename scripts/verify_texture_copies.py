#!/usr/bin/env python3
"""Compare captured UI upload bytes with DDS payloads from the user's local dump."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import tomllib
import zipfile
from port import ROOT, resolve

ASSETS = ('data/2d/sprites/gameloftlogo/gameloftlogo001.dds',
          'data/2d/sprites/menutests/splash004.dds')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('textures', help='Ignored capture directory containing textures.jsonl')
    args = parser.parse_args()
    directory = resolve(args.textures)
    if not directory.is_relative_to(ROOT / 'analysis/rendering'):
        parser.error('Use a local directory under ignored analysis/rendering/')
    config = tomllib.loads((ROOT / 'config.toml').read_text())
    assets = resolve(config['paths']['vfs_root']) / 'USRDIR'
    observed = [json.loads(line) for line in (directory / 'textures.jsonl').read_text().splitlines()]
    found = {}
    for path in assets.glob('*.edat.dec'):
        if not zipfile.is_zipfile(path):
            continue
        with zipfile.ZipFile(path) as archive:
            names = {name.lower(): name for name in archive.namelist()}
            for asset in ASSETS:
                if asset in names:
                    found[asset] = archive.read(names[asset])
    result = []
    for asset in ASSETS:
        dds = found.get(asset)
        if not dds or dds[:4] != b'DDS ' or dds[84:88] != b'DXT5':
            parser.error('Required local DXT5 asset unavailable: ' + asset)
        height, width = struct.unpack_from('<II', dds, 12)
        size = ((width + 3) // 4) * ((height + 3) // 4) * 16
        expected = dds[128:128 + size]
        candidates = [r for r in observed if (r['width'], r['height'], r['format'] & 0x9f) == (width, height, 0x88)]
        if not candidates:
            parser.error('Required UI texture was not captured: ' + asset)
        matches = []
        for row in candidates:
            raw = directory / f"texture-{row['n']:03}-{row['offset']:08x}.raw"
            data = raw.read_bytes()
            matches.append(dict(offset=row['offset'], bytes=len(data), matches=data == expected,
                                sha256=hashlib.sha256(data).hexdigest()))
        result.append(dict(asset=asset, width=width, height=height, expected_bytes=size,
                           expected_sha256=hashlib.sha256(expected).hexdigest(), uploads=matches,
                           passed=any(r['matches'] for r in matches)))
    report = dict(passed=all(r['passed'] for r in result), textures=result,
                  limitation='Verifies the two observed base-level BC3 payloads before GPU upload, not shader output or all textures.')
    (directory / 'copy-verification.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
