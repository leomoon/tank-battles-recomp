/* Differential strategy adapted from GoW-Ascension-Recomp's
 * ps3kit/libs/video/tests/test_rsx_tex_decode.c at 55e277c.
 * MIT attribution is retained in licenses/. Oracle is this port's untouched
 * pinned decoder body, not a rewritten approximation of its output. */
#include "texture_decode.h"
#include <algorithm>
#include <cassert>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <cstdlib>
#include <thread>
#include <vector>
#include <array>
#include <string>
static uint32_t seed=0x1a4d732b;
static u8 random_byte(){seed^=seed<<13;seed^=seed>>17;seed^=seed<<5;return u8(seed);}
static unsigned cases;
static void check(u32 format,u32 w,u32 h,u32 padding=0,bool all16=false) {
 rsx_tex_layout tl{};rsx_texture_layout(format,w,h,&tl);
 rsx_texture_layout_pitched(format,w,h,tl.row_bytes+padding,&tl);
 const u32 pitch=tl.dst_row_bytes+padding+3;
 std::vector<u8> input(33+tl.face_bytes+64);for(auto& b:input)b=random_byte();
 if(all16)for(u32 y=0;y<h;y++)for(u32 x=0;x<w;x++) {
  const size_t off=tl.swizzled?size_t(y*w+x)*2:size_t(y)*tl.row_bytes+x*2;
  const u32 v=y*w+x;input[33+off]=u8(v>>8);input[34+off]=u8(v);
 }
 const auto untouched=input;
 for(int argb:{0,1}) {
  std::vector<u8> a(19+size_t(pitch)*tl.rows+64,0xa5),b=a,c=a;
  tb_texture_decode_reference(a.data()+19,pitch,input.data()+33,w,h,&tl,argb);
  rsx_texture_decode(b.data()+19,pitch,input.data()+33,w,h,&tl,argb);
  if(!tb_texture_decode_specialized(c.data()+19,pitch,input.data()+33,w,h,&tl,argb))tb_texture_decode_reference(c.data()+19,pitch,input.data()+33,w,h,&tl,argb);
  if(a!=b || a!=c){std::fprintf(stderr,"Mismatch format=%x %ux%u swizzle=%d pitch=%u argb=%d\n",format,w,h,tl.swizzled,pitch,argb);std::abort();}
  assert(input==untouched);++cases;
 }
}
static void concurrency() {
 // Simultaneous first-use LUT initialization, with every packed value covered.
 std::array<std::thread,8> workers;
 for(unsigned t=0;t<workers.size();t++)workers[t]=std::thread([t]{
  const u32 formats[]={0x82,0x83,0x84,0x97,0x9d};rsx_tex_layout tl{};rsx_texture_layout(formats[t%5]|0x20,256,256,&tl);
  std::vector<u8> input(131072),a(262144),b(262144);
  for(u32 i=0;i<65536;i++){input[i*2]=u8(i>>8);input[i*2+1]=u8(i);}
  tb_texture_decode_reference(a.data(),1024,input.data(),256,256,&tl,0);
  assert(tb_texture_decode_specialized(b.data(),1024,input.data(),256,256,&tl,0));assert(a==b);
 });
 for(auto& t:workers)t.join();
}
static void alias_and_fallback() {
 rsx_tex_layout tl{};rsx_texture_layout(0xb2,16,2,&tl);
 for(unsigned offset:{0u,1u,3u,8u}) {
  std::vector<u8> a(512),b;for(auto& x:a)x=random_byte();b=a;
  tb_texture_decode_reference(a.data()+17+offset,32,a.data()+17,16,2,&tl,0);
  assert(!tb_texture_decode_specialized(b.data()+17+offset,32,b.data()+17,16,2,&tl,0));
  rsx_texture_decode(b.data()+17+offset,32,b.data()+17,16,2,&tl,0);assert(a==b);
 }
 std::vector<u8> data(256,7);auto original=data;
 assert(!tb_texture_decode_specialized(nullptr,32,data.data(),16,2,&tl,0));
 rsx_texture_decode(nullptr,32,data.data(),16,2,&tl,0);
 assert(!tb_texture_decode_specialized(data.data(),32,nullptr,16,2,&tl,0));
 assert(!tb_texture_decode_specialized(data.data(),32,data.data(),0,2,&tl,0));
 assert(!tb_texture_decode_specialized(data.data(),32,data.data(),16,2,nullptr,0));assert(data==original);
}
static void benchmark() {
 rsx_tex_layout tl{};rsx_texture_layout(0xb2,2048,2048,&tl);
 std::vector<u8> src(tl.face_bytes),a(tl.face_bytes),b(tl.face_bytes);for(auto& x:src)x=random_byte();
 std::vector<double> ref,fast;
 for(int pair=0;pair<7;pair++)for(int order=0;order<2;order++) {
  const bool optimized=(pair+order)%2;const auto begin=std::chrono::steady_clock::now();
  for(int i=0;i<8;i++) {
   if(optimized){assert(tb_texture_decode_specialized(b.data(),4096,src.data(),2048,2048,&tl,0));}
   else tb_texture_decode_reference(a.data(),4096,src.data(),2048,2048,&tl,0);
  }
  const double ns=std::chrono::duration<double,std::nano>(std::chrono::steady_clock::now()-begin).count()/8;
  (optimized?fast:ref).push_back(ns);
 }
 assert(a==b);std::sort(ref.begin(),ref.end());std::sort(fast.begin(),fast.end());
 std::printf("{\"format\":146,\"width\":2048,\"height\":2048,\"reference_ns\":%.1f,\"specialized_ns\":%.1f,\"speedup\":%.3f}\n",ref[3],fast[3],ref[3]/fast[3]);
}
int main(int argc,char** argv) {
 if(argc>1 && std::string(argv[1])=="--benchmark"){benchmark();return 0;}
 concurrency();
 const u32 formats[]={0x81,0x82,0x83,0x84,0x85,0x86,0x87,0x88,0x8b,0x8c,0x8d,0x90,0x91,0x92,0x94,0x95,0x97,0x9a,0x9b,0x9c,0x9d,0x9e,0x9f};
 const std::pair<u32,u32> sizes[]={{1,1},{1,16},{16,1},{2,8},{8,32},{13,9},{127,65},{256,128}};
 for(auto f:formats)for(auto [w,h]:sizes)for(u32 flags:{0u,0x20u})for(u32 pad:{0u,7u})check(f|flags,w,h,pad);
 for(auto f:{0x82u,0x83u,0x84u,0x97u,0x9du,0x92u,0x94u})for(u32 flags:{0u,0x20u})check(f|flags,256,256,0,true);
 for(auto f:{0x85u,0x92u,0x9au,0x9bu}) {
  rsx_tex_level levels[RSX_MAX_TEXTURE_LEVELS];const u32 n=rsx_texture_mip_chain(f,128,64,14,0,levels);
  for(unsigned face=0;face<6;face++)for(u32 i=0;i<n;i++)check(f,levels[i].w,levels[i].h);
 }
 alias_and_fallback();std::printf("PASS: %u differential cases, packed-value sweep, native formats, padded/unaligned buffers, mip/cube levels, concurrent initialization and aliases\n",cases);return 0;
}
