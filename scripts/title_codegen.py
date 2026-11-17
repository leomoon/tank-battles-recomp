#!/usr/bin/env python3
"""Build-time codegen refinements for locally translated PPU sources.

  python scripts/title_codegen.py --input recompiled/ppu_recomp_000.cpp --output build/title/ppu_recomp_000.cpp

The lifter's output stays untouched and disposable; the build compiles a
transformed copy. Currently one transform:

  restrict-context  Mark each lifted function's ppu_context parameter
                    __restrict in its definition (declarations are unchanged,
                    which C and C++ allow for top-level parameter qualifiers).
                    Guest memory is a separate host allocation, never the
                    context, but guest stores go through byte pointers that
                    under -fno-strict-aliasing could alias anything, so the
                    compiler reloads ctx->gpr/cr/xer after every guest store.
                    Every call out of lifted code passes ctx explicitly, so
                    callees that modify the context are still seen as doing so.
"""
import argparse
import re
from pathlib import Path

DEFINITION = re.compile(r'^(void func_[0-9A-Fa-f]+)\(ppu_context\* ctx\) \{(?=\r?$)', re.M)


def restrict_context(text):
    return DEFINITION.subn(r'\1(ppu_context* __restrict ctx) {', text)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    # Byte-preserving: keep the lifter's encoding and line endings exactly.
    text = args.input.read_bytes().decode('latin-1')
    text, count = restrict_context(text)
    if count == 0 and 'ppu_context* ctx) {' in text:
        raise SystemExit('No lifted function definitions matched in ' + str(args.input))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + '.tmp')
    temporary.write_bytes(text.encode('latin-1'))
    temporary.replace(args.output)


if __name__ == '__main__':
    main()
