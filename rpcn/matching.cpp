#include "matching.h"
#include "signaling.h"
#include "client.h"
#include "matching_data.h"
#include "fps_overlay.h"
#include "stubs.h"
#include "ppu_recomp.h"
#include "ps3emu/nid.h"

#include <algorithm>
#include <array>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <map>
#include <mutex>
#include <optional>
#include <span>
#include <utility>

extern "C" int np_is_initialized();
extern "C" int32_t cellSysutilQueueGuestCallbackArgs(uint32_t, const uint64_t*);

namespace tb::rpcn {
bool guest_range(uint64_t addr, uint64_t size) {
    return addr>=0x10000 && addr<0xe0000000 && size<=0xe0000000-addr &&
           (!ppu_vm_size || (addr<=ppu_vm_size && size<=ppu_vm_size-addr));
}

uint64_t read_le(std::span<const unsigned char> bytes) {
    uint64_t result=0;
    for (size_t i=0;i<bytes.size();++i) result|=uint64_t(bytes[i]) << (i*8);
    return result;
}

namespace {

using namespace std::chrono_literals;
constexpr uint32_t not_initialized=0x80022303, context_max=0x80022304, no_context=0x80022306;
constexpr uint32_t already_started=0x80022307, not_started=0x80022308, invalid_argument=0x8002230a;
constexpr uint32_t invalid_server=0x8002230c, timeout=0x8002231d, closed=0x80022322;
constexpr uint32_t server_unavailable=0x8002232d, event_not_found=0x80022339;
constexpr uint16_t server_info_event=0x0001, world_list_event=0x0002;
constexpr uint16_t context_started=0x6f02, context_failed=0x6f01, context_stopped=0x6f03;

struct Callback { uint32_t opd=0, arg=0; };

struct Context {
    Bytes communication_id;
    Callback life, room_event, room_message;
    std::future<Reply> discovery;
    std::vector<uint16_t> servers;
    uint64_t room=0;
    uint32_t room_flags=0;
    uint16_t owner=0;
    bool started=false;
};

struct Event { uint16_t context; Image image; };

struct Completion {
    uint16_t context, event;
    uint32_t id;
    Callback callback;
    Request command;
    std::future<Reply> reply;
    Bytes communication_id;
    bool cancelled=false;
    uint16_t previous_owner=0;
};

struct GuestCall {
    uint16_t context;
    uint32_t opd;
    std::array<uint64_t,8> args;
};

struct PublicRoom {
    uint32_t offset;
    uint64_t id;
    uint16_t players, slots;
    std::string label;
};

struct RoomBrowser {
    uint16_t context;
    uint32_t request;
    Callback callback;
    Bytes packet;
    std::future<Reply> reply;
    Image image;
    std::vector<PublicRoom> rooms;
    size_t selected=0;
    std::chrono::steady_clock::time_point deadline;
};

std::mutex lock;
bool initialized;
bool room_exited=false;
uint32_t next_context=1, next_request=1, next_event=1;
std::map<uint16_t,Context> contexts;
std::map<uint32_t,Event> events;
std::vector<Completion> completions;
std::vector<GuestCall> calls;
std::optional<RoomBrowser> browser;
bool discovery_event_pending=false;
uint32_t discovery_event_node=0;

bool trace_enabled() {
    static const bool enabled = [] {
        const char* value=getenv("TB_OFFLINE_TRACE");
        return value && value[0]=='1';
    }();
    return enabled;
}

void drop_context_work(uint16_t id) {
    if (browser && browser->context==id) { browser.reset();tb_panel_close(); }
    // Keep pending room joins long enough to clean up a successful late reply.
    for (auto& completion:completions)
        if (completion.context==id) completion.cancelled=true;
    std::erase_if(calls,[=](const auto& item){return item.context==id;});
    std::erase_if(events,[=](const auto& item){return item.second.context==id;});
}

void leave(const Bytes& communication_id,uint64_t room) {
    if (!room) return;
    Bytes body{8}; // LeaveRoomRequest.roomId, wire type varint.
    while (room>=128) {
        body.push_back(static_cast<unsigned char>((room&127)|128));
        room>>=7;
    }
    body.push_back(static_cast<unsigned char>(room));
    submit(Request::leave_room,frame_protobuf(communication_id,body));
}

void clear_matching() {
    reset_signaling();
    initialized=false;
    discovery_event_pending=false;
    discovery_event_node=0;
    for (const auto& [id,context]:contexts) {
        if (context.room) room_exited=true;
        leave(context.communication_id,context.room);
        drop_context_work(id);
    }
    contexts.clear();
    events.clear();
    calls.clear();
}

void context_event(uint16_t id, const Context& ctx, uint16_t event, uint32_t error) {
    if (ctx.life.opd)
        calls.push_back({id,ctx.life.opd,{id,event,error?10u:11u,error,ctx.life.arg}});
    if (trace_enabled()) fprintf(stderr,"[rpcn-matching] context=%u event=0x%04x error=0x%08x\n",id,event,error);
}

void finish_discovery(uint16_t id, Context& ctx) {
    const auto body=ctx.discovery.get().body;
    const auto count=body.size()>=3 && !body[0] ? read_le(std::span(body).subspan(1,2)) : 0;
    if (!count || count>64 || body.size()!=3+2*count) {
        context_event(id,ctx,context_failed,server_unavailable);
        return;
    }
    for (uint32_t i=0;i<count;++i)
        ctx.servers.push_back(static_cast<uint16_t>(read_le(std::span(body).subspan(3+i*2,2))));
    ctx.started=true;
    context_event(id,ctx,context_started,0);
    if (trace_enabled()) fprintf(stderr,"[rpcn-matching] server list size=%zu first=%u\n",ctx.servers.size(),ctx.servers.front());
}

Image world_image(const Reply& reply) {
    const auto body=std::span(reply.body);
    if (body.size()<5 || body[0]) return {};
    const auto count=static_cast<uint32_t>(read_le(body.subspan(1,4)));
    if (count>64 || body.size()!=5+4*count) return {};
    Image result{Bytes(8+60*count),{}};
    if (count) result.pointer(0,8);
    result.write(4,count,4);
    for (uint32_t i=0;i<count;++i) result.write(8+60*i,read_le(body.subspan(5+4*i,4)),4);
    if (trace_enabled()) fprintf(stderr,"[rpcn-matching] world list size=%u first=%u\n",
                                 count,count?static_cast<uint32_t>(read_le(body.subspan(5,4))):0u);
    return result;
}

uint32_t store_image(uint16_t ctx,Image image) {
    if (image.bytes.empty()) return 0;
    if (events.size()>=128) events.erase(events.begin());
    const auto key=next_event++;
    events.emplace(key,Event{ctx,std::move(image)});
    return key;
}

uint32_t request_id(uint32_t options) {
    return (options?uint32_t(vm_read16(options+12))<<16:0) | (next_request++ & 0xffff);
}

void answer(uint16_t ctx, uint16_t event, uint32_t id, Callback cb, uint32_t error, Image image={}) {
    if (!cb.opd) return;
    const size_t size=image.bytes.size();
    const auto key=store_image(ctx,std::move(image));
    calls.push_back({ctx,cb.opd,{ctx,id,event,key,error,size,cb.arg}});
    if (trace_enabled())
        fprintf(stderr,"[rpcn-matching] ctx=%u req=%u event=0x%04x key=%u error=0x%08x bytes=%zu\n",
                ctx,id,event,key,error,size);
}

void show_browser() {
    std::vector<std::string> labels;
    if (!browser->reply.valid())
        for (const auto& room:browser->rooms) labels.push_back(room.label);
    tb_panel_show(browser->reply.valid()?InvitePicker::searching_rooms:InvitePicker::rooms,labels,browser->selected);
}

void finish_browser(Image image,uint32_t error=0) {
    answer(browser->context,0x0106,browser->request,browser->callback,error,std::move(image));
    browser.reset();
    tb_panel_close();
}

void pump_browser() {
    if (!browser) return;
    if (!contexts.contains(browser->context) || status().state!=State::online) {
        finish_browser({},closed);
        return;
    }
    auto& view=*browser;
    const auto buttons=tb_panel_take_input();
    if (buttons&0x2000) {
        // cellPadGetData forwards this press to the title's search cancellation
        // handler. Returning an empty search result would start another search.
        browser.reset();
        tb_panel_close();
        return;
    }
    if (view.reply.valid()) {
        if (view.reply.wait_for(0ms)!=std::future_status::ready) {
            if (std::chrono::steady_clock::now()<view.deadline) return;
            view.reply={};
            tb_show_network_toast("ROOM SEARCH TIMED OUT","PRESS SQUARE OR A TO RETRY");
        } else {
            const auto reply=view.reply.get();
            try {
                if (reply.body.empty() || reply.body.front()) throw std::runtime_error("Room search failed");
                auto result=room_reply(Request::search_room,reply,status().npid);
                const auto selected_id=view.rooms.empty()?0:view.rooms[view.selected].id;
                std::vector<PublicRoom> rooms;
                auto offset=static_cast<uint32_t>(result.image.read(12,4));
                for (unsigned i=0;i<result.count;++i) {
                    if (!offset || offset+88>result.image.bytes.size()) throw std::runtime_error("Invalid public room entry");
                    const auto owner=static_cast<uint32_t>(result.image.read(offset+48,4));
                    std::string name;
                    if (owner && owner+16<=result.image.bytes.size())
                        for (unsigned j=0;j<16 && result.image.bytes[owner+j];++j)
                            name.push_back(result.image.bytes[owner+j]);
                    const auto id=result.image.read(offset+24,8);
                    const auto players=static_cast<uint16_t>(result.image.read(offset+38,2));
                    const auto slots=static_cast<uint16_t>(result.image.read(offset+32,2));
                    if (name.empty()) name="ROOM "+std::to_string(id);
                    rooms.push_back({offset,id,players,slots,name+"  "+std::to_string(players)+"/"+std::to_string(slots)});
                    offset=static_cast<uint32_t>(result.image.read(offset,4));
                }
                view.image=std::move(result.image);
                view.rooms=std::move(rooms);
                const auto selected=std::find_if(view.rooms.begin(),view.rooms.end(),[=](const auto& room){return room.id==selected_id;});
                view.selected=selected==view.rooms.end()?0:static_cast<size_t>(selected-view.rooms.begin());
                if (trace_enabled()) fprintf(stderr,"[rpcn-browser] rooms=%zu selected=%zu\n",view.rooms.size(),view.selected);
            } catch (const std::runtime_error& error) {
                fprintf(stderr,"[rpcn-browser] %s\n",error.what());
                tb_show_network_toast("ROOM SEARCH FAILED","PRESS SQUARE OR A TO RETRY");
            }
        }
        show_browser();
        return;
    }
    if (buttons&0x8000) {
        view.reply=submit(Request::search_room,view.packet);
        view.deadline=std::chrono::steady_clock::now()+8s;
        if (!view.reply.valid()) tb_show_network_toast("ROOM SEARCH FAILED","CHECK CONNECTION AND RETRY");
        show_browser();
    } else if ((buttons&0x50) && !view.rooms.empty()) {
        view.selected=(view.selected+(buttons&0x40?1:view.rooms.size()-1))%view.rooms.size();
        show_browser();
    } else if ((buttons&0x4000) && !view.rooms.empty()) {
        const auto& selected=view.rooms[view.selected];
        if (selected.slots && selected.players>=selected.slots) {
            tb_show_network_toast("ROOM IS FULL","REFRESH OR CHOOSE ANOTHER ROOM");
            return;
        }
        if (trace_enabled()) fprintf(stderr,"[rpcn-browser] selected room=%llu\n",static_cast<unsigned long long>(selected.id));
        auto image=std::move(view.image);
        image.write(0,1,4);image.write(4,1,4);image.write(8,1,4);
        image.write(12,selected.offset,4);image.write(selected.offset,0,4);
        std::erase(image.pointers,selected.offset);
        finish_browser(std::move(image));
    }
}

enum class Op {
    init, term, create, destroy, context_callback, start, stop, abort,
    server_list, server_info, world_list, event_data, room_callback, message_callback, signaling_callback,
    create_room, search_room, join_room, leave_room, set_room_data, send_message, ping_room, kick
};

uint32_t perform(Op operation, const ppu_context& c) {
    const uint32_t id=c.gpr[3], req=c.gpr[4], opt=c.gpr[5], out=c.gpr[6];
    if (operation==Op::init) {
        if (!np_is_initialized()) return not_initialized;
        if (initialized) return 0x80022302;
        initialized=true;
        return 0;
    }
    if (!initialized) return not_initialized;
    if (operation==Op::term) { clear_matching(); return 0; }
    if (operation==Op::create) {
        if (!guest_range(c.gpr[3],36) || !guest_range(c.gpr[4],12) ||
            !guest_range(c.gpr[5],128) || !guest_range(c.gpr[6],2)) return invalid_argument;
        if (contexts.size()>=16 || next_context>0xffff) return context_max;
        Context ctx;
        for (unsigned i=0;i<9;++i) {
            const auto ch=vm_read8(req+i);
            if (!((ch>='A' && ch<='Z') || (ch>='0' && ch<='9'))) return invalid_argument;
            ctx.communication_id.push_back(ch);
        }
        const auto suffix=vm_read8(req+10);
        if (vm_read8(req+9)!=0 || suffix>99) return invalid_argument;
        ctx.communication_id.insert(ctx.communication_id.end(),{'_',static_cast<unsigned char>('0'+suffix/10),
                                                                  static_cast<unsigned char>('0'+suffix%10)});
        const auto context_id=static_cast<uint16_t>(next_context++);
        contexts.emplace(context_id,std::move(ctx));
        vm_write16(out,context_id);
        return 0;
    }
    const auto it=id<=0xffff?contexts.find(static_cast<uint16_t>(id)):contexts.end();
    if (it==contexts.end()) return no_context;
    const auto ctx_id=it->first;
    auto& ctx=it->second;
    switch (operation) {
    case Op::destroy:
        discovery_event_pending=false;
        if (ctx.room) room_exited=true;
        leave(ctx.communication_id,ctx.room);
        drop_context_work(ctx_id);
        contexts.erase(it);
        return 0;
    case Op::context_callback:
        if (req && !guest_range(c.gpr[4],8)) return invalid_argument;
        ctx.life={req,opt};
        return 0;
    case Op::start:
        if (ctx.started || ctx.discovery.valid()) return already_started;
        ctx.discovery=submit(Request::server_list,ctx.communication_id);
        return ctx.discovery.valid()?0:server_unavailable;
    case Op::stop:
    case Op::abort:
        discovery_event_pending=false;
        if (!ctx.started && !ctx.discovery.valid()) return not_started;
        ctx.discovery={};
        ctx.started=false;
        if (ctx.room) room_exited=true;
        leave(ctx.communication_id,ctx.room);
        ctx.room=0;
        ctx.servers.clear();
        drop_context_work(ctx_id);
        if (operation==Op::stop) context_event(ctx_id,ctx,context_stopped,0);
        return 0;
    case Op::server_list: {
        if (ctx.discovery.valid()) {
            // This API returns IDs synchronously, including when called just after ContextStartAsync.
            if (ctx.discovery.wait_for(500ms)!=std::future_status::ready) return timeout;
            finish_discovery(ctx_id,ctx);
        }
        if (!ctx.started || status().state!=State::online) return server_unavailable;
        const auto count=std::min<size_t>(opt,ctx.servers.size());
        if (req && count && !guest_range(c.gpr[4],count*2)) return invalid_argument;
        for (size_t i=0;req && i<count;++i) vm_write16(req+2*i,ctx.servers[i]);
        return static_cast<uint32_t>(ctx.servers.size());
    }
    case Op::server_info:
    case Op::world_list: {
        if (!ctx.started) return not_started;
        if (status().state!=State::online) return server_unavailable;
        if (!guest_range(c.gpr[4],2) || (opt && !guest_range(c.gpr[5],16)) ||
            (out && !guest_range(c.gpr[6],4))) return invalid_argument;
        const auto server=vm_read16(req);
        if (std::find(ctx.servers.begin(),ctx.servers.end(),server)==ctx.servers.end()) return invalid_server;
        const Callback cb=opt?Callback{vm_read32(opt),vm_read32(opt+4)}:Callback{};
        if (cb.opd && !guest_range(cb.opd,8)) return invalid_argument;
        if (completions.size()>=128 || calls.size()>=128) return 0x8002231f;
        const auto request=request_id(opt);
        if (operation==Op::server_info) {
            Image result{Bytes(4),{}};
            result.write(0,server,2);
            result.write(2,1,1); // Available, as confirmed by the server-list reply.
            answer(ctx_id,server_info_event,request,cb,0,std::move(result));
        } else {
            auto data=ctx.communication_id;
            data.push_back(static_cast<unsigned char>(server));
            data.push_back(static_cast<unsigned char>(server>>8));
            auto reply=submit(Request::world_list,std::move(data));
            if (!reply.valid()) return server_unavailable;
            completions.push_back({ctx_id,world_list_event,request,cb,Request::world_list,std::move(reply),{}});
        }
        if (out) vm_write32(out,request);
        return 0;
    }
    case Op::event_data: {
        const auto event=events.find(req);
        if (event==events.end() || event->second.context!=ctx_id) return event_not_found;
        const auto& image=event->second.image;
        const auto count=std::min<size_t>(out,image.bytes.size());
        if (!guest_range(c.gpr[5],count)) return invalid_argument;
        for (size_t i=0;i<count;++i) vm_write8(opt+i,image.bytes[i]);
        for (const auto ptr:image.pointers)
            if (ptr+4<=count) vm_write32(opt+ptr,opt+vm_read32(opt+ptr));
        return static_cast<uint32_t>(count);
    }
    case Op::room_callback:
    case Op::message_callback:
    case Op::signaling_callback:
        if (req && !guest_range(c.gpr[4],8)) return invalid_argument;
        if (operation==Op::room_callback) ctx.room_event={req,opt};
        if (operation==Op::message_callback) ctx.room_message={req,opt};
        return 0;
    case Op::kick:
        // Stock RPCN protocol 34 has no kick command. Do not report a fabricated success.
        return ctx.started?0x8002232e:not_started;
    case Op::create_room:
    case Op::search_room:
    case Op::join_room:
    case Op::leave_room:
    case Op::set_room_data:
    case Op::send_message:
    case Op::ping_room: {
        if (operation!=Op::ping_room) discovery_event_pending=false;
        if (!ctx.started) return not_started;
        if (status().state!=State::online) return server_unavailable;
        if (completions.size()>=128 || calls.size()>=128) return 0x8002231f;
        if ((opt && !guest_range(c.gpr[5],16)) || (out && !guest_range(c.gpr[6],4))) return invalid_argument;
        const Callback cb=opt?Callback{vm_read32(opt),vm_read32(opt+4)}:Callback{};
        if (cb.opd && !guest_range(cb.opd,8)) return invalid_argument;
        static constexpr std::array commands{Request::create_room,Request::search_room,Request::join_room,
                                             Request::leave_room,Request::set_room_data,Request::send_room_message,
                                             Request::ping_room_owner};
        static constexpr uint16_t codes[]={0x0101,0x0106,0x0102,0x0103,0x0109,0x0108,0x0e01};
        const auto index=static_cast<size_t>(operation)-static_cast<size_t>(Op::create_room);
        const auto command=commands[index];
        const bool browse=operation==Op::search_room && c.lr==0x001cc8f0 && cb.opd;
        if (browse && browser) return 0x8002231f;
        Bytes data;
        try {
            data=room_request(command,req);
        } catch (const std::runtime_error& error) {
            if (trace_enabled()) fprintf(stderr,"[rpcn-matching] command=%u %s\n",static_cast<unsigned>(command),error.what());
            return invalid_argument;
        }
        Bytes packet=ctx.communication_id;
        if (command==Request::ping_room_owner) packet.insert(packet.end(),data.begin(),data.end());
        else packet=frame_protobuf(std::move(packet),data);
        auto reply=submit(command,browse?packet:std::move(packet));
        if (!reply.valid()) return server_unavailable;
        if (operation==Op::leave_room && ctx.room && vm_read64(req)==ctx.room) room_exited=true;
        const auto request=request_id(opt);
        if (browse) {
            browser.emplace(ctx_id,request,cb,std::move(packet),std::move(reply),Image{},std::vector<PublicRoom>{},0,
                            std::chrono::steady_clock::now()+8s);
            show_browser();
            if (out) vm_write32(out,request);
            return 0;
        }
        completions.push_back({ctx_id,codes[index],request,cb,command,std::move(reply),ctx.communication_id});
        if (out) vm_write32(out,request);
        return 0;
    }
    default:
        return server_unavailable;
    }
}

template<Op operation>
void handler(ppu_context* c) {
    std::lock_guard guard(lock);
    c->gpr[3]=perform(operation,*c);
    static unsigned trace_count=0;
    if (trace_enabled() && trace_count++<1024)
        fprintf(stderr,"[rpcn-matching] guest lr=0x%08x result=0x%08x\n",
                static_cast<uint32_t>(c->lr),static_cast<uint32_t>(c->gpr[3]));
}

}

void reset_matching() {
    std::lock_guard guard(lock);
    clear_matching();
}

bool pump_matching() {
    pump_signaling();
    std::lock_guard guard(lock);
    for (auto& [id,ctx]:contexts)
        if (ctx.discovery.valid() && ctx.discovery.wait_for(0ms)==std::future_status::ready)
            finish_discovery(id,ctx);
    const auto identity=status();
    std::erase_if(completions,[&](auto& item) {
        if (item.reply.wait_for(0ms)!=std::future_status::ready) return false;
        const auto reply=item.reply.get();
        uint32_t error=0;
        Image image;
        if (reply.body.empty()) error=closed;
        else if (const auto code=reply.body.front()) {
            switch (code) {
            case 14: error=0x80022335; break; // Room has disappeared.
            case 15: error=0x80022328; break;
            case 16: error=0x80022b19; break;
            default: error=server_unavailable; break;
            }
        } else if (item.command==Request::world_list) {
            image=world_image(reply);
            if (image.bytes.empty()) error=server_unavailable;
            else if (!item.cancelled) {
                discovery_event_pending=true;
                discovery_event_node=0;
            }
        } else {
            try {
                auto result=room_reply(item.command,reply,identity.npid);
                auto ctx=contexts.find(item.context);
                const bool joined=item.command==Request::join_room || item.command==Request::create_room;
                if (joined && (item.cancelled || ctx==contexts.end()))
                    leave(item.communication_id,result.room);
                else if (ctx!=contexts.end() && !item.cancelled) {
                    if (joined) {
                        ctx->second.room=result.room;
                        ctx->second.owner=result.owner;
                        ctx->second.room_flags=result.flags;
                    }
                    if (item.command==Request::leave_room && ctx->second.room==result.room) {
                        ctx->second.room=0;
                        ctx->second.owner=0;
                        ctx->second.room_flags=0;
                    }
                    if (item.command==Request::get_room_data && ctx->second.room==result.room) {
                        ctx->second.owner=result.owner;
                        const auto cb=ctx->second.room_event;
                        if (cb.opd && item.previous_owner && result.owner!=item.previous_owner) {
                            Image owner{Bytes(32),{}};
                            owner.write(0,item.previous_owner,2);
                            owner.write(2,result.owner,2);
                            owner.write(4,1,1); // SCE_NP_MATCHING2_EVENT_CAUSE_LEAVE_ACTION.
                            const auto key=store_image(item.context,std::move(owner));
                            calls.push_back({item.context,cb.opd,
                                {item.context,result.room,0x1105,key,0,32,cb.arg}});
                        }
                    }
                    if (trace_enabled())
                        fprintf(stderr,"[rpcn-matching] room=%llu members=%u local=%u owner=%u\n",
                                static_cast<unsigned long long>(result.room),result.count,result.member,result.owner);
                }
                image=std::move(result.image);
            } catch (const std::runtime_error& problem) {
                if (trace_enabled()) fprintf(stderr,"[rpcn-matching] %s\n",problem.what());
                error=server_unavailable;
            }
        }
        if (error && !item.cancelled && item.command==Request::join_room)
            tb_show_network_toast(error==0x80022b19?"ROOM IS FULL":"ROOM IS UNAVAILABLE","REFRESH THE PUBLIC ROOM LIST");
        if (!item.cancelled) answer(item.context,item.event,item.id,item.callback,error,std::move(image));
        return true;
    });
    pump_browser();
    for (const auto& packet:take_notifications()) {
        try {
            auto notice=room_notice(packet,identity.npid);
            auto ctx=std::find_if(contexts.begin(),contexts.end(),
                                  [&](const auto& entry){return notice.room && entry.second.room==notice.room;});
            if (ctx==contexts.end()) continue;
            const auto id=ctx->first;
            const bool message=notice.event==0x2102;
            const auto cb=message?ctx->second.room_message:ctx->second.room_event;
            auto& room=ctx->second;
            const bool owner_left=notice.event==0x1102 && notice.result.member==room.owner;
            if (owner_left && !(room.room_flags & room_owner_auto_grant)) {
                // Stock RPCN elects a successor even when automatic transfer is disabled.
                // Leave its migrated room and deliver the original departure cause to the game.
                leave(room.communication_id,notice.room);
                const auto& member_update=notice.result.image;
                Image closure{Bytes(28),{}};
                closure.write(0,member_update.read(4,1),1);
                closure.copy(8,std::span(member_update.bytes).subspan(8,20));
                notice.event=0x1104;
                notice.result.image=std::move(closure);
            }
            if (notice.event==0x1104) {
                room_exited=true;
                room.room=0;
                room.owner=0;
                room.room_flags=0;
            }
            if (notice.event==0x1106) room.room_flags=notice.result.flags;
            if (owner_left && notice.event==0x1102) {
                // RPCN elects the successor. Fetch that result instead of guessing from member IDs.
                Bytes room_id{8};
                auto value=notice.room;
                while (value>=128) { room_id.push_back((value&127)|128); value>>=7; }
                room_id.push_back(static_cast<unsigned char>(value));
                auto reply=submit(Request::get_room_data,frame_protobuf(ctx->second.communication_id,room_id));
                if (reply.valid()) completions.push_back({id,0,0,{},Request::get_room_data,std::move(reply),
                                                          {},false,ctx->second.owner});
            }
            if (!cb.opd) continue;
            const size_t size=notice.result.image.bytes.size();
            const auto key=store_image(id,std::move(notice.result.image));
            if (message)
                calls.push_back({id,cb.opd,{id,notice.room,notice.result.member,notice.event,key,0,size,cb.arg}});
            else
                calls.push_back({id,cb.opd,{id,notice.room,notice.event,key,0,size,cb.arg}});
            if (trace_enabled())
                fprintf(stderr,"[rpcn-matching] notification ctx=%u room=%llu event=0x%04x member=%u key=%u\n",
                        id,static_cast<unsigned long long>(notice.room),notice.event,notice.result.member,key);
        } catch (const std::runtime_error& problem) {
            if (trace_enabled()) fprintf(stderr,"[rpcn-matching] %s\n",problem.what());
        }
    }
    std::erase_if(calls,[](const auto& call) {
        return !cellSysutilQueueGuestCallbackArgs(call.opd,call.args.data());
    });
    return std::exchange(room_exited,false);
}

void register_matching() {
    register_signaling();
    struct Binding {const char* name; tank_battles_override_fn handler;};
    static constexpr Binding bindings[]={
        {"sceNpMatching2Init",handler<Op::init>},
        {"sceNpMatching2Term",handler<Op::term>},
        {"sceNpMatching2CreateContext",handler<Op::create>},
        {"sceNpMatching2DestroyContext",handler<Op::destroy>},
        {"sceNpMatching2RegisterContextCallback",handler<Op::context_callback>},
        {"sceNpMatching2ContextStartAsync",handler<Op::start>},
        {"sceNpMatching2ContextStop",handler<Op::stop>},
        {"sceNpMatching2AbortContextStart",handler<Op::abort>},
        {"sceNpMatching2GetServerIdListLocal",handler<Op::server_list>},
        {"sceNpMatching2GetServerInfo",handler<Op::server_info>},
        {"sceNpMatching2GetWorldInfoList",handler<Op::world_list>},
        {"sceNpMatching2GetEventData",handler<Op::event_data>},
        {"sceNpMatching2RegisterRoomEventCallback",handler<Op::room_callback>},
        {"sceNpMatching2RegisterRoomMessageCallback",handler<Op::message_callback>},
        {"sceNpMatching2RegisterSignalingCallback",handler<Op::signaling_callback>},
        {"sceNpMatching2CreateJoinRoom",handler<Op::create_room>},
        {"sceNpMatching2SearchRoom",handler<Op::search_room>},
        {"sceNpMatching2JoinRoom",handler<Op::join_room>},
        {"sceNpMatching2LeaveRoom",handler<Op::leave_room>},
        {"sceNpMatching2SetRoomDataInternal",handler<Op::set_room_data>},
        {"sceNpMatching2SendRoomMessage",handler<Op::send_message>},
        {"sceNpMatching2SignalingGetPingInfo",handler<Op::ping_room>},
        {"sceNpMatching2KickoutRoomMember",handler<Op::kick>},
    };
    for (const auto& binding:bindings)
        tank_battles_register_override(ps3_compute_nid(binding.name),binding.handler);
}

}

extern "C" void tb_rpcn_retain_discovery_event(ppu_context* context) {
    if (!tb::rpcn::enabled()) return;
    using namespace tb::rpcn;
    std::lock_guard guard(lock);
    if (!discovery_event_pending) return;
    // The title expires untouched network events after two update passes. A LAN world
    // reply can arrive before the Create/Join menu finishes animating. Retain exactly
    // its first success node until the guest consumes it. Duplicate status events and
    // all other events retain their original lifetime. No network result is fabricated.
    const auto queue=context->gpr[3];
    if (!guest_range(queue,0x5c)) return;
    const auto head=vm_read32(queue+0x54), count=vm_read32(queue+0x58);
    if (!guest_range(head,8) || count>128) return;
    auto node=vm_read32(head);
    for (uint32_t i=0;i<count && node!=head;++i) {
        if (!guest_range(node,16)) return;
        if (!discovery_event_node && vm_read32(node+8)==1) discovery_event_node=node;
        if (node==discovery_event_node) {
            vm_write8(node+12,0); // The original expiration function will mark it once more.
            return;
        }
        node=vm_read32(node);
    }
    if (discovery_event_node) discovery_event_pending=false;
}
