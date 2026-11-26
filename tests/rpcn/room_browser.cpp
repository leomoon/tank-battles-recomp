#include "ppu_recomp.h"
#include "ps3emu/nid.h"
#include "stubs.h"
#include "fps_overlay.h"
#include "rpcn/matching.h"
#include "rpcn/matching_data.h"
#include <array>
#include <cassert>
#include <cstring>
#include <map>

using namespace tb::rpcn;
using Callback=std::array<uint64_t,8>;
static std::array<uint8_t,1<<20> memory;
static std::map<uint32_t,tank_battles_override_fn> handlers;
static std::vector<Callback> callbacks;
static std::promise<Reply> response;
static Bytes last_packet;
static unsigned searches=0;
static uint32_t buttons=0;
static bool panel=false, online=true;
static InvitePicker panel_kind;
static std::vector<std::string> labels;
static std::string toast;

extern "C" {
uint32_t ppu_vm_size=memory.size();
int np_is_initialized(){return 1;}
int32_t cellSysutilQueueGuestCallbackArgs(uint32_t,const uint64_t* args){
    Callback value;std::copy_n(args,value.size(),value.begin());callbacks.push_back(value);return 0;
}
uint8_t vm_read8(uint64_t a){return memory.at(a);}
uint16_t vm_read16(uint64_t a){return uint16_t(vm_read8(a))<<8|vm_read8(a+1);}
uint32_t vm_read32(uint64_t a){return uint32_t(vm_read16(a))<<16|vm_read16(a+2);}
uint64_t vm_read64(uint64_t a){return uint64_t(vm_read32(a))<<32|vm_read32(a+4);}
void vm_write8(uint64_t a,uint8_t v){memory.at(a)=v;}
void vm_write16(uint64_t a,uint16_t v){vm_write8(a,v>>8);vm_write8(a+1,v);}
void vm_write32(uint64_t a,uint32_t v){vm_write16(a,v>>16);vm_write16(a+2,v);}
void vm_write64(uint64_t a,uint64_t v){vm_write32(a,v>>32);vm_write32(a+4,v);}
}
void tank_battles_register_override(uint32_t nid,tank_battles_override_fn handler){handlers[nid]=handler;}
void tb_panel_show(InvitePicker kind,std::span<const std::string> choices,size_t){
    panel=true;panel_kind=kind;labels.assign(choices.begin(),choices.end());
}
void tb_panel_close(){panel=false;}
uint32_t tb_panel_take_input(){auto value=buttons;buttons=0;return value;}
void tb_show_network_toast(const char* heading,const char*){toast=heading;}
namespace tb::rpcn {
bool enabled(){return true;}
Status status(){return {online?State::online:State::offline,"BrowserPlayer",1,"fixture"};}
void register_signaling(){}
void reset_signaling(){}
void pump_signaling(){}
std::vector<Notification> take_notifications(){return {};}
std::future<Reply> submit(Request command,Bytes packet){
    response=std::promise<Reply>();
    auto future=response.get_future();
    if(command==Request::server_list) response.set_value({{0,1,0,1,0}});
    else {if(command==Request::search_room) ++searches;last_packet=std::move(packet);}
    return future;
}
}

static void number(Bytes& output,unsigned field,uint64_t value){
    output.push_back(field*8);
    do {output.push_back((value&127)|(value>=128?128:0));value>>=7;} while(value);
}
static void data(Bytes& output,unsigned field,const Bytes& value){
    output.push_back(field*8+2);output.push_back(value.size());
    output.insert(output.end(),value.begin(),value.end());
}
static Bytes room(uint64_t id,const char* host,unsigned players=1,unsigned slots=4,unsigned open_public=~0u){
    Bytes row,wrapped,user;
    number(row,6,id);
    number(wrapped,1,open_public==~0u?slots-players:open_public);data(row,7,wrapped);
    wrapped.clear();number(wrapped,1,slots);data(row,8,wrapped);
    wrapped.clear();number(wrapped,1,players);data(row,10,wrapped);
    data(user,1,Bytes(host,host+std::strlen(host)));data(row,12,user);
    return row;
}
static Reply search_result(std::initializer_list<Bytes> rooms){
    Bytes body;
    number(body,1,1);number(body,2,rooms.size());
    for(const auto& value:rooms)data(body,3,value);
    auto packet=frame_protobuf({},body);packet.insert(packet.begin(),0);return {packet};
}
static uint32_t call(const char* name,std::initializer_list<uint64_t> args={},uint32_t lr=0){
    ppu_context c{};c.lr=lr;std::copy(args.begin(),args.end(),c.gpr+3);
    handlers.at(ps3_compute_nid(name))(&c);return c.gpr[3];
}
constexpr uint32_t ids=0x11000,request=0x12000,options=0x13000,out=0x14000,result=0x15000;
static uint16_t context;
static void search(){
    callbacks.clear();
    assert(!call("sceNpMatching2SearchRoom",{context,request,options,out},0x001cc8f0));
    assert(panel && panel_kind==InvitePicker::searching_rooms);
}
static void press(uint32_t mask){buttons=mask;pump_matching();}

