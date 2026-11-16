#!/usr/bin/env python3
"""Regenerate the original depth conversion shader or check its embedded bytes."""
import argparse
import re
import struct
import subprocess
import tempfile
from pathlib import Path

root=Path(__file__).resolve().parent.parent
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--check',action='store_true')
args=parser.parse_args()
with tempfile.TemporaryDirectory(prefix='tb-depth-shader-') as temporary:
    binary=Path(temporary)/'depth16.spv'
    subprocess.run(['glslangValidator','-V',str(root/'shaders/depth16.comp'),'-o',str(binary)],check=True)
    data=binary.read_bytes()
words=struct.unpack('<'+'I'*(len(data)//4),data)
target=root/'shaders/depth16_spirv.inc'
if args.check:
    embedded=tuple(int(word,16) for word in re.findall(r'0x([0-9a-fA-F]+)u',target.read_text()))
    if words!=embedded:
        raise SystemExit('Embedded SPIR-V differs: review the shader/compiler change and regenerate explicitly.')
    print('PASS: embedded SPIR-V matches the original shader with the installed glslangValidator')
else:
    text='/* Generated from original depth16.comp using glslangValidator -V. */\nstatic const uint32_t tb_depth16_spirv[]={\n'
    text+='\n'.join(','.join(f'0x{word:08X}u' for word in words[i:i+8])+',' for i in range(0,len(words),8))
    target.write_text(text+'\n};\n')
