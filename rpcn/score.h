#pragma once
#include "client.h"
#include <span>

namespace tb::rpcn {
void register_score();
void reset_score();
namespace score_wire {
using Bytes=std::vector<unsigned char>;
struct Rank {
    std::string npid, name;
    int32_t pc=0;
    uint32_t rank=0;
    int64_t score=0;
    bool has_data=false;
    uint64_t date=0;
};
struct Ranking {
    std::vector<Rank> ranks;
    std::vector<std::string> comments;
    std::vector<Bytes> infos;
    uint64_t sorted=0;
    uint32_t total=0;
};
void number(Bytes&,unsigned,uint64_t);
void data(Bytes&,unsigned,std::span<const unsigned char>);
void append_packet(Bytes&,std::span<const unsigned char>);
std::span<const unsigned char> packet_data(std::span<const unsigned char>);
Bytes frame(const Bytes& communication,const Bytes& protobuf);
Ranking ranking(std::span<const unsigned char> packet);
}
}
