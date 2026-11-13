#!/usr/bin/env python3
"""Test the generated input source with SDL virtual devices and keyboard fixtures."""
import os
import shlex
import subprocess
import sys
from port import ROOT

out=ROOT/'build-tests/input';out.mkdir(parents=True,exist_ok=True)
sdk=ROOT/'external/ps3recomp'
subprocess.run([sys.executable,str(ROOT/'scripts/instrument_sdk.py'),'--sdk',str(sdk),'--output',str(out/'overlay')],check=True)
(out/'cellPad_under_test.c').write_text((out/'overlay/libs/input/cellPad.c').read_text())
flags=shlex.split(subprocess.check_output(['pkg-config','--cflags','--libs','sdl2'],text=True))
env={k:v for k,v in os.environ.items() if not k.startswith(('SDL_','PAD_','TB_'))}
env.update(SDL_VIDEODRIVER='dummy',SDL_AUDIODRIVER='dummy',SDL_JOYSTICK_HIDAPI='0',SDL_GAMECONTROLLER_IGNORE_DEVICES_EXCEPT='0x0000/0x0000')
subprocess.run(['c++','-std=c++17','-I'+str(ROOT),*flags,'-c',str(ROOT/'input_events.cpp'),'-o',str(out/'events.o')],check=True)
for name,source in [('positions',(ROOT/'tests/input_positions.c').read_text()),('keyboard',(sdk/'libs/input/tests/test_pad_keyboard.c').read_text().replace('#include "../cellPad.c"','#include "cellPad_under_test.c"'))]:
    if name=='keyboard': source += '\nvoid tb_input_sample(uint32_t k,uint32_t p,uint32_t b) {(void)k;(void)p;(void)b;}\n'
    if name=='keyboard':
        source=source.replace('    /* Several keys at once. */', '    /* A complete down/up before the next poll must survive one report. */\n    keys(0,0,0);\n    tb_keyboard_down(SDL_SCANCODE_Z);\n    pad_poll_backend();usleep(20000);pad_poll_backend();\n    assert(tb_keyboard_peek()==CELL_PAD_CTRL_CROSS);\n    assert(D2()==CROSS);\n    assert(D2()==0);\n    tb_keyboard_down(SDL_SCANCODE_DOWN);\n    assert(D1()==DOWN);\n    assert(D1()==0);\n    s_focus=0;tb_keyboard_down(SDL_SCANCODE_Z);\n    assert(D2()==0);s_focus=1;assert(D2()==0);\n    /* Several keys at once. */')
    c=out/(name+'.c');c.write_text(source)
    exe=out/name
    subprocess.run(['cc','-std=gnu17','-O2','-ffunction-sections','-fdata-sections','-Wl,--gc-sections','-I'+str(ROOT),'-I'+str(sdk/'include'),str(c),str(out/'events.o'),*flags,'-pthread','-lm','-lstdc++','-o',str(exe)],check=True)
    result=subprocess.run([str(exe)],env=env,capture_output=True,text=True,timeout=20)
    (out/(name+'.log')).write_text(result.stdout+result.stderr)
    print(result.stdout+result.stderr,end='')
    if result.returncode:raise SystemExit(result.returncode)
