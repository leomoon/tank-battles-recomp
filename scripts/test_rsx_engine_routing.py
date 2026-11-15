#!/usr/bin/env python3
"""Reproduce observed 2D-to-3D corruption through the real GCM FIFO walker."""
import os
from pathlib import Path
import subprocess
import sys
from port import ROOT
sdk=ROOT/'external/ps3recomp';out=ROOT/'build-tests/rsx-routing';out.mkdir(parents=True,exist_ok=True)
subprocess.run([sys.executable,str(ROOT/'scripts/instrument_sdk.py'),'--sdk',str(sdk),'--output',str(out/'overlay')],check=True)
s=(sdk/'libs/video/tests/test_gcm_walker_prof.c').read_text()
s=s[:s.index('int main(int argc, char** argv)')]
s=s.replace('#include "../cellGcmSys.c"','#include "'+str(out/'overlay/libs/video/cellGcmSys.c')+'"').replace('#include "../rsx_tiling.c"','#include "'+str(sdk/'libs/video/rsx_tiling.c')+'"')
s=s.replace('int  rsx_draw_engine_enabled(void) { return 0; }','int  rsx_draw_engine_enabled(void) { return 1; }')
s=s.replace('void rsx_draw_engine_method(u32 method, u32 arg) { (void)method; (void)arg; }',
    '#include "rsx_dispatch.h"\nstatic rsx_dispatch captured;\nvoid rsx_draw_engine_method(u32 method,u32 arg){rsx_dispatch_method(&captured,method&0x1ffc,arg);}')
s=s.replace('int  rsx_process_method(rsx_state* state, u32 method, u32 data) { (void)state; (void)method; (void)data; return 0; }',
    'static unsigned flushes, expected_publication_flush;\nint rsx_process_method(rsx_state* state,u32 method,u32 data){(void)state;(void)data;if((method==0x1D70||method==0x1D74)&&expected_publication_flush&&flushes!=expected_publication_flush){fprintf(stderr,"label published before flush\\n");abort();}return 0;}')
