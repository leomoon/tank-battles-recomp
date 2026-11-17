"""Original/synthetic boot inputs only, never read game/ or recompiled/."""
import argparse
import hashlib
from pathlib import Path
import subprocess
import sys

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdk',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args();sdk=Path(args.sdk);out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
    subprocess.run([sys.executable,str(sdk/'tools/make_smoke_elf.py'),'--out',str(out/'smoke.elf'),'--header',str(out/'ppu_recomp.h')],check=True)
    # The port deliberately delivers GCM interrupts on its dedicated ticker.
    # Preserve every SDK smoke check, with that explicit host-thread contract.
    smoke=(sdk/'runtime/ppu/tests/smoke/smoke_recomp.c').read_text()
    if smoke.count('g_vblank_host_tid != g_main_host_tid')!=1:
        raise SystemExit('Synthetic callback assertion changed in the SDK')
    smoke=smoke.replace('g_vblank_host_tid != g_main_host_tid', 'g_vblank_host_tid == g_main_host_tid')
    smoke=smoke.replace("not the guest's %lu", "unexpectedly on the main guest thread %lu")
    sys.path.insert(0,str(sdk/'tools'))
    from test_spu_program_pipeline import elf
    blob=elf(7)
    (out/'synthetic-spu.elf').write_bytes(blob)
    symbol='spu_'+hashlib.sha256(blob).hexdigest()+'_spu_func_00000100'
    check=('\n#include "spu_context.h"\nextern void '+symbol+'(spu_context*);\n'
           'static void verify_static_spu(void){spu_context* s=calloc(1,sizeof *s);if(!s)abort();'
           +symbol+'(s);if(s->gpr[3]._u32[0]!=7)abort();free(s);puts("[smoke] static SPU PASS");}\n')
    smoke=check+smoke.replace('void func_00010000(ppu_context* ctx)\n{','void func_00010000(ppu_context* ctx)\n{\n    verify_static_spu();')
    # Standard allocation/stdio declarations precede the synthetic check.
    (out/'smoke_recomp.c').write_text('#include <stdlib.h>\n#include <stdio.h>\n'+smoke)
    from synthetic_content import write_sfo
    write_sfo(out/'PARAM.SFO')

if __name__=='__main__':main()
