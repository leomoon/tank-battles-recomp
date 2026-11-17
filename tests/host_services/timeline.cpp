#include "frame_budget.h"
#include "host_services.h"
#include "ps3emu/ps3_ftrace.h"
#include <cassert>
#include <cstdio>
#include <thread>
#include <chrono>
extern "C" { uint8_t* vm_base=nullptr;uint32_t ppu_hle_inject_base=0; }
int main(int argc,char** argv) {
    assert(argc==3);
    tb_host_env("TB_FRAME_BUDGET",argv[1],1);
    tb_host_env("TB_FRAME_BUDGET_TRIGGER",argv[2],1);
    tb_host_env("TB_FRAME_BUDGET_DELAY","0",1);
    tb_host_env("TB_FRAME_BUDGET_SECONDS","1",1);
    tb_frame_budget_init();
    FILE* trigger=fopen(argv[2],"wb");assert(trigger);fclose(trigger);
    for(unsigned i=0;i<150;++i) {
        uint64_t now=tb_frame_budget_now();
        ps3_ft_span(8,0,i,0,now,now);
        tb_frame_budget_span(TB_FT_DRAIN_CALL,0,now,now+10000);
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
    ps3_ft_dump();
    return 0;
}
