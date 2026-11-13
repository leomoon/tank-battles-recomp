"""Local packaged-read evidence; outputs contain game metadata and remain ignored."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import struct
import tomllib
import zipfile
import zlib
from port import ROOT, resolve


def write_media_watch(directory, config):
    imports=resolve(config['paths']['analysis'])/'EBOOT.imports.json'
    rows=json.loads(imports.read_text())
    if isinstance(rows,dict):rows=rows.get('imports',[])
    selected=[r for r in rows if r.get('library') in ('cellSail','cellSailRec')]
    (directory/'media-nids.txt').write_text(''.join(f"{int(r['nid'],0):08x}\n" for r in selected))
    (directory/'media-imports.json').write_text(json.dumps(selected,indent=2)+'\n')


def inspect_resources(run, allow_failed=False):
    report=json.loads((run/'session.json').read_text())
    if not report.get('strict_passed') and not allow_failed:
        raise ValueError('Use --allow-failed for diagnosis; a failed session cannot pass verification')
    title=Path(report['content_root']);directory=run/'resources'
    events=[json.loads(line) for line in (directory/'reads.jsonl').read_text().splitlines()]
    data=(directory/'reads.bin').read_bytes()
    if any(r.get('trace_limit') for r in events):raise ValueError('Resource trace limit reached; narrow the scenario before claiming coverage')
    reads=[r for r in events if 'archive' in r]
    media=Counter(r['media_nid'] for r in events if 'media_nid' in r)
    index={}; verified=[];read_matches=0
    for path in (title/'USRDIR').glob('assets_*.edat.dec'):
        chunks=[r for r in reads if r['archive']==path.name]
        with path.open('rb') as f,zipfile.ZipFile(path) as archive:
            for r in chunks:
                f.seek(r['offset']);actual=data[r['capture_offset']:r['capture_offset']+r['bytes']]
                if actual!=f.read(r['bytes']):raise ValueError('Native archive read differs from local cache')
                read_matches+=1
            for entry in archive.infolist():
                if entry.is_dir():continue
                f.seek(entry.header_offset);header=f.read(30)
                start=entry.header_offset+30+sum(struct.unpack_from('<HH',header,26))
                end=start+entry.compress_size
                index.setdefault(entry.filename.lower(),[]).append(dict(archive=path.name,path=entry.filename))
                overlapping=[r for r in chunks if r['offset']<end and r['offset']+r['bytes']>start]
                if not overlapping:continue
                intervals=sorted((max(start,r['offset']),min(end,r['offset']+r['bytes'])) for r in overlapping)
                covered=start
                for a,b in intervals:
                    if a>covered:break
                    covered=max(covered,b)
                if covered<end:continue
                # Reconstruct from bytes actually delivered to the guest, not a new host archive read.
                compressed=bytearray(entry.compress_size)
                for r in overlapping:
                    a=max(start,r['offset']);b=min(end,r['offset']+r['bytes'])
                    pos=r['capture_offset']+a-r['offset'];compressed[a-start:b-start]=data[pos:pos+b-a]
                plain=zlib.decompress(compressed,-15) if entry.compress_type==8 else bytes(compressed)
                ok=len(plain)==entry.file_size and zlib.crc32(plain)==entry.CRC and plain==archive.read(entry)
                if not ok:raise ValueError('Reconstructed packaged entry failed size/CRC/reference comparison')
                verified.append(dict(archive=path.name,path=entry.filename,plain_bytes=len(plain),
                                     crc32=f'{entry.CRC:08x}',sha256=hashlib.sha256(plain).hexdigest()))
    log=(run/'startup.log').read_text(errors='replace');failures=[]
    for request in sorted(set(re.findall(r'\[sys_fs\] open FAILED: (.*?) \(errno=',log))):
        if '/USRDIR/' not in request:continue
        relative=request.split('/USRDIR/',1)[1]
        exact=index.get(relative.lower(),[])
        basename=[v for key,values in index.items() if Path(key).name==Path(relative).name.lower() for v in values]
        observed={r['path'].lower() for r in verified}
        failures.append(dict(request=relative,exact_casefold=exact,basename_candidates=basename,
                             candidate_payloads_read=[v for v in basename if v['path'].lower() in observed],
                             conclusion='No filesystem alias inferred from a basename match; inspect guest fallback and reference behavior.'))
    result=dict(strict_passed=report['strict_passed'], accepted_for_verification=bool(report['strict_passed'] and report.get('session',{}).get('launcher_exit_status')==124),reads_verified=read_matches,payloads=verified,
                loose_failures=failures,media_calls={f'{k:08x}':v for k,v in media.items()},
                media_imports=len(json.loads((directory/'media-imports.json').read_text())),
                limitations='Packaged-read bytes and independent host decompression/CRC are verified. This alone does not prove the guest decompressed every entry correctly. Compare captured guest texture outputs separately. No recorded Sail call only covers the observed route.')
    (directory/'report.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(dict(reads=read_matches,complete_packaged_entries=len(verified),loose_failures=len(failures),media_calls=result['media_calls'])))
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('run');parser.add_argument('--allow-failed',action='store_true',help='Inspect failed strict sessions as diagnostic evidence only');args=parser.parse_args()
    run=resolve(args.run)
    if not run.is_relative_to(ROOT/'analysis/rendering'):parser.error('Use ignored analysis/rendering/ output')
    inspect_resources(run,allow_failed=args.allow_failed)


if __name__=='__main__':main()
