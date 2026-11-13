/* Opt-in test of actual native HLE dispatch and the SDK completion queue. */
#include "offline_network.h"
#include "ppu_recomp.h"
#include "ps3emu/nid.h"
#include "ps3emu/guest_call.h"
#include <array>
#include <cstdio>
#include <initializer_list>
#include <stdexcept>
#include <vector>
extern "C" void ps3_hle_call(uint32_t,ppu_context*);
namespace {
struct Callback {uint32_t opd;std::array<uint64_t,8> args;};
std::vector<Callback> callbacks;
unsigned checks;
void receive(uint32_t opd,uint64_t a,uint64_t b,uint64_t c,uint64_t d,
             uint64_t e,uint64_t f,uint64_t g,uint64_t h){callbacks.push_back({opd,{a,b,c,d,e,f,g,h}});}
void check(bool ok,const char* what){++checks;if(!ok)throw std::runtime_error(what);}
uint32_t call(const char* name,std::initializer_list<uint64_t> args={}){
    ppu_context c{};c.thread_id=60;c.gpr[1]=0x03fff000;unsigned i=3;
    for(auto value:args)c.gpr[i++]=value;
    ps3_hle_call(ps3_compute_nid(name),&c);return c.gpr[3];
}
constexpr uint32_t data=0x02000000, output=data+0x1000, parameter=data+0x2000;
constexpr uint32_t cb=0x00022000, matching_cb=0x00022010, voice_cb=0x00022020;
void equal(uint32_t actual,uint32_t expected,const char* message){check(actual==expected,message);}
void poll(){equal(call("cellSysutilCheckCallback"),0,"callback poll");}
}
int tank_battles_probe_offline_network(){
    g_ps3_guest_caller=receive;
    try{
        vm_write32(output,0xfeedface);
        equal(call("sceNpManagerGetStatus",{output}),0x8002aa01,"manager before NP init");
        equal(vm_read32(output),0xfeedface,"failed manager query leaves output alone");
        equal(call("sceNpTusInit",{1001}),0x8002aa01,"TUS requires NP");
        equal(call("sceNpScoreInit"),0x8002aa01,"score requires NP");
        equal(call("sceNpInit",{131072,data}),0,"NP init");
        equal(call("sceNpManagerGetStatus",{output}),0,"offline manager query");
        equal(vm_read32(output),0xffffffff,"manager really offline");
        equal(call("sceNpManagerGetStatus",{0}),0x8002aa03,"manager null output");
        equal(call("sceNpManagerRegisterCallback",{0,0}),0x8002aa03,"manager callback rejects null");
        equal(call("sceNpManagerRegisterCallback",{matching_cb,0xbeef}),0,"manager callback registration");
        poll();check(callbacks.empty(),"stable offline manager invents no sign-in event");
        equal(call("sceNpManagerUnregisterCallback"),0,"manager callback unregistered");
        equal(call("sceNpMatching2Init",{0x4000,1001}),0x8002aa01,"matching requires NP2");
        equal(call("sceNp2Init",{131072,data}),0,"NP2 init after NP");
        equal(call("sceNp2Init",{131072,data}),0x8002aa02,"duplicate NP2 init");
        vm_write32(output,0xfeedface);
        equal(call("cellNetCtlGetState",{output}),0x80130101,"NetCtl before init");
        equal(vm_read32(output),0xfeedface,"failed NetCtl leaves output alone");
        equal(call("cellNetCtlInit"),0,"NetCtl init");
        equal(call("cellNetCtlGetState",{output}),0,"initialized NetCtl succeeds");
        equal(vm_read32(output),0,"disconnected state");
        equal(call("cellNetCtlGetState",{0}),0x80130107,"NetCtl null output");
        equal(call("cellSysutilRegisterCallback",{0,cb,0x1234}),0,"sysutil registration");
        vm_write32(parameter,12);vm_write32(parameter+4,1);vm_write32(parameter+8,0);
        equal(call("cellNetCtlNetStartDialogLoadAsync",{0}),0x80130107,"dialog null input");
        equal(call("cellNetCtlNetStartDialogLoadAsync",{parameter}),0,"offline dialog accepted");
        check(callbacks.empty(),"dialog callbacks deferred");poll();
        check(callbacks.size()==2 && callbacks[0].opd==cb && callbacks[0].args[0]==0x801 &&
              callbacks[1].args[0]==0x802 && callbacks[1].args[2]==0x1234,"loaded/finished sysutil events");
        vm_write32(output,8);vm_write32(output+4,0xfeedface);vm_write32(output+8,0xfeedface);
        equal(call("cellNetCtlNetStartDialogUnloadAsync",{output}),0,"dialog unload");
        equal(vm_read32(output),8,"dialog result size retained");
        equal(vm_read32(output+4),0x80130190,"dialog cancelled offline");
        equal(vm_read32(output+8),0xfeedface,"dialog output boundary");poll();
        check(callbacks.size()==3 && callbacks[2].args[0]==0x803,"unloaded sysutil event");
        equal(call("cellNetCtlNetStartDialogUnloadAsync",{output}),0x80130109,"no stale dialog unload");
        equal(call("cellNetCtlNetStartDialogLoadAsync",{parameter}),0,"second dialog accepted");
        equal(call("cellNetCtlTerm"),0,"NetCtl term");poll();
        equal(callbacks.size(),3,"NetCtl term cancels pending events");callbacks.clear();
        for(const char* service:{"Score","Tus"}){
            const bool tus=service[0]=='T';
            const char* init=tus?"sceNpTusInit":"sceNpScoreInit";
            const char* create=tus?"sceNpTusCreateTitleCtx":"sceNpScoreCreateTitleCtx";
            const char* trans=tus?"sceNpTusCreateTransactionCtx":"sceNpScoreCreateTransactionCtx";
            const char* destroy=tus?"sceNpTusDestroyTitleCtx":"sceNpScoreDestroyTitleCtx";
            const char* wait=tus?"sceNpTusPollAsync":"sceNpScoreWaitAsync";
            equal(call(init,{1001}),0,"community init");
            equal(call(init,{1001}),0x8002a101,"duplicate community init");
            equal(call(create,{0,data,data}),0x8002a104,"invalid title arguments");
            auto title=call(create,{data,data+0x100,data+0x200});
            check(title>0 && title<0x80000000,"valid local title handle");
            equal(call(trans,{title}),tus?0x8002a1a4:0x8002a106,"offline transaction rejected");
            vm_write32(output,0xfeedface);
            equal(call(wait,{0,output}),0x8002a1a3,"no fabricated transaction completion");
            equal(vm_read32(output),0xfeedface,"failed poll leaves output alone");
            if(tus){
                for(const char* name:{"sceNpTusGetMultiSlotVariableAsync","sceNpTusSetMultiSlotVariable"})
                    equal(call(name,{0,data,data,output,1}),0x8002a1a3,"offline TUS request rejected");
            }else{
                for(const char* name:{"sceNpScoreGetRankingByRangeAsync","sceNpScoreGetRankingByNpIdAsync",
                    "sceNpScoreRecordScoreAsync","sceNpScoreRecordGameDataAsync","sceNpScoreGetGameDataAsync"})
                    equal(call(name,{0,data,data,output,1}),0x8002a1a3,"offline score request rejected");
            }
            equal(vm_read32(output),0xfeedface,"rejected requests preserve output");
            equal(call(destroy,{title}),0,"title context released");
            equal(call(trans,{title}),0x8002a1a3,"destroyed title rejected");
            for(unsigned i=0;i<40;++i){auto id=call(create,{data,data+0x100,data+0x200});
                check(id && id<0x80000000,"repeated title allocation");equal(call(destroy,{id}),0,"repeated title release");}
        }
        equal(call("sceNpMatching2Init",{0x4000,1001}),0,"matching init");
        vm_write32(output,0xfeedface);
        equal(call("sceNpMatching2CreateContext",{data,data+0x100,data+0x200,output,0}),0,"matching context");
        auto context=vm_read16(output);
        check(context && vm_read16(output+2)==0xface,"matching 16-bit context output");
        equal(call("sceNpMatching2RegisterContextCallback",{context,matching_cb,0xbeef}),0,"matching callback");
        equal(call("sceNpMatching2RegisterRoomEventCallback",{context,matching_cb,0xbeef}),0,"room callback local registration");
        equal(call("sceNpMatching2ContextStartAsync",{context,10000}),0,"matching start accepted");
        check(callbacks.empty(),"matching callback deferred");
        equal(call("sceNpMatching2ContextStartAsync",{context,10000}),0x80022307,"no duplicate pending start");poll();
        check(callbacks.size()==1 && callbacks[0].opd==matching_cb && callbacks[0].args==
            std::array<uint64_t,8>{context,0x6f01,10,0x80022334,0xbeef,0,0,0},"matching failed-start ABI");
        poll();equal(callbacks.size(),1,"no repeated completion");callbacks.clear();
        vm_write32(output,0xfeedface);
        equal(call("sceNpMatching2SearchRoom",{context,data,0,output}),0x80022334,"room search offline");
        equal(vm_read32(output),0xfeedface,"rejected request assigns no ID");
        equal(call("sceNpMatching2ContextStartAsync",{context,10000}),0,"start before abort");
        equal(call("sceNpMatching2AbortContextStart",{context}),0,"pending start aborted");poll();
        check(callbacks.empty(),"aborted callback suppressed");
        equal(call("sceNpMatching2AbortContextStart",{context}),0x80022308,"nothing left to abort");
        equal(call("sceNpMatching2ContextStartAsync",{context,10000}),0,"start before destroy");
        equal(call("sceNpMatching2DestroyContext",{context}),0,"destroy matching context");poll();
        check(callbacks.empty(),"destroyed context callback suppressed");
        equal(call("sceNpMatching2ContextStartAsync",{context,10000}),0x80022306,"destroyed context unavailable");
        for(unsigned i=0;i<40;++i){
            equal(call("sceNpMatching2CreateContext",{data,data+0x100,data+0x200,output,0}),0,"repeated matching create");
            auto id=vm_read16(output);
            equal(call("sceNpMatching2RegisterContextCallback",{id,matching_cb,7}),0,"repeated matching callback");
            equal(call("sceNpMatching2ContextStartAsync",{id,10000}),0,"repeated matching start");
            equal(call("sceNpMatching2DestroyContext",{id}),0,"repeated matching release");
        }
        poll();check(callbacks.empty(),"no stale completions after repeated teardown");
        for(unsigned i=0;i<136;++i)vm_write8(parameter+i,0xa5);
        equal(call("cellSysutilAvc2InitParam",{140,parameter}),0,"voice defaults initialized");
        equal(vm_read16(parameter),140,"voice version big-endian");
        for(unsigned i=2;i<132;++i)equal(vm_read8(parameter+i),0,"voice default output initialized");
        equal(vm_read32(parameter+132),0xa5a5a5a5,"voice output boundary");
        vm_write32(parameter+40,1);
        vm_write16(parameter,0);
        equal(call("cellSysutilAvc2LoadAsync",{1,0,voice_cb,0xcafe,parameter}),0x8002b705,"voice version validated");
        vm_write16(parameter,140);
        equal(call("cellSysutilAvc2LoadAsync",{1,0,voice_cb,0xcafe,parameter}),0x8002b705,"voice options validated");
        vm_write16(parameter+2,8);vm_write32(parameter+44,1);vm_write16(parameter+48,8);
        equal(call("cellSysutilAvc2LoadAsync",{1,0,voice_cb,0xcafe,parameter}),0,"voice load accepted");
        check(callbacks.empty(),"voice failure deferred");poll();
        check(callbacks.size()==1 && callbacks[0].opd==voice_cb && callbacks[0].args==
            std::array<uint64_t,8>{2,2,0xcafe,0,0,0,0,0},"voice failed-load ABI");callbacks.clear();
        equal(call("cellSysutilAvc2StartStreaming"),0x8002b703,"no fake streaming success");
        equal(call("cellSysutilAvc2LoadAsync",{1,0,voice_cb,0xcafe,parameter}),0,"voice reload accepted");
        equal(call("cellSysutilAvc2Unload"),0x8002b703,"unload cancels pending failed load");poll();
        check(callbacks.empty(),"unloaded voice callback suppressed");
        equal(call("sceNpBasicSendMessageGui",{data,0}),0x8002a678,"offline message GUI rejected");
        equal(call("sceNpBasicRecvMessageCustom",{0,0,0}),0x8002a678,"offline invitation rejected");
        vm_write32(output,0xfeedface);
        equal(call("sceNpBasicAddPlayersHistoryAsync",{data,1,data,output}),0x8002a678,"history rejected offline");
        equal(vm_read32(output),0xfeedface,"rejected history assigns no request");
        equal(call("sceNpSignalingCreateCtx",{data,matching_cb,0xbeef,output}),0,"local signaling context");
        auto signal=vm_read32(output);vm_write32(output,0xfeedface);
        equal(call("sceNpSignalingActivateConnection",{signal,data,output}),0x8002a80f,"peer unreachable offline");
        equal(vm_read32(output),0xfeedface,"no pending connection ID");
        equal(call("sceNpSignalingDestroyCtx",{signal}),0,"signaling context released");
        equal(call("sceNpSignalingActivateConnection",{signal,data,output}),0x8002a805,"released signal context rejected");
        equal(call("sceNpTerm"),0,"NP teardown");
        equal(call("sceNpTusCreateTitleCtx",{data,data,data}),0x8002a102,"TUS cleared on NP teardown");
        equal(call("sceNpMatching2ContextStartAsync",{1,1000}),0x80022303,"matching cleared on NP teardown");
        poll();check(callbacks.empty(),"no callbacks left after NP teardown");
        printf("[offline-probe] ALL PASS checks=%u\n",checks);
        return 0;
    }catch(const std::exception& error){fprintf(stderr,"[offline-probe] FAIL: %s\n",error.what());return 1;}
}
