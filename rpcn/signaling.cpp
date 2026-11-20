#include "signaling.h"
#include "external/ps3recomp/libs/network/np_peer_transport.h"
#include "matching_data.h"
#include "peer_udp.h"
#include "stubs.h"
#include "ppu_recomp.h"
#include "ps3emu/nid.h"
#include <array>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <map>
#include <memory>
#include <mutex>

extern "C" int np_is_initialized();
extern "C" int32_t cellSysutilQueueGuestCallbackArgs(uint32_t,const uint64_t*);
namespace tb::rpcn {
namespace {
constexpr uint32_t not_initialized=0x8002a801, bad_context=0x8002a805;
constexpr uint32_t bad_connection=0x8002a80e, unreachable=0x8002a80f, invalid_argument=0x8002a815;
constexpr uint32_t own_np_id=0x8002a816, too_many_connections=0x8002a817;
struct Handler {uint32_t opd=0,arg=0;};
struct Context {Handler handler,extended;};
struct Connection {
    uint32_t context;
    std::string peer;
    std::future<Reply> endpoint;
    PeerAddress address;
    uint32_t status=1,latency=0;
    bool probing=false;
    std::chrono::steady_clock::time_point begin=std::chrono::steady_clock::now(),retry{};

};
struct Callback {uint32_t context,opd;std::array<uint64_t,8> args;};
std::mutex lock;
std::map<uint32_t,Context> contexts;
std::map<uint32_t,Connection> connections;
std::vector<Callback> callbacks;
std::unique_ptr<PeerUdp> udp;
uint32_t next_context=1,next_connection=1;

void event(uint32_t id,Connection& connection,uint32_t code,uint32_t error=0) {
    const auto ctx=contexts.find(connection.context);
    if (ctx==contexts.end()) return;
    const std::array handlers{ctx->second.handler,ctx->second.extended};
    for (unsigned i=code>=10?1:0;i<handlers.size();++i) {
        const auto h=handlers[i];
        if (h.opd) callbacks.push_back({connection.context,h.opd,{connection.context,id,code,error,h.arg}});
    }
    fprintf(stderr,"[rpcn-signaling] context=%u connection=%u peer=%s event=%u error=0x%08x\n",
            connection.context,id,connection.peer.c_str(),code,error);
}

enum class Op {create,destroy,extended,activate,deactivate,status,info,lookup};
uint32_t execute(Op op,ppu_context& c) {
    if (!np_is_initialized()) return not_initialized;
    const uint32_t a=c.gpr[3],b=c.gpr[4],out=c.gpr[5];
    if (op==Op::create) {
        if (!guest_range(a,36) || !guest_range(c.gpr[6],4) || (b && !guest_range(b,8))) return invalid_argument;
        if (contexts.size()>=8) return 0x8002a812;
        const auto identity=status();
        if (identity.state!=State::online) return unreachable;
        if (!udp) {
            try {
                const auto colon=identity.endpoint.rfind(':');
                udp=std::make_unique<PeerUdp>(identity.npid,identity.user_id,identity.endpoint.substr(0,colon));
            } catch (const std::exception& error) {
                fprintf(stderr,"[rpcn-signaling] %s\n",error.what());return unreachable;
            }
        }
        const auto id=next_context++;
        contexts.emplace(id,Context{{b,out},{}});vm_write32(c.gpr[6],id);return 0;
    }
    const auto ctx=contexts.find(a);
    if (ctx==contexts.end()) return bad_context;
    if (op==Op::destroy) {
        for (auto it=connections.begin();it!=connections.end();) {
            if (it->second.context==a) {
                const auto peer=it->second.peer;
                it=connections.erase(it);
                if (udp && std::none_of(connections.begin(),connections.end(),[&](const auto& item){return item.second.peer==peer;}))
                    udp->deactivate(peer);
            }
            else ++it;
        }
        std::erase_if(callbacks,[=](const auto& item){return item.context==a;});
        contexts.erase(ctx);
        if (contexts.empty()) udp.reset();
        return 0;
    }
    if (op==Op::extended) {
        if (b && !guest_range(b,8)) return invalid_argument;
        ctx->second.extended={b,out};return 0;
    }
    if (op==Op::activate || op==Op::lookup) {
        if (op==Op::activate && callbacks.size()>=128) return 0x8002a812;
        if (op==Op::activate && (!udp || !udp->usable())) {
            const auto identity=status();
            if (identity.state!=State::online) return unreachable;
            try {
                udp=std::make_unique<PeerUdp>(identity.npid,identity.user_id,
                                            identity.endpoint.substr(0,identity.endpoint.rfind(':')));
            } catch (const std::exception& error) {
                fprintf(stderr,"[rpcn-signaling] %s\n",error.what());
                return unreachable;
            }
        }
        if (!guest_range(b,36) || !guest_range(out,4)) return invalid_argument;
        std::string name;
        for (unsigned i=0;i<16;++i) {const char byte=vm_read8(b+i);if(!byte)break;name.push_back(byte);}
        if (name.empty()) return invalid_argument;
        if (name==status().npid) return own_np_id;
        for (auto it=connections.begin();it!=connections.end();++it) {
            if (it->second.context!=a || it->second.peer!=name) continue;
            if (it->second.status) {vm_write32(out,it->first);return 0;}
            connections.erase(it);break;
        }
        if (op==Op::lookup) return bad_connection;
        if (connections.size()>=16) return too_many_connections;
        const auto id=next_connection++;
        const auto shared=std::find_if(connections.begin(),connections.end(),[&](const auto& item){
            return item.second.peer==name && item.second.status==2;
        });
        Connection next{a,std::move(name),{}, {}};
        if (shared!=connections.end()) {
            next.address=shared->second.address;
            next.status=2;
            next.latency=shared->second.latency;
            next.probing=true;
        }
        auto inserted=connections.emplace(id,std::move(next)).first;
        if (inserted->second.status==2) {
            event(id,inserted->second,1);
            event(id,inserted->second,10);
            event(id,inserted->second,12);
        }
        vm_write32(out,id);return 0;
    }
    auto found=connections.find(b);
    if (found==connections.end() || found->second.context!=a) return bad_connection;
    auto& connection=found->second;
    if (op==Op::deactivate) {
        const auto peer=connection.peer;
        connections.erase(found);
        if (udp && std::none_of(connections.begin(),connections.end(),[&](const auto& item){return item.second.peer==peer;}))
            udp->deactivate(peer);
        return 0;
    }
    if (op==Op::status) {
        if (!guest_range(out,4) || (c.gpr[6] && !guest_range(c.gpr[6],4)) ||
            (c.gpr[7] && !guest_range(c.gpr[7],2))) return invalid_argument;
        vm_write32(out,connection.status);
        uint32_t ip=0;for (const auto byte:connection.address.ip) ip=ip<<8|byte;
        if (c.gpr[6]) vm_write32(c.gpr[6],ip);
        if (c.gpr[7]) vm_write16(c.gpr[7],connection.address.port);
        return 0;
    }
    const auto destination=c.gpr[6];
    if (op==Op::info) {
        const uint32_t size=out==3?36:out==4 || out==5?8:4;
        if (!guest_range(destination,size)) return invalid_argument;
        if (connection.status!=2) return bad_connection;
        if (out==1) {vm_write32(destination,connection.latency);return 0;}
        if (out==3) {
            for (unsigned i=0;i<36;++i) vm_write8(destination+i,i<connection.peer.size()?connection.peer[i]:0);
            return 0;
        }
        if (out==4 || out==5) {
            const auto address=out==4?connection.address:udp->mapped();
            uint32_t ip=0;for (const auto byte:address.ip) ip=ip<<8|byte;
            vm_write32(destination,ip);vm_write16(destination+4,address.port);vm_write16(destination+6,0);return 0;
        }
        return invalid_argument;
    }
    return invalid_argument;
}
template<Op op> void handler(ppu_context* c) {
    std::lock_guard guard(lock);
    const auto ctx=c->gpr[3], argument=c->gpr[4], code=c->gpr[5], caller=c->lr;
    c->gpr[3]=execute(op,*c);
    const char* trace=std::getenv("TB_OFFLINE_TRACE");
    if (trace && trace[0]=='1')
        fprintf(stderr,"[rpcn-signaling-call] op=%u context=%llu argument=0x%llx code=0x%llx result=0x%08x lr=0x%08x\n",
                static_cast<unsigned>(op),static_cast<unsigned long long>(ctx),
                static_cast<unsigned long long>(argument),static_cast<unsigned long long>(code),
                static_cast<uint32_t>(c->gpr[3]),static_cast<uint32_t>(caller));
}
}
void reset_signaling() {
    std::lock_guard guard(lock);contexts.clear();connections.clear();callbacks.clear();udp.reset();
}
void pump_signaling() {
    using namespace std::chrono_literals;
    std::lock_guard guard(lock);
    if (!udp) {
        std::erase_if(callbacks,[](const auto& item) {
            return !contexts.contains(item.context) || !cellSysutilQueueGuestCallbackArgs(item.opd,item.args.data());
        });
        return;
    }
    if (status().state!=State::online) {
        for (auto& [id,connection]:connections) if (connection.status) {
            connection.status=0;event(id,connection,0,unreachable);
        }
        udp.reset();
    } else {
        const auto notifications=udp->pump();
        const auto now=std::chrono::steady_clock::now();
        for (auto& [id,connection]:connections) {
            if (connection.status==1 && now-connection.begin>8s) {
                connection.status=0;
                connection.endpoint={};
                udp->deactivate(connection.peer);
                event(id,connection,0,unreachable);
                continue;
            }
            if (connection.status==1 && !connection.endpoint.valid() &&
                !connection.address.port && udp->mapped().port && now>=connection.retry) {
                Bytes request(connection.peer.begin(),connection.peer.end());
                request.push_back(0);
                connection.endpoint=submit(Request::signaling_info,std::move(request));
                connection.retry=now+250ms;
            }
            const bool was_probing=connection.probing;
            if (connection.endpoint.valid() && connection.endpoint.wait_for(0ms)==std::future_status::ready) {
                try {
                    connection.address=signaling_address(connection.endpoint.get());
                    const auto established=udp->activate(connection.peer,connection.address);
                    connection.probing=true;
                    if (established) {
                        connection.status=2;
                        connection.latency=*established;
                        event(id,connection,1);
                        event(id,connection,10);
                        event(id,connection,12);
                    }
                } catch (const std::exception& error) {
                    fprintf(stderr,"[rpcn-signaling] peer=%s %s\n",connection.peer.c_str(),error.what());
                    // The other client may still be registering its UDP endpoint.
                    // Retry inside the activation deadline before reporting failure.
                    connection.retry=now+250ms;
                }
            }
            for (const auto& notification:notifications) if (was_probing && connection.peer==notification.npid) {
                if ((!connection.status && !notification.active) || (connection.status==2 && notification.active)) continue;
                connection.status=notification.active?2:0;connection.latency=notification.latency_us;
                event(id,connection,notification.active?1:0,notification.active?0:unreachable);
                if (notification.active) {event(id,connection,10);event(id,connection,12);}
            }
        }
    }
    std::erase_if(callbacks,[](const auto& item) {
        return !contexts.contains(item.context) || !cellSysutilQueueGuestCallbackArgs(item.opd,item.args.data());
    });
}
void register_signaling() {
    static const np_peer_transport transport{
        []()->int64_t {std::lock_guard guard(lock);return udp?udp->take_socket():-1;},
        [](const void* packet,uint32_t size,const uint8_t ip[4],uint16_t port)->int {
            std::lock_guard guard(lock);
            PeerAddress source;std::copy_n(ip,4,source.ip.begin());source.port=port;
            return udp && udp->filter(packet,size,source);
        },
        [] {
            std::lock_guard guard(lock);
            if (udp) udp->socket_closed();
            for (auto& [id,connection]:connections) {
                connection.endpoint={};
                if (connection.status) {
                    connection.status=0;
                    event(id,connection,0);
                }
            }
        }
    };
    np_set_peer_transport(&transport);
    struct Binding {const char* name;tank_battles_override_fn handler;};
    static constexpr Binding bindings[]={
        {"sceNpSignalingCreateCtx",handler<Op::create>},{"sceNpSignalingDestroyCtx",handler<Op::destroy>},
        {"sceNpSignalingAddExtendedHandler",handler<Op::extended>},{"sceNpSignalingActivateConnection",handler<Op::activate>},
        {"sceNpSignalingDeactivateConnection",handler<Op::deactivate>},{"sceNpSignalingGetConnectionStatus",handler<Op::status>},
        {"sceNpSignalingGetConnectionInfo",handler<Op::info>},{"sceNpSignalingGetConnectionFromNpId",handler<Op::lookup>},
    };
    for (const auto& binding:bindings) tank_battles_register_override(ps3_compute_nid(binding.name),binding.handler);
}
}
