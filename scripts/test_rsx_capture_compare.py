#!/usr/bin/env python3
"""Check inherited GPU state, shader constants, continuations and corrupt captures."""
import gzip
from pathlib import Path
import struct
import tempfile
import unittest
from rsx_capture_compare import read_rrc, reference_manifest, fp_program, VP_BYTES, REG_BYTES

def vle(n):
    result=bytearray()
    while n>=128:result.append((n&127)|128);n>>=7
    result.append(n);return bytes(result)

def fixture():
    # One fragment program, one inherited texture and one triangle-strip draw.
    fp=struct.pack('>4I',0x10000,0,0,0)
    raw=bytearray(b'RRC\0'+struct.pack('<II',6,1))
    raw+=vle(0) # tile map
    raw+=vle(2)
    raw+=struct.pack('<IIQQ',0x100,0,10,1)+struct.pack('<IIQQ',0x200,0,11,2)
    raw+=vle(2)+vle(len(fp))+fp+struct.pack('<Q',10)
    texture=b'abcdefgh';raw+=vle(8)+texture+struct.pack('<Q',11)
    raw+=vle(0) # displays
    commands=[(0,0,[1,2]),(0x40000|0x1808,6,[]),(0x40000|0x1814,3<<24,[]),(0x40000|0x1808,0,[])]
    raw+=vle(len(commands))
    for cmd,arg,mem in commands:
        raw+=struct.pack('<II',cmd,arg)+vle(len(mem))+b''.join(struct.pack('<Q',m) for m in mem)+bytes(16)
    vp=bytearray(VP_BYTES);struct.pack_into('<I',vp,12,1);raw+=vp
    regs=[0]*(REG_BYTES//4)
    for addr,value in {0x8e4:0x101,0x310:1,0x314:0x03020302,0x1a00:0x200,0x1a04:0x18501,0x1a0c:0x80000000,0x1a18:0x20001}.items():regs[addr//4]=value
    raw+=struct.pack('<16384I',*regs)
    return bytes(raw)

class CaptureTests(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'frame.rrc.gz'
    def tearDown(self):self.temp.cleanup()
    def save(self,raw):self.path.write_bytes(gzip.compress(raw))
    def test_inherited_state_and_memory(self):
        self.save(fixture());r=reference_manifest(self.path);d=r['draws'][0]
        self.assertEqual(len(r['draws']),1);self.assertEqual(d['count'],4)
        self.assertEqual(d['pipeline']['blend_enable'],1);self.assertEqual(d['fp']['instructions'],1)
        self.assertEqual(d['vp']['instructions'],1);self.assertEqual(d['textures'][0]['captured_bytes'],8)
    def test_rejects_truncation_and_unknown_layout(self):
        for raw in [fixture()[:-1],fixture()+b'other-layout',b'RRC\0'+struct.pack('<II',7,1)]:
            self.save(raw)
            with self.assertRaises(ValueError):read_rrc(self.path)
    def test_shader_identity_excludes_inline_constant(self):
        instruction=struct.pack('>4I',0x10000,0x20000,0,0)
        a=fp_program(instruction+bytes(16));b=fp_program(instruction+b'0123456789abcdef')
        self.assertEqual(a['code_sha256'],b['code_sha256']);self.assertNotEqual(a['constants_sha256'],b['constants_sha256'])
        self.assertIsNone(fp_program(instruction+bytes(15)))
    def test_nonincrement_continuation(self):
        # A second raw command value with no header must retain its method.
        raw=fixture();old=struct.pack('<II',0x40000|0x1814,3<<24)+bytes(17)
        new=struct.pack('<II',0x40000000|0x80000|0x1814,1<<24)+bytes(17)+struct.pack('<II',0,1<<24)+bytes(17)
        raw=raw.replace(bytes([4])+struct.pack('<II',0,0),bytes([5])+struct.pack('<II',0,0),1).replace(old,new,1)
        self.save(raw);r=reference_manifest(self.path);self.assertEqual(r['draws'][0]['count'],4)
        self.assertEqual(r['methods']['1814'],2)

if __name__=='__main__':unittest.main()
