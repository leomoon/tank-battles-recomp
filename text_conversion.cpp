#include "ppu_recomp.h"
#include "stubs.h"
#include <cerrno>
#include <cstdio>
#include <iconv.h>
#include <atomic>
static const char* encoding(unsigned code){
 static const char* names[]={"UTF-8","UTF-16BE","UTF-32BE","UCS-2BE","UCS-4BE","ISO-8859-1"};
 return code<6?names[code]:nullptr;
}
static bool range(uint64_t ea,uint64_t size){return ea>=0x10000 && size<=0x1000000 && ea+size<0xe0000000ull;}
static void convert(ppu_context* c){
 auto source=encoding(c->gpr[3]),destination=encoding(c->gpr[6]);
 if(!source||!destination){c->gpr[3]=3;return;}
 const auto sl=c->gpr[5],dl=c->gpr[8];
 if(!range(sl,4)||!range(dl,4)){c->gpr[3]=1;return;}
 size_t remaining=vm_read32(sl),capacity=vm_read32(dl);
 if((remaining && !range(c->gpr[4],remaining)) || (c->gpr[7] && !range(c->gpr[7],capacity))){c->gpr[3]=1;return;}
 iconv_t codec=iconv_open(destination,source);if(codec==(iconv_t)-1){c->gpr[3]=3;return;}
 char* input=(char*)vm_base+(uint32_t)c->gpr[4];size_t produced=0;unsigned result=0;
 if(c->gpr[7]){
  char* output=(char*)vm_base+(uint32_t)c->gpr[7];size_t available=capacity;
  if(iconv(codec,&input,&remaining,&output,&available)==(size_t)-1)result=errno==E2BIG?2:1;
  produced=capacity-available;
 }else{
  do{char scratch[256];char* output=scratch;size_t available=sizeof scratch;
   size_t status=iconv(codec,&input,&remaining,&output,&available);produced+=sizeof scratch-available;
   if(status==(size_t)-1 && errno!=E2BIG){result=1;break;}
  }while(remaining);
 }
 iconv_close(codec);vm_write32(dl,produced);c->gpr[3]=result;
 static std::atomic<unsigned> calls{0};if(calls.fetch_add(1)<8)fprintf(stderr,"[l10n] %s -> %s produced=%zu status=%u\n",source,destination,produced,result);
}
void tank_battles_register_text_conversion(){
 tank_battles_register_override(0x931FF25A,convert);
}
extern "C" void ps3_hle_call(uint32_t,ppu_context*);
int tb_conversion_probe(){
 constexpr uint32_t src=0x2000000,dst=0x2001000,sl=0x2002000,dl=sl+4;
 auto run=[&](unsigned from,unsigned to,unsigned size,unsigned cap,bool query){
  vm_write32(sl,size);vm_write32(dl,cap);ppu_context c{};c.gpr[1]=0xD00FF000;c.thread_id=58;
  c.gpr[3]=from;c.gpr[4]=src;c.gpr[5]=sl;c.gpr[6]=to;c.gpr[7]=query?0:dst;c.gpr[8]=dl;
  ps3_hle_call(0x931FF25A,&c);return (unsigned)c.gpr[3];
 };
 vm_write8(src,'A');vm_write8(src+1,0xe9);vm_write8(dst+3,0xaa);
 bool ok=run(5,0,2,3,false)==0 && vm_read32(dl)==3 && vm_read32(sl)==2 && vm_read8(dst)=='A' && vm_read8(dst+1)==0xc3 && vm_read8(dst+2)==0xa9 && vm_read8(dst+3)==0xaa;
 ok=(run(5,0,2,0,true)==0 && vm_read32(dl)==3)&&ok;
 ok=(run(5,0,2,1,false)==2 && vm_read32(dl)==1)&&ok;
 vm_write8(src,0xc3);ok=(run(0,1,1,8,false)==1)&&ok;
 ok=(run(999,0,1,8,false)==3 && vm_read32(dl)==8)&&ok;
 vm_write8(src,'A');ok=(run(0,1,1,2,false)==0 && vm_read16(dst)==0x41 && vm_read32(dl)==2)&&ok;
 fprintf(stderr,"[conversion-probe] %s\n",ok?"PASS":"FAIL");return ok?0:1;
}
