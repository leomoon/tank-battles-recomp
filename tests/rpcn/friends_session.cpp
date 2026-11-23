// Interactive game-free driver. The Python fixture owns the account and input.
#include "rpcn/friends.h"
#include <chrono>
#include <cstdio>
#include <iostream>
#include <sstream>
#include <thread>

using namespace std::chrono_literals;
using namespace tb::rpcn;

const char* outcome(FriendOutcome code) {
    switch (code) {
    case FriendOutcome::request_sent:return "sent";
    case FriendOutcome::accepted:return "accepted";
    case FriendOutcome::canceled:return "canceled";
    case FriendOutcome::declined:return "declined";
    case FriendOutcome::removed:return "removed";
    case FriendOutcome::already_pending:return "already_pending";
    case FriendOutcome::already_friends:return "already_friends";
    case FriendOutcome::not_found:return "not_found";
    case FriendOutcome::blocked:return "blocked";
    case FriendOutcome::rejected:return "rejected";
    case FriendOutcome::confirmation_required:return "confirmation_required";
    case FriendOutcome::unknown:return "unknown";
    case FriendOutcome::self:return "self";
    case FriendOutcome::invalid_name:return "invalid_name";
    default:return "other";
    }
}

void emit(const char* code) {
    const auto s=relationships();
    std::printf("{\"result\":\"%s\",\"generation\":%llu,\"friends\":[",code,static_cast<unsigned long long>(s.generation));
    for (size_t i=0;i<s.friends.size();++i)
        std::printf("%s\"%s\"",i?",":"",s.friends[i].npid.c_str());
    std::printf("],\"online\":[");
    for (size_t i=0;i<s.friends.size();++i) std::printf("%s%s",i?",":"",s.friends[i].online?"true":"false");
    std::printf("],\"outgoing\":[");
    for (size_t i=0;i<s.outgoing.size();++i) std::printf("%s\"%s\"",i?",":"",s.outgoing[i].c_str());
    std::printf("],\"incoming\":[");
    for (size_t i=0;i<s.incoming.size();++i) std::printf("%s\"%s\"",i?",":"",s.incoming[i].c_str());
    std::printf("]}\n");std::fflush(stdout);
}

int main(int argc,char** argv) {
    if (argc!=2 || !start(argv[1])) return 2;
    auto deadline=std::chrono::steady_clock::now()+6s;
    while (status().state==State::connecting && std::chrono::steady_clock::now()<deadline)
        std::this_thread::sleep_for(5ms);
    if (status().state!=State::online) {stop();return 3;}
    emit("ready");
    FriendsController controller;
    for (std::string line;std::getline(std::cin,line);) {
        std::istringstream input(line);
        std::string action,name;input>>action>>name;
        if (action=="quit") break;
        if (action=="reconnect") {
            deadline=std::chrono::steady_clock::now()+6s;
            while (status().state!=State::offline && std::chrono::steady_clock::now()<deadline)
                std::this_thread::sleep_for(2ms);
            retry_sign_in();
            while (status().state==State::connecting && std::chrono::steady_clock::now()<deadline)
                std::this_thread::sleep_for(2ms);
            if (status().state!=State::online) {stop();return 7;}
            emit("reconnected");continue;
        }
        if (action=="expire-queued") {
            auto barrier=submit(Request::server_list,{});
            const auto generation=relationships().generation;
            std::vector<unsigned char> body(name.begin(),name.end());body.push_back(0);
            auto expiring=submit(Request::add_friend,std::move(body),generation,std::chrono::steady_clock::now()+30ms);
            if (!barrier.valid() || !expiring.valid() || barrier.wait_for(6s)!=std::future_status::ready) return 5;
            barrier.get();
            if (expiring.wait_for(6s)!=std::future_status::ready || !expiring.get().body.empty()) return 6;
            emit("expired");continue;
        }
        if (action=="snapshot") {
            auto barrier=submit(Request::server_list,{});
            if (!barrier.valid() || barrier.wait_for(6s)!=std::future_status::ready) {stop();return 4;}
            barrier.get();emit("snapshot");continue;
        }
        const auto kind=action=="add"?FriendAction::add:action=="accept"?FriendAction::accept:
                        action=="cancel"?FriendAction::cancel:action=="decline"?FriendAction::decline:FriendAction::remove;
        auto result=controller.begin(kind,name,action=="remove-confirmed");
        while (controller.busy()) {
            if (auto completed=controller.poll()) result=*completed;
            else std::this_thread::sleep_for(1ms);
        }
        emit(outcome(result.outcome));
    }
    stop();
}
