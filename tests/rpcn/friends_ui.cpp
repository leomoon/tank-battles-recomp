// Exercise production Friends input/callback handling over a real RPCN connection.
// Only guest memory and the renderer are replaced; friendship, message and room
// transports use the same implementations as the player.
#define TB_FRIENDS_UI_LIVE
#include "basic_adapter.cpp"
#include "rpcn/matching.h"
#include "external/ps3recomp/libs/network/np_peer_transport.h"
extern "C" void np_set_peer_transport(const np_peer_transport*) {}
#include <cstring>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <thread>

using namespace tb::rpcn;
constexpr uint32_t comm=0x30000,handler=0x30100,message=0x30200,payload=0x30300;
constexpr uint32_t request=0x30400,options=0x30500,request_id=0x30600,result=0x31000;
constexpr uint32_t event=0x32000,from=0x32100,data=0x32200,capacity=0x32300;
uint16_t room_context=0;
uint64_t room_id=0;

Callback wait_callback(unsigned index,unsigned code) {
    const auto deadline=std::chrono::steady_clock::now()+6s;
    while (std::chrono::steady_clock::now()<deadline) {
        pump_basic();pump_matching();
        const auto it=std::find_if(callbacks.begin(),callbacks.end(),[&](const auto& cb){return cb[index]==code;});
        if(it!=callbacks.end()) {const auto cb=*it;callbacks.erase(it);return cb;}
        std::this_thread::sleep_for(2ms);
    }
    throw std::runtime_error("Timed out waiting for guest callback");
}
Callback answer(unsigned code) {
    const auto cb=wait_callback(2,code);
    if(cb[4]) throw std::runtime_error("Matching2 rejected the room operation");
    if(cb[5]) assert(call("sceNpMatching2GetEventData",{room_context,cb[3],result,cb[5]})==cb[5]);
    return cb;
}
void setup_matching() {
    if(room_context) return;
    register_matching();assert(!call("sceNpMatching2Init"));
    assert(!call("sceNpMatching2CreateContext",{comm+64,comm,comm+128,comm+300,3}));
    room_context=vm_read16(comm+300);
    assert(!call("sceNpMatching2RegisterContextCallback",{room_context,handler,0}));
    assert(!call("sceNpMatching2ContextStartAsync",{room_context,0}));
    assert(wait_callback(1,0x6f02)[3]==0);
    assert(call("sceNpMatching2GetServerIdListLocal",{room_context,request,1})==1);
    const auto server=vm_read16(request);
    vm_write32(options,handler);vm_write16(request,server);
    assert(!call("sceNpMatching2GetWorldInfoList",{room_context,request,options,request_id}));
    answer(2);
}
void barrier() {
    const std::string id="NPWR00718_00";
    auto future=submit(Request::server_list,{id.begin(),id.end()});
    if(!future.valid() || future.wait_for(6s)!=std::future_status::ready || future.get().body.empty())
        throw std::runtime_error("RPCN barrier failed");
    pump_basic();
}
void emit(const std::string& code) {
    pump_basic();
    const auto state=relationships();
    auto list=[](const auto& rows){
        std::cout<<'[';
        for(size_t i=0;i<rows.size();++i) std::cout<<(i?",":"")<<std::quoted(rows[i]);
        std::cout<<']';
    };
    std::cout<<"{\"result\":"<<std::quoted(code)<<",\"friends\":";
    std::vector<std::string> mutual;for(const auto& f:state.friends) mutual.push_back(f.npid);list(mutual);
    std::cout<<",\"incoming\":";list(state.incoming);std::cout<<",\"outgoing\":";list(state.outgoing);
    std::cout<<",\"choices\":";list(displayed_choices);
    std::cout<<",\"rows\":";list(friend_view.rows);
    std::cout<<",\"actions\":";list(friend_view.actions);
    std::cout<<",\"section\":"<<unsigned(friend_view.section)<<",\"selected\":"<<friend_view.selected
             <<",\"friends_open\":"<<(friend_panel?"true":"false")<<",\"picker_open\":"<<(panel?"true":"false")
             <<",\"status\":"<<std::quoted(friend_view.status)<<",\"room\":"<<room_id
             <<",\"sent_callbacks\":"<<std::count_if(callbacks.begin(),callbacks.end(),[](const auto& cb){return cb[0]==13 && !cb[1];})
             <<",\"request_toasts\":"<<friend_toasts.size()<<",\"invite_toast\":"<<std::quoted(toast_sender)<<"}\n"<<std::flush;
}
int main(int argc,char** argv) {
    if(argc!=2 || !start(argv[1])) return 2;
    try {
        const auto deadline=std::chrono::steady_clock::now()+6s;
        while(status().state==State::connecting && std::chrono::steady_clock::now()<deadline) std::this_thread::sleep_for(2ms);
        if(status().state!=State::online) throw std::runtime_error("Login failed");
        register_basic();std::memcpy(memory.data()+comm,"NPWR00718",9);
        assert(!call("sceNpBasicRegisterContextSensitiveHandler",{comm,handler,0}));
        emit("ready");
        for(std::string line;std::getline(std::cin,line);) {
            std::istringstream input(line);std::string command;input>>command;
            if(command=="quit") break;
            if(command=="snapshot") barrier();
            else if(command=="toggle") {toggle_requested=true;pump_basic();}
            else if(command=="press") {std::string mask;input>>mask;buttons=std::stoul(mask,nullptr,0);pump_basic();}
            else if(command=="text") {std::string text;input>>text;tb_friends_input event{TB_FRIENDS_TEXT,0,{}};
                assert(text.size()<sizeof event.text);std::memcpy(event.text,text.data(),text.size());native_inputs.push_back(event);pump_basic();}
            else if(command=="done") {native_inputs.push_back({TB_FRIENDS_KEY,TB_FRIENDS_ENTER,{}});pump_basic();}
            else if(command=="finish") {
                const auto deadline=std::chrono::steady_clock::now()+7s;
                do {pump_basic();std::this_thread::sleep_for(2ms);} while(friend_view.busy && std::chrono::steady_clock::now()<deadline);
                assert(!friend_view.busy);barrier();
            } else if(command=="create") {
                setup_matching();const auto world=vm_read32(vm_read32(result));
                std::memset(memory.data()+request,0,108);vm_write32(request,world);
                vm_write32(request+16,4);vm_write32(request+20,0x10000000); // Hidden from public searches.
                assert(!call("sceNpMatching2CreateJoinRoom",{room_context,request,options,request_id}));answer(0x101);
                room_id=vm_read64(vm_read32(result)+16);assert(room_id);
            } else if(command=="invite") {
                if(!room_id) input>>room_id;assert(room_id);
                vm_write16(message+8,3);vm_write32(message+12,1);vm_write32(message+32,payload);vm_write32(message+36,24);
                vm_write64(payload,room_id);
                assert(!call("sceNpBasicSendMessageGui",{message,0xffffffff}));
            } else if(command=="receive") {assert(!call("sceNpBasicRecvMessageCustom",{3,0,0xffffffff}));}
            else if(command=="join") {
                // Read the title's selected invitation using its real event and attachment APIs.
                bool accepted=false;
                for(unsigned i=0;i<64;++i) {
                    vm_write32(capacity,128);
                    if(call("sceNpBasicGetEvent",{event,from,data,capacity})) break;
                    if(vm_read32(event)==14) {accepted=true;break;}
                }
                assert(accepted);vm_write32(capacity,24);
                assert(!call("sceNpBasicRecvMessageAttachmentLoad",{1,data,capacity}));
                const auto invited=vm_read64(data);assert(invited);
                setup_matching();std::memset(memory.data()+request,0,48);vm_write64(request,invited);
                assert(!call("sceNpMatching2JoinRoom",{room_context,request,options,request_id}));answer(0x102);
                const auto room=vm_read32(result);room_id=vm_read64(room+16);
                assert(room_id==invited && vm_read32(room+40)==2);
            } else throw std::runtime_error("Unknown fixture input");
            emit(command);
        }
        reset_basic();reset_matching();stop();return 0;
    } catch(const std::exception& e) {std::fprintf(stderr,"friends-ui: %s\n",e.what());stop();return 1;}
}
