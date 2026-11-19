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
static void compare_oracle(rsx_vertex_remap* remap, const rsx_vertex_ref* source, u32 count) {
    rsx_vertex_ref* refs = malloc((size_t)count * sizeof *refs);
    rsx_vertex_ref* expected = malloc((size_t)count * sizeof *expected);
    u32* mapping = malloc((size_t)count * sizeof *mapping);
    assert(refs && expected && mapping);
    u32 expected_count = 0;
    for (u32 i = 0; i < count; i++) {
        u32 slot = 0;
        while (slot < expected_count &&
               (expected[slot].vertex_id != source[i].vertex_id ||
                expected[slot].base_index != source[i].base_index))
            slot++;
        if (slot == expected_count)
            expected[expected_count++] = source[i];
        mapping[i] = slot;
    }
    memcpy(refs, source, (size_t)count * sizeof *refs);
    u32 unique = count;
    assert(rsx_vertex_remap_build(remap, refs, count, &unique));
    assert(unique == expected_count);
    assert(!memcmp(refs, expected, (size_t)unique * sizeof *refs));
    assert(!memcmp(remap->occurrence_to_unique, mapping, (size_t)count * sizeof *mapping));
    free(mapping);
    free(expected);
    free(refs);
}
static void test_direct_and_hash(void) {
    rsx_vertex_remap remap = {0};
    rsx_vertex_ref source[1024] = {{0}};
    u32 random = 12345;
    for (u32 draw = 0; draw < 128; draw++) {
        u32 count = 1 + draw * 7 % 1024;
        for (u32 i = 0; i < count; i++) {
            random = random * 1664525u + 1013904223u;
            source[i].vertex_id = draw % 3 == 0 ? random : UINT32_MAX - (random % count);
            source[i].base_index = draw % 2 == 0 ? draw : i % 3;
        }
        compare_oracle(&remap, source, count);
    }
    remap.generation = UINT32_MAX;
    compare_oracle(&remap, source, 1024);
    rsx_vertex_remap_destroy(&remap);
}
int main(void){
 test_direct_and_hash();
 setenv("TB_DRAW_REMAP_CACHE","1",1);setenv("TB_DRAW_REMAP_VERIFY","1",1);
 for(u32 n=2;n<1024;n=n*2+1) {compare_case(n,1);compare_case(n,2);compare_case(n,3);compare_case(n,1);}
 compare_case(65536,1);compare_case(65537,1);tb_remap_reset();compare_case(300,11);right.generation=UINT32_MAX;compare_case(300,12);tb_remap_reset();rsx_vertex_remap_destroy(&right);
 rsx_vertex_remap r={0};u32 n=3;assert(!tb_remap_build(NULL,NULL,3,&n));assert(tb_remap_build(&r,NULL,0,&n));assert(n==0);rsx_vertex_remap_destroy(&r);
 puts("PASS: exact remap bytes/order/base indices, repeated hits, changes, eviction, bounded fallback, fresh verification and reset");
}
'''
(out/'fixture.c').write_text(s)
subprocess.run(['cc','-std=gnu17','-O2','-fsanitize=address,undefined','-fno-sanitize-recover=all','-ffunction-sections','-fdata-sections','-I'+str(ROOT),'-I'+str(sdk/'include'),'-I'+str(sdk/'libs/video'),str(out/'fixture.c'),str(sdk/'libs/video/rsx_vertex_compact.c'),'-Wl,--gc-sections','-o',str(out/'fixture')],check=True)
subprocess.run([str(out/'fixture')],check=True,env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0'))
