#!/usr/bin/env python3
"""Run the actual generated draw engine and an original scanout-memory regression."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from rsx_capture_compare import native_manifest
from port import ROOT
sdk=ROOT/'external/ps3recomp';out=ROOT/'build-tests/scanout';out.mkdir(parents=True,exist_ok=True)
subprocess.run([sys.executable,str(ROOT/'scripts/instrument_sdk.py'),'--sdk',str(sdk),'--output',str(out/'overlay')],check=True)
s=(sdk/'libs/video/tests/test_rsx_draw_engine.c').read_text()
assert s.count('int main(void)')==1
s=s.replace('int main(void)',(ROOT/'tests/scanout_registration.inc').read_text()+'\nint main(void)')
s=s.replace('    test_queued_flip_buffer();','    test_queued_flip_buffer();\n    test_registered_scanout_memory();\n    test_texture_write_within_frame();')
s=s.replace('    test_surface_set();','    test_guest_flip_trace_scope();\n    test_surface_set();')
s+='\nvoid tb_frame_order(uint32_t b,uint32_t o,uint64_t s,uint32_t p){(void)b;(void)o;(void)s;(void)p;}\nvoid tb_scanout_sample(uint32_t b,uint32_t s,uint32_t o){(void)b;(void)s;(void)o;}\nvoid tb_rsx_texture_capture(uint32_t a,uint32_t b,uint32_t c,uint32_t d,uint32_t e,uint32_t f,const void* g,uint32_t h,const void* i,uint32_t j,uint32_t k){(void)a;(void)b;(void)c;(void)d;(void)e;(void)f;(void)g;(void)h;(void)i;(void)j;(void)k;}\n'
s+='\nvoid tb_fps_guest_flip(void){}\n'
(out/'fixture.c').write_text(s)
sources=[out/'fixture.c',out/'overlay/libs/video/rsx_draw_engine.c',*[sdk/'libs/video'/name for name in ['rsx_dispatch.c','rsx_vertex_compact.c','rsx_texture_layout.c','rsx_vp_decompiler.c','rsx_fp_decompiler.c']]]
subprocess.run(['cc','-std=gnu17','-I'+str(ROOT),'-I'+str(sdk/'include'),'-I'+str(sdk/'libs/video'),*[str(p) for p in sources],'-lm','-o',str(out/'fixture')],check=True)
env={k:v for k,v in os.environ.items() if not k.startswith(('GCM_','TB_','RSX_'))}
subprocess.run([str(out/'fixture')],env=env,check=True,timeout=20)

legacy=subprocess.run([str(out/'fixture')],env=dict(env,GCM_SCANOUT_SURFACES='0'),capture_output=True,text=True,timeout=20)
(out/'legacy.log').write_text(legacy.stdout+legacy.stderr)
assert legacy.returncode and '[FAIL] scanout-only flip' in legacy.stdout
print('PASS: legacy fallback reproduces wrong scanout selection')
with tempfile.TemporaryDirectory(prefix='rsx-trace-') as directory:
    trace=Path(directory);(trace/'request').write_text('fixture\n')
    subprocess.run([str(out/'fixture')],env=dict(env,TB_RSX_TRACE=directory),check=True,timeout=20,stdout=subprocess.DEVNULL)
    assert len(list(trace.glob('*.done')))==3
    for i in range(3):
        manifest=native_manifest(trace/f'fixture-{i:03d}')
        assert len(manifest['draws'])==2, manifest
    print('PASS: actual-engine trace records three guest frames; extra host refreshes do not split draws')
