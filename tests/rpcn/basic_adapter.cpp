#include "rpcn/basic.h"
#include "fps_overlay.h"
#include "execution_policy_api.h"
#include "ppu_recomp.h"
#include "ps3emu/nid.h"
#include "stubs.h"
#include <algorithm>
#include <array>
#include <cassert>
#include <chrono>
#include <cstdio>
#include <deque>
#include <map>
#include <utility>
#include <vector>
using namespace std::chrono_literals;
using Callback = std::array<uint64_t,8>;
static std::array<uint8_t,1 << 20> memory;
static std::map<uint32_t,tank_battles_override_fn> hle;
static std::vector<Callback> callbacks;


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


static uint32_t buttons=0;
static bool panel=false;
static bool friend_panel=false,toggle_requested=false;
static InvitePicker panel_kind;
static FriendsPanelView friend_view;
static std::deque<tb_friends_input> native_inputs;
static bool focused=true;
static std::vector<std::string> displayed_choices;
static std::string toast_sender;
void tb_show_invite_toast(const char* sender) { toast_sender=sender; }
void tb_clear_invite_toast() { toast_sender.clear(); }
void tb_panel_show(InvitePicker kind,std::span<const std::string> choices,size_t) {
    panel=true;
    panel_kind=kind;friend_panel=false;
    displayed_choices.assign(choices.begin(),choices.end());
}
void tb_panel_close() { panel=false;friend_panel=false; }
bool tb_friends_panel_show(const FriendsPanelView& view,bool nested) {
    if (!friend_panel && panel && !(nested && panel_kind==InvitePicker::friends)) return false;
    friend_panel=true;friend_view=view;return true;
}
bool tb_friends_panel_visible() {return friend_panel;}
void tb_friends_panel_close() {friend_panel=false;}
bool tb_show_friends_hint() {return true;}
extern "C" void tb_friends_request_toggle() {toggle_requested=true;}
extern "C" int tb_friends_take_toggle() {return std::exchange(toggle_requested,false);}
extern "C" int tb_friends_window_focused() {return focused;}
extern "C" int tb_friends_take_input(tb_friends_input* input) {
    if(native_inputs.empty()) return 0;*input=native_inputs.front();native_inputs.pop_front();return 1;
}
PanelPadInput tb_panel_input(uint32_t value) {
    buttons=value;
    return panel || friend_panel?PanelPadInput::captured:PanelPadInput::passthrough;
}
uint32_t tb_panel_take_input() {auto value=buttons;buttons=0;return value;}
static uint32_t physical_buttons=0;
static bool physical_connected=true;
extern "C" void tb_sdk_ps3_hle_call(uint32_t,ppu_context* c) {
    if (!physical_connected) {c->gpr[3]=0x80121107;return;}
    const auto out=c->gpr[4];
    vm_write32(out,8);vm_write16(out+8,physical_buttons&0xff);vm_write16(out+10,physical_buttons>>8);c->gpr[3]=0;
}
namespace tb::rpcn {
bool guest_range(uint64_t a,uint64_t size) {return a>=0x10000 && a<=memory.size() && size<=memory.size()-a;}
static std::vector<Notification> notices;
static bool peer_online=true;
static unsigned submitted_messages=0;
static bool online=true;
static std::vector<unsigned char> submitted_payload;
Status status() {return {online?State::online:State::offline,"PlayerA",1,"localhost:31313"};}
std::vector<Friend> friends() {return {{"PlayerB",peer_online,0},{"Offline",false,0}};}
Relationships relationships() {
    if (!online) return {};
    Relationships s;s.npid="PlayerA";s.endpoint="localhost:31313";s.generation=1;s.user_id=1;
    s.friends=friends();s.incoming={"RequestA"};s.outgoing={"RequestB"};return s;
}
std::vector<Notification> take_basic_notifications() {auto result=notices;notices.clear();return result;}
std::future<Reply> submit(Request command,std::vector<unsigned char> bytes) {
 assert(command==Request::send_message && bytes.size()>4);
 ++submitted_messages;
 submitted_payload=bytes;
 std::promise<Reply> completion;completion.set_value({{0}});return completion.get_future();
}
}
int main() {
 using namespace tb::rpcn;
 register_basic();
 constexpr uint32_t context=0x11000,handler=0x11100,message=0x11200,payload=0x11300;
 constexpr uint32_t event=0x11400,from=0x11500,data=0x11600,size=0x11700;
 assert(!call("sceNpBasicGetFriendListEntryCount",{size}) && vm_read32(size)==2);
 assert(!call("sceNpBasicGetFriendListEntry",{1,from}));
 for (unsigned i=0;i<36;++i) assert(vm_read8(from+i)==(i<7?"Offline"[i]:0));
 assert(call("sceNpBasicGetFriendListEntry",{2,from})==0x8002aa06);
 for (unsigned i=0;i<9;++i) vm_write8(context+i,"NPWR00718"[i]);
 assert(!call("sceNpBasicRegisterContextSensitiveHandler",{context,handler,123}));
 vm_write16(message+8,3);vm_write32(message+12,1);vm_write32(message+32,payload);vm_write32(message+36,24);
 vm_write64(payload,56);
 assert(!call("sceNpBasicSendMessageGui",{message,0xffffffff}));assert(panel);
 assert(displayed_choices==(std::vector<std::string>{"PlayerB","MANAGE FRIENDS"}));
 const auto before_manage=callbacks.size();
 buttons=0x40;pump_basic(); // Select Manage Friends.
 buttons=0x4000;pump_basic();assert(friend_panel && panel);
 assert(callbacks.size()==before_manage && submitted_messages==0);
 assert(friend_view.account=="PlayerA" && friend_view.endpoint=="localhost:31313");
 buttons=0x20;pump_basic();assert(friend_view.section==FriendsSection::received);
 assert(friend_view.rows==std::vector<std::string>{"RequestA"});
 buttons=0x2000;pump_basic();assert(!friend_panel && panel);
 assert(callbacks.size()==before_manage);
 buttons=0x10;pump_basic(); // Return to PlayerB; invitation bytes remain unchanged.
 buttons=0x4000;pump_basic();assert(!panel);
 assert(callbacks.back()[0]==13 && callbacks.back()[1]==0 && callbacks.back()[3]==123);
 BasicMessage preserved;preserved.communication_id="NPWR00718_00";preserved.features=1;preserved.data.resize(24);
 preserved.data[7]=56;
 assert(submitted_payload==encode_basic_message(preserved,{"PlayerB"}));
 assert(!call("sceNpBasicSendMessageGui",{message,0xffffffff}));
 buttons=0x2000;pump_basic();assert(!panel && callbacks.back()[1]==UINT64_MAX);
 assert(!call("sceNpBasicSendMessageGui",{message,0xffffffff}));
 peer_online=false;buttons=0x4000;pump_basic();assert(!panel && callbacks.back()[1]==UINT64_MAX);
 peer_online=true;
 constexpr uint32_t recipient=0x11800;
 for (unsigned i=0;i<7;++i) vm_write8(recipient+i,"PlayerC"[i]);
 vm_write32(message+16,recipient);vm_write32(message+20,1);
 assert(!call("sceNpBasicSendMessageGui",{message,0xffffffff}));
 assert(displayed_choices==std::vector<std::string>{"MANAGE FRIENDS"});
 buttons=0x4000;pump_basic();assert(friend_panel && panel);
 buttons=0x2000;pump_basic();assert(!friend_panel && panel);
 buttons=0x2000;pump_basic();assert(!panel && callbacks.back()[1]==UINT64_MAX);
 assert(submitted_messages==1);
 BasicMessage msg;msg.communication_id="NPWR00718_00";msg.data.resize(24);msg.data[7]=56;msg.features=1;
 const auto encoded=encode_basic_message(msg,{"PlayerA"});
 assert(encoded[4]==10 && encoded[5]<128);
 const unsigned length=encoded[5];
 std::vector<unsigned char> notice={'P','l','a','y','e','r','B',0};
 for(unsigned i=0;i<4;++i) notice.push_back(length>>(8*i));
 notice.insert(notice.end(),encoded.begin()+6,encoded.begin()+6+length);
 assert(!call("sceNpBasicRecvMessageCustom",{3,0,0xffffffff}));
 assert(panel && displayed_choices.empty());
 buttons=0x4000;notices.push_back({10,notice});pump_basic();
 assert(panel && displayed_choices==std::vector<std::string>{"PlayerB"});
 assert(callbacks.back()[0]==5);
 assert(toast_sender=="PlayerB");
 vm_write32(size,128);
 assert(!call("sceNpBasicGetEvent",{event,from,data,size}));
 assert(vm_read32(event)==5 && vm_read32(size)==24 && vm_read64(data)==56);
 assert(vm_read8(from)=='P' && vm_read8(from+36)=='P');
 buttons=0x4000;pump_basic();assert(callbacks.back()[0]==14 && !panel);
 vm_write32(size,32);
 assert(call("sceNpBasicGetEvent",{event,from,0,size})==0x8002a665);
 assert(!call("sceNpBasicGetEvent",{event,from,data,size}));
 assert(vm_read32(event)==14 && vm_read32(data+16)==1 && vm_read32(data+20)==24);
 assert(vm_read32(data+24)==2 && vm_read8(data+28)==1);
 vm_write32(size,24);
 assert(!call("sceNpBasicRecvMessageAttachmentLoad",{1,data,size}));assert(vm_read64(data)==56);
 vm_write32(size,8);
 assert(call("sceNpBasicRecvMessageAttachmentLoad",{1,data,size})==0x8002a66e);
 assert(vm_read32(size)==8);
 assert(call("sceNpBasicGetEvent",{event,from,data,size})==0x8002a66a);
 assert(!call("sceNpBasicRecvMessageCustom",{3,0,0xffffffff}));
 assert(panel && displayed_choices.empty());
 const auto before_back=callbacks.size();
 buttons=0x4000;pump_basic();assert(panel && callbacks.size()==before_back);
 buttons=0x2000;pump_basic();assert(!panel && callbacks.size()==before_back+1);
 assert(callbacks.back()[0]==14 && callbacks.back()[1]==UINT64_MAX);
 assert(call("sceNpBasicGetEvent",{event,from,data,size})==0x8002a66a);
 reset_basic();
 assert(toast_sender.empty());
 assert(call("sceNpBasicRecvMessageAttachmentLoad",{1,data,size})==0x8002a66d);
 const auto before_standalone=callbacks.size();
 toggle_requested=true;pump_basic();assert(friend_panel && !panel);
 buttons=0x4000;pump_basic();assert(friend_view.details);
 buttons=0x2000;pump_basic();assert(friend_panel && !friend_view.details);
 buttons=0x2000;pump_basic();assert(!friend_panel && !panel);
 assert(callbacks.size()==before_standalone);
 toggle_requested=true;pump_basic(true);assert(!friend_panel); // System dialog owns input.
 toggle_requested=true;pump_basic();assert(friend_panel);
 pump_basic(true);assert(!friend_panel && callbacks.size()==before_standalone);
 toggle_requested=true;pump_basic();assert(friend_panel);
 online=false;pump_basic();assert(friend_panel && !friend_view.connected);
 online=true;buttons=0x2000;pump_basic();assert(!friend_panel);
 tb_panel_show(InvitePicker::rooms,{},0);
 toggle_requested=true;pump_basic();assert(!friend_panel && panel);
 tb_panel_close();
 constexpr uint32_t pad=0x11900;
 physical_buttons=0;assert(!call("cellPadGetData",{0,pad}));
 physical_buttons=0x1001;assert(!call("cellPadGetData",{0,pad}));
 assert(!vm_read16(pad+8) && !vm_read16(pad+10));
 pump_basic();assert(friend_panel);
 call("cellPadGetData",{0,pad});pump_basic();assert(friend_panel); // Held chord does not repeat.
 physical_buttons=0;call("cellPadGetData",{0,pad});pump_basic();
 physical_buttons=0x1001;call("cellPadGetData",{0,pad});pump_basic();assert(!friend_panel);
 physical_buttons=0;call("cellPadGetData",{0,pad});
 toggle_requested=true;pump_basic();assert(friend_panel);
 for (unsigned i=0;i<3;++i) {buttons=0x20;pump_basic();}
 assert(friend_view.section==FriendsSection::add);
 buttons=0x4000;pump_basic();assert(friend_view.editing);
 native_inputs.push_back({TB_FRIENDS_KEY,TB_FRIENDS_CONFIRM,{}});
 native_inputs.push_back({TB_FRIENDS_TEXT,0,"z"});
 native_inputs.push_back({TB_FRIENDS_KEY,TB_FRIENDS_BACK,{}});
 native_inputs.push_back({TB_FRIENDS_TEXT,0,"xUser"});
 pump_basic();assert(friend_view.editing && friend_view.username=="zxUser");
 native_inputs.push_back({TB_FRIENDS_TEXT,0,"bad@name"});
 pump_basic();assert(friend_view.username=="zxUser" && !friend_view.message.empty());
 native_inputs.push_back({TB_FRIENDS_KEY,TB_FRIENDS_BACKSPACE,{}});
 native_inputs.push_back({TB_FRIENDS_KEY,TB_FRIENDS_ENTER,{}});
 pump_basic();assert(!friend_view.editing && friend_view.username=="zxUse");
 buttons=0x4000;pump_basic();assert(friend_view.editing);
 native_inputs.push_back({TB_FRIENDS_TEXT,0,"-2"});
 native_inputs.push_back({TB_FRIENDS_KEY,TB_FRIENDS_ESCAPE,{}});
 pump_basic();assert(!friend_view.editing && friend_view.username=="zxUse");
 buttons=0x4000;pump_basic();
 buttons=0x1000;pump_basic();assert(friend_view.upper);
 buttons=0x4000;pump_basic();assert(friend_view.username=="zxUseA");
 buttons=0x8000;pump_basic();assert(friend_view.username=="zxUse");
 buttons=0x20;pump_basic();buttons=0x4000;pump_basic();assert(friend_view.username=="zxUseB");
 buttons=8;pump_basic();assert(!friend_view.editing);
 buttons=0x4000;pump_basic();
 physical_connected=false;call("cellPadGetData",{0,pad});pump_basic();
 assert(friend_view.editing && friend_view.username=="zxUseB");
 physical_connected=true;physical_buttons=0;call("cellPadGetData",{0,pad});pump_basic();
 assert(friend_view.editing && friend_view.username=="zxUseB");
 native_inputs.push_back({TB_FRIENDS_TEXT,0,"Tmp"});pump_basic();
 focused=false;native_inputs.push_back({TB_FRIENDS_FOCUS_LOST,0,{}});pump_basic();
 assert(!friend_view.editing && friend_view.username=="zxUseB");
 focused=true;pump_basic();
 buttons=0x2000;pump_basic();assert(!friend_panel);
 assert(callbacks.size()==before_standalone && submitted_messages==1);
 puts("PASS: invite send/receive callbacks, guest events, attachment bounds and cleanup");
}
