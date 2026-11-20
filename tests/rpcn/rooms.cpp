#include "ppu_recomp.h"
#include "ps3emu/nid.h"
#include "stubs.h"
#include "rpcn/client.h"
#include "rpcn/matching.h"
#include "rpcn/matching_data.h"
#include "external/ps3recomp/libs/network/np_peer_transport.h"

#include <array>
#include <cassert>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <map>
#include <thread>
#include <vector>

using namespace std::chrono_literals;
using Callback = std::array<uint64_t,8>;
static std::array<uint8_t,1 << 20> memory;
static std::map<uint32_t,tank_battles_override_fn> hle;
static std::vector<Callback> callbacks;
extern "C" void tb_rpcn_retain_discovery_event(ppu_context*);

extern "C" {
uint32_t ppu_vm_size=memory.size();
int np_is_initialized() { return 1; }
int32_t cellSysutilQueueGuestCallbackArgs(uint32_t, const uint64_t* args) {
    Callback callback;
    std::copy_n(args,callback.size(),callback.begin());
    callbacks.push_back(callback);
    return 0;
}
uint8_t vm_read8(uint64_t a) { return memory.at(a); }
uint16_t vm_read16(uint64_t a) { return (vm_read8(a)<<8)|vm_read8(a+1); }
uint32_t vm_read32(uint64_t a) { return (uint32_t(vm_read16(a))<<16)|vm_read16(a+2); }
uint64_t vm_read64(uint64_t a) { return (uint64_t(vm_read32(a))<<32)|vm_read32(a+4); }
void vm_write8(uint64_t a,uint8_t v) { memory.at(a)=v; }
void vm_write16(uint64_t a,uint16_t v) { vm_write8(a,v>>8); vm_write8(a+1,v); }
void vm_write32(uint64_t a,uint32_t v) { vm_write16(a,v>>16); vm_write16(a+2,v); }
void vm_write64(uint64_t a,uint64_t v) { vm_write32(a,v>>32); vm_write32(a+4,v); }
}
void tank_battles_register_override(uint32_t nid,tank_battles_override_fn handler) { hle.emplace(nid,handler); }

uint32_t call(const char* name,std::initializer_list<uint64_t> args={}) {
    ppu_context c{};
    std::copy(args.begin(),args.end(),c.gpr+3);
    hle.at(ps3_compute_nid(name))(&c);
    return c.gpr[3];
}

constexpr uint32_t opd=0x10100, ids=0x10200, request=0x10300, options=0x10400, result=0x11000;
constexpr uint32_t reqid=0x10500;
constexpr uint32_t attribute=0x10600, payload=0x10700;
uint16_t context=0;
uint16_t server=0;
uint32_t world=0;

Callback wait_for(unsigned arg,uint16_t event) {
    for (unsigned i=0;i<500;++i) {
        tb::rpcn::pump_matching();
        auto match=std::find_if(callbacks.begin(),callbacks.end(),
                                [&](const auto& cb){return cb[arg]==event;});
        if (match!=callbacks.end()) {
            auto value=*match;
            callbacks.erase(match);
            return value;
        }
        std::this_thread::sleep_for(10ms);
    }
    fprintf(stderr,"Timed out waiting for 0x%x\n",event);
    abort();
}

Callback answer(uint16_t event) {
    auto cb=wait_for(2,event);
    assert(cb[0]==context && cb[1]==vm_read32(reqid));
    assert((cb[1]>>16)==0x1234);
    if (!cb[4] && cb[5])
        assert(call("sceNpMatching2GetEventData",{context,cb[3],result,cb[5]})==cb[5]);
    return cb;
}

