#include "basic.h"
#include "friend_name.h"
#include "friends.h"
#include "matching_data.h"
#include "fps_overlay.h"
#include "execution_policy_api.h"
#include "stubs.h"
#include "ppu_recomp.h"
#include "ps3emu/nid.h"
#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <deque>
#include <mutex>
#include <optional>
#include <string_view>

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
std::vector<std::string> allowed_recipients;
Relationships observed;
State connection_state=State::offline;
uint64_t last_generation=0;
FriendsController friend_ops;
uint64_t ui_serial=0,operation_owner=0;
struct FriendsUi {
    FriendsSection section=FriendsSection::friends;
    size_t selected=0,action_selected=0;
    uint64_t serial=0;
    std::string selected_user,status;
    bool details=false, from_invite=false,confirm_remove=false;
    bool unconfirmed=false;
    FriendNameEntry name;
};
std::optional<FriendsUi> friends_ui;
bool hint_shown=false;
uint32_t receive_options=0;
std::future<Reply> sending;
std::chrono::steady_clock::time_point send_deadline;
uint64_t send_generation=0;

void trace_friends_visibility(bool open) {
    static const bool enabled=[] {
        const auto* setting=std::getenv("TB_OFFLINE_TRACE");
        return setting && setting[0]=='1';
    }();
    if (enabled) fprintf(stderr,"[rpcn-friends] panel %s\n",open?"opened":"closed");
}

