// Native RPCN score adapter. Guest ABI follows sceNp.h / sceNp.cpp in RPCS3.
#include "score.h"
#include "matching_data.h"
#include "stubs.h"
#include "ppu_recomp.h"
#include "ps3emu/nid.h"
#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <map>
#include <mutex>
#include <thread>
extern "C" int np_is_initialized();

namespace tb::rpcn {
namespace {
using namespace std::chrono_literals;
using Clock=std::chrono::steady_clock;
using score_wire::Bytes;
constexpr uint32_t not_init=0x8002a102,argument=0x8002a104,invalid=0x8002a1a3;
constexpr uint32_t no_login=0x8002a106,busy=0x8002a1ab,aborted=0x8002a109;
constexpr uint32_t bad_reply=0x8002a10b,timeout=0x8002a10f,unavailable=0x8002a409;
constexpr uint32_t max_game_data=6'000'000, variable_info_size=196, variable_payload_size=189;
struct Title {
    Bytes communication;
    int32_t pc=0;
    uint64_t timeout_us=10000000;
};
struct Transaction {
    Title title;
    uint32_t result=0,board=0;
    bool pending=false,by_id=false,deprecated=false;
    Request command{};
    std::future<Reply> reply;
    Clock::time_point deadline;
    Bytes cache_key;
    uint32_t rank=0,comments=0,info=0,info_stride=64,num=0,sorted=0,total=0;
    struct Upload {
        Bytes bytes;
        uint64_t score=0;
        uint32_t board=0,size=0;
    } upload;
    struct Download {
        Bytes bytes;
        std::string npid;
        uint32_t board=0,total=0,dest=0,capacity=0;
        size_t consumed=0;
    } download;
};
std::mutex lock;
std::atomic_bool display_empty_board{false};
uint32_t display_board=0;
bool initialized=false;
uint32_t next_id=1;
std::map<uint32_t,Title> titles;
std::map<uint32_t,Transaction> transactions;
struct CachedRanking {
    Bytes key;
    Reply reply;
    Clock::time_point expires;
};
// The title polls empty pages and personal rankings repeatedly. Cache one
// recent query per operation to bound traffic and pick up new scores quickly.
std::array<CachedRanking,2> ranking_cache;
CachedRanking& cache_for(Request command) {
    return ranking_cache[command==Request::score_range?0:1];
}
uint64_t arg(ppu_context* c,unsigned index) {
    if(index<8)return c->gpr[3+index];
    auto at=c->gpr[1]+112+8*(index-8);
    if(!guest_range(at,8))throw argument;
    return vm_read64(at);
}
void range(uint64_t a,uint64_t n) {if(!guest_range(a,n))throw argument;}
void zero(uint32_t a,uint32_t n) {
    range(a,n);
    for(uint32_t i=0;i<n;++i)vm_write8(a+i,0);
}
Bytes bytes(uint32_t a,uint32_t n) {
    range(a,n);
    Bytes b(n);
    for(uint32_t i=0;i<n;++i)b[i]=vm_read8(a+i);
    return b;
}
std::string text(uint32_t a,uint32_t max) {
    range(a,max);
    std::string s;
    for(uint32_t i=0;i<max;++i) {
        auto ch=vm_read8(a+i);
        if(!ch)break;
        s.push_back(char(ch));
    }
    return s;
}
void copy(uint32_t a,std::span<const unsigned char> b) {
    range(a,b.size());
    for(size_t i=0;i<b.size();++i)vm_write8(a+i,b[i]);
}
std::span<const unsigned char> text_bytes(const std::string& s) {
    return {reinterpret_cast<const unsigned char*>(s.data()),s.size()};
}
void string(uint32_t a,const std::string& s) {copy(a,text_bytes(s));}
uint32_t error(Request command,unsigned e) {
    switch(e) {
    case 23:return 0x8002a406; // Unauthorized
    case 26:return command==Request::score_data?0x8002a418:0x8002a414; // Not found
    case 29:return 0x8002a415; // Score not best
    case 30:return 0x8002a423; // Invalid score
    case 31:return 0x8002a42c; // Game data already exists
    default:return unavailable;
    }
}
void read_chunk(Transaction& t) {
    auto& d=t.download;
    const auto size=std::min<size_t>(d.capacity,d.bytes.size()-d.consumed);
    range(d.total,4);
    copy(d.dest,std::span(d.bytes).subspan(d.consumed,size));
    vm_write32(d.total,uint32_t(d.bytes.size()));
    d.consumed+=size;
    t.result=uint32_t(size);
}
void output(Transaction& t,const Reply& reply) {
    const auto& b=reply.body;
    if(b.empty()){t.result=unavailable;return;}
    if(b[0]){t.result=error(t.command,b[0]);return;}
    if(t.command==Request::record_score_data) {
        if(b.size()!=1)throw bad_reply;
        return;
    }
    if(t.command==Request::score_data) {
        auto data=score_wire::packet_data(std::span(b).subspan(1));
        if(data.size()>max_game_data)throw bad_reply;
        t.download.bytes.assign(data.begin(),data.end());
        read_chunk(t);
        return;
    }
    if(t.command==Request::record_score) {
        if(b.size()!=5)throw bad_reply;
        if(t.rank){range(t.rank,4);vm_write32(t.rank,uint32_t(read_le(std::span(b).subspan(1))));}
        return;
    }
    auto r=score_wire::ranking(std::span(b).subspan(1));
    if(r.ranks.size()>t.num || r.comments.size()>t.num || r.infos.size()>t.num)throw bad_reply;
    if(t.info) {
        const auto limit=t.info_stride==variable_info_size?variable_payload_size:64;
        for(const auto& i:r.infos)if(i.size()>limit)throw bad_reply;
    }
    const uint32_t rank_size=t.deprecated?120:128,stride=rank_size+(t.by_id?8:0);
    // Validate the complete response before touching guest memory.
    range(t.rank,stride*t.num);range(t.sorted,8);range(t.total,4);
    if(t.comments)range(t.comments,64*t.num);
    if(t.info)range(t.info,t.info_stride*t.num);
    zero(t.rank,stride*t.num);
    if(t.comments)zero(t.comments,64*t.num);
    if(t.info)zero(t.info,t.info_stride*t.num);
    for(size_t i=0;i<r.ranks.size();++i) {
        const auto& row=r.ranks[i];if(!row.date)continue;
        auto at=t.rank+uint32_t(i)*stride;
        if(t.by_id){vm_write32(at,1);at+=8;}
        string(at,row.npid);string(at+36,row.name);
        unsigned off=88;
        if(!t.deprecated){vm_write32(at+off,row.pc);off+=4;}
        vm_write32(at+off,row.rank);vm_write32(at+off+4,row.rank);vm_write32(at+off+8,row.rank);
        vm_write32(at+off+12,row.has_data);
        vm_write64(at+(t.deprecated?104:112),uint64_t(row.score));
        vm_write64(at+(t.deprecated?112:120),row.date);
    }
    for(size_t i=0;t.comments && i<r.comments.size();++i)string(t.comments+uint32_t(i)*64,r.comments[i]);
    for(size_t i=0;t.info && i<r.infos.size();++i) {
        auto at=t.info+uint32_t(i)*t.info_stride;
        if(t.info_stride==variable_info_size){vm_write32(at,uint32_t(r.infos[i].size()));at+=4;}
        copy(at,r.infos[i]);
    }
    vm_write64(t.sorted,r.sorted);vm_write32(t.total,r.total);
    if((r.total==0 || t.by_id) && !t.cache_key.empty()) {
        cache_for(t.command)={std::move(t.cache_key),reply,Clock::now()+2s};
    }
    if(t.command==Request::score_range && t.num>1) {
        display_board=t.board;
        display_empty_board.store(r.total==0,std::memory_order_relaxed);
    }
    // Tank Battles retries an empty board indefinitely on RANKING_NOT_FOUND.
    // Return a completed empty page or an unranked personal row instead.
    t.result=t.by_id?0:uint32_t(r.ranks.size());
}
bool score_trace() {
    const auto* value=std::getenv("TB_OFFLINE_TRACE");
    return value && value[0]=='1';
}
void finish(Transaction& t) {
    if(!t.pending)return;
    if(t.reply.wait_for(0ms)==std::future_status::ready) {
        t.result=0;
        try{output(t,t.reply.get());}
        catch(uint32_t e){t.result=e;}
        catch(const std::exception& e){fprintf(stderr,"[rpcn-score] invalid response: %s\n",e.what());t.result=bad_reply;}
        t.pending=false;
        if(score_trace())fprintf(stderr,"[rpcn-score] complete board=%u op=%u result=0x%08x\n",t.board,unsigned(t.command),t.result);
    } else if(Clock::now()>=t.deadline){
        t.reply={};t.result=timeout;t.pending=false;
    }
}
Transaction& transaction(uint32_t id) {
    auto i=transactions.find(id);
    if(i==transactions.end())throw invalid;
    return i->second;
}
void begin(Transaction& t,Request command,uint32_t board,const Bytes& request,std::span<const unsigned char> extra={}) {
    if(status().state!=State::online)throw no_login;
    finish(t);if(t.pending)throw busy;
    t.result=0;t.command=command;t.board=board;
    auto body=score_wire::frame(t.title.communication,request);
    if(!extra.empty())score_wire::append_packet(body,extra);
    if(command==Request::score_range && board!=display_board)
        display_empty_board.store(false,std::memory_order_relaxed);
    t.cache_key.clear();
    if(command==Request::score_range || command==Request::score_npid) {
        const auto& cached=cache_for(command);
        if(cached.expires>Clock::now() && cached.key==body) {
            output(t,cached.reply);
            return;
        }
        t.cache_key=body;
    } else if(command==Request::record_score || command==Request::record_score_data) {
        ranking_cache={};
    }
    t.reply=submit(command,std::move(body));
    if(!t.reply.valid())throw unavailable;
    t.pending=true;t.deadline=Clock::now()+std::chrono::microseconds(t.title.timeout_us);
    if(score_trace())fprintf(stderr,"[rpcn-score] request board=%u op=%u count=%u pc=%d\n",board,unsigned(command),t.num,t.title.pc);
}
enum class Op {init,term,title,destroy_title,create,destroy,wait,poll,abort,pc,timeout,record,by_range,by_id,upload,download};
uint32_t perform(Op op,ppu_context* c,std::unique_lock<std::mutex>& guard) {
    auto a=uint32_t(c->gpr[3]),b=uint32_t(c->gpr[4]);
    if(op==Op::init) {
        if(!np_is_initialized())return 0x8002aa01;
        if(initialized)return 0x8002a101;
        initialized=true;
        return 0;
    }
    if(!initialized)return not_init;
    switch(op) {
    case Op::term:
        titles.clear();transactions.clear();initialized=false;
        ranking_cache={};
        display_empty_board.store(false,std::memory_order_relaxed);
        return 0;
    case Op::title: {
        range(a,12);range(b,128);range(c->gpr[5],36);
        auto id=text(a,9);if(id.size()!=9 || vm_read8(a+10)>99)return argument;
        char suffix[5];snprintf(suffix,sizeof(suffix),"_%02u",vm_read8(a+10));id+=suffix;
        if(!std::all_of(id.begin(),id.begin()+9,[](char ch){return (ch>='A' && ch<='Z') || (ch>='0' && ch<='9');}))return argument;
        if(titles.size()>=16)return 0x8002a107;
        auto handle=next_id++;titles.emplace(handle,Title{Bytes(id.begin(),id.end())});
        if(score_trace())
            fprintf(stderr,"[rpcn-score] title=%s context=%u\n",id.c_str(),handle);
        return handle;
    }
    case Op::destroy_title:if(!titles.erase(a))return invalid;return 0;
    case Op::create: {
        auto i=titles.find(a);if(i==titles.end())return invalid;
        if(status().state!=State::online)return no_login;
        if(transactions.size()>=32)return 0x8002a107;
        auto handle=next_id++;Transaction t;t.title=i->second;transactions.emplace(handle,std::move(t));return handle;
    }
    case Op::destroy:if(!transactions.erase(a))return invalid;return 0;
    case Op::pc:case Op::timeout: {
        if(op==Op::pc && int32_t(b)<0)return argument;
        if(op==Op::timeout && c->gpr[4]<10000000)return argument;
        auto title=titles.find(a);
        auto* value=title!=titles.end()?&title->second:&transaction(a).title;
        if(op==Op::pc)value->pc=int32_t(b);else value->timeout_us=b;return 0;
    }
    case Op::abort: {
        auto& t=transaction(a);
        t.reply={};
        t.upload={};
        t.download={};
        t.pending=false;
        t.result=aborted;
        return 0;
    }
    case Op::wait:case Op::poll: {
        range(b,4);
        // Unlock while waiting so another guest thread can cancel/destroy/terminate.
        for(;;) {
            auto& t=transaction(a);
            finish(t);
            if(!t.pending) {
                vm_write32(b,t.result);
                return 0;
            }
            if(op==Op::poll)return 1;
            guard.unlock();
            std::this_thread::sleep_for(1ms);
            guard.lock();
            if(!initialized)return not_init;
        }
    }
    case Op::record: {
        auto& t=transaction(a);finish(t);if(t.pending)return busy;
        auto comment=uint32_t(arg(c,3)),info=uint32_t(arg(c,4)),tmp=uint32_t(arg(c,5));
        if(arg(c,7))return argument;
        if(tmp)range(tmp,4);
        Bytes pb;score_wire::number(pb,1,b);score_wire::number(pb,2,uint64_t(int64_t(t.title.pc)));score_wire::number(pb,3,arg(c,2));
        if(comment) {
            auto s=text(comment,64);
            if(s.size()>63)return argument;
            score_wire::data(pb,4,text_bytes(s));
        }
        if(info) {
            score_wire::data(pb,5,bytes(info,64));
        }
        t.rank=tmp;t.num=0;t.upload={};t.download={};begin(t,Request::record_score,b,pb);return 0;
    }
    case Op::upload: {
        auto& t=transaction(a);finish(t);if(t.pending)return busy;
        if(status().state!=State::online)return no_login;
        const auto score=arg(c,2);
        const auto total=uint32_t(arg(c,3)),size=uint32_t(arg(c,4)),source=uint32_t(arg(c,5));
        if(!size || size>total || total>max_game_data || arg(c,7))return argument;
        auto& u=t.upload;
        if(u.size && (u.size!=total || u.board!=b || u.score!=score || size>total-u.bytes.size()))
            return argument;
        auto chunk=bytes(source,size);
        if(!u.size)u={{},score,b,total};
        u.bytes.insert(u.bytes.end(),chunk.begin(),chunk.end());
        t.result=0;t.download={};t.num=0;
        if(u.bytes.size()==total) {
            Bytes pb;
            score_wire::number(pb,1,b);
            score_wire::number(pb,2,uint64_t(t.title.pc));
            score_wire::number(pb,3,score);
            auto payload=std::move(u.bytes);
            u={};
            begin(t,Request::record_score_data,b,pb,payload);
        }
        return 0;
    }
    case Op::download: {
        auto& t=transaction(a);finish(t);if(t.pending)return busy;
        if(status().state!=State::online)return no_login;
        auto npid=text(uint32_t(arg(c,2)),17);
        const auto total=uint32_t(arg(c,3)),capacity=uint32_t(arg(c,4)),dest=uint32_t(arg(c,5));
        if(npid.empty() || npid.size()>16 || !capacity || capacity>max_game_data || arg(c,7))return argument;
        range(total,4);range(dest,capacity);
        t.upload={};
        auto& d=t.download;
        if(d.board==b && d.npid==npid && d.consumed<d.bytes.size()) {
            d.total=total;d.capacity=capacity;d.dest=dest;
            read_chunk(t);return 0;
        }
        d={{},std::move(npid),b,total,dest,capacity,0};
        Bytes pb;
        score_wire::number(pb,1,b);
        score_wire::data(pb,2,text_bytes(d.npid));
        score_wire::number(pb,3,uint64_t(t.title.pc));
        t.num=0;
        begin(t,Request::score_data,b,pb);
        return 0;
    }
    case Op::by_range:case Op::by_id: {
        auto& t=transaction(a);finish(t);if(t.pending)return busy;
        const bool by_id=op==Op::by_id;
        // By-NP-ID adds the npIdArraySize argument ahead of rankArray.
        const unsigned shift=by_id?1:0;
        auto num=uint32_t(arg(c,9+shift));
        if(!num || num>(by_id?101u:100u))return argument;
        auto rank=uint32_t(arg(c,3+shift)),rank_bytes=uint32_t(arg(c,4+shift));
        auto comment=uint32_t(arg(c,5+shift)),comment_bytes=uint32_t(arg(c,6+shift));
        auto info=uint32_t(arg(c,7+shift)),info_bytes=uint32_t(arg(c,8+shift));
        auto sorted=uint32_t(arg(c,10+shift)),total=uint32_t(arg(c,11+shift));
        auto option=uint32_t(arg(c,13+shift));
        uint32_t start=uint32_t(arg(c,2));
        if(by_id) {
            if(option || arg(c,3)!=uint64_t(num)*36)return argument;
            range(start,uint64_t(num)*36);
        } else if(option) {
            range(option,12);
            if(vm_read32(option)!=12)return argument;
            auto ptr=vm_read32(option+4);
            if(ptr) {
                range(ptr,4);
                start=vm_read32(ptr);
            }
        }
        if(!start)return argument;
        bool deprecated=rank_bytes==num*(by_id?128:120);
        if(rank_bytes!=num*(by_id?136:128) && !deprecated)return 0x8002a1ac;
        if(comment && comment_bytes!=num*64)return 0x8002a1ac;
        if(info && info_bytes!=num*64 && info_bytes!=num*variable_info_size)return 0x8002a1ac;
        range(rank,rank_bytes);range(sorted,8);range(total,4);if(comment)range(comment,comment_bytes);if(info)range(info,info_bytes);
        Bytes pb;score_wire::number(pb,1,b);
        if(by_id) {
            for(uint32_t i=0;i<num;++i) {
                auto s=text(start+i*36,17);
                if(s.empty() || s.size()>16)return argument;
                Bytes nested;
                score_wire::data(nested,1,text_bytes(s));
                score_wire::data(pb,2,nested);
            }
        } else {
            score_wire::number(pb,2,start);
            score_wire::number(pb,3,num);
        }
        score_wire::number(pb,by_id?3:4,comment!=0);score_wire::number(pb,by_id?4:5,info!=0);
        t.rank=rank;t.comments=comment;t.info=info;t.info_stride=info?info_bytes/num:64;t.sorted=sorted;t.total=total;t.num=num;t.by_id=by_id;t.deprecated=deprecated;
        t.upload={};t.download={};
        begin(t,by_id?Request::score_npid:Request::score_range,b,pb);return 0;
    }
    default:return argument;
    }
}
template<Op op>
void handler(ppu_context* c) {
    std::unique_lock guard(lock);
    uint32_t result;
    try {
        result=perform(op,c,guard);
    } catch(uint32_t error) {
        result=error;
    } catch(const std::exception& e) {
        fprintf(stderr,"[rpcn-score] %s\n",e.what());
        result=bad_reply;
    }
    c->gpr[3]=uint64_t(int64_t(int32_t(result)));
}
}
void reset_score() {
    std::lock_guard guard(lock);
    transactions.clear();
    titles.clear();
    initialized=false;
    ranking_cache={};
    display_empty_board.store(false,std::memory_order_relaxed);
}
void register_score(){
#define B(name,op) tank_battles_register_override(ps3_compute_nid(name),handler<Op::op>)
    B("sceNpScoreInit",init);B("sceNpScoreTerm",term);B("sceNpScoreCreateTitleCtx",title);B("sceNpScoreDestroyTitleCtx",destroy_title);
    B("sceNpScoreCreateTransactionCtx",create);B("sceNpScoreDestroyTransactionCtx",destroy);
    B("sceNpScoreSetTimeout",timeout);B("sceNpScoreSetPlayerCharacterId",pc);B("sceNpScoreAbortTransaction",abort);
    B("sceNpScoreWaitAsync",wait);B("sceNpScorePollAsync",poll);B("sceNpScoreRecordScoreAsync",record);
    B("sceNpScoreRecordGameDataAsync",upload);B("sceNpScoreGetGameDataAsync",download);
    B("sceNpScoreGetRankingByRangeAsync",by_range);B("sceNpScoreGetRankingByNpIdAsync",by_id);
#undef B
}

// The generated hooks are limited to the two High Scores list placeholders.
extern "C" uint32_t tb_rpcn_score_placeholder(uint32_t loading,uint32_t empty) {
    return tb::rpcn::display_empty_board.load(std::memory_order_relaxed)?empty:loading;
}
}
