// RPCN protocol 34 score messages, from np2_structs.proto. No generated game data.
#include "score.h"
#include <algorithm>
#include <stdexcept>

namespace tb::rpcn::score_wire {
using View=std::span<const unsigned char>;
namespace {
uint64_t integer(View& in) {
    uint64_t n=0;
    for(unsigned shift=0;shift<64;shift+=7) {
        if(in.empty())throw std::runtime_error("Truncated score integer");
        auto b=in.front();in=in.subspan(1);
        if(shift==63 && b>1)throw std::runtime_error("Score integer overflow");
        n|=uint64_t(b&127)<<shift;
        if(!(b&128))return n;
    }
    throw std::runtime_error("Invalid score integer");
}
struct Field {unsigned id,type;uint64_t value=0;View bytes;};
Field field(View& in) {
    auto tag=integer(in);if(!tag || tag>>3>0x1fffffff)throw std::runtime_error("Invalid score field");
    Field f{unsigned(tag>>3),unsigned(tag&7),0,{}};
    if(f.type==0)f.value=integer(in);
    else {
        uint64_t size=f.type==2?integer(in):f.type==1?8:f.type==5?4:UINT64_MAX;
        if(size>in.size())throw std::runtime_error("Truncated score field");
        f.bytes=in.first(size);in=in.subspan(size);
    }
    return f;
}
std::string text(View in,size_t max) {
    if(in.size()>max || std::find(in.begin(),in.end(),0)!=in.end())throw std::runtime_error("Invalid score text");
    return {in.begin(),in.end()};
}
Rank rank(View in) {
    Rank r;
    while(!in.empty()) {
        auto f=field(in);
        if(f.id==1 && f.type==2)r.npid=text(f.bytes,16);
        else if(f.id==2 && f.type==2)r.name=text(f.bytes,48);
        else if(f.type==0)switch(f.id) {
            case 3:r.pc=int32_t(f.value);break;case 4:r.rank=uint32_t(f.value);break;
            case 5:r.score=int64_t(f.value);break;case 6:r.has_data=f.value!=0;break;case 7:r.date=f.value;break;
        }
    }
    if(r.date && (r.npid.empty() || r.name.empty() || !r.rank))throw std::runtime_error("Invalid score rank");
    return r;
}
}
static void put_integer(Bytes& out,uint64_t value) {
    while(value>=128) {
        out.push_back((value&127)|128);
        value>>=7;
    }
    out.push_back(value);
}
void number(Bytes& out,unsigned tag,uint64_t n) {
    put_integer(out,uint64_t(tag)*8);
    put_integer(out,n);
}
void data(Bytes& out,unsigned tag,View bytes) {
    put_integer(out,uint64_t(tag)*8+2);
    put_integer(out,bytes.size());
    out.insert(out.end(),bytes.begin(),bytes.end());
}
void append_packet(Bytes& out,View bytes) {
    for(unsigned i=0;i<4;++i)out.push_back(bytes.size()>>(8*i));
    out.insert(out.end(),bytes.begin(),bytes.end());
}
View packet_data(View packet) {
    if(packet.size()<4)throw std::runtime_error("Missing score packet length");
    uint32_t size=0;
    for(unsigned i=0;i<4;++i)size|=uint32_t(packet[i])<<(8*i);
    if(size!=packet.size()-4)throw std::runtime_error("Invalid score packet length");
    return packet.subspan(4);
}
Bytes frame(const Bytes& communication,const Bytes& protobuf) {
    if(communication.size()!=12 || protobuf.size()>8*1024*1024)throw std::runtime_error("Invalid score request");
    Bytes out=communication;
    append_packet(out,protobuf);
    return out;
}
Ranking ranking(View packet) {
    if(packet.size()>1024*1024)throw std::runtime_error("Score packet too large");
    auto in=packet_data(packet);
    Ranking r;
    while(!in.empty()) {
        auto f=field(in);
        if(f.id==1 && f.type==2)r.ranks.push_back(rank(f.bytes));
        else if(f.id==2 && f.type==2)r.comments.push_back(text(f.bytes,63));
        else if(f.id==3 && f.type==2) {
            Bytes info;auto nested=f.bytes;
            while(!nested.empty()){auto v=field(nested);if(v.id==1 && v.type==2){if(v.bytes.size()>1024)throw std::runtime_error("Score info too large");info.assign(v.bytes.begin(),v.bytes.end());}}
            r.infos.push_back(std::move(info));
        } else if(f.id==4 && f.type==0)r.sorted=f.value;
        else if(f.id==5 && f.type==0)r.total=uint32_t(f.value);
        if(r.ranks.size()>101 || r.comments.size()>101 || r.infos.size()>101)throw std::runtime_error("Score response too large");
    }
    return r;
}
}
