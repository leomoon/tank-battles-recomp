"""Emit only the common lifter ABI and reject incompatible title headers."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdk',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--check-title-header')
    args=parser.parse_args()
    sys.path.insert(0,str(Path(args.sdk)/'tools'))
    from ppu_lifter import HEADER_PREAMBLE,PPULifter
    header=PPULifter().emit_header()
    if args.check_title_header and not Path(args.check_title_header).read_text().startswith(HEADER_PREAMBLE):
        raise SystemExit('Title ABI differs from the reusable runtime; regenerate using the matching lifter.')
    output=Path(args.output);output.mkdir(parents=True,exist_ok=True)
    values={'ppu_recomp.h':header,'abi.json':json.dumps(dict(schema=1,header_sha256=hashlib.sha256(header.encode()).hexdigest()),indent=2)+'\n'}
    for name,value in values.items():
        path=output/name
        data=value.encode("utf-8")
        if not path.exists() or path.read_bytes()!=data:path.write_bytes(data)

if __name__=='__main__':main()
