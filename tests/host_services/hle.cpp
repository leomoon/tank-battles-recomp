// Production HLE adapter, port boundary and TOC guard with original synthetic VM.
#define PPU_NO_INLINE_LOADS
#include "ppu_recomp.h"
#include "stubs.h"
#include "boot_diagnostics.h"
#include "hle_inventory.h"
#include "execution_policy_api.h"
#include <cassert>
#include <cstdlib>
#include <cstring>
#include <cstdio>
#include "host_services.h"
#include <thread>
#include <chrono>
extern "C" {
uint8_t* vm_base;
uint32_t ppu_vm_size;
uint32_t g_ydkj_real_taskset_ea,g_ydkj_real_spurs_ea;
uint32_t ppu_hle_inject_base;
uint8_t vm_read8(uint64_t a) { assert(a<ppu_vm_size);return vm_base[a]; }
uint16_t vm_read16(uint64_t a) {return (uint16_t)vm_read8(a)<<8|vm_read8(a+1);}
uint32_t vm_read32(uint64_t a) {return (uint32_t)vm_read16(a)<<16|vm_read16(a+2);}
uint64_t vm_read64(uint64_t a) {return (uint64_t)vm_read32(a)<<32|vm_read32(a+4);}
void vm_write8(uint64_t a,uint8_t v) {assert(a<ppu_vm_size);vm_base[a]=v;}
void vm_write16(uint64_t a,uint16_t v) {vm_write8(a,v>>8);vm_write8(a+1,v);}
void vm_write32(uint64_t a,uint32_t v) {vm_write16(a,v>>16);vm_write16(a+2,v);}
void vm_write64(uint64_t a,uint64_t v) {vm_write32(a,v>>32);vm_write32(a+4,v);}
void ps3_msf(const char*,...) {}
uint32_t prx_resolve_export(uint32_t) { return 0; }
void ps3_indirect_call(ppu_context*) { std::abort(); }
uint64_t ppu_guest_call(uint32_t,uint64_t,uint64_t,uint64_t,uint64_t,uint64_t,uint64_t,uint64_t,uint64_t) { return 0; }
void ppu_gcm_pump() {}
void ppu_prof_stamp(void*,unsigned) {}
uint32_t ppu_prof_resolve_host(void*) { return 0; }
void tb_resource_hle(uint32_t) {}
void tb_spu_execution_probe(unsigned) {}
void ps3_hle_call(uint32_t,ppu_context*);
void ps3_hle_register(uint32_t,const char*,void*);
void ps3_hle_register_ctx(uint32_t,const char*,void(*)(ppu_context*));
void ps3_hle_register_fast(uint32_t,int(*)(ppu_context*));
void ppu_dump_guest_stack(ppu_context*,const char*) {}
void ppu_dump_bctrl_ring(uint32_t,const char*) {}
void ppu_guest_callstack(const char*) {}
}
void tank_battles_vulkan_report() {}
int tb_conversion_probe() {return 0;}
void tank_battles_register_text_conversion() {}
void tank_battles_register_asset_overrides() {}
void tank_battles_register_entitlement_overrides() {}
void tank_battles_register_offline_network() {}
static uint64_t generic(uint64_t a,uint64_t b,uint64_t c,uint64_t,uint64_t,uint64_t,uint64_t,uint64_t) { return a*100+b*10+c; }
static void clobber(ppu_context* c) { c->gpr[2]=0;vm_write64(c->gpr[1]+40,0);c->gpr[3]=0x54424f4b; }
static int fast(ppu_context* c) {c->gpr[3]=0xBAD;return 1;}
int main(int argc,char** argv) {
    vm_base=(uint8_t*)calloc(1,0x10000);ppu_vm_size=0x10000;
    tank_battles_diagnostics_init();
#ifdef _WIN32
    if(argc>1 && !strcmp(argv[1],"crash")) {
        SetErrorMode(SEM_NOGPFAULTERRORBOX);
        RaiseException(EXCEPTION_ACCESS_VIOLATION,0,0,nullptr);
        return 3;
    }
#endif
    ppu_context context{};context.thread_id=1;context.gpr[1]=0x1000;context.gpr[2]=0x12345678;
    if(argc>1 && !strcmp(argv[1],"missing")) {ps3_hle_call(0xfefefefe,&context);return 3;}
    if(argc>1 && !strcmp(argv[1],"fallback")) {tb_execution_event(3,0x100,1,0);return 3;}
    if(argc>2 && !strcmp(argv[1],"event")) {tb_execution_event((unsigned)strtoul(argv[2],nullptr,10),0x100,1,0);return 3;}
    ps3_hle_register(0x12345678,"generic",(void*)generic);
    context.gpr[3]=7;context.gpr[4]=8;context.gpr[5]=9;
    ps3_hle_call(0x12345678,&context);assert(context.gpr[3]==789);
    ps3_hle_register_ctx(0x12345679,"context",clobber);
    ps3_hle_call(0x12345679,&context);
    assert(context.gpr[2]==0x12345678 && vm_read64(0x1028)==0x12345678);
    ps3_hle_register_fast(0x12345678,fast);
    tank_battles_register_override(0x12345678,clobber);
    ps3_hle_call(0x12345678,&context);
    assert(context.gpr[3]==0x54424f4b && context.gpr[2]==0x12345678 && vm_read64(0x1028)==0x12345678);
    assert(tank_battles_write_hle_inventory());
    tank_battles_report("host fixture snapshot");
    if(const char* trigger=getenv("TB_DIAGNOSTIC_TRIGGER")) {
        FILE* file=fopen(trigger,"wb");assert(file);fclose(file);
        std::this_thread::sleep_for(std::chrono::milliseconds(200));
    }
    free(vm_base);puts("PASS: generic, context, fast override and TOC slot");return 0;
}
