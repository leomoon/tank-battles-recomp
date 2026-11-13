/* Original offline profile for the interfaces imported by NPUB30154.
 * ABI/error/event definitions checked against the local RPCS3 source. */
#include "offline_network.h"
#include "stubs.h"
#include "ppu_recomp.h"
#include "ps3emu/nid.h"
#include <algorithm>
#include <array>
#include <cstdio>
#include <cstdlib>
#include <mutex>
#include <vector>
extern "C" {
int np_is_initialized();
int32_t cellSysutilQueueGuestCallbackArgs(uint32_t, const uint64_t*);
void cellSysutilQueueEvent(int, uint32_t, uint32_t);
#if defined(__linux__)
void __real_ps3_hle_call(uint32_t, ppu_context*);
#endif
}
namespace {
constexpr uint32_t np_not_init=0x8002aa01, np_already=0x8002aa02, np_argument=0x8002aa03;
constexpr uint32_t community_already=0x8002a101, community_not_init=0x8002a102;
constexpr uint32_t community_argument=0x8002a104, no_login=0x8002a106;
constexpr uint32_t community_no_ids=0x8002a107, invalid_id=0x8002a1a3, invalid_online_id=0x8002a1a4;
constexpr uint32_t m2_already=0x80022302, m2_not_init=0x80022303, m2_ctx_missing=0x80022306;
constexpr uint32_t m2_not_started=0x80022308, m2_argument=0x8002230a, m2_offline=0x80022334;
constexpr uint32_t avc_not_supported=0x8002b702, avc_not_init=0x8002b703, avc_argument=0x8002b705;
constexpr uint32_t net_not_init=0x80130101, net_address=0x80130107, net_unavailable=0x80130109;
constexpr uint32_t net_size=0x8013010b, net_cancelled=0x80130190;
struct TitlePool {
    bool initialized=false;
    std::array<uint32_t,16> titles{};
    uint32_t next_id=1;
    bool contains(uint32_t id) const { for (auto value:titles) if (value && value==id) return true; return false; }
    void clear() { initialized=false; titles.fill(0); }
};
struct MatchingContext { uint32_t id=0, callback=0, arg=0; bool pending=false; };
enum class Owner { net, matching, voice };
struct Completion { Owner owner; uint32_t id, generation, callback; std::array<uint64_t,8> args; uint32_t status=0; };
std::mutex lock;
TitlePool score,tus;
bool np2=false, matching=false, net=false, dialog=false, voice_pending=false;
uint32_t net_generation=0, voice_generation=0, next_context=1;
std::array<MatchingContext,16> contexts;
std::array<uint32_t,16> signaling{};
uint32_t next_signal=1;
std::vector<Completion> completions;
bool ea(uint64_t address, unsigned count=4) {
    return address>=0x10000 && address<0xe0000000ull-count && (!ppu_vm_size || address+count<=ppu_vm_size);
}
MatchingContext* context(uint32_t id) { for (auto& c:contexts) if(c.id && c.id==id) return &c; return nullptr; }
void clear_matching() { matching=false; for(auto& c:contexts)c={}; std::erase_if(completions,[](const auto& e){return e.owner==Owner::matching;}); }
void reset_np_services() { score.clear();tus.clear();np2=false;clear_matching();voice_pending=false;++voice_generation;signaling.fill(0);std::erase_if(completions,[](const auto& e){return e.owner!=Owner::net;}); }
void delegate(const char* name, ppu_context* c) {
#if defined(__linux__)
    __real_ps3_hle_call(ps3_compute_nid(name),c);
#else
    (void)name;c->gpr[3]=np_not_init;
#endif
}
uint32_t title_create(TitlePool& pool, ppu_context* c) {
    if(!pool.initialized)return community_not_init;
    if(!ea(c->gpr[3])||!ea(c->gpr[4])||!ea(c->gpr[5]))return community_argument;
    for(auto& id:pool.titles)if(!id){id=pool.next_id++;return id;}
    return community_no_ids;
}
uint32_t title_destroy(TitlePool& pool, uint32_t id) {
    if(!pool.initialized)return community_not_init;
    for(auto& value:pool.titles)if(value && value==id){value=0;return 0;}
    return invalid_id;
}
uint32_t transaction_create(TitlePool& pool, uint32_t id, uint32_t offline_error) {
    if(!pool.initialized)return community_not_init;
    if(!pool.contains(id))return invalid_id;
    return offline_error; // No request or transaction handle is allocated offline.
}
enum class Op {
    np_term,np2_init,np2_term,manager_status,manager_register,manager_unregister,
    net_init,net_term,net_state,dialog_load,dialog_unload,
    score_init,score_term,score_title,score_destroy,score_transaction,score_request,
    tus_init,tus_term,tus_title,tus_destroy,tus_transaction,tus_request,
    matching_init,matching_term,matching_create,matching_destroy,matching_callback,
    matching_start,matching_stop,matching_abort,matching_request,matching_event,matching_aux_callback,
    voice_init,voice_load,voice_unload,voice_other,basic_request,basic_abort,signal_create,signal_destroy,signal_callback,signal_query,signal_activate
};
uint32_t perform(Op op,ppu_context* c) {
    const uint32_t a=c->gpr[3],b=c->gpr[4];
    switch(op) {
    case Op::np_term: reset_np_services();delegate("sceNpTerm",c);return c->gpr[3];
    case Op::np2_init:
        if(np2)return np_already;
        delegate("sceNp2Init",c);if(!c->gpr[3])np2=true;return c->gpr[3];
    case Op::np2_term:
        if(!np2)return np_not_init;
        reset_np_services();delegate("sceNp2Term",c);return c->gpr[3];
    case Op::manager_status:
        if(!np_is_initialized())return np_not_init;
        if(!ea(c->gpr[3]))return np_argument;
        vm_write32(a,0xffffffffu);return 0;
    case Op::manager_register:
        if(!np_is_initialized())return np_not_init;
        if(!ea(c->gpr[3],8))return np_argument;
        delegate("sceNpManagerRegisterCallback",c);return c->gpr[3];
    case Op::manager_unregister:
        if(!np_is_initialized())return np_not_init;
        delegate("sceNpManagerUnregisterCallback",c);return c->gpr[3];
    case Op::net_init:
        delegate("cellNetCtlInit",c);if(!c->gpr[3])net=true;return c->gpr[3];
    case Op::net_term:
        net=false;dialog=false;++net_generation;
        std::erase_if(completions,[](const auto& e){return e.owner==Owner::net;});delegate("cellNetCtlTerm",c);return 0;
    case Op::net_state:
        if(!net)return net_not_init;
        if(!ea(c->gpr[3]))return net_address;
        vm_write32(a,0);return 0;
    case Op::dialog_load:
        if(!net)return net_not_init;
        if(!ea(c->gpr[3],12))return net_address;
        if(vm_read32(a)!=12)return net_size;
        if(vm_read32(a+4)>1)return 0x8013010au;
        if(dialog || completions.size()>60)return net_unavailable;
        dialog=true;++net_generation;
        for(uint32_t status:{0x801u,0x802u})completions.push_back({Owner::net,0,net_generation,0,{},status});
        return 0;
    case Op::dialog_unload:
        if(!net)return net_not_init;
        if(!ea(c->gpr[3],8))return net_address;
        if(vm_read32(a)!=8)return net_size;
        if(!dialog || completions.size()>=64)return net_unavailable;
        vm_write32(a+4,net_cancelled);dialog=false;
        completions.push_back({Owner::net,0,net_generation,0,{},0x803});return 0;
    case Op::score_init: case Op::tus_init: {
        auto& pool=op==Op::score_init?score:tus;
        if(!np_is_initialized())return np_not_init;
        if(pool.initialized)return community_already;
        pool.initialized=true;return 0;
    }
    case Op::score_term:case Op::tus_term: {
        auto& pool=op==Op::score_term?score:tus;
        if(!pool.initialized)return community_not_init;
        pool.clear();return 0;
    }
    case Op::score_title:return title_create(score,c);
    case Op::tus_title:return title_create(tus,c);
    case Op::score_destroy:return title_destroy(score,a);
    case Op::tus_destroy:return title_destroy(tus,a);
    case Op::score_transaction:return transaction_create(score,a,no_login);
    case Op::tus_transaction:return transaction_create(tus,a,invalid_online_id);
    case Op::score_request:return score.initialized?invalid_id:community_not_init;
    case Op::tus_request:return tus.initialized?invalid_id:community_not_init;
    case Op::matching_init:
        if(!np2)return np_not_init;
        if(matching)return m2_already;
        matching=true;return 0;
    case Op::matching_term:
        if(!matching)return m2_not_init;
        clear_matching();return 0;
    case Op::matching_create:
        if(!matching)return m2_not_init;
        if(!ea(c->gpr[3],36)||!ea(c->gpr[4],12)||!ea(c->gpr[5],128)||!ea(c->gpr[6],2))return m2_argument;
        if(next_context>0xffffu)return 0x80022304u;
        for(auto& ctx:contexts)if(!ctx.id){ctx.id=next_context++;vm_write16(c->gpr[6],ctx.id);return 0;}
        return 0x80022304u;
    case Op::matching_destroy:case Op::matching_callback:case Op::matching_start:
    case Op::matching_stop:case Op::matching_abort:case Op::matching_request:case Op::matching_event:case Op::matching_aux_callback: {
        if(!matching)return m2_not_init;
        auto* ctx=context(a);if(!ctx)return m2_ctx_missing;
        if(op==Op::matching_destroy){
            std::erase_if(completions,[a](const auto& e){return e.owner==Owner::matching && e.id==a;});
            *ctx={};return 0;
        }
        if(op==Op::matching_aux_callback){
            if(b && !ea(c->gpr[4],8))return m2_argument;
            return 0; // No room/message/signaling events are produced offline.
        }
        if(op==Op::matching_callback){
            if(b && !ea(c->gpr[4],8))return m2_argument;
            if(!b){std::erase_if(completions,[a](const auto& e){return e.owner==Owner::matching && e.id==a;});ctx->pending=false;}
            ctx->callback=b;ctx->arg=c->gpr[5];return 0;
        }
        if(op==Op::matching_start){
            if(ctx->pending)return 0x80022307u;
            if(ctx->callback){
                if(completions.size()>=64)return 0x80022301u;
                completions.push_back({Owner::matching,ctx->id,0,ctx->callback,
                    {ctx->id,0x6f01,10,m2_offline,ctx->arg,0,0,0}});
                ctx->pending=true;
            }
            return 0;
        }
        if(op==Op::matching_abort || op==Op::matching_stop){
            if(!ctx->pending)return m2_not_started;
            // The queued start has not been delivered. Cancel it explicitly.
            std::erase_if(completions,[a](const auto& e){return e.owner==Owner::matching && e.id==a;});
            ctx->pending=false;return 0;
        }
        if(op==Op::matching_event)return 0x80022339u;
        return m2_offline; // Rejected synchronously; assignedReqId is untouched.
    }
    case Op::voice_init:
        if(!ea(c->gpr[4],132))return avc_argument;
        if(a!=100 && a!=110 && a!=120 && a!=130 && a!=140)return avc_argument;
        for(unsigned i=0;i<132;++i)vm_write8(b+i,0);
        vm_write16(b,a);return 0;
    case Op::voice_load:
        if(!ea(c->gpr[5],8)||!ea(c->gpr[7],132))return avc_argument;
        {
            const auto option=(uint32_t)c->gpr[7];
            const auto version=vm_read16(option);
            if(version!=100 && version!=110 && version!=120 && version!=130 && version!=140)return avc_argument;
            if(vm_read32(option+40)!=1)return avc_not_supported;
            const auto players=vm_read16(option+2),speakers=vm_read16(option+48);
            if(players<2 || players>64 || vm_read16(option+4)>100 ||
               vm_read32(option+44)!=1 || !speakers || speakers>16)return avc_argument;
        }
        if(voice_pending || completions.size()>=64)return 0x8002b70au;
        voice_pending=true;++voice_generation;
        completions.push_back({Owner::voice,0,voice_generation,(uint32_t)c->gpr[5],
            {2,2,c->gpr[6],0,0,0,0,0}});return 0; // LOAD_FAILED, NOT_SUPPORTED.
    case Op::voice_unload:
        voice_pending=false;++voice_generation;
        std::erase_if(completions,[](const auto& e){return e.owner==Owner::voice;});return avc_not_init;
    case Op::voice_other:return avc_not_init;
    case Op::basic_request:return np_is_initialized()?0x8002a678u:0x8002a662u;
    case Op::basic_abort:return np_is_initialized()?0x8002a670u:0x8002a662u;
    case Op::signal_create:
        if(!np_is_initialized())return 0x8002a801u;
        if(!ea(c->gpr[3],36)||!ea(c->gpr[6]))return 0x8002a815u;
        for(auto& id:signaling)if(!id){id=next_signal++;vm_write32(c->gpr[6],id);return 0;}
        return 0x8002a812u;
    case Op::signal_destroy:case Op::signal_callback:case Op::signal_query:case Op::signal_activate:
        if(!np_is_initialized())return 0x8002a801u;
        if(std::find(signaling.begin(),signaling.end(),a)==signaling.end() || !a)return 0x8002a805u;
        if(op==Op::signal_destroy){*std::find(signaling.begin(),signaling.end(),a)=0;return 0;}
        if(op==Op::signal_callback)return b && !ea(c->gpr[4],8)?0x8002a815u:0;
        if(op==Op::signal_query)return 0x8002a80eu;
        if(!ea(c->gpr[4],36)||!ea(c->gpr[5]))return 0x8002a815u;
        return 0x8002a80fu; // PEER_UNREACHABLE; no permanently pending connection.
    }
    return np_argument;
}
template<Op op>void handler(ppu_context* c){std::lock_guard guard(lock);c->gpr[3]=perform(op,c);}
void check_callbacks(ppu_context* c) {
    uint32_t queue_error=0;
    {
        std::lock_guard guard(lock);
        std::vector<Completion> retry;
        for(const auto& event:completions){
            if(event.owner==Owner::net){
                if(net && event.generation==net_generation)
                    for(int slot=0;slot<4;++slot)cellSysutilQueueEvent(slot,event.status,0);
                continue;
            }
            if(!event.callback)continue;
            MatchingContext* ctx=nullptr;
            if(event.owner==Owner::matching){
                ctx=context(event.id);
                if(!matching || !ctx || !ctx->pending)continue;
            }else if(!voice_pending || event.generation!=voice_generation)continue;
            const auto result=cellSysutilQueueGuestCallbackArgs(event.callback,event.args.data());
            if(result){queue_error=result;retry.push_back(event);continue;}
            if(ctx)ctx->pending=false;else voice_pending=false;
        }
        completions=std::move(retry);
    }
    delegate("cellSysutilCheckCallback",c);
    if(!c->gpr[3] && queue_error)c->gpr[3]=queue_error;
}

struct Binding {const char* name;tank_battles_override_fn fn;};
#define B(name,op) {name,handler<Op::op>}
const Binding bindings[]={
    B("sceNpTerm",np_term),B("sceNp2Init",np2_init),B("sceNp2Term",np2_term),B("sceNpManagerGetStatus",manager_status),
    B("sceNpManagerRegisterCallback",manager_register),B("sceNpManagerUnregisterCallback",manager_unregister),
    B("cellNetCtlInit",net_init),B("cellNetCtlTerm",net_term),B("cellNetCtlGetState",net_state),
    B("cellNetCtlNetStartDialogLoadAsync",dialog_load),B("cellNetCtlNetStartDialogUnloadAsync",dialog_unload),
    B("sceNpScoreInit",score_init),B("sceNpScoreTerm",score_term),B("sceNpScoreCreateTitleCtx",score_title),
    B("sceNpScoreDestroyTitleCtx",score_destroy),B("sceNpScoreCreateTransactionCtx",score_transaction),
    B("sceNpScoreDestroyTransactionCtx",score_request),B("sceNpScoreWaitAsync",score_request),
    B("sceNpScoreGetRankingByRangeAsync",score_request),B("sceNpScoreGetRankingByNpIdAsync",score_request),
    B("sceNpScoreRecordScoreAsync",score_request),B("sceNpScoreRecordGameDataAsync",score_request),B("sceNpScoreGetGameDataAsync",score_request),
    B("sceNpTusInit",tus_init),B("sceNpTusTerm",tus_term),B("sceNpTusCreateTitleCtx",tus_title),
    B("sceNpTusDestroyTitleCtx",tus_destroy),B("sceNpTusCreateTransactionCtx",tus_transaction),
    B("sceNpTusDestroyTransactionCtx",tus_request),B("sceNpTusPollAsync",tus_request),
    B("sceNpTusGetMultiSlotVariableAsync",tus_request),B("sceNpTusSetMultiSlotVariable",tus_request),
    B("sceNpMatching2Init",matching_init),B("sceNpMatching2Term",matching_term),B("sceNpMatching2CreateContext",matching_create),
    B("sceNpMatching2DestroyContext",matching_destroy),B("sceNpMatching2RegisterContextCallback",matching_callback),
    B("sceNpMatching2RegisterRoomEventCallback",matching_aux_callback),
    B("sceNpMatching2RegisterRoomMessageCallback",matching_aux_callback),
    B("sceNpMatching2RegisterSignalingCallback",matching_aux_callback),
    B("sceNpMatching2ContextStartAsync",matching_start),B("sceNpMatching2ContextStop",matching_stop),
    B("sceNpMatching2AbortContextStart",matching_abort),B("sceNpMatching2GetEventData",matching_event),
    B("sceNpMatching2GetWorldInfoList",matching_request),B("sceNpMatching2SearchRoom",matching_request),
    B("sceNpMatching2LeaveRoom",matching_request),B("sceNpMatching2SendRoomMessage",matching_request),
    B("sceNpMatching2GetServerInfo",matching_request),B("sceNpMatching2JoinRoom",matching_request),
    B("sceNpMatching2KickoutRoomMember",matching_request),B("sceNpMatching2SetRoomDataInternal",matching_request),
    B("sceNpMatching2SignalingGetConnectionStatus",matching_request),B("sceNpMatching2SignalingGetPingInfo",matching_request),
    B("sceNpMatching2GetServerIdListLocal",matching_request),B("sceNpMatching2CreateJoinRoom",matching_request),
    B("cellSysutilAvc2InitParam",voice_init),B("cellSysutilAvc2LoadAsync",voice_load),B("cellSysutilAvc2Unload",voice_unload),
    B("cellSysutilAvc2JoinChatRequest",voice_other),B("cellSysutilAvc2LeaveChatRequest",voice_other),
    B("cellSysutilAvc2StartStreaming",voice_other),B("cellSysutilAvc2StopStreaming",voice_other),
    B("cellSysutilAvc2StartVoiceDetection",voice_other),B("cellSysutilAvc2SetAttribute",voice_other),
    B("sceNpBasicSendMessageGui",basic_request),B("sceNpBasicRecvMessageCustom",basic_request),
    B("sceNpBasicRecvMessageAttachmentLoad",basic_request),B("sceNpBasicGetBlockListEntry",basic_request),
    B("sceNpBasicAddPlayersHistoryAsync",basic_request),B("sceNpBasicAbortGui",basic_abort),
    B("sceNpSignalingCreateCtx",signal_create),B("sceNpSignalingDestroyCtx",signal_destroy),
    B("sceNpSignalingAddExtendedHandler",signal_callback),B("sceNpSignalingGetConnectionStatus",signal_query),
    B("sceNpSignalingGetConnectionInfo",signal_query),B("sceNpSignalingDeactivateConnection",signal_query),
    B("sceNpSignalingActivateConnection",signal_activate),
    {"cellSysutilCheckCallback",check_callbacks}
};
#undef B
}
void tank_battles_init_offline_network(){
#ifdef _WIN32
    _putenv_s("PS3_NET_ONLINE","");_putenv_s("PSNR_SERVER","");
#else
    unsetenv("PS3_NET_ONLINE");unsetenv("PSNR_SERVER");
#endif
    fprintf(stderr,"[offline] Tank Battles profile: PSN and host matchmaking disabled\n");
}
void tank_battles_register_offline_network(){
#if defined(__linux__)
    for(const auto& binding:bindings)tank_battles_register_override(ps3_compute_nid(binding.name),binding.fn);
#endif
}
