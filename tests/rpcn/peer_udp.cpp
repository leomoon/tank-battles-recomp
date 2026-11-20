#ifdef _WIN32
#include <winsock2.h>
#else
#include <arpa/inet.h>
#include <sys/socket.h>
#include <unistd.h>
#endif
#include "rpcn/peer_udp.h"
#include <cstring>
#include <cassert>
#include <chrono>
#include <thread>
#include <cstdio>

int main() {
    using namespace tb::rpcn;
    PeerUdp first("PeerAlpha",1,"127.0.0.1",9,"127.0.0.1",0);
    PeerUdp second("PeerBeta",2,"127.0.0.1",9,"127.0.0.1",0);
    first.activate("PeerBeta",second.local());
    second.activate("PeerAlpha",first.local());
    unsigned a=0,b=0;
    const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(2);
    while (std::chrono::steady_clock::now()<deadline && (!a || !b)) {
        for (const auto& event:first.pump()) {assert(event.active && event.npid=="PeerBeta");++a;}
        for (const auto& event:second.pump()) {assert(event.active && event.npid=="PeerAlpha");++b;}
        std::this_thread::sleep_for(std::chrono::milliseconds(2));
    }
    assert(a==1 && b==1);
    for (unsigned i=0;i<200;++i) {
        assert(first.pump().empty());assert(second.pump().empty());
        std::this_thread::sleep_for(std::chrono::milliseconds(2));
    }
    second.deactivate("PeerAlpha");
    first.deactivate("PeerBeta");
    first.activate("PeerBeta",second.local());
    // Sending alone cannot establish a peer whose activation was removed.
    for (unsigned i=0;i<150;++i) {
        assert(first.pump().empty());assert(second.pump().empty());
        std::this_thread::sleep_for(std::chrono::milliseconds(2));
    }
    unsigned char forged[45]{};
    std::memcpy(forged,"TBP1",4);forged[4]=2;
    std::memcpy(forged+5,"PeerBeta",8);std::memcpy(forged+21,"PeerAlpha",9);
    assert(first.filter(forged,sizeof(forged),second.local()));
    assert(first.pump().empty()); // Wrong nonce cannot establish the peer.
    assert(!first.filter(forged,4,second.local()));
    // Re-activation replaces old challenges, then the same bound sockets
    // carry guest bytes after ownership transfers to the network layer.
    first.activate("PeerBeta",second.local());
    second.activate("PeerAlpha",first.local());
    a=b=0;
    const auto retry=std::chrono::steady_clock::now()+std::chrono::seconds(2);
    while (std::chrono::steady_clock::now()<retry && (!a || !b)) {
        for (const auto& e:first.pump()) {assert(e.active);++a;}
        for (const auto& e:second.pump()) {assert(e.active);++b;}
        std::this_thread::sleep_for(std::chrono::milliseconds(2));
    }
    assert(a==1 && b==1);
#ifdef _WIN32
    using Socket=SOCKET;
#else
    using Socket=int;
#endif
    const Socket left=static_cast<Socket>(first.take_socket());
    const Socket right=static_cast<Socket>(second.take_socket());
    assert(first.take_socket()==-1 && second.take_socket()==-1);
    sockaddr_in target{};target.sin_family=AF_INET;
    const auto local=first.local();
    std::memcpy(&target.sin_addr,local.ip.data(),4);target.sin_port=htons(local.port);
    const char payload[]="guest UDP movement packet";
    assert(sendto(right,payload,sizeof(payload),0,reinterpret_cast<sockaddr*>(&target),sizeof(target))==sizeof(payload));
    bool delivered=false;
    for (unsigned i=0;i<500 && !delivered;++i) {
        char packet[2048];sockaddr_in from{};
#ifdef _WIN32
        int size=sizeof(from);
#else
        socklen_t size=sizeof(from);
#endif
        const int n=recvfrom(left,packet,sizeof(packet),0,reinterpret_cast<sockaddr*>(&from),&size);
        if (n>=0) {
            tb::rpcn::PeerAddress source;std::memcpy(source.ip.data(),&from.sin_addr,4);source.port=ntohs(from.sin_port);
            if (!first.filter(packet,n,source)) {
                assert(n==sizeof(payload) && !std::memcmp(payload,packet,sizeof(payload)));
                delivered=true;
            }
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(2));
    }
    assert(delivered);
    first.socket_closed();second.socket_closed();
#ifdef _WIN32
    closesocket(left);closesocket(right);
#else
    close(left);close(right);
#endif
    puts("PASS: mutual UDP challenge/ack, endpoint identity, bounded polling, no fabricated activation");
}