uint32_t ready() {
    if (!np_is_initialized()) return 0x8002a662;
    if (!callback) return not_registered;
    return 0;
}
InvitePicker picker_kind() {
    return panel==Panel::send?InvitePicker::friends:InvitePicker::invitations;
}
void close_panel() {
    if (panel!=Panel::none) tb_panel_close_if(picker_kind());
    tb_friends_panel_close();
    panel=Panel::none;
    choices.clear();choice=0;
    if (friends_ui) trace_friends_visibility(false);
    friends_ui.reset();
    allowed_recipients.clear();outgoing={};
}
void result(uint32_t event,int32_t status=0) {
    if (callback && callbacks.size()<64) callbacks.push_back({event,uint64_t(int64_t(status)),0,callback_arg});
    fprintf(stderr,"[rpcn-basic] callback event=%u result=0x%08x\n",event,uint32_t(status));
}
void show() {
    tb_panel_show(picker_kind(),choices,choice);
}
void update_picker() {
    tb_panel_update(picker_kind(),choices,choice);
}
void cancel_invitation() {
    if (panel!=Panel::none || sending.valid()) {
        result(panel==Panel::receive?14:13,-1);
        close_panel();
        sending={};
    }
    // A terminal result that the guest callback queue could not accept yet
    // still belongs to this GUI operation. Cancel it in place.
    for (auto& args:callbacks)
        if (args[0]==13 || args[0]==14) args[1]=UINT64_MAX;
}
void clear_session_invitations() {
    cancel_invitation();
    inbox.clear();selected.reset();events.clear();
    std::erase_if(callbacks,[](const auto& args){return args[0]!=13 && args[0]!=14;});
    tb_clear_invite_toast();
}
bool same_name(std::string_view a,std::string_view b) {
    const auto lower=[](char c){return c>='A' && c<='Z'?char(c-'A'+'a'):c;};
    return a.size()==b.size() && std::equal(a.begin(),a.end(),b.begin(),[&](char x,char y){return lower(x)==lower(y);});
}
void refresh_choices() {
    const auto previous=choice<choices.size()?choices[choice]:std::string{};
    choices.clear();
    for (const auto& f:observed.friends) {
        if (!f.online) continue;
        if (allowed_recipients.empty() || std::find(allowed_recipients.begin(),allowed_recipients.end(),f.npid)!=allowed_recipients.end())
            choices.push_back(f.npid);
    }
    choices.push_back("MANAGE FRIENDS");
    const auto it=std::find(choices.begin(),choices.end(),previous);
    choice=previous.empty()?0:it==choices.end()?choices.size()-1:size_t(it-choices.begin());
}
FriendsPanelView friends_view() {
    auto& ui=*friends_ui;
    const auto& state=observed;
    FriendsPanelView view;
    view.account=state.npid.empty()?"OFFLINE":state.npid;view.endpoint=state.endpoint;
    view.connected=connection_state==State::online;
    view.connecting=connection_state==State::connecting;
    view.section=ui.section;view.details=ui.details;view.busy=friend_ops.busy();
    view.editing=ui.name.editing;view.upper=ui.name.upper;
    view.keyboard_key=ui.name.key;view.username=ui.name.value;view.message=ui.name.error;
    std::vector<std::string> names;
    switch (view.section) {
    case FriendsSection::friends:
        for (const auto& f:state.friends) {names.push_back(f.npid);view.rows.push_back(f.npid+(f.online?"  ONLINE":"  OFFLINE"));}
        break;
    case FriendsSection::received:names=state.incoming;view.rows=names;break;
    case FriendsSection::sent:names=state.outgoing;view.rows=names;break;
    case FriendsSection::add:break;
    }
    if (!ui.selected_user.empty()) {
        const auto it=std::find_if(names.begin(),names.end(),[&](const auto& name){return same_name(name,ui.selected_user);});
        if (it!=names.end()) ui.selected=size_t(it-names.begin());
        else {ui.details=false;ui.confirm_remove=false;ui.action_selected=0;}
    }
    ui.selected=names.empty()?0:std::min(ui.selected,names.size()-1);
    ui.selected_user=names.empty()?std::string{}:names[ui.selected];
    view.selected=ui.selected;view.details=ui.details;view.confirm_remove=ui.confirm_remove;
    if (view.section==FriendsSection::add) view.actions={"EDIT USERNAME","SEND REQUEST"};
    else if (ui.details) {
        if (ui.confirm_remove) view.actions={"KEEP FRIEND","REMOVE FRIEND"};
        else if (view.section==FriendsSection::friends) view.actions={"REMOVE FRIEND"};
        else if (view.section==FriendsSection::received) view.actions={"ACCEPT REQUEST","DECLINE REQUEST"};
        else view.actions={"CANCEL REQUEST"};
    }
    ui.action_selected=view.actions.empty()?0:std::min(ui.action_selected,view.actions.size()-1);
    view.action_selected=ui.action_selected;view.status=ui.status;
    return view;
}
void open_friends(bool from_invite,bool received=false) {
    friends_ui=FriendsUi{};friends_ui->from_invite=from_invite;friends_ui->serial=++ui_serial;
    if (received) friends_ui->section=FriendsSection::received;
    if (tb_friends_panel_show(friends_view(),from_invite)) trace_friends_visibility(true);
    else friends_ui.reset();
}
void close_friends() {
    const bool nested=friends_ui && friends_ui->from_invite;
    tb_friends_panel_close();
    if (friends_ui) trace_friends_visibility(false);
    friends_ui.reset();
    if (nested && panel==Panel::send) update_picker();
}
void start_friend_action(FriendAction action,const std::string& name,bool confirmed=false) {
    operation_owner=friends_ui->serial;
    const auto reply=friend_ops.begin(action,name,confirmed,observed.generation);
    friends_ui->confirm_remove=false;
    if (reply.outcome!=FriendOutcome::pending) friends_ui->status=reply.message;
}
bool refresh_relationships(State state) {
    const bool state_changed=state!=connection_state;
    connection_state=state;
    if (state!=State::online) {
        if (state_changed || !observed.npid.empty()) {
            const bool report_uncertain=friends_ui && friends_ui->from_invite && friends_ui->unconfirmed;
            clear_session_invitations();
            if (report_uncertain) tb_show_network_toast("FRIEND CHANGE UNCONFIRMED","RECONNECT TO CHECK ITS STATUS");
            tb_clear_friend_request_toasts();
            hint_shown=false;
            observed={};
            if (friends_ui) {
                auto& ui=*friends_ui;
                ui.name={};
                ui.selected=ui.action_selected=0;
                ui.selected_user.clear();
                ui.details=ui.confirm_remove=false;
                if (!ui.unconfirmed) ui.status.clear();
            }
        }
        return state_changed;
    }
    if (!state_changed && observed.revision==relationships_revision()) return false;
    auto next=relationships();
    if (next.npid.empty()) return refresh_relationships(State::offline);
    const bool new_session=last_generation && last_generation!=next.generation;
    if (new_session) {
        clear_session_invitations();
        close_friends();
        tb_clear_friend_request_toasts();
        hint_shown=false;
    }
    last_generation=next.generation;
    for (const auto& name:next.incoming) {
        if (!new_session && std::any_of(observed.incoming.begin(),observed.incoming.end(),
                                       [&](const auto& old){return same_name(old,name);})) continue;
        tb_show_friend_request_toast(name.c_str());
    }
    observed=std::move(next);
    if (panel==Panel::send) {refresh_choices();update_picker();}
    return true;
}
void pump_friends(uint32_t buttons,bool dirty) {
    auto& ui=*friends_ui;
    auto& name=ui.name;
    tb_friends_input input{};
    while (tb_friends_take_input(&input)) {
        dirty=true;
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
            // SDL_TEXTINPUT owns Z and X while editing; never forward these as confirm/back.
            if (!name.editing) {tb_friends_panel_show(friends_view(),ui.from_invite);return;}
        } else {
            if (scan==TB_FRIENDS_UP) buttons|=0x10;
            if (scan==TB_FRIENDS_RIGHT) buttons|=0x20;
            if (scan==TB_FRIENDS_DOWN) buttons|=0x40;
            if (scan==TB_FRIENDS_LEFT) buttons|=0x80;
            if (scan==TB_FRIENDS_CONFIRM || scan==TB_FRIENDS_ENTER) buttons|=0x4000;
            if (scan==TB_FRIENDS_BACK || scan==TB_FRIENDS_ESCAPE) buttons|=0x2000;
        }
    }
    if (!dirty && !buttons) return;
    if (!tb_friends_window_focused()) {
        tb_friends_panel_show(friends_view(),ui.from_invite);return;
    }
    if (name.editing) {
        if (buttons&0x2000) name.cancel();
        else if (buttons&0x8000) name.erase();
        else if (buttons&0x1000) name.upper=!name.upper;
        else if (buttons&8) name.done();
        else if (buttons&0xf0) name.move(buttons&0x20?1:buttons&0x80?-1:0,buttons&0x40?1:buttons&0x10?-1:0);
        else if (buttons&0x4000) name.choose();
        tb_friends_panel_show(friends_view(),ui.from_invite);return;
    }
    if (!ui.status.empty()) {
        if (buttons&0x6000) {
            ui.status.clear();
            ui.unconfirmed=false;
        }
    }
    else if ((buttons&0x2000) && friend_ops.busy()) {close_friends();return;}
    else if (buttons&0x2000) {
        if (ui.confirm_remove) {ui.confirm_remove=false;ui.action_selected=0;}
        else if (ui.details) ui.details=false;
        else {close_friends();return;}
    } else if (!buttons || friend_ops.busy() || connection_state!=State::online) {
        // Repaint status and relationship updates without changing selection.
    } else if (buttons&0xa0) {
        const auto section=static_cast<unsigned>(ui.section);
        ui.section=static_cast<FriendsSection>((section+(buttons&0x20?1:3))%4);
        ui.selected=ui.action_selected=0;ui.selected_user.clear();ui.details=ui.confirm_remove=false;
    } else if (buttons&0x50) {
        const auto view=friends_view();
        if (!view.actions.empty()) ui.action_selected=(ui.action_selected+(buttons&0x40?1:view.actions.size()-1))%view.actions.size();
        else if (!view.rows.empty()) {ui.selected=(ui.selected+(buttons&0x40?1:view.rows.size()-1))%view.rows.size();ui.selected_user.clear();}
    } else if (buttons&0x4000) {
        const auto view=friends_view();
        if (ui.section==FriendsSection::add) {
            if (!ui.action_selected) name.open();
            else start_friend_action(FriendAction::add,name.value);
        } else if (ui.details && !ui.selected_user.empty()) {
            if (ui.section==FriendsSection::received) start_friend_action(ui.action_selected?FriendAction::decline:FriendAction::accept,ui.selected_user);
            else if (ui.section==FriendsSection::sent) start_friend_action(FriendAction::cancel,ui.selected_user);
            else if (!ui.confirm_remove) {ui.confirm_remove=true;ui.action_selected=0;}
            else if (!ui.action_selected) ui.confirm_remove=false;
            else start_friend_action(FriendAction::remove,ui.selected_user,true);
        } else if (!view.rows.empty()) {ui.details=true;ui.action_selected=0;}
    }
    tb_friends_panel_show(friends_view(),ui.from_invite);
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
    refresh_relationships(status().state);
    if (connection_state!=State::online) return not_connected;
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
    allowed_recipients.clear();
    if (count && !guest_range(npids,uint64_t(count)*36)) return invalid;
    for (uint32_t i=0;i<count;++i) {
        const auto name=guest_text(npids+i*36,16);
        if (!name || name->empty()) return invalid;
        allowed_recipients.push_back(*name);
    }
    choices.clear();refresh_choices();
    fprintf(stderr,"[rpcn-basic] send type=%u subtype=%u features=0x%08x bytes=%zu online-friends=%zu\n",
        message.type,message.subtype,message.features,message.data.size(),choices.size()-1);
    outgoing=std::move(message);panel=Panel::send;choice=0;show();return 0;
}
uint32_t receive(ppu_context* c) {
    fprintf(stderr,"[rpcn-basic] receive lr=0x%08x type=%u options=0x%08x container=%u inbox=%zu\n",
        (uint32_t)c->lr,(uint32_t)c->gpr[3],(uint32_t)c->gpr[4],(uint32_t)c->gpr[5],inbox.size());
    if (const auto error=ready()) return error;
    refresh_relationships(status().state);
    if (connection_state!=State::online) return not_connected;
    if (panel!=Panel::none || sending.valid()) return 0x8002a66f;
    if (c->gpr[3]!=3 || (c->gpr[4]&~7u)) return invalid;
    choices.clear();
    for (const auto& msg:inbox) choices.push_back(msg.sender);
    receive_options=c->gpr[4];
    panel=Panel::receive;choice=0;show();return 0;
}
uint32_t get_event(ppu_context* c) {
    if (const auto error=ready()) return error;
    refresh_relationships(status().state);
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
    refresh_relationships(status().state);
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
    inbox.clear();selected.reset();sending={};
    tb_clear_invite_toast();
}
void pump_basic(bool system_dialog,bool room_exited) {
    std::lock_guard guard(basic_lock);
    if (room_exited) {
        cancel_invitation();
        close_friends();
        selected.reset();
        std::erase_if(events,[](const auto& event){return event.code==14;});
    } else if (panel!=Panel::none && (system_dialog || !tb_panel_visible(picker_kind()))) {
        cancel_invitation();
    }
    const auto reply=friend_ops.poll();
    const bool result_owned=friends_ui && friends_ui->serial==operation_owner;
    bool dirty=refresh_relationships(status().state);
    if (reply && result_owned) {
        if (friends_ui && friends_ui->serial==operation_owner) {
            friends_ui->status=reply->message;
            friends_ui->unconfirmed=reply->outcome==FriendOutcome::unknown;
            dirty=true;
        } else if (reply->outcome==FriendOutcome::unknown && connection_state!=State::online) {
            tb_show_network_toast("FRIEND CHANGE UNCONFIRMED","RECONNECT TO CHECK ITS STATUS");
        }
    }
    if (friends_ui && (!tb_friends_panel_visible() || system_dialog)) close_friends();
    if (tb_friends_take_toggle() && !room_exited && !system_dialog && !sending.valid()) {
        const bool received=tb_friend_request_toast_visible();
        if (friends_ui && received) {
            auto& ui=*friends_ui;ui.name.cancel();ui.section=FriendsSection::received;
            ui.selected=ui.action_selected=0;ui.selected_user.clear();ui.details=ui.confirm_remove=false;
            ui.status.clear();ui.unconfirmed=false;
            dirty=true;
        } else if (friends_ui) close_friends();
        else if (panel==Panel::none || panel==Panel::send) open_friends(panel==Panel::send,received);
        if (received && friends_ui) tb_clear_friend_request_toasts();
    }
    if (!system_dialog && !hint_shown && connection_state==State::online)
        hint_shown=tb_show_friends_hint();
    const auto buttons=panel==Panel::none && !friends_ui?0:tb_panel_take_input();
    const bool confirm=(buttons&0x4000) && (panel==Panel::send || !choices.empty());
    if (connection_state==State::online) for (auto& notice:take_basic_notifications(observed.generation)) {
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
                update_picker();
            }
            result(event);
        } catch (const std::exception& e) { fprintf(stderr,"[rpcn-basic] rejected message: %s\n",e.what()); }
    }
    if (friends_ui) {
        pump_friends(buttons,dirty);
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
                    send_deadline=std::chrono::steady_clock::now()+6s;
                    send_generation=observed.generation;
                    sending=submit(Request::send_message,encode_basic_message(outgoing,{choices[choice]}),
                                   send_generation,send_deadline);
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
            choice=(choice+(buttons&0x40?1:choices.size()-1))%choices.size();update_picker();
        }
    }
    if (sending.valid()) {
        if (sending.wait_for(0s)==std::future_status::ready) {
            const auto reply=sending.get().body;
            const auto session=relationships();
            const bool current=session.generation==send_generation && !session.npid.empty();
            result(13,current && reply.size()==1 && !reply.front()?0:-1);
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
        std::lock_guard guard(basic_lock);cancel_invitation();c->gpr[3]=0;
    });
    tank_battles_register_override(ps3_compute_nid("cellPadGetData"),[](ppu_context* c){
        const auto port=c->gpr[3];
        const auto out=c->gpr[4];
        tb_sdk_ps3_hle_call(ps3_compute_nid("cellPadGetData"),c);
        static std::atomic<bool> shortcut_held=false;
        if (!port && c->gpr[3]) {
            shortcut_held.store(false);
            (void)tb_panel_input(0);
        }
        if (!port && !c->gpr[3] && guest_range(out,12) && vm_read32(out)>0) {
            const auto buttons=vm_read16(out+8)|(vm_read16(out+10)<<8);
            const bool shortcut=(buttons&0x1001)==0x1001;
            if (!shortcut_held.exchange(shortcut) && shortcut) tb_friends_request_toggle();
            auto handling=tb_panel_input(buttons);
            if (shortcut) handling=PanelPadInput::captured;
            if (handling!=PanelPadInput::passthrough) {
                const bool forward_back=handling==PanelPadInput::back;
                vm_write16(out+8,0);
                vm_write16(out+10,forward_back?0x20:0);
                const auto count=std::min(vm_read32(out),20u);
                if (guest_range(out,4+count*2))
                    for (unsigned i=4;i<count;++i)
                        if (!forward_back || i!=13)
                            vm_write16(out+4+i*2,i<8?128:0); // PS3 sticks center at 128.
            }
        }
    });
}
}
