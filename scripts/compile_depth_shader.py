#!/usr/bin/env python3
"""Regenerate the port's embedded compute shaders or check their embedded bytes.

depth16.comp (exact Z16 conversion) and nv3089_gather.comp (exact NV3089
point sampling) are compiled with glslangValidator -V into shaders/*_spirv.inc.
"""
import argparse
import re
import struct
import subprocess
import tempfile
from pathlib import Path

root=Path(__file__).resolve().parent.parent
SHADERS={'depth16':'tb_depth16_spirv','nv3089_gather':'tb_nv3089_gather_spirv'}
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--check',action='store_true')
parser.add_argument('--shader',choices=sorted(SHADERS),action='append',help='Default: all')
args=parser.parse_args()
for name in args.shader or sorted(SHADERS):
    with tempfile.TemporaryDirectory(prefix='tb-shader-') as temporary:
        binary=Path(temporary)/(name+'.spv')
        subprocess.run(['glslangValidator','-V',str(root/'shaders'/(name+'.comp')),'-o',str(binary)],check=True,capture_output=True)
        data=binary.read_bytes()
    words=struct.unpack('<'+'I'*(len(data)//4),data)
    target=root/'shaders'/(name+'_spirv.inc')
    if args.check:
        embedded=tuple(int(word,16) for word in re.findall(r'0x([0-9a-fA-F]+)u',target.read_text()))
        if words!=embedded:
            raise SystemExit(name+': embedded SPIR-V differs: review the shader/compiler change and regenerate explicitly.')
        print('PASS: embedded SPIR-V matches the original '+name+' shader with the installed glslangValidator')
    else:
        text='/* Generated from original '+name+'.comp using glslangValidator -V. */\nstatic const uint32_t '+SHADERS[name]+'[]={\n'
        text+='\n'.join(','.join(f'0x{word:08X}u' for word in words[i:i+8])+',' for i in range(0,len(words),8))
        target.write_text(text+'\n};\n')
