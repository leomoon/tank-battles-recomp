/* Original offline profile for the interfaces imported by NPUB30154.
 * ABI/error/event definitions checked against the local RPCS3 source. */
#include "offline_network.h"
#include "fps_overlay.h"
#include "stubs.h"
#include "rpcn/client.h"
#include "rpcn/basic.h"
#include "rpcn/matching.h"
#include "rpcn/score.h"
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
void tb_sdk_ps3_hle_call(uint32_t, ppu_context*);
void ppu_thread_set_startup_wait_entry(uint64_t);
void ppu_thread_note_block(uint64_t);
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
bool dialog_waiting=false;
tb::rpcn::ConnectionError shown_connection_error=tb::rpcn::ConnectionError::none;
struct StatusCallback {
    uint32_t opd=0, arg=0, previous=0;
};
StatusCallback manager_callback;
std::array<StatusCallback,16> net_handlers;
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
void reset_np_services() {
    score.clear();tus.clear();np2=false;clear_matching();manager_callback={};
    voice_pending=false;++voice_generation;signaling.fill(0);
    std::erase_if(completions,[](const auto& e){return e.owner!=Owner::net;});
    if (tb::rpcn::enabled()) { tb::rpcn::reset_matching(); tb::rpcn::reset_basic(); tb::rpcn::reset_score(); }
}
void delegate(const char* name, ppu_context* c) {
    tb_sdk_ps3_hle_call(ps3_compute_nid(name),c);
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
    np_term,np2_init,np2_term,manager_status,manager_register,manager_unregister,manager_id,
    net_init,net_term,net_state,net_add,net_delete,dialog_load,dialog_unload,
    score_init,score_term,score_title,score_destroy,score_transaction,score_request,
    tus_init,tus_term,tus_title,tus_destroy,tus_transaction,tus_request,
    matching_init,matching_term,matching_create,matching_destroy,matching_callback,
    matching_start,matching_stop,matching_abort,matching_request,matching_event,matching_aux_callback,
    voice_init,voice_load,voice_unload,voice_other,basic_request,basic_abort,signal_create,signal_destroy,signal_callback,signal_query,signal_activate
};

uint32_t manager_state(tb::rpcn::State state) {
    if (state == tb::rpcn::State::online) return 3;
    if (state == tb::rpcn::State::connecting) return 2;
    return 0xffffffffu;
}

uint32_t net_state(tb::rpcn::State state) {
    if (state == tb::rpcn::State::online) return 3;
    if (state == tb::rpcn::State::connecting) return 1;
    return 0;
}

