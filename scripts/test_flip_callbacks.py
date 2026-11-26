#!/usr/bin/env python3
"""Exercise actual generated GCM flip delivery without game files or a GPU."""
import os
import subprocess
import sys
from port import ROOT
out=ROOT/'build-tests/flip-callbacks';out.mkdir(parents=True,exist_ok=True)
sdk=ROOT/'external/ps3recomp'
subprocess.run([sys.executable,str(ROOT/'scripts/instrument_sdk.py'),'--sdk',str(sdk),'--output',str(out/'overlay')],check=True)
source=(sdk/'libs/video/tests/test_gcm_walker_prof.c').read_text()
source=source[:source.index('int main(int argc, char** argv)')]
source=source.replace('#include "../cellGcmSys.c"','#include "'+str(out/'overlay/libs/video/cellGcmSys.c')+'"').replace('#include "../rsx_tiling.c"','#include "'+str(sdk/'libs/video/rsx_tiling.c')+'"')
source+=r'''
#include <assert.h>
void tb_rsx_memory_written(uint32_t l,uint32_t o,uint32_t n){(void)l;(void)o;(void)n;}
void tb_rsx_transfer_trace(uint32_t s,uint32_t m,uint32_t a){(void)s;(void)m;(void)a;}
void tb_frame_event(uint32_t k,uint32_t a,uint32_t b,uint32_t c,uint32_t d){(void)k;(void)a;(void)b;(void)c;(void)d;}
static unsigned flushes;
void rsx_draw_engine_flush(void){flushes++;}
BOOL SetEvent(HANDLE h) { (void)h; assert(0 && "Unexpected segmented-ring event"); return 0; }
static atomic_int calls;
static unsigned long callback_tid;
static void callback(u32 opd,uint64_t a,uint64_t b,uint64_t c,uint64_t d,uint64_t e,uint64_t f,uint64_t h,uint64_t i) {
    (void)a;(void)b;(void)c;(void)d;(void)e;(void)f;(void)h;(void)i;
    assert(opd==0x100); callback_tid=GetCurrentThreadId();atomic_fetch_add(&calls,1);
}
int main(int argc,char** argv) {
    ppu_vm_size=ppu_hle_inject_base+0x10000u;vm_base=calloc(1,ppu_vm_size);assert(vm_base);
    g_ps3_guest_caller=callback;s_flip_handler_opd=0x100;s_display_buffer_set[0]=1;
    unsigned long owner=GetCurrentThreadId();
    if(argc>1) {
        assert(cellGcmSetFlipCommand(0)==CELL_OK);
        assert(atomic_load(&calls)==1);
        cellGcmTickFlip();ppu_gcm_pump();
        assert(atomic_load(&calls)==2);
        puts("PASS: diagnostic legacy timing reproduces two callbacks for one flip");
        return 0;
    }
    (void)argv;
    for(int n=1;n<=3;n++) {
        assert(cellGcmSetFlipCommand(0)==CELL_OK);
        assert(atomic_load(&calls)==n-1); // Requesting is not completing.
        cellGcmTickFlip();ppu_gcm_pump();
        for(int wait=0;wait<2000 && atomic_load(&calls)<n;wait++)usleep(1000);
        assert(atomic_load(&calls)==n && callback_tid!=owner);
        for(int repeat=0;repeat<10;repeat++){cellGcmTickFlip();ppu_gcm_pump();usleep(1000);}
        assert(atomic_load(&calls)==n); // No extra completion while idle.
    }
    u32 ctx_out=host_alloc(4,4);
    cellGcmSetupContext(ctx_out,CMD_SIZE,IO_SIZE,IO_ADDR,host_alloc,host_w32);
    s_display_buffer_set[0]=1;s_flip_handler_opd=0x100;
    u32 ctx=vm_read32(ctx_out);
    assert(_cellGcmSetFlipCommand((void*)(uintptr_t)ctx,0)==0);
    cellGcmTickFlip();ppu_gcm_pump();usleep(20000);
    assert(atomic_load(&calls)==3); // Not flushed or executed yet.
    vm_write32(GCM_CONTROL_GUEST_ADDR,gcm_ea2io(vm_read32(ctx+8)));
    cellGcm_rsx_process_fifo();
    assert(s_fifo_flips_reached==s_fifo_flips_queued);
    cellGcmTickFlip();ppu_gcm_pump();
    for(int wait=0;wait<2000 && atomic_load(&calls)<4;wait++)usleep(1000);
    assert(atomic_load(&calls)==4);
    /* Model a title moving through its own command-buffer segments. Its
     * writer can publish current before updating end or PUT. The walker's
     * generic overflow fallback must not rewind those guest-owned fields. */
    const u32 runtime_callback=vm_read32(ctx+12), own_callback=host_alloc(8,4);
    vm_write32(own_callback,0x10000);vm_write32(own_callback+4,0);
    vm_write32(ctx+12,own_callback);
    const u32 begin=vm_read32(ctx),end=vm_read32(ctx+4),put=s_fifo_getoff;
    vm_write32(ctx+8,end+4);
    cellGcm_rsx_process_fifo();
    assert(vm_read32(ctx+8)==end+4);
    assert(vm_read32(GCM_CONTROL_GUEST_ADDR)==put && s_fifo_getoff==put);
    /* Having observed that transition must not arm later near-end rewinds. */
    vm_write32(ctx+8,end-8);
    assert(_cellGcmSetFlipCommand((void*)(uintptr_t)ctx,0)==0);
    cellGcm_rsx_process_fifo();
    assert(vm_read32(ctx+8)==end-4 && vm_read32(ctx)==begin);
    assert(vm_read32(GCM_CONTROL_GUEST_ADDR)==put && s_fifo_getoff==put);
    cellGcmTickFlip();ppu_gcm_pump();usleep(20000);
    assert(atomic_load(&calls)==4); // Still unpublished; no fabricated callback.
    vm_write32(GCM_CONTROL_GUEST_ADDR,gcm_ea2io(vm_read32(ctx+8)));
    cellGcm_rsx_process_fifo();cellGcmTickFlip();ppu_gcm_pump();
    for(int wait=0;wait<2000 && atomic_load(&calls)<5;wait++)usleep(1000);
    assert(atomic_load(&calls)==5 && s_fifo_flips_reached==s_fifo_flips_queued);
    vm_write32(ctx+8,begin);vm_write32(ctx+12,runtime_callback);
    puts("PASS: walker preserves title-owned context during segment transitions");
    vm_write32(ctx+8,end+4);
    cellGcm_rsx_process_fifo();
    assert(vm_read32(ctx+8)==begin && s_fifo_getoff==gcm_ea2io(begin));
    puts("PASS: runtime-owned buffer recycling is retained");
    puts("PASS: queued flip completes only after real FIFO execution");
    puts("PASS: no early callback, one completion per flip, dedicated delivery while caller waits, no idle callbacks");
    return 0;
}
'''
(out/'fixture.c').write_text(source)
subprocess.run(['cc','-std=gnu17','-O1','-ffunction-sections','-fdata-sections','-I'+str(ROOT),'-I'+str(sdk/'include'),'-I'+str(sdk/'libs/video'),str(out/'fixture.c'),'-Wl,--gc-sections','-pthread','-lm','-o',str(out/'fixture')],check=True)
env={k:v for k,v in os.environ.items() if not k.startswith(('GCM_','TB_','RSX_','PS3_'))}
subprocess.run([str(out/'fixture')],env=env,check=True,timeout=10)

subprocess.run([str(out/'fixture'),'legacy'],env=dict(env,GCM_FLIPCB_ONTICK='0',GCM_HANDLER_THREAD='0'),check=True,timeout=10)
