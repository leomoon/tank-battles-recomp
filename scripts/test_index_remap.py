#!/usr/bin/env python3
"""Compare original index-remap results with bounded exact-input reuse."""
import os,subprocess
from pathlib import Path
from port import ROOT
out=ROOT/'build-tests/index-remap';out.mkdir(parents=True,exist_ok=True);sdk=ROOT/'external/ps3recomp'
s=r'''
#include "rsx_vertex_compact.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "sdk_overrides/depth_cost_trace.h"
static struct { u32 frames; } g;
#include "sdk_overrides/index_remap_cache.inc"
static rsx_vertex_remap right;
static void compare_case(u32 count,u32 salt) {
 rsx_vertex_ref* source=malloc((size_t)count*8);rsx_vertex_ref* a=malloc((size_t)count*8);rsx_vertex_ref* b=malloc((size_t)count*8);
 for(u32 i=0;i<count;i++)source[i]=(rsx_vertex_ref){(i*17u+salt)%(count/3+1),(i%7==0)?salt:0};
 rsx_vertex_remap left={0};u32 na=count,nb=count;
 for(unsigned repeat=0;repeat<3;repeat++) {
  memcpy(a,source,(size_t)count*8);memcpy(b,source,(size_t)count*8);
  assert(rsx_vertex_remap_build(&left,a,count,&na));assert(tb_remap_build(&right,b,count,&nb));
  assert(na==nb && !memcmp(a,b,(size_t)na*8));
  assert(!memcmp(left.occurrence_to_unique,right.occurrence_to_unique,(size_t)count*4));
 }
 rsx_vertex_remap_destroy(&left);free(source);free(a);free(b);
}
int main(void){
 setenv("TB_DRAW_REMAP_CACHE","1",1);setenv("TB_DRAW_REMAP_VERIFY","1",1);
 for(u32 n=2;n<1024;n=n*2+1) {compare_case(n,1);compare_case(n,2);compare_case(n,3);compare_case(n,1);}
 compare_case(65536,1);compare_case(65537,1);tb_remap_reset();compare_case(300,11);right.generation=UINT32_MAX;compare_case(300,12);tb_remap_reset();rsx_vertex_remap_destroy(&right);
 rsx_vertex_remap r={0};u32 n=3;assert(!tb_remap_build(NULL,NULL,3,&n));assert(tb_remap_build(&r,NULL,0,&n));assert(n==0);rsx_vertex_remap_destroy(&r);
 puts("PASS: exact remap bytes/order/base indices, repeated hits, changes, eviction, bounded fallback, fresh verification and reset");
}
'''
(out/'fixture.c').write_text(s)
subprocess.run(['cc','-std=gnu17','-O2','-ffunction-sections','-fdata-sections','-I'+str(ROOT),'-I'+str(sdk/'include'),'-I'+str(sdk/'libs/video'),str(out/'fixture.c'),str(sdk/'libs/video/rsx_vertex_compact.c'),'-Wl,--gc-sections','-o',str(out/'fixture')],check=True)
subprocess.run([str(out/'fixture')],check=True)
