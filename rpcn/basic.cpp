#include "basic.h"
#include "friend_name.h"
#include "matching_data.h"
#include "fps_overlay.h"
#include "execution_policy_api.h"
#include "stubs.h"
#include "ppu_recomp.h"
#include "ps3emu/nid.h"
#include <algorithm>
#include <array>
#include <chrono>
#include <cstdio>
#include <deque>
#include <mutex>
#include <optional>

extern "C" int np_is_initialized();
extern "C" int32_t cellSysutilQueueGuestCallbackArgs(uint32_t,const uint64_t*);
extern "C" void tb_sdk_ps3_hle_call(uint32_t,ppu_context*);
namespace tb::rpcn {
namespace {
using namespace std::chrono_literals;
constexpr uint32_t invalid=0x8002a665,not_registered=0x8002a66d;
constexpr uint32_t no_event=0x8002a66a,not_connected=0x8002a678;
std::mutex basic_lock;
uint32_t callback=0,callback_arg=0;
std::string communication;
struct Event { uint32_t code; BasicMessage message; };
std::deque<Event> events;
std::deque<std::array<uint64_t,8>> callbacks;
std::deque<BasicMessage> inbox;
std::optional<BasicMessage> selected;
uint64_t next_id=1;
enum class Panel { none, send, receive };
Panel panel=Panel::none;
std::vector<std::string> choices;
size_t choice=0;
BasicMessage outgoing;
struct FriendsUi {
    FriendsSection section=FriendsSection::friends;
    size_t selected=0;
    bool details=false, from_invite=false;
    FriendNameEntry name;
};
std::optional<FriendsUi> friends_ui;
bool hint_shown=false;
uint32_t receive_options=0;
std::future<Reply> sending;
std::chrono::steady_clock::time_point send_deadline;

uint32_t ready() {
    if (!np_is_initialized()) return 0x8002a662;
    if (!callback) return not_registered;
    return 0;
}
void close_panel() { panel=Panel::none;choices.clear();choice=0;friends_ui.reset();tb_panel_close(); }
void result(uint32_t event,int32_t status=0) {
    if (callback && callbacks.size()<64) callbacks.push_back({event,uint64_t(int64_t(status)),0,callback_arg});
    fprintf(stderr,"[rpcn-basic] callback event=%u result=0x%08x\n",event,uint32_t(status));
}
void show() {
    tb_panel_show(panel==Panel::send?InvitePicker::friends:InvitePicker::invitations,choices,choice);
}
FriendsPanelView friends_view() {
    const auto state=relationships();
    FriendsPanelView view;
    view.account=state.npid.empty()?"OFFLINE":state.npid;
    view.endpoint=state.endpoint;
    view.connected=status().state==State::online;
    view.section=friends_ui->section;view.details=friends_ui->details;
    view.editing=friends_ui->name.editing;view.upper=friends_ui->name.upper;
    view.keyboard_key=friends_ui->name.key;view.username=friends_ui->name.value;
    view.message=friends_ui->name.error;
    switch (view.section) {
    case FriendsSection::friends:
        for (const auto& f:state.friends) view.rows.push_back(f.npid+(f.online?"  ONLINE":"  OFFLINE"));
        break;
    case FriendsSection::received:view.rows=state.incoming;break;
    case FriendsSection::sent:view.rows=state.outgoing;break;
    case FriendsSection::add:break;
    }
    friends_ui->selected=view.rows.empty()?0:std::min(friends_ui->selected,view.rows.size()-1);
    view.selected=friends_ui->selected;
    return view;
}
void open_friends(bool from_invite) {
    friends_ui=FriendsUi{};friends_ui->from_invite=from_invite;
    if (!tb_friends_panel_show(friends_view(),from_invite)) friends_ui.reset();
}
void close_friends() {
    tb_friends_panel_close();friends_ui.reset();
}
void pump_friends(uint32_t buttons) {
    auto& name=friends_ui->name;
    tb_friends_input input{};
    while (tb_friends_take_input(&input)) {
        if (input.kind==TB_FRIENDS_FOCUS_LOST) {if(name.editing) name.cancel();buttons=0;break;}
        if (!tb_friends_window_focused()) continue;
        if (input.kind==TB_FRIENDS_TEXT) {if(name.editing) name.append(input.text);continue;}
        if (input.kind!=TB_FRIENDS_KEY) continue;
        const auto scan=input.value;
        if (name.editing) {
            if (scan==TB_FRIENDS_BACKSPACE) name.erase();
            else if (scan==TB_FRIENDS_ENTER) name.done();
            else if (scan==TB_FRIENDS_ESCAPE) name.cancel();
            else if (scan==TB_FRIENDS_UP) name.move(0,-1);
            else if (scan==TB_FRIENDS_DOWN) name.move(0,1);
            else if (scan==TB_FRIENDS_LEFT) name.move(-1,0);
            else if (scan==TB_FRIENDS_RIGHT) name.move(1,0);
            // Letters come only from SDL_TEXTINPUT, including Z and X.
            if (!name.editing) {tb_friends_panel_show(friends_view(),friends_ui->from_invite);return;}
        } else {
            if (scan==TB_FRIENDS_UP) buttons|=0x10;
            if (scan==TB_FRIENDS_RIGHT) buttons|=0x20;
            if (scan==TB_FRIENDS_DOWN) buttons|=0x40;
            if (scan==TB_FRIENDS_LEFT) buttons|=0x80;
            if (scan==TB_FRIENDS_CONFIRM || scan==TB_FRIENDS_ENTER) buttons|=0x4000;
            if (scan==TB_FRIENDS_BACK || scan==TB_FRIENDS_ESCAPE) buttons|=0x2000;
        }
    }
    if (!tb_friends_window_focused()) {
        tb_friends_panel_show(friends_view(),friends_ui->from_invite);return;
    }
    if (name.editing) {
        if (buttons&0x2000) name.cancel();
        else if (buttons&0x8000) name.erase();
        else if (buttons&0x1000) name.upper=!name.upper;
        else if (buttons&8) name.done();
        else if (buttons&0xf0) name.move(buttons&0x20?1:buttons&0x80?-1:0,buttons&0x40?1:buttons&0x10?-1:0);
        else if (buttons&0x4000) name.choose();
        tb_friends_panel_show(friends_view(),friends_ui->from_invite);return;
    }
    auto view=friends_view();
    if (buttons&0x2000) {
        if (friends_ui->details) friends_ui->details=false;
        else {close_friends();return;}
    } else if (buttons&0xa0) {
        const auto section=static_cast<unsigned>(friends_ui->section);
        friends_ui->section=static_cast<FriendsSection>((section+(buttons&0x20?1:3))%4);
        friends_ui->selected=0;friends_ui->details=false;
    } else if (!friends_ui->details && !view.rows.empty() && (buttons&0x50)) {
        friends_ui->selected=(friends_ui->selected+(buttons&0x40?1:view.rows.size()-1))%view.rows.size();
    } else if ((buttons&0x4000) && friends_ui->section==FriendsSection::add && view.connected) name.open();
    else if ((buttons&0x4000) && !view.rows.empty()) friends_ui->details=true;
    tb_friends_panel_show(friends_view(),friends_ui->from_invite);
}
std::optional<std::string> guest_text(uint32_t address,size_t limit) {
    if (!address) return std::string{};
    std::string text;
    for (size_t i=0;i<=limit;++i) {
        if (!guest_range(uint64_t(address)+i,1)) return {};
        const auto ch=vm_read8(address+i);
        if (!ch) return text;
        text.push_back(ch);
    }
    return {};
}
uint32_t register_handler(ppu_context* c) {
    if (!np_is_initialized()) return 0x8002a662;
    if (callback) return 0x8002a66b;
    if (!guest_range(c->gpr[3],12) || !guest_range(c->gpr[4],8)) return invalid;
    if (vm_read8(c->gpr[3]+10)>99) return invalid;
    communication.clear();
    for (unsigned i=0;i<9;++i) communication.push_back(vm_read8(c->gpr[3]+i));
    char suffix[5];std::snprintf(suffix,sizeof suffix,"_%02u",vm_read8(c->gpr[3]+10));
    communication+=suffix;
    callback=c->gpr[4];callback_arg=c->gpr[5];
    fprintf(stderr,"[rpcn-basic] registered communication=%s callback=0x%08x\n",communication.c_str(),callback);
    return 0;
}
uint32_t send(ppu_context* c) {
    if (const auto error=ready()) return error;
    if (status().state!=State::online) return not_connected;
    if (panel!=Panel::none || sending.valid()) return 0x8002a66f;
    const auto a=c->gpr[3];
    if (!guest_range(a,40)) return invalid;
    BasicMessage message;
    message.communication_id=communication;message.id=vm_read64(a);
    message.type=vm_read16(a+8);message.subtype=vm_read16(a+10);message.features=vm_read32(a+12);
    const auto size=vm_read32(a+36),data=vm_read32(a+32),count=vm_read32(a+20),npids=vm_read32(a+16);
    const auto subject=guest_text(vm_read32(a+24),128),body=guest_text(vm_read32(a+28),512);
    if (!subject || !body || count>16 || !size || size>1024 || !guest_range(data,size) ||
        message.id || message.type!=3 || message.subtype>1 || (message.features&0x7ff8)) return invalid;
    message.subject=*subject;message.body=*body;
    for (uint32_t i=0;i<size;++i) message.data.push_back(vm_read8(data+i));
    choices.clear();
    for (const auto& f:friends()) {
        if (!f.online) continue;
        bool allowed=!count;
        if (count && !guest_range(npids,uint64_t(count)*36)) return invalid;
        for (uint32_t i=0;i<count;++i) {
            const auto name=guest_text(npids+i*36,16);
            if (!name) return invalid;
            if (*name==f.npid) allowed=true;
        }
        if (allowed) choices.push_back(f.npid);
    }
    fprintf(stderr,"[rpcn-basic] send type=%u subtype=%u features=0x%08x bytes=%zu online-friends=%zu\n",
        message.type,message.subtype,message.features,message.data.size(),choices.size());
    choices.push_back("MANAGE FRIENDS");
    outgoing=std::move(message);panel=Panel::send;choice=0;show();return 0;
}
uint32_t receive(ppu_context* c) {
    fprintf(stderr,"[rpcn-basic] receive lr=0x%08x type=%u options=0x%08x container=%u inbox=%zu\n",
        (uint32_t)c->lr,(uint32_t)c->gpr[3],(uint32_t)c->gpr[4],(uint32_t)c->gpr[5],inbox.size());
    if (const auto error=ready()) return error;
    if (panel!=Panel::none || sending.valid()) return 0x8002a66f;
    if (c->gpr[3]!=3 || (c->gpr[4]&~7u)) return invalid;
    choices.clear();
    for (const auto& msg:inbox) choices.push_back(msg.sender);
    receive_options=c->gpr[4];
    panel=Panel::receive;choice=0;show();return 0;
}
uint32_t get_event(ppu_context* c) {
    if (const auto error=ready()) return error;
    const auto out=c->gpr[3],from=c->gpr[4],data=c->gpr[5],size=c->gpr[6];
    if (!guest_range(out,4) || !guest_range(from,216) || !guest_range(size,4)) return invalid;
    if (events.empty()) return no_event;
    const auto& event=events.front();
    const auto capacity=vm_read32(size);
    Image attachment{std::vector<unsigned char>(32),{}};
    attachment.write(8,event.message.id,8);attachment.write(16,1,4);
    attachment.write(20,event.message.data.size(),4);attachment.write(24,2,4);
    attachment.write(28,(receive_options&1)?0:1,1);
    const auto& bytes=event.code==14?attachment.bytes:event.message.data;
    const auto count=std::min<size_t>(capacity,bytes.size());
    if (!guest_range(data,count)) return invalid;
    vm_write32(out,event.code);
    for (unsigned i=0;i<216;++i) vm_write8(from+i,0);
    for (size_t i=0;i<event.message.sender.size();++i) {
        vm_write8(from+i,event.message.sender[i]);vm_write8(from+36+i,event.message.sender[i]);
    }
    for (unsigned i=0;i<count;++i) vm_write8(data+i,bytes[i]);
    const bool lost=count<bytes.size();
    vm_write32(size,count);events.pop_front();
    return lost?0x8002a66e:0;
}
uint32_t attachment(ppu_context* c) {
    if (const auto error=ready()) return error;
    if (c->gpr[3]!=1 || !selected) return 0x8002a673;
    const auto data=c->gpr[4],size=c->gpr[5];
    if (!guest_range(size,4)) return invalid;
    const auto count=std::min<size_t>(vm_read32(size),selected->data.size());
    if (!guest_range(data,count)) return invalid;
    for (size_t i=0;i<count;++i) vm_write8(data+i,selected->data[i]);
    vm_write32(size,count);
    fprintf(stderr,"[rpcn-basic] attachment id=1 bytes=%zu sender=%s\n",count,selected->sender.c_str());
    return count<selected->data.size()?0x8002a66e:0;
}
uint32_t friend_count(ppu_context* c) {
    if (!np_is_initialized()) return 0x8002a662;
    if (!guest_range(c->gpr[3],4)) return invalid;
    if (status().state!=State::online) return 0x8002aa06;
    vm_write32(c->gpr[3],uint32_t(friends().size()));
    return 0;
}
uint32_t friend_entry(ppu_context* c) {
    if (!np_is_initialized()) return 0x8002a662;
    const auto index=c->gpr[3],out=c->gpr[4];
    if (!guest_range(out,36)) return invalid;
    if (status().state!=State::online) return 0x8002a66f;
    const auto list=friends();
    if (index>=list.size()) return 0x8002aa06;
    if (list[index].npid.size()>16) return invalid;
    const auto& npid=list[index].npid;
    for (unsigned i=0;i<36;++i) vm_write8(out+i,i<npid.size()?uint8_t(npid[i]):0);
    return 0;
}
template<uint32_t (*call)(ppu_context*)> void handle(ppu_context* c) {
    std::lock_guard guard(basic_lock); c->gpr[3]=call(c);
}
}
void reset_basic() {
    std::lock_guard guard(basic_lock);
    close_panel();callback=callback_arg=0;communication.clear();events.clear();callbacks.clear();
    inbox.clear();selected.reset();sending={};outgoing={};
    tb_clear_invite_toast();
}
void pump_basic(bool system_dialog) {
    std::lock_guard guard(basic_lock);
    const auto identity=status();
    if (identity.state!=State::online) {
        hint_shown=false;
        if (panel!=Panel::none || sending.valid()) {result(panel==Panel::receive?14:13,-1);close_panel();}
        inbox.clear();selected.reset();sending={};events.clear();
        tb_clear_invite_toast();
    }
    if (friends_ui && (!tb_friends_panel_visible() || system_dialog)) close_friends();
    if (tb_friends_take_toggle() && !system_dialog && !sending.valid()) {
        if (friends_ui) close_friends();
        else if (panel==Panel::none || panel==Panel::send) open_friends(panel==Panel::send);
    }
    if (!system_dialog && !hint_shown && identity.state==State::online)
        hint_shown=tb_show_friends_hint();
    const auto buttons=panel==Panel::none && !friends_ui?0:tb_panel_take_input();
    const bool confirm=(buttons&0x4000) && (panel==Panel::send || !choices.empty());
    for (auto& notice:take_basic_notifications()) {
        try {
            auto message=decode_basic_notification(notice.body);
            if (message.communication_id!=communication || inbox.size()>=64) continue;
            message.id=next_id++;
            fprintf(stderr,"[rpcn-basic] received sender=%s type=%u subtype=%u features=0x%08x bytes=%zu id=%llu\n",
                message.sender.c_str(),message.type,message.subtype,message.features,message.data.size(),(unsigned long long)message.id);
            const auto event=(message.features&2)?23u:5u;
            if (events.size()<64) events.push_back({event,message});
            inbox.push_back(std::move(message));
            tb_show_invite_toast(inbox.back().sender.c_str());
            if (panel==Panel::receive) {
                choices.push_back(inbox.back().sender);
                show();
            }
            result(event);
        } catch (const std::exception& e) { fprintf(stderr,"[rpcn-basic] rejected message: %s\n",e.what()); }
    }
    if (friends_ui) {
        pump_friends(buttons);
    } else if (panel!=Panel::none) {
        if (buttons&0x2000) { result(panel==Panel::send?13:14,-1);close_panel(); }
        else if (confirm) {
            if (!choices.empty() && panel==Panel::send) {
                if (choice==choices.size()-1) {open_friends(true);return;}
                const auto online=friends();
                const bool available=std::any_of(online.begin(),online.end(),[&](const Friend& f){
                    return f.npid==choices[choice] && f.online;
                });
                if (!available) { result(13,-1);close_panel(); }
                else {
                    sending=submit(Request::send_message,encode_basic_message(outgoing,{choices[choice]}));
                    send_deadline=std::chrono::steady_clock::now()+6s;
                    if (!sending.valid()) result(13,-1);
                    close_panel();
                }
            } else if (!choices.empty()) {
                selected=inbox[choice];
                if (!(receive_options&1)) inbox.erase(inbox.begin()+choice);
                if (events.size()<64) events.push_back({14,*selected});
                result(14);close_panel();
            } else { result(13,-1);close_panel(); }
        } else if (!choices.empty() && (buttons&0x50)) {
            choice=(choice+(buttons&0x40?1:choices.size()-1))%choices.size();show();
        }
    }
    if (sending.valid()) {
        if (sending.wait_for(0s)==std::future_status::ready) {
            const auto reply=sending.get().body;
            result(13,reply.size()==1 && !reply.front()?0:-1);
        } else if (std::chrono::steady_clock::now()>=send_deadline) { sending={};result(13,-1); }
    }
    while (!callbacks.empty() && callback) {
        if (cellSysutilQueueGuestCallbackArgs(callback,callbacks.front().data())) break;
        callbacks.pop_front();
    }
}
void register_basic() {
    tank_battles_register_override(ps3_compute_nid("sceNpBasicRegisterContextSensitiveHandler"),handle<register_handler>);
    tank_battles_register_override(ps3_compute_nid("sceNpBasicSendMessageGui"),handle<send>);
    tank_battles_register_override(ps3_compute_nid("sceNpBasicRecvMessageCustom"),handle<receive>);
    tank_battles_register_override(ps3_compute_nid("sceNpBasicGetEvent"),handle<get_event>);
    tank_battles_register_override(ps3_compute_nid("sceNpBasicRecvMessageAttachmentLoad"),handle<attachment>);
    tank_battles_register_override(ps3_compute_nid("sceNpBasicGetFriendListEntryCount"),handle<friend_count>);
    tank_battles_register_override(ps3_compute_nid("sceNpBasicGetFriendListEntry"),handle<friend_entry>);
    tank_battles_register_override(ps3_compute_nid("sceNpBasicUnregisterHandler"),[](ppu_context* c){reset_basic();c->gpr[3]=0;});
    tank_battles_register_override(ps3_compute_nid("sceNpBasicAbortGui"),[](ppu_context* c){
        std::lock_guard guard(basic_lock);close_panel();c->gpr[3]=0;
    });
    tank_battles_register_override(ps3_compute_nid("cellPadGetData"),[](ppu_context* c){
        const auto port=c->gpr[3];
        const auto out=c->gpr[4];
        tb_sdk_ps3_hle_call(ps3_compute_nid("cellPadGetData"),c);
        if (!port && c->gpr[3]) (void)tb_panel_input(0); // Release a disconnected controller's held buttons.
        if (!port && !c->gpr[3] && guest_range(out,12) && vm_read32(out)>0) {
            const auto buttons=vm_read16(out+8)|(vm_read16(out+10)<<8);
            static bool shortcut_held=false;
            const bool shortcut=(buttons&0x1001)==0x1001;
            if (shortcut && !shortcut_held) tb_friends_request_toggle();
            shortcut_held=shortcut;
            auto handling=tb_panel_input(buttons);
            if (shortcut) handling=PanelPadInput::captured;
            if (handling!=PanelPadInput::passthrough) {
                const bool forward_back=handling==PanelPadInput::back;
                vm_write16(out+8,0);
                vm_write16(out+10,forward_back?0x20:0);
                const auto count=std::min(vm_read32(out),20u);
                if (guest_range(out,4+count*2))
                    for (unsigned i=8;i<count;++i)
                        if (!forward_back || i!=13) vm_write16(out+4+i*2,0);
            }
        }
    });
}
}
