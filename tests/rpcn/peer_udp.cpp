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

namespace {
using namespace tb::rpcn;
#ifdef _WIN32
using Socket=SOCKET;
#else
using Socket=int;
#endif

std::array<unsigned char,45> read_probe(Socket socket) {
    using namespace std::chrono_literals;
    std::array<unsigned char,45> packet{};
    const auto deadline=std::chrono::steady_clock::now()+1s;
    while (std::chrono::steady_clock::now()<deadline) {
        const int read=recvfrom(socket,reinterpret_cast<char*>(packet.data()),
                               static_cast<int>(packet.size()),0,nullptr,nullptr);
        if (read==static_cast<int>(packet.size())) return packet;
        std::this_thread::sleep_for(1ms);
    }
    assert(false && "Expected a reciprocal signaling packet");
    return {};
}

void reordered_handshake() {
    PeerUdp host("PeerAlpha",1,"127.0.0.1",9,"127.0.0.1",0);
    PeerUdp remote("PeerBeta",2,"127.0.0.1",9,"127.0.0.1",0);
    const Socket socket=static_cast<Socket>(remote.take_socket());
    host.activate("PeerBeta",remote.local());
    assert(host.pump().empty());
    const auto first_hello=read_probe(socket);
    assert(first_hello[4]==1);

    auto reply=first_hello;
    reply[4]=2;
    std::memcpy(reply.data()+5,first_hello.data()+21,16);
    std::memcpy(reply.data()+21,first_hello.data()+5,16);
    assert(host.filter(reply.data(),reply.size(),remote.local()));
    // A delayed or reordered HELLO must precede activation, even with a
    // genuine ACK. The game can leave the lobby immediately on activation.
    assert(!host.activate("PeerBeta",remote.local()).has_value());
    reply[4]=1;
    reply.back()^=1;
    assert(host.filter(reply.data(),reply.size(),remote.local()));
    const auto renewed_hello=read_probe(socket);
    const auto returned_ack=read_probe(socket);
    assert(renewed_hello==first_hello && returned_ack[4]==2);
    assert(returned_ack.back()==reply.back());
    const auto events=host.pump();
    assert(events.size()==1 && events[0].active && events[0].npid=="PeerBeta");
    remote.socket_closed();
#ifdef _WIN32
    closesocket(socket);
#else
    close(socket);
#endif
}
}

int main() {
    reordered_handshake();
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
    puts("PASS: mutual UDP challenge/ack, reordered activation, endpoint identity and socket handoff");
}