void net_dialog_event(uint32_t event) {
    completions.push_back({Owner::net,0,net_generation,0,{},event});
}

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
        vm_write32(a,tb::rpcn::enabled()?manager_state(tb::rpcn::status().state):0xffffffffu);return 0;
    case Op::manager_register:
        if(!np_is_initialized())return np_not_init;
        if(!ea(c->gpr[3],8))return np_argument;
        if (tb::rpcn::enabled()) {
            manager_callback={a,b,0xffffffffu};
            return 0;
        }
        delegate("sceNpManagerRegisterCallback",c);return c->gpr[3];
    case Op::manager_unregister:
        if(!np_is_initialized())return np_not_init;
        if (tb::rpcn::enabled()) {
            manager_callback={};
            return 0;
        }
        delegate("sceNpManagerUnregisterCallback",c);return c->gpr[3];
    case Op::manager_id: {
        if(!np_is_initialized())return np_not_init;
        if(!ea(c->gpr[3],36))return np_argument;
        const auto identity=tb::rpcn::status();
        if (identity.state!=tb::rpcn::State::online) return 0x8002aa0cu;
        for (unsigned i=0; i<36; ++i)
            vm_write8(a+i,i<identity.npid.size()?static_cast<uint8_t>(identity.npid[i]):0);
        return 0;
    }
    case Op::net_init:
        delegate("cellNetCtlInit",c);if(!c->gpr[3])net=true;return c->gpr[3];
    case Op::net_term:
        net=false;dialog=false;dialog_waiting=false;++net_generation;
        net_handlers.fill({});
        std::erase_if(completions,[](const auto& e){return e.owner==Owner::net;});delegate("cellNetCtlTerm",c);return 0;
    case Op::net_state:
        if(!net)return net_not_init;
        if(!ea(c->gpr[3]))return net_address;
        vm_write32(a,tb::rpcn::enabled()?net_state(tb::rpcn::status().state):0);return 0;
    case Op::net_add: {
        if(!net)return net_not_init;
        const auto out=c->gpr[5];
        if(!ea(c->gpr[3],8)||!ea(out))return net_address;
        delegate("cellNetCtlAddHandler",c);
        if (!c->gpr[3]) {
            const auto index=vm_read32(out);
            if (index<net_handlers.size()) net_handlers[index]={a,b,0};
        }
        return c->gpr[3];
    }
    case Op::net_delete:
        delegate("cellNetCtlDelHandler",c);
        if (!c->gpr[3] && a<net_handlers.size()) net_handlers[a]={};
        return c->gpr[3];
    case Op::dialog_load:
        if(!net)return net_not_init;
        if(!ea(c->gpr[3],12))return net_address;
        if(vm_read32(a)!=12)return net_size;
        if(vm_read32(a+4)>1)return 0x8013010au;
        if(dialog || completions.size()>60)return net_unavailable;
        dialog=true;++net_generation;
        net_dialog_event(0x801u);
        tb::rpcn::retry_sign_in();
        dialog_waiting=tb::rpcn::enabled() && tb::rpcn::status().state==tb::rpcn::State::connecting;
        if (!dialog_waiting) net_dialog_event(0x802u);
        return 0;
    case Op::dialog_unload:
        if(!net)return net_not_init;
        if(!ea(c->gpr[3],8))return net_address;
        if(vm_read32(a)!=8)return net_size;
        if(!dialog || completions.size()>=64)return net_unavailable;
        vm_write32(a+4,tb::rpcn::enabled() && tb::rpcn::status().state==tb::rpcn::State::online?0:net_cancelled);
        dialog=false;dialog_waiting=false;
        net_dialog_event(0x803u);return 0;
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
        // The title starts a chat worker as soon as initialization succeeds.
        // Reject unavailable voice before it can start and retry that worker.
        if (tb::rpcn::enabled()) return avc_not_supported;
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
bool offline_trace_enabled() {
    static const bool enabled=[] { const char* value=getenv("TB_OFFLINE_TRACE");return value && value[0]=='1'; }();
    return enabled;
}
void trace_offline(const char* name, uint32_t result, uint32_t detail, uint32_t lr) {
    static unsigned count=0;
    if(offline_trace_enabled() && count<1024)
        fprintf(stderr,"[offline-trace] seq=%u name=%s result=0x%08X detail=0x%08X pending=%zu lr=0x%08X\n",
                ++count,name,result,detail,completions.size(),lr);
}
template<Op op>void handler(ppu_context* c,const char* name){
    std::lock_guard guard(lock);
    const uint64_t address=c->gpr[3], event_key=c->gpr[4], context_out=c->gpr[6];
    const uint32_t lr=c->lr;
    c->gpr[3]=perform(op,c);
    if (!offline_trace_enabled()) return;
    const uint32_t result=c->gpr[3];
    uint32_t detail=0;
    if constexpr(op==Op::manager_status || op==Op::net_state) {
        detail=result?0:vm_read32(address);
        // Repeated polls at one site should leave room for other menu callers.
        struct PollSite { uint32_t lr, result, state; };
        static std::array<PollSite, 32> sites{};
        for (auto& site:sites) {
            if (site.lr==lr && site.result==result && site.state==detail && lr) return;
            if (site.lr==lr || !site.lr) {
                site={lr,result,detail};
                break;
            }
        }
    } else if constexpr(op==Op::dialog_unload) {
        if (!result) detail=vm_read32(address+4);
    } else if constexpr(op==Op::matching_create) {
        if (!result) detail=vm_read16(context_out);
    } else if constexpr(op==Op::matching_event) {
        detail=event_key;
    } else if constexpr(op==Op::matching_callback || op==Op::matching_aux_callback ||
                        op==Op::matching_start || op==Op::matching_request) {
        detail=address; // Context ID. Offline requests never assign a request ID.
    }
    trace_offline(name,result,detail,lr);
}
void check_callbacks(ppu_context* c) {
    const uint32_t lr=c->lr;
    uint32_t queue_error=0;
    bool queued=false;
    {
        std::lock_guard guard(lock);
        if (tb::rpcn::enabled()) {
            const auto identity=tb::rpcn::status();
            const auto state=identity.state;
            if (identity.error!=shown_connection_error) {
                shown_connection_error=identity.error;
                if (identity.error==tb::rpcn::ConnectionError::account_in_use)
                    tb_show_network_toast("ACCOUNT ALREADY SIGNED IN","CLOSE OTHER SESSION AND RETRY");
                else if (identity.error==tb::rpcn::ConnectionError::unavailable)
                    tb_show_network_toast("ONLINE CONNECTION FAILED","CHECK CONNECTION AND PROFILE");
            }
            if (dialog_waiting && state!=tb::rpcn::State::connecting) {
                net_dialog_event(0x802u);
                dialog_waiting=false;
            }
            const auto np=manager_state(state);
            if (manager_callback.opd && np!=2 && manager_callback.previous!=np) {
                const uint64_t args[8]={np,0,manager_callback.arg};
                const auto result=cellSysutilQueueGuestCallbackArgs(manager_callback.opd,args);
                trace_offline("rpcn-manager-event",result,np,lr);
                if (!result) manager_callback.previous=np;
            }
            const auto link=net_state(state);
            for (auto& handler:net_handlers) {
                if (!handler.opd || handler.previous==link) continue;
                const uint32_t event=link==3?2:link==0?5:0;
                const uint64_t args[8]={handler.previous,link,event,0,handler.arg};
                const auto result=cellSysutilQueueGuestCallbackArgs(handler.opd,args);
                trace_offline("rpcn-netctl-event",result,link,lr);
                if (!result) handler.previous=link;
            }
        }
        std::vector<Completion> retry;
        for(const auto& event:completions){
            if(event.owner==Owner::net){
                if(net && event.generation==net_generation) {
                    for(int slot=0;slot<4;++slot)cellSysutilQueueEvent(slot,event.status,0);
                    trace_offline("net-dialog-event",0,event.status,lr);
                    queued=true;
                }
                continue;
            }
            if(!event.callback)continue;
            MatchingContext* ctx=nullptr;
            if(event.owner==Owner::matching){
                ctx=context(event.id);
                if(!matching || !ctx || !ctx->pending)continue;
            }else if(!voice_pending || event.generation!=voice_generation)continue;
            const auto result=cellSysutilQueueGuestCallbackArgs(event.callback,event.args.data());
            trace_offline("guest-callback-queue",result,event.args[1],lr);
            if(result){queue_error=result;retry.push_back(event);continue;}
            queued=true;
            if(ctx)ctx->pending=false;else voice_pending=false;
        }
        completions=std::move(retry);
    }
    if (tb::rpcn::enabled()) { tb::rpcn::pump_matching(); tb::rpcn::pump_basic(); }
    delegate("cellSysutilCheckCallback",c);
    if(!c->gpr[3] && queue_error)c->gpr[3]=queue_error;
    if (queued && offline_trace_enabled()) {
        std::lock_guard guard(lock);
        trace_offline("cellSysutilCheckCallback",c->gpr[3],0,lr);
    }
}