int main(){
    register_matching();
    assert(!call("sceNpMatching2Init"));
    std::memcpy(memory.data()+ids,"NPWR00718",9);
    assert(!call("sceNpMatching2CreateContext",{ids+64,ids,ids+128,out,3}));context=vm_read16(out);
    assert(!call("sceNpMatching2ContextStartAsync",{context,0}));pump_matching();
    vm_write32(options,ids+512);vm_write32(request+4,1);vm_write32(request+16,1);vm_write32(request+20,20);

    search();const auto original_packet=last_packet;
    response.set_value(search_result({room(101,"HostA"),room(202,"HostB")}));pump_matching();
    assert(callbacks.empty() && labels==std::vector<std::string>({"HostA  1/4","HostB  1/4"}));
    for(unsigned i=0;i<20;++i)pump_matching();assert(callbacks.empty());
    press(0x0040);press(0x4000);
    assert(!panel && callbacks.size()==1 && callbacks.back()[2]==0x0106);
    const auto answer=callbacks.back();
    assert(call("sceNpMatching2GetEventData",{context,answer[3],result,answer[5]})==answer[5]);
    assert(vm_read32(result+8)==1);
    const auto selected=vm_read32(result+12);
    assert(vm_read64(selected+24)==202 && vm_read32(selected)==0);

    // Open slots are not total capacity: 2/4 and 3/4 must remain joinable.
    for(unsigned players:{2u,3u}) {
        search();response.set_value(search_result({room(606,"FourPlayerHost",players)}));pump_matching();
        assert(labels==std::vector<std::string>{"FourPlayerHost  "+std::to_string(players)+"/4"});
        press(0x4000);assert(!panel && callbacks.size()==1 && !callbacks.back()[4]);
    }
    // A room can have reserved private capacity without a public opening.
    search();response.set_value(search_result({room(707,"ReservedHost",1,4,0)}));pump_matching();
    assert(labels==std::vector<std::string>{"ReservedHost  1/4"});
    press(0x4000);assert(panel && callbacks.empty() && toast=="ROOM IS FULL");
    press(0x2000);

    search();response.set_value(search_result({}));pump_matching();
    assert(labels.empty() && panel && callbacks.empty());
    const auto before_refresh=searches;
    press(0x8000);assert(last_packet==original_packet && searches==before_refresh+1);
    response.set_value(search_result({room(303,"FullHost",4)}));pump_matching();
    press(0x4000);assert(panel && callbacks.empty() && toast=="ROOM IS FULL");
    press(0x8000);response.set_value(search_result({room(404,"OnlyHost")}));pump_matching();
    assert(panel && callbacks.empty()); // A single room also requires explicit confirmation.
    press(0x8000);response.set_value({{1}});pump_matching();
    assert(panel && labels==std::vector<std::string>{"OnlyHost  1/4"} && toast=="ROOM SEARCH FAILED");
    press(0x2000);assert(!panel && callbacks.empty());

    search();press(0x2000);response.set_value(search_result({room(505,"LateHost")}));pump_matching();
    assert(!panel && callbacks.empty()); // A late response cannot reopen a cancelled browser.
    // A room can disappear or fill after selection and before the server joins it.
    for(const auto code:{14,16}) {
        callbacks.clear();
        std::memset(memory.data()+request,0,48);vm_write64(request,404);
        assert(!call("sceNpMatching2JoinRoom",{context,request,options,out}));
        response.set_value({{static_cast<unsigned char>(code)}});pump_matching();
        assert(!panel && callbacks.size()==1 && callbacks.back()[2]==0x0102 && callbacks.back()[4]);
        assert(toast==(code==16?"ROOM IS FULL":"ROOM IS UNAVAILABLE"));
    }
    search();assert(!call("sceNpMatching2DestroyContext",{context}));pump_matching();
    assert(!panel && callbacks.empty());
    puts("PASS: public room selection, empty and single results, refresh, full rooms, cancellation and context cleanup");
}