void connect(const char* profile) {
    assert(tb::rpcn::start(profile));
    for (int i=0;i<500 && tb::rpcn::status().state!=tb::rpcn::State::online;++i)
        std::this_thread::sleep_for(10ms);
    assert(tb::rpcn::status().state==tb::rpcn::State::online);
    tb::rpcn::register_matching();
    assert(!call("sceNpMatching2Init"));
    memcpy(memory.data()+ids,"NPWR00718",9);
    assert(!call("sceNpMatching2CreateContext",{ids+64,ids,ids+128,ids+300,3}));
    context=vm_read16(ids+300);
    assert(!call("sceNpMatching2RegisterContextCallback",{context,opd,0}));
    assert(!call("sceNpMatching2RegisterRoomEventCallback",{context,opd,0}));
    assert(!call("sceNpMatching2RegisterRoomMessageCallback",{context,opd,0}));
    assert(!call("sceNpMatching2ContextStartAsync",{context,0}));
    const auto start=wait_for(1,0x6f02);
    assert(start[3]==0);
    assert(call("sceNpMatching2GetServerIdListLocal",{context,ids,1})==1);
    server=vm_read16(ids);
    assert(call("sceNpMatching2GetServerIdListLocal",{context,ids,0})==1);
    vm_write16(request,server);
    vm_write32(options,opd);
    vm_write16(options+12,0x1234);
    assert(!call("sceNpMatching2GetWorldInfoList",{context,request,options,reqid}));
    assert(answer(2)[4]==0);
    assert(vm_read32(result+4)>0);
    world=vm_read32(vm_read32(result));
    printf("server=%u world=%u\n",server,world);
    // A very fast reply must survive the title's menu animation. Retain one
    // success node, then stop retaining after that node has been consumed.
    constexpr uint32_t queue=0x20000, head=0x20100, first=0x20200, second=0x20220;
    vm_write32(queue+0x54,head); vm_write32(queue+0x58,2);
    vm_write32(head,first); vm_write32(first,second); vm_write32(second,head);
    vm_write32(first+8,1); vm_write32(second+8,1);
    vm_write8(first+12,1); vm_write8(second+12,1);
    ppu_context c{}; c.gpr[3]=queue;
    tb_rpcn_retain_discovery_event(&c);
    assert(vm_read8(first+12)==0 && vm_read8(second+12)==1);
    vm_write32(head,second); vm_write32(queue+0x58,1);
    tb_rpcn_retain_discovery_event(&c);
    assert(vm_read8(second+12)==1);
    vm_write32(reqid,0xdeadbeef);
    assert(call("sceNpMatching2CreateJoinRoom",{context,0xffffffff,options,reqid})==0x8002230a);
    assert(vm_read32(reqid)==0xdeadbeef);
}

uint64_t create(unsigned slots, bool auto_grant) {
    std::memset(memory.data()+request,0,108);
    vm_write32(request,world);
    vm_write32(request+16,slots);
    if (auto_grant) vm_write32(request+20,tb::rpcn::room_owner_auto_grant);
    assert(!call("sceNpMatching2CreateJoinRoom",{context,request,options,reqid}));
    const auto cb=answer(0x101);
    assert(cb[4]==0);
    const auto room=vm_read32(result);
    assert(vm_read32(room+40)==1 && vm_read32(room+44)==vm_read32(room+48));
    printf("created room %llu\n",static_cast<unsigned long long>(vm_read64(room+16)));
    return vm_read64(room+16);
}

uint64_t search(bool empty=false) {
    std::memset(memory.data()+request,0,56);
    vm_write32(request+4,world);
    vm_write32(request+16,1);
    vm_write32(request+20,20);
    assert(!call("sceNpMatching2SearchRoom",{context,request,options,reqid}));
    assert(answer(0x106)[4]==0);
    if (empty) {
        assert(vm_read32(result+8)==0 && vm_read32(result+12)==0);
        printf("no stale room found\n");
        return 0;
    }
    assert(vm_read32(result+8)==1);
    const auto room=vm_read32(result+12);
    assert(vm_read16(room+38)==1);
    const auto id=vm_read64(room+24);
    printf("found room %llu\n",static_cast<unsigned long long>(id));
    vm_write64(request,id);
    assert(!call("sceNpMatching2SignalingGetPingInfo",{context,request,options,reqid}));
    assert(answer(0xe01)[4]==0);
    assert(vm_read16(result)==server && vm_read32(result+4)==world && vm_read64(result+8)==id);
    assert(vm_read32(result+16)==20000); // Stock RPCN's server-supplied value, not peer RTT.
    return id;
}

