#!/usr/bin/env python3
"""Exercise the actual NV0039 port override using original, game-free bytes."""
from pathlib import Path
import subprocess
import unittest
from port import ROOT


class MemoryCopyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        directory = ROOT / 'build-tests/rsx-memory-copy'
        directory.mkdir(parents=True, exist_ok=True)
        fixture = directory / 'fixture.c'
        fixture.write_text(r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef uint32_t u32; typedef int32_t s32; typedef uint8_t u8;
enum { RSX_LOCATION_LOCAL, RSX_LOCATION_MAIN };
static struct { u32 localSize; } s_config = {8192};
static int s_gcm_mem;
static u8 memory[2][8192];
static unsigned reads, writes;
static int rsx_draw_engine_enabled(void) { return 1; }
static void rsx_draw_engine_guest_read(u32 l,u32 o,u32 p,u32 w,u32 h) { reads++; }
static void rsx_draw_engine_guest_wrote(u32 l,u32 o,u32 p,u32 w,u32 h) { writes++; }
static int rsx_tiled_read(const void* m,u32 l,u32 o,void* d,u32 n) {
    if (l>1 || (uint64_t)o+n>8192) return 0;
    memcpy(d,memory[l]+o,n);return 1;
}
static int rsx_tiled_write(const void* m,u32 l,u32 o,const void* s,u32 n) {
    if (l>1 || (uint64_t)o+n>8192) return 0;
    memcpy(memory[l]+o,s,n);return 1;
}
#include "sdk_overrides/rsx_memory_copy.inc"
static void setup(u32 sd,u32 dd,u32 src,u32 dst,s32 sp,s32 dp,u32 n,u32 rows,u32 fmt) {
    tb_m2mf_method(0x184,sd);tb_m2mf_method(0x188,dd);
    tb_m2mf_method(0x30c,src);tb_m2mf_method(0x310,dst);
    tb_m2mf_method(0x314,(u32)sp);tb_m2mf_method(0x318,(u32)dp);
    tb_m2mf_method(0x31c,n);tb_m2mf_method(0x320,rows);tb_m2mf_method(0x324,fmt);
}
int main(int argc,char** argv) {
    unsigned test=atoi(argv[1]);
    for(unsigned l=0;l<2;l++)for(unsigned i=0;i<8192;i++)memory[l][i]=(i*71+i/13+l*97)%256;
    u8 expected[2][8192];memcpy(expected,memory,sizeof(memory));
    u32 sl=0,dl=0,src=1024,dst=64,n=713,rows=1,si=1,di=1;
    s32 sp=0,dp=0;
    if(test==1){sl=1;dl=0;src=37;dst=2003;rows=5;n=31;sp=64;dp=47;}
    if(test==2){src=64;dst=80;n=128;rows=4;sp=64;dp=64;}
    if(test==3){sl=0;dl=1;src=300;dst=700;n=23;rows=4;sp=-40;dp=-32;}
    if(test==4){sl=1;src=100;dst=900;n=39;rows=3;sp=200;dp=180;si=4;di=3;}
    if(test==5){n=0;rows=0;}
    if(test==6){dst=8100;n=200;}
    if(test==7){src=4;rows=3;sp=-100;}
    if(test==8){si=0;}
    if(test==9){sl=1;src=8100;n=200;}
    setup(sl?0xfeed0001:0xfeed0000,dl?0xfeed0001:0xfeed0000,src,dst,sp,dp,n,rows,si|(di<<8));
    if(test<5)for(u32 y=0;y<rows;y++)for(u32 x=0;x<n;x++)
        expected[dl][dst+(int64_t)y*dp+x*di]=memory[sl][src+(int64_t)y*sp+x*si];
    /* State writes alone must not transfer anything. */
    if(reads || writes)return 10;
    tb_m2mf_method(0x328,0);
    if(memcmp(expected,memory,sizeof(memory)))return 11;
    if(test<5 && (reads!=rows || writes!=rows))return 12;
    if(test>=5 && writes)return 13;
    return 0;
}
''')
        cls.binary = directory / 'fixture'
        subprocess.run(['cc', '-std=c17', '-I', str(ROOT), str(fixture), '-o', str(cls.binary)], check=True)

    def test_transfer_semantics(self):
        cases = ['linear zero-pitch row', 'main-to-local pitched rows', 'overlapping rows',
                 'negative pitches', 'byte-column strides', 'empty area', 'local overflow',
                 'negative offset', 'invalid stride', 'unmapped main input']
        for number, label in enumerate(cases):
            with self.subTest(case=label):
                subprocess.run([str(self.binary), str(number)], check=True)


if __name__ == '__main__':
    unittest.main()
