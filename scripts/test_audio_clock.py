#!/usr/bin/env python3
"""Exercise the real Linux mixer with an SDL dummy device and with no device."""
import os
from pathlib import Path
import subprocess
import sys
from port import ROOT
sdk=ROOT/'external/ps3recomp';out=ROOT/'build-tests/audio-clock';out.mkdir(parents=True,exist_ok=True)
subprocess.run([sys.executable,str(ROOT/'scripts/instrument_sdk.py'),'--sdk',str(sdk),'--output',str(out/'overlay')],check=True)
generated=out/'overlay/libs/audio/cellAudio.c'
source=generated if generated.exists() else sdk/'libs/audio/cellAudio.c'
s=source.read_text()
if source==sdk/'libs/audio/cellAudio.c':
 import re
 s=re.sub(r'#include "([^"\n]+)"',lambda m:'#include "'+str((source.parent/m[1]).resolve())+'"' if (source.parent/m[1]).is_file() else m[0],s)
s='#include <stdatomic.h>\nstatic atomic_uint observed_blocks;\n'+s
s=s.replace('static void audio_notify_event_queues(void)\n{','static void audio_notify_event_queues(void)\n{\n    atomic_fetch_add(&observed_blocks,1);')
s+=r'''
#include <assert.h>
uint8_t* vm_base;uint32_t ppu_vm_size;
int g_resv_store_active;uint32_t g_ww_lo,g_ww_hi;
void ppu_resv_break_store(uint64_t a){(void)a;}
void ps3_ww_report_inline(uint32_t a,uint64_t b,int c){(void)a;(void)b;(void)c;}
void spu_lockline_lock(void){}void spu_lockline_unlock(void){}
void spu_coh_notify_write(uint32_t a){(void)a;}
int sys_event_queue_push_by_id(uint32_t a,uint64_t b,uint64_t c,uint64_t d,uint64_t e){return 0;}
uint32_t sys_event_find_queue_by_key(uint64_t k){return 0;}
int main(int argc,char** argv){
 assert(cellAudioInit()==0);
 struct timespec begin,end;clock_gettime(CLOCK_MONOTONIC,&begin);
 unsigned max_queue=0;
 for(int i=0;i<1600;i++){
  unsigned q=audio_backend_queued_samples();if(q>max_queue)max_queue=q;
  usleep(1000);
 }
 unsigned blocks=atomic_load(&observed_blocks);
 clock_gettime(CLOCK_MONOTONIC,&end);
 double seconds=(end.tv_sec-begin.tv_sec)+(end.tv_nsec-begin.tv_nsec)/1e9;
 cellAudioQuit();
 printf("mixed=%u blocks_per_second=%.2f max_queued_frames=%u latency_ms=%.1f\n",blocks,blocks/seconds,max_queue,max_queue*1000.0/48000);
 if(max_queue>1024)return 1; // Four blocks, never an increasing backlog.
 if(argc>1 && fabs(blocks-seconds*187.5)>8)return 2;
 return 0;
}
'''
(out/'fixture.c').write_text(s)
subprocess.run(['cc','-std=gnu17','-O1','-ffunction-sections','-fdata-sections','-I'+str(ROOT),'-I'+str(sdk/'include'),'-I'+str(sdk/'libs/audio'),str(out/'fixture.c'),'-Wl,--gc-sections','-pthread','-lSDL2','-lm','-o',str(out/'fixture')],check=True)
env={k:v for k,v in os.environ.items() if not k.startswith(('AUDIO_','SDL_AUDIO','PS3_NO_AUDIO','TB_'))}
subprocess.run([str(out/'fixture')],env=dict(env,SDL_AUDIODRIVER='dummy'),check=True,timeout=10)
subprocess.run([str(out/'fixture'),'no-device'],env=dict(env,SDL_AUDIODRIVER='nonexistent-test-driver'),check=True,timeout=10)