void leave(uint64_t id) {
    std::memset(memory.data()+request,0,28);
    vm_write64(request,id);
    assert(!call("sceNpMatching2LeaveRoom",{context,request,options,reqid}));
    assert(answer(0x103)[4]==0);
    assert(vm_read64(result)==id);
}

void send(uint64_t id,const char* text) {
    std::memset(memory.data()+request,0,32);
    vm_write64(request,id);
    vm_write8(request+8,1); // Broadcast to other members.
    std::memcpy(memory.data()+payload,text,std::strlen(text));
    vm_write32(request+20,payload);
    vm_write32(request+24,std::strlen(text));
    assert(!call("sceNpMatching2SendRoomMessage",{context,request,options,reqid}));
    assert(answer(0x108)[4]==0);
}

void receive(uint64_t id,const char* expected) {
    const auto event=wait_for(3,0x2102);
    assert(event[0]==context && event[1]==id && event[2]);
    assert(call("sceNpMatching2GetEventData",{context,event[4],result,event[6]})==event[6]);
    assert(vm_read32(result+16)==std::strlen(expected));
    assert(!std::memcmp(memory.data()+vm_read32(result+12),expected,std::strlen(expected)));
    printf("room message received\n");
}

void update(uint64_t id) {
    std::memset(memory.data()+request,0,44);
    vm_write64(request,id);
    vm_write16(attribute,0x57);
    vm_write32(attribute+4,payload);
    vm_write32(attribute+8,7);
    std::memcpy(memory.data()+payload,"fixture",7);
    vm_write32(request+16,attribute);
    vm_write32(request+20,1);
    assert(!call("sceNpMatching2SetRoomDataInternal",{context,request,options,reqid}));
    assert(answer(0x109)[4]==0);
}

void updated(uint64_t id) {
    const auto event=wait_for(2,0x1106);
    assert(event[0]==context && event[1]==id);
    assert(call("sceNpMatching2GetEventData",{context,event[3],result,event[5]})==event[5]);
    assert(vm_read32(result+32)==1);
    const auto attr=vm_read32(vm_read32(result+28));
    assert(vm_read16(attr+12)==0x57 && vm_read32(attr+20)==7);
    assert(!std::memcmp(memory.data()+vm_read32(attr+16),"fixture",7));
    printf("room attribute update received\n");
}

void wire_checks() {
    using namespace tb::rpcn;
    vm_write32(request,1); vm_write32(request+16,2);
    const Bytes expected{8,1,24,2,0x82,1,0};
    assert(room_request(Request::create_room,request)==expected);
    for (const Reply bad : {Reply{},Reply{{0,255,255,255,255}},Reply{{0,1,0,0,0,255}}}) {
        bool rejected=false;
        try { room_reply(Request::create_room,bad,"FixturePlayer"); }
        catch (const std::runtime_error&) { rejected=true; }
        assert(rejected);
    }
    Notification destroyed{2,{9,0,0,0,0,0,0,0,4,0,0,0,10,2,8,1}};
    const auto notice=room_notice(destroyed,"FixturePlayer");
    assert(notice.room==9 && notice.event==0x1104 && notice.result.image.bytes[0]==1);
    Notification message{9,{9,0,0,0,0,0,0,0,16,0,9,0,0,0,18,2,8,1,42,3,'f','o','o'}};
    const auto msg=room_notice(message,"FixturePlayer");
    assert(msg.room==9 && msg.event==0x2102 && msg.result.member==16);
    assert(msg.result.image.read(16,4)==3);
    const auto pointer=msg.result.image.read(12,4);
    assert(!std::memcmp(msg.result.image.bytes.data()+pointer,"foo",3));
    const auto peer=signaling_address(Reply{{0,11,0,0,0,10,4,192,168,50,20,18,3,8,202,28}});
    assert((peer.ip==std::array<unsigned char,4>{192,168,50,20}) && peer.port==3658);
    for (const Reply bad:{Reply{},Reply{{1}},Reply{{0,0,0,0,0}},Reply{{0,11,0,0,0,10,4,0,0,0,0,18,3,8,202,28}}}) {
        bool rejected=false;
        try {signaling_address(bad);} catch (const std::runtime_error&) {rejected=true;}
        assert(rejected);
    }
    printf("wire format and malformed replies passed\n");
}

