#include "rpcn/score.h"
#include "rpcn/matching_data.h"
#include "ppu_recomp.h"
#include "ps3emu/nid.h"
#include "stubs.h"
#include <array>
#include <cassert>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <map>
#include <thread>
using namespace tb::rpcn;
using namespace std::chrono_literals;
static std::array<uint8_t,1<<20> memory;
static std::map<uint32_t,tank_battles_override_fn> hle;
extern "C" {
uint32_t ppu_vm_size=memory.size();
int np_is_initialized(){return 1;}
uint8_t vm_read8(uint64_t a){return memory.at(a);}
uint16_t vm_read16(uint64_t a){return uint16_t(vm_read8(a))<<8|vm_read8(a+1);}
uint32_t vm_read32(uint64_t a){return uint32_t(vm_read16(a))<<16|vm_read16(a+2);}
uint64_t vm_read64(uint64_t a){return uint64_t(vm_read32(a))<<32|vm_read32(a+4);}
void vm_write8(uint64_t a,uint8_t v){memory.at(a)=v;}
void vm_write16(uint64_t a,uint16_t v){vm_write8(a,v>>8);vm_write8(a+1,v);}
void vm_write32(uint64_t a,uint32_t v){vm_write16(a,v>>16);vm_write16(a+2,v);}
void vm_write64(uint64_t a,uint64_t v){vm_write32(a,v>>32);vm_write32(a+4,v);}
}
void tank_battles_register_override(uint32_t n,tank_battles_override_fn f){hle[n]=f;}
namespace tb::rpcn {
bool guest_range(uint64_t a,uint64_t n){return a>=0x10000 && a<=memory.size() && n<=memory.size()-a;}
uint64_t read_le(std::span<const unsigned char> b){uint64_t n=0;for(size_t i=0;i<b.size();++i)n|=uint64_t(b[i])<<(i*8);return n;}
}
#ifndef TB_SCORE_LIVE
static std::promise<Reply> response;
static unsigned submissions;
static Request requested;
static Bytes request;
static bool online=true;
namespace tb::rpcn {
Status status(){return {online?State::online:State::offline,"ScorePlayer",1,"fixture"};}
std::future<Reply> submit(Request op,Bytes b){++submissions;requested=op;request=std::move(b);response=std::promise<Reply>();return response.get_future();}
}
#endif
uint32_t call(const char* name,std::initializer_list<uint64_t> args={}) {
    ppu_context c{};c.gpr[1]=0x80000;unsigned n=0;
    for(auto value:args){if(n<8)c.gpr[3+n]=value;else vm_write64(c.gpr[1]+112+8*(n-8),value);++n;}
    hle.at(ps3_compute_nid(name))(&c);return uint32_t(c.gpr[3]);
}
static void string(uint32_t a,const char* s){for(size_t i=0;i<=strlen(s);++i)vm_write8(a+i,s[i]);}
static constexpr uint32_t comm=0x10000,pass=0x11000,self=0x12000,result=0x13000,rows=0x20000,sorted=0x30000,total=0x31000;
static uint32_t wait(uint32_t t){assert(!call("sceNpScoreWaitAsync",{t,result}));return vm_read32(result);}
static uint32_t title(){assert(!call("sceNpScoreInit"));string(comm,"TBSCORE00");string(self,"ScorePlayer");return call("sceNpScoreCreateTitleCtx",{comm,pass,self});}
static void query(uint32_t t,uint32_t board=123){assert(!call("sceNpScoreGetRankingByRangeAsync",{t,board,1,rows,256,0,0,0,0,2,sorted,total,0,0}));}
static Bytes ranked() {
    Bytes row,pb;auto text=[&](unsigned tag,const char* s){score_wire::data(row,tag,{reinterpret_cast<const unsigned char*>(s),strlen(s)});};
    text(1,"ScorePlayer");text(2,"Score Player");score_wire::number(row,3,7);score_wire::number(row,4,1);
    score_wire::number(row,5,uint64_t(int64_t(-9223372036854770000ll)));score_wire::number(row,6,1);score_wire::number(row,7,123456789);
    score_wire::data(pb,1,row);score_wire::number(pb,4,987654321);score_wire::number(pb,5,1);
    auto framed=score_wire::frame(Bytes(12,'X'),pb);framed.erase(framed.begin(),framed.begin()+12);framed.insert(framed.begin(),0);return framed;
}
int main(int argc,char** argv) {
    register_score();
#ifdef TB_SCORE_LIVE
    if(argc!=2 && argc!=3)return 2;
    if(!start(argv[1]))return 1;
    for(unsigned i=0;i<200 && status().state!=State::online;++i)std::this_thread::sleep_for(50ms);
    if(status().state!=State::online){stop();return 1;}
    auto ctx=title(),t=call("sceNpScoreCreateTransactionCtx",{ctx});
    const auto npid=argc==3?std::string(argv[2]):status().npid;
    string(self,npid.c_str());
    constexpr uint32_t source=0x14000,destination=0x15000,game_data_size=7;
    string(source,"fixture");
    // Isolated fixture title, never write invented scores to Tank Battles boards.
    if(argc==2) {
        assert(!call("sceNpScoreRecordScoreAsync",{t,123,1234567,0,0,total,0,0}));
        assert(wait(t)==0);
        assert(!call("sceNpScoreRecordGameDataAsync",{t,123,1234567,game_data_size,3,source,0,0}));
        assert(wait(t)==0);
        assert(!call("sceNpScoreRecordGameDataAsync",{t,123,1234567,game_data_size,4,source+3,0,0}));
        assert(wait(t)==0);
        assert(!call("sceNpScoreRecordScoreAsync",{t,123,1234566,0,0,total,0,0}));
        assert(wait(t)==0x8002a415);
    }
    query(t);assert(wait(t)>=1 && vm_read32(total)>=1);
    assert(vm_read32(rows+92)>=1 && vm_read64(rows+112)==1234567);
    assert(!call("sceNpScoreGetRankingByNpIdAsync",{t,123,self,36,rows,136,0,0,0,0,1,sorted,total,0,0}));
    assert(wait(t)==0 && vm_read32(rows)==1 && vm_read32(rows+8+104)==1 && vm_read64(rows+8+112)==1234567);
    assert(!call("sceNpScoreGetGameDataAsync",{t,123,self,total,2,destination,0,0}));
    assert(wait(t)==2 && vm_read32(total)==game_data_size);
    assert(!call("sceNpScoreGetGameDataAsync",{t,123,self,total,5,destination+2,0,0}));
    assert(wait(t)==5 && vm_read32(total)==game_data_size);
    for(uint32_t i=0;i<game_data_size;++i)assert(vm_read8(destination+i)==vm_read8(source+i));
    reset_score();stop();puts("PASS: live RPCN score, rank and attached game data");
#else
    (void)argc;(void)argv;
    assert(call("sceNpScoreCreateTransactionCtx",{1})==0x8002a102);
    auto ctx=title();assert(ctx && ctx<100);
    auto t=call("sceNpScoreCreateTransactionCtx",{ctx});assert(t>ctx);
    query(t);assert(requested==Request::score_range && request.size()>16);
    assert(request[0]=='T' && request[11]=='0' && request[16]==8 && request[17]==123);
    assert(call("sceNpScorePollAsync",{t,result})==1);
    assert(call("sceNpScoreGetRankingByRangeAsync",{t,123,1,rows,256,0,0,0,0,2,sorted,total,0,0})==0x8002a1ab);
    response.set_value({ranked()});assert(wait(t)==1);
    assert(vm_read32(rows+88)==7 && vm_read32(rows+92)==1 && vm_read32(rows+96)==1 && vm_read32(rows+100)==1);
    assert(vm_read32(rows+104)==1 && vm_read64(rows+112)==uint64_t(int64_t(-9223372036854770000ll)) && vm_read64(rows+120)==123456789);
    assert(vm_read64(sorted)==987654321 && vm_read32(total)==1 && vm_read32(rows+128)==0);
    assert(!call("sceNpScoreGetRankingByNpIdAsync",{t,123,self,36,rows,136,0,0,0,0,1,sorted,total,0,0}));
    response.set_value({ranked()});assert(wait(t)==0 && vm_read32(rows)==1 && vm_read32(rows+8+88)==7);
    assert(!call("sceNpScoreGetRankingByRangeAsync",{t,123,1,rows,240,0,0,0,0,2,sorted,total,0,0}));
    response.set_value({ranked()});assert(wait(t)==1 && vm_read64(rows+104)==uint64_t(int64_t(-9223372036854770000ll)));
    query(t);response.set_value({{0,0,0,0,0}});assert(wait(t)==0 && vm_read32(total)==0);
    const auto before=submissions;
    query(t);assert(wait(t)==0 && submissions==before);
    query(t,124);auto malformed=ranked();malformed.pop_back();response.set_value({malformed});vm_write32(rows,0xdeadbeef);
    assert(wait(t)==0x8002a10b && vm_read32(rows)==0xdeadbeef);
    query(t,124);assert(!call("sceNpScoreAbortTransaction",{t}));response.set_value({ranked()});assert(wait(t)==0x8002a109);
    query(t,124);assert(!call("sceNpScoreDestroyTransactionCtx",{t}));response.set_value({ranked()});assert(call("sceNpScorePollAsync",{t,result})==0x8002a1a3);
    t=call("sceNpScoreCreateTransactionCtx",{ctx});
    assert(!call("sceNpScoreRecordScoreAsync",{t,123,uint64_t(-7ll),0,0,total,0,0}));response.set_value({{29}});assert(wait(t)==0x8002a415);
    assert(call("sceNpScoreGetRankingByRangeAsync",{t,1,1,0,128,0,0,0,0,1,sorted,total,0,0})==0x8002a104);
    assert(call("sceNpScoreGetRankingByRangeAsync",{t,1,1,rows,127,0,0,0,0,1,sorted,total,0,0})==0x8002a1ac);
    online=false;assert(call("sceNpScoreCreateTransactionCtx",{ctx})==0x8002a106);
    assert(!call("sceNpScoreTerm"));assert(call("sceNpScorePollAsync",{t,result})==0x8002a102);
    puts("PASS: score async lifecycle, PPC stack ABI, current/deprecated ranks, cancellation, errors and malformed replies");
#endif
}
