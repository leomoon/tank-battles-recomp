// Game-free driver for scripted TLS relationship fixtures.
#include "rpcn/client.h"
#include <chrono>
#include <cstdio>
#include <thread>

using namespace std::chrono_literals;

void snapshot(unsigned phase) {
    const auto s=tb::rpcn::relationships();
    std::printf("[relationships] {\"phase\":%u,\"endpoint\":\"%s\",\"npid\":\"%s\","
                "\"generation\":%llu,\"revision\":%llu,\"friends\":[",phase,s.endpoint.c_str(),s.npid.c_str(),
                (unsigned long long)s.generation,(unsigned long long)s.revision);
    for (size_t i=0;i<s.friends.size();++i)
        std::printf("%s[\"%s\",%s,%llu]",i?",":"",s.friends[i].npid.c_str(),s.friends[i].online?"true":"false",
                    (unsigned long long)s.friends[i].timestamp);
    std::printf("],\"outgoing\":[");
    for (size_t i=0;i<s.outgoing.size();++i) std::printf("%s\"%s\"",i?",":"",s.outgoing[i].c_str());
    std::printf("],\"incoming\":[");
    for (size_t i=0;i<s.incoming.size();++i) std::printf("%s\"%s\"",i?",":"",s.incoming[i].c_str());
    const auto room=tb::rpcn::take_notifications(),basic=tb::rpcn::take_basic_notifications();
    std::printf("],\"room_notices\":%zu,\"invite_notices\":%zu}\n",room.size(),basic.size());
    std::fflush(stdout);
}

int main(int argc,char** argv) {
    if (argc<3) return 2;
    for (int phase=2;phase<argc;++phase) {
        if (!tb::rpcn::start(argv[phase])) return 3;
        const auto deadline=std::chrono::steady_clock::now()+6s;
        while (tb::rpcn::status().state==tb::rpcn::State::connecting &&
               std::chrono::steady_clock::now()<deadline) std::this_thread::sleep_for(5ms);
        for (unsigned stage=0;stage<32 && tb::rpcn::status().state==tb::rpcn::State::online;++stage) {
            // Server-list command is a barrier: notifications preceding its
            // reply have been applied before the future becomes ready.
            auto future=tb::rpcn::submit(tb::rpcn::Request::server_list,{});
            if (!future.valid() || future.wait_for(6s)!=std::future_status::ready) return 4;
            const auto reply=future.get();
            if (reply.body.empty()) break;
            snapshot(phase);
            if (reply.body.size()<2 || !reply.body[1]) break;
        }
        tb::rpcn::stop();
        snapshot(phase);
    }
}