void check_login_retry(const char* profile) {
    using namespace tb::rpcn;
    const auto start_time=std::chrono::steady_clock::now();
    assert(start(profile));
    while (status().state==State::connecting && std::chrono::steady_clock::now()<start_time+8s)
        std::this_thread::sleep_for(10ms);
    assert(status().state==State::offline);

    // The fixture holds the old login beyond the initial retry window.
    std::this_thread::sleep_until(start_time+7s);
    retry_sign_in();
    retry_sign_in();
    const auto deadline=std::chrono::steady_clock::now()+3s;
    while (status().state==State::connecting && std::chrono::steady_clock::now()<deadline)
        std::this_thread::sleep_for(10ms);
    assert(status().state==State::online && status().user_id==42);
    retry_sign_in();
    stop();
    retry_sign_in();
    assert(status().state==State::offline);
}

int main(int argc,char** argv) {
    if (argc==2 && std::string_view(argv[1])=="--wire") { wire_checks(); return 0; }
    if (argc==3 && std::string_view(argv[1])=="--login-retry") {
        check_login_retry(argv[2]);
        return 0;
    }
    if (argc!=4) {
        fprintf(stderr,"Usage: matching_rooms PROFILE host|join normal|fullROOM|owner|close\n"
                       "       matching_rooms PROFILE peer|signal PEER_NPID\n");
        return 2;
    }
    setvbuf(stdout,nullptr,_IONBF,0);
    connect(argv[1]);
    std::string mode=argv[2];
    const bool owner=std::string_view(argv[3])=="owner";
    const bool close_room=std::string_view(argv[3])=="close";
    const bool full=std::string_view(argv[3]).starts_with("full");
    if (mode=="signal" || mode=="peer") {
        const auto identity=tb::rpcn::status();
        std::memset(memory.data()+payload,0,36);
        std::memcpy(memory.data()+payload,identity.npid.data(),identity.npid.size());
        assert(!call("sceNpSignalingCreateCtx",{payload,opd,0x1234,ids+400}));
        const auto signaling=vm_read32(ids+400);
        bool registered=false;
        for (unsigned attempt=0;attempt<10 && !registered;++attempt) {
            tb::rpcn::pump_matching();
            std::this_thread::sleep_for(100ms);
            tb::rpcn::Bytes self(identity.npid.begin(),identity.npid.end());self.push_back(0);
            auto query=tb::rpcn::submit(tb::rpcn::Request::signaling_info,std::move(self));
            assert(query.valid() && query.wait_for(3s)==std::future_status::ready);
            try {
                const auto address=tb::rpcn::signaling_address(query.get());
                registered=address.port==3658;
            } catch (const std::runtime_error&) {}
        }
        assert(registered);
        puts("RPCN confirmed the login user ID and registered local signaling address");
        assert(!call("sceNpSignalingAddExtendedHandler",{signaling,opd+8,0x5678}));
        std::memset(memory.data()+payload,0,36);
        assert(std::strlen(argv[3])<=16);
        std::memcpy(memory.data()+payload,argv[3],std::strlen(argv[3]));
        if (mode=="peer") {
            printf("Waiting up to 120 seconds for %s to register a UDP endpoint\n",argv[3]);
            const auto deadline=std::chrono::steady_clock::now()+120s;
            bool available=false;
            while (!available && std::chrono::steady_clock::now()<deadline) {
                tb::rpcn::pump_matching();
                tb::rpcn::Bytes target(argv[3],argv[3]+std::strlen(argv[3])+1);
                auto query=tb::rpcn::submit(tb::rpcn::Request::signaling_info,std::move(target));
                assert(query.valid() && query.wait_for(3s)==std::future_status::ready);
                try { available=tb::rpcn::signaling_address(query.get()).port!=0; }
                catch (const std::runtime_error&) {}
                std::this_thread::sleep_for(100ms);
            }
            if (!available) {
                fprintf(stderr,"RPCN did not return a UDP endpoint for %s before the deadline\n",argv[3]);
                tb::rpcn::reset_matching();
                tb::rpcn::stop();
                return 1;
            }
        }
        assert(!call("sceNpSignalingActivateConnection",{signaling,payload,ids+404}));
        const auto connection=vm_read32(ids+404);
        assert(!call("sceNpSignalingGetConnectionStatus",{signaling,connection,result,result+4,result+8}));
        assert(vm_read32(result)==1);
        assert(call("sceNpSignalingGetConnectionInfo",{signaling,connection,2,result+12})==0x8002a80e);
        if (mode=="peer") {
            const auto deadline=std::chrono::steady_clock::now()+10s;
            while (std::chrono::steady_clock::now()<deadline && vm_read32(result)!=2) {
                tb::rpcn::pump_matching();
                assert(!call("sceNpSignalingGetConnectionStatus",{signaling,connection,result,result+4,result+8}));
                std::this_thread::sleep_for(10ms);
            }
            if (vm_read32(result)!=2) {
                fprintf(stderr,"UDP activation with %s did not complete\n",argv[3]);
                tb::rpcn::reset_matching();
                tb::rpcn::stop();
                return 1;
            }
            assert(vm_read32(result+4) && vm_read16(result+8));
            const auto peer_ip=vm_read32(result+4);
            const auto peer_port=vm_read16(result+8);
            for (const auto [event,arg]:std::array<std::pair<unsigned,unsigned>,4>{{{1,0x1234},{1,0x5678},{10,0x5678},{12,0x5678}}}) {
                assert(std::count_if(callbacks.begin(),callbacks.end(),[&](const auto& cb){
                    return cb[0]==signaling && cb[1]==connection && cb[2]==event && cb[3]==0 && cb[4]==arg;
                })==1);
            }
            assert(!call("sceNpSignalingGetConnectionInfo",{signaling,connection,4,result}));
            assert(vm_read32(result)==peer_ip && vm_read16(result+4)==peer_port);
            assert(!call("sceNpSignalingGetConnectionInfo",{signaling,connection,2,result}));
            assert(vm_read32(result)==100'000'000);
            assert(!call("sceNpSignalingGetConnectionInfo",{signaling,connection,3,result}));
            assert(!std::memcmp(memory.data()+result,argv[3],std::strlen(argv[3])));
            // A second context shares the verified peer without removing the
            // first context's transport when that second context is destroyed.
            assert(!call("sceNpSignalingCreateCtx",{payload,opd,0x1234,ids+408}));
            const auto second=vm_read32(ids+408);
            assert(!call("sceNpSignalingActivateConnection",{second,payload,ids+412}));
            assert(!call("sceNpSignalingGetConnectionStatus",{second,vm_read32(ids+412),result,0,0}));
            assert(vm_read32(result)==2);
            assert(!call("sceNpSignalingDestroyCtx",{second}));
            for (unsigned i=0;i<200;++i) {tb::rpcn::pump_matching();std::this_thread::sleep_for(10ms);}
            assert(!call("sceNpSignalingGetConnectionStatus",{signaling,connection,result,0,0}));
            assert(vm_read32(result)==2);
            printf("PASS: HLE peer active at %u.%u.%u.%u:%u, exact primary/extended callbacks and shared context cleanup\n",
                   peer_ip>>24,(peer_ip>>16)&255,(peer_ip>>8)&255,peer_ip&255,peer_port);
            assert(!call("sceNpSignalingDestroyCtx",{signaling}));
            tb::rpcn::reset_matching();tb::rpcn::stop();
            return 0;
        }
        const auto deadline=std::chrono::steady_clock::now()+10s;
        bool dead=false;
        while (std::chrono::steady_clock::now()<deadline && !dead) {
            tb::rpcn::pump_matching();
            dead=std::any_of(callbacks.begin(),callbacks.end(),[&](const auto& cb){return cb[0]==signaling && cb[1]==connection && cb[2]==0 && cb[3]==0x8002a80f;});
            std::this_thread::sleep_for(10ms);
        }
        assert(dead);
        assert(!call("sceNpSignalingGetConnectionStatus",{signaling,connection,result,0,0}));
        assert(vm_read32(result)==0);
        assert(!call("sceNpSignalingDestroyCtx",{signaling}));
        assert(!call("sceNpSignalingCreateCtx",{payload,0,0,ids+400}));
        assert(vm_read32(ids+400)!=signaling);
        assert(!call("sceNpSignalingDestroyCtx",{vm_read32(ids+400)}));
        tb::rpcn::reset_matching();tb::rpcn::stop();
        puts("PASS: signaling pending, bounded unreachable callback, inactive status and context/port reuse");
        return 0;
    } else if (mode=="cancel") {
        std::memset(memory.data()+request,0,108);
        vm_write32(request,world); vm_write32(request+16,2);
        assert(!call("sceNpMatching2CreateJoinRoom",{context,request,options,reqid}));
        assert(!call("sceNpMatching2ContextStop",{context}));
        assert(!call("sceNpMatching2DestroyContext",{context}));
        for (unsigned i=0;i<150;++i) { tb::rpcn::pump_matching(); std::this_thread::sleep_for(10ms); }
        assert(std::none_of(callbacks.begin(),callbacks.end(),[](const auto& cb){return cb[2]==0x101;}));
        printf("pending create cancelled\n");
        tb::rpcn::stop();
        return 0;
    } else if (mode=="empty") {
        search(true);
    } else if (mode=="host") {
        const auto id=create(full?1:2,owner);
        if (full) {
            std::this_thread::sleep_for(3s);
        } else {
            auto joined=wait_for(2,0x1101);
            assert(joined[1]==id && joined[5]);
            assert(call("sceNpMatching2GetEventData",{context,joined[3],result,joined[5]})==joined[5]);
            assert(vm_read16(vm_read32(result)+56)!=0);
            printf("member joined\n");
            send(id,"host lobby");
            receive(id,"host lobby"); // RPCN broadcasts also reach the sender.
            update(id);
            receive(id,"guest lobby");
            if (owner || close_room) {
                leave(id);
                printf("owner left\n");
                tb::rpcn::stop();
                return 0;
            }
            auto left=wait_for(2,0x1102);
            assert(left[1]==id);
            printf("member left\n");
        }
        leave(id);
    } else {
        const auto id=full?std::strtoull(argv[3]+4,nullptr,10):search();
        std::memset(memory.data()+request,0,48);
        vm_write64(request,id);
        assert(!call("sceNpMatching2JoinRoom",{context,request,options,reqid}));
        auto joined=answer(0x102);
        if (full) assert(joined[4]==0x80022b19);
        else {
            assert(joined[4]==0);
            const auto room=vm_read32(result);
            assert(vm_read64(room+16)==id && vm_read32(room+40)==2 && vm_read32(room+44)!=vm_read32(room+48));
            printf("joined room %llu, two members\n",static_cast<unsigned long long>(id));
            receive(id,"host lobby");
            updated(id);
            send(id,"guest lobby");
            if (owner) {
                wait_for(2,0x1102);
                const auto update=wait_for(2,0x1105);
                assert(update[1]==id && update[5]==32);
                assert(call("sceNpMatching2GetEventData",{context,update[3],result,32})==32);
                assert(vm_read16(result)!=vm_read16(result+2));
                printf("server elected new owner %u\n",vm_read16(result+2));
            }
            if (close_room) {
                const auto closed=wait_for(2,0x1104);
                assert(closed[1]==id && closed[4]==0 && closed[5]==28);
                assert(call("sceNpMatching2GetEventData",{context,closed[3],result,28})==28);
                assert(vm_read8(result)==1);
                search(true);
            } else {
                leave(id);
            }
        }
    }
    assert(!call("sceNpMatching2ContextStop",{context}));
    assert(!call("sceNpMatching2DestroyContext",{context}));
    tb::rpcn::stop();
}

extern "C" void np_set_peer_transport(const np_peer_transport*) {}
