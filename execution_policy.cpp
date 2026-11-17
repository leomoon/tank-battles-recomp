#include "execution_policy_api.h"
#include "ppu_recomp.h"
#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include "host_services.h"
static_assert(std::atomic<uint32_t>::is_always_lock_free);
static_assert(std::atomic<uint64_t>::is_always_lock_free);
static std::atomic<uint32_t> counts[8];
static std::atomic<uint64_t> last_target[8];
static std::atomic<uint64_t> images[64];
static const char* labels[]={"ppu-indirect","ppu-miss","hle-missing","spu-interpreter","spu-untranslated","spu-indirect","raw-spu-image","ppu-recovery"};
static bool strict_mode(){const char* value=getenv("TB_EXECUTION_MODE");return !value || strcmp(value,"diagnostic");}
// Kinds 0 (PPU indirect call) and 5 (SPU indirect branch) occur on every guest
// indirect transfer. They are informational, so each thread accumulates them
// locally and publishes the count and its last target to the shared report
// every 1024 events with relaxed atomics: no locked instruction or shared
// cache-line write per call. The first 16 of each kind are still logged as
// they happen, and every strict-stop kind keeps the exact global path below.
// A report taken from another thread may miss each thread's unpublished
// remainder (under 1024 per thread) for these two kinds only.
namespace {
struct alignas(64) HotShared { std::atomic<uint32_t> logged{0}; };
HotShared hot_logged[2];
struct HotLocal { uint32_t pending=0; uint64_t last=0; };
thread_local HotLocal hot_local[2];
inline void publish_hot(unsigned kind,unsigned slot){
 HotLocal& local=hot_local[slot];
 counts[kind].fetch_add(local.pending,std::memory_order_relaxed);
 last_target[kind].store(local.last,std::memory_order_relaxed);
 local.pending=0;
}
}
static void publish_current_thread(){
 for(unsigned slot=0;slot<2;++slot)if(hot_local[slot].pending)publish_hot(slot?5:0,slot);
}
extern "C" void tb_execution_event(unsigned kind,uint32_t address,uint32_t image,uint32_t detail){
 if(kind>=8)return;
 if(kind==0 || kind==5){
  const unsigned slot=kind==5;
  HotLocal& local=hot_local[slot];
  local.last=(uint64_t(image)<<32)|address;
  ++local.pending;
  if(kind==5){
   const uint64_t key=(uint64_t(image)<<32)|detail;
   static thread_local uint64_t previous=0;
   if(key && key!=previous){
    bool recorded=false;
    for(auto& entry:images){auto value=entry.load();if(value==key){recorded=true;break;}
     if(!value && entry.compare_exchange_strong(value,key)){recorded=true;fprintf(stderr,"[execution-image] image=0x%08X overlay=0x%08X\n",image,detail);break;}}
    if(!recorded){fprintf(stderr,"[execution] image inventory capacity exceeded\n");_exit(86);}
    previous=key;
   }
  }
  if(hot_logged[slot].logged.load(std::memory_order_relaxed)<16){
   const uint32_t n=hot_logged[slot].logged.fetch_add(1,std::memory_order_relaxed)+1;
   if(n<=16){publish_hot(kind,slot);fprintf(stderr,"[execution] %s address=0x%08X image=0x%08X detail=0x%08X count=%u\n",labels[kind],address,image,detail,n);}
  }
  if(local.pending>=1024)publish_hot(kind,slot);
  return;
 }
 last_target[kind].store((uint64_t(image)<<32)|address);
 if(kind==5 || kind==6){
  const uint64_t key=(uint64_t(image)<<32)|detail;
  static thread_local uint64_t previous=0;
  if(key && key!=previous){
   bool recorded=false;
   for(auto& slot:images){auto value=slot.load();if(value==key){recorded=true;break;}
    if(!value && slot.compare_exchange_strong(value,key)){recorded=true;fprintf(stderr,"[execution-image] image=0x%08X overlay=0x%08X\n",image,detail);break;}}
   if(!recorded){fprintf(stderr,"[execution] image inventory capacity exceeded\n");_exit(86);}
   previous=key;
  }
 }
 auto n=counts[kind].fetch_add(1)+1;
 if(n<=16)fprintf(stderr,"[execution] %s address=0x%08X image=0x%08X detail=0x%08X count=%u\n",labels[kind],address,image,detail,n);
 if((kind==1 || kind==2 || kind==3 || kind==4 || kind==7) && strict_mode()){
  fprintf(stderr,"[execution] STRICT STOP: %s\n",labels[kind]);
  tank_battles_execution_report();fflush(stderr);_exit(86);
 }
}
static void hex_line(uint64_t value){
 char text[20]="0x0000000000000000\n";const char* digits="0123456789ABCDEF";
 for(unsigned i=0;i<16;++i)text[17-i]=digits[(value>>(4*i))&15];
 tb_host_write(text,19);
}
void tank_battles_execution_report(){
 publish_current_thread();
 for(unsigned k=0;k<8;++k){
  const char* prefix="[execution-count] ";tb_host_write(prefix,18);size_t length=0;while(labels[k][length])++length;tb_host_write(labels[k],length);
  char number[13]="=0x00000000\n";const char* digits="0123456789ABCDEF";auto value=counts[k].load();
  for(unsigned i=0;i<8;++i)number[10-i]=digits[(value>>(4*i))&15];
  tb_host_write(number,12);
  const char* last="[execution-last image:address] ";size_t size=0;while(last[size])++size;tb_host_write(last,size);
  tb_host_write(labels[k],length);tb_host_write("=",1);hex_line(last_target[k].load());
 }
 for(auto& slot:images)if(auto value=slot.load()){tb_host_write("[execution-image-pair] ",23);hex_line(value);}
}
extern "C" void ps3_hle_call(uint32_t,ppu_context*);
extern "C" void ps3_indirect_call(ppu_context*);
extern "C" void tb_spu_execution_probe(unsigned);
int tb_conversion_probe();
int tank_battles_execution_probe(){
 const char* mode=getenv("TB_EXECUTION_PROBE");
 ppu_context ctx{};ctx.thread_id=59;ctx.gpr[1]=0xD00FF000;ctx.lr=0x10200;
 if(!strcmp(mode,"conversion"))return tb_conversion_probe();
 if(!strcmp(mode,"import"))ps3_hle_call(0xFEFEFEFE,&ctx);
 else if(!strcmp(mode,"null-target")){ctx.ctr=0;ps3_indirect_call(&ctx);}
 else if(!strcmp(mode,"target")){ctx.ctr=0x12345678;ps3_indirect_call(&ctx);}
 else if(!strcmp(mode,"interpreter"))tb_spu_execution_probe(0);
 else if(!strcmp(mode,"microstep"))tb_spu_execution_probe(1);
 else return 2;
 tank_battles_execution_report();return 0;
}