s+=r'''
#include <assert.h>
void tb_rsx_memory_written(u32 l,u32 o,u32 n){(void)l;(void)o;(void)n;}
void tb_rsx_transfer_trace(u32 s,u32 m,u32 v){(void)s;(void)m;(void)v;}
void tb_frame_event(u32 k,u32 a,u32 b,u32 c,u32 d){(void)k;(void)a;(void)b;(void)c;(void)d;}
static u32 before_label;static int before_report;static u64 report_stamp;
void rsx_draw_engine_flush(void){if(before_label)assert(vm_read32(before_label)==0);if(before_report)assert(s_report_data[0].timestamp==report_stamp);flushes++;}
BOOL SetEvent(HANDLE h){(void)h;return 1;}
int main(int argc,char** argv){
 ppu_vm_size=ppu_hle_inject_base+0x10000u;vm_base=calloc(1,ppu_vm_size);assert(vm_base);
 rsx_dispatch_init(&captured,NULL);
 u32 ctx_out=host_alloc(4,4);
 cellGcmSetupContext(ctx_out,CMD_SIZE,IO_SIZE,IO_ADDR,host_alloc,host_w32);
 if(argc>1 && !strcmp(argv[1],"consumers")){
  u32 old=flushes;
  for(u32 m=0x1D70;m<=0x1D74;m+=4){expected_publication_flush=old+1;gcm_3d_method(NULL,m,0,0);check(flushes==++old,"back-end label waits before publication");}
  const u64 stamp=s_report_data[0].timestamp;before_report=1;report_stamp=stamp;
  gcm_3d_method(NULL,0x1800,0,0);check(flushes==++old&&s_report_data[0].timestamp>stamp,"GET_REPORT waits before publication");before_report=0;
  /* Feed the real walker an OFFSET + RELEASE pair. Flush observes old label. */
  s_fifo_getoff=0x1800;const u32 label=GCM_LABEL_GUEST_BASE+0x20;
  vm_write32(label,0);before_label=label;u32 packet[]={0x00040064,0x20,0x0004006c,0x1234};
  for(u32 i=0;i<4;i++)host_w32(IO_ADDR+0x1800+i*4,packet[i]);
  vm_write32(GCM_CONTROL_GUEST_ADDR,0x1800+sizeof packet);cellGcm_rsx_process_fifo();
  check(flushes==++old&&vm_read32(label)==0x1234,"FIFO semaphore release completes pending draws");before_label=0;
  gcm_3d_method(NULL,0x50,0,0);check(flushes==++old,"SET_REFERENCE conservatively completes earlier draws");
  return fails?1:0;
 }
 if(argc>1 && !strcmp(argv[1],"near-end")){
  u32 ctx=vm_read32(ctx_out),cur=IO_ADDR+0x1000;
  s_seg_size=0;s_display_buffer_set[0]=1;
  vm_write32(ctx+8,cur);vm_write32(ctx+4,cur+32);
  check(_cellGcmSetFlipCommand((void*)(uintptr_t)ctx,0)==0,"near-end flip accepts valid command space");
  check(s_flip_request_count==0,"near-end flip stays queued instead of presenting early");
  check(vm_read32(cur)==GCM_FLIP_MARKER,"near-end flip marker follows existing GPU work");
  return fails?1:0;
 }
 if(argc>1){
  u32 packet[]={0x00100300,1,0,0x204,0};
  s_fifo_getoff=0x1000;
  for(u32 i=0;i<5;i++)host_w32(IO_ADDR+0x1000+i*4,packet[i]);
  vm_write32(GCM_CONTROL_GUEST_ADDR,0x100c); // Header plus only two published arguments.
  for(int repeat=0;repeat<12;repeat++)cellGcm_rsx_process_fifo();
  check(s_fifo_getoff==0x1000,"incomplete FIFO method waits at its header");
  if(fails)return 1;
  vm_write32(GCM_CONTROL_GUEST_ADDR,0x1014);
  cellGcm_rsx_process_fifo();
  check(s_fifo_getoff==0x1014&&rsx_dsp_reg(&captured,0x308)==0x204,"published tail completes the method exactly once");
  return fails?1:0;
 }
 u32 stream[]={
  0x00040300,1,0x00040304,0,0x00040308,0x204,0x0004030c,0,
  0x00040314,0x10001,0x00040318,0,
  0x00046300,11,0x00046304,0x10001000,0x0004630c,0x4500,
  0x0004a304,8,0x0004a308,0x10004,0x0004a30c,0x10004,
  0x00040324,0x01010101
 };
 s_fifo_getoff=0x1000;
 for(u32 i=0;i<sizeof stream/sizeof stream[0];i++)host_w32(IO_ADDR+0x1000+i*4,stream[i]);
 vm_write32(GCM_CONTROL_GUEST_ADDR,0x1000+sizeof stream);
 cellGcm_rsx_process_fifo();
 check(rsx_dsp_reg(&captured,0x300)==1,"2D color format does not replace 3D dither");
 check(rsx_dsp_reg(&captured,0x304)==0,"2D pitch does not enable alpha test");
 check(rsx_dsp_reg(&captured,0x308)==0x204,"2D input dimensions do not replace alpha function");
 check(rsx_dsp_reg(&captured,0x30c)==0,"2D output dimensions do not replace alpha reference");
 check(rsx_dsp_reg(&captured,0x314)==0x10001&&rsx_dsp_reg(&captured,0x318)==0,"blend state survives 2D commands");
 check(rsx_dsp_reg(&captured,0x324)==0x01010101,"subsequent real 3D command still dispatches");
 check(s_gcm2d.color_fmt==11&&s_gcm2d.dst_offset==0x4500,"2D commands still reach the 2D engine");
 return fails?1:0;
}
'''
(out/'fixture.c').write_text(s)
subprocess.run(['cc','-std=gnu17','-O1','-ffunction-sections','-fdata-sections','-I'+str(ROOT),'-I'+str(sdk/'include'),'-I'+str(sdk/'libs/video'),str(out/'fixture.c'),str(sdk/'libs/video/rsx_dispatch.c'),'-Wl,--gc-sections','-pthread','-lm','-o',str(out/'fixture')],check=True)
env={k:v for k,v in os.environ.items() if not k.startswith(('GCM_','TB_','RSX_','PS3_'))}
subprocess.run([str(out/'fixture')],env=env,check=True,timeout=10)
subprocess.run([str(out/'fixture'),'partial'],env=env,check=True,timeout=10)
subprocess.run([str(out/'fixture'),'near-end'],env=env,check=True,timeout=10)

subprocess.run([str(out/'fixture'),'consumers'],env=env,check=True,timeout=10)