struct Binding {const char* name;tank_battles_override_fn fn;};
#define B(name,op) {name,[](ppu_context* c){handler<Op::op>(c,name);}}
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
void tank_battles_init_offline_network(bool use_rpcn){
#ifdef _WIN32
    _putenv_s("PS3_NET_ONLINE",use_rpcn?"1":"");_putenv_s("PSNR_SERVER","");
#else
    if (use_rpcn) setenv("PS3_NET_ONLINE","1",1);
    else unsetenv("PS3_NET_ONLINE");
    unsetenv("PSNR_SERVER");
#endif
    if (use_rpcn)
        fprintf(stderr,"[rpcn] Tank Battles profile: connecting to the selected server\n");
    else
        fprintf(stderr,"[offline] Tank Battles profile: PSN and host matchmaking disabled\n");
}
void tank_battles_register_offline_network(){
    for(const auto& binding:bindings)tank_battles_register_override(ps3_compute_nid(binding.name),binding.fn);
    if (tb::rpcn::enabled()) {
        tb::rpcn::register_basic();
        tb::rpcn::register_matching();
        tb::rpcn::register_score();
        // The NPUB30154 data_receiver publishes its socket before its first
        // recvfrom. The creator immediately tries the peer handshake.
        ppu_thread_set_startup_wait_entry(0x00AB6880);
        tank_battles_register_override(ps3_compute_nid("recvfrom"),
            [](ppu_context* c){
                if (c->lr==0x001D178C) ppu_thread_note_block(c->thread_id);
                delegate("recvfrom",c);
            });
        tank_battles_register_override(ps3_compute_nid("cellMsgDialogOpen2"),
            [](ppu_context* c){
                // The empty-search notice uses a buttonless dialog, but the
                // Join Game screen only exits on the dialog's OK or Cancel.
                // Identify its caller through the dialog wrapper's saved LR.
                const auto caller_slot = c->gpr[1] + 0x2B0;
                if (c->lr==0x001D6D7C && ea(caller_slot,8) && vm_read64(caller_slot)==0x000DB778)
                    c->gpr[3]=(c->gpr[3] & ~0x30ull) | 0x20ull; // BUTTON_TYPE_OK
                delegate("cellMsgDialogOpen2",c);
            });
        tank_battles_register_override(ps3_compute_nid("sceNpManagerGetNpId"),
            [](ppu_context* c){handler<Op::manager_id>(c,"sceNpManagerGetNpId");});
        tank_battles_register_override(ps3_compute_nid("cellNetCtlAddHandler"),
            [](ppu_context* c){handler<Op::net_add>(c,"cellNetCtlAddHandler");});
        tank_battles_register_override(ps3_compute_nid("cellNetCtlDelHandler"),
            [](ppu_context* c){handler<Op::net_delete>(c,"cellNetCtlDelHandler");});
    }
}

void tank_battles_trace_socket(uint32_t nid, uint32_t result, uint32_t lr) {
    // NPUB30154 sys_net imports, in stub order. Never inspect socket buffers.
    static constexpr std::array<uint32_t,18> imports{
        0x139a9e9b,0x1f953b9f,0x28e208bb,0x3f09e20a,0x6005cde1,0x64f66d35,
        0x6db6e8cd,0x88f03575,0x8d1b77fb,0x9647570b,0x9c056962,0xa50777c6,
        0xb0a59804,0xb68d5625,0xc94f6939,0xdc751b40,0xfba04f37,0xfdb8f926
    };
    if (std::find(imports.begin(),imports.end(),nid)==imports.end()) return;
    std::lock_guard guard(lock);
    trace_offline("sys_net",result,nid,lr);
}
