#include "peer_udp.h"
#ifdef _WIN32
#include <winsock2.h>
#include <ws2tcpip.h>
#else
#include <arpa/inet.h>
#include <fcntl.h>
#include <netdb.h>
#include <sys/socket.h>
#include <unistd.h>
#endif
#include <algorithm>
#include <chrono>
#include <cstring>
#include <cstdio>
#include <map>
#include <random>
#include <span>
#include <stdexcept>

namespace tb::rpcn {
namespace {
using Clock=std::chrono::steady_clock;
using namespace std::chrono_literals;
#ifdef _WIN32
using Socket=SOCKET;
constexpr Socket invalid=INVALID_SOCKET;
void close_socket(Socket fd) { closesocket(fd); }
#else
using Socket=int;
constexpr Socket invalid=-1;
void close_socket(Socket fd) { close(fd); }
#endif
sockaddr_in address(PeerAddress peer) {
    sockaddr_in result{};
    result.sin_family=AF_INET;
    std::memcpy(&result.sin_addr,peer.ip.data(),4);
    result.sin_port=htons(peer.port);
    return result;
}
PeerAddress endpoint(const sockaddr_in& source) {
    PeerAddress result;
    std::memcpy(result.ip.data(),&source.sin_addr,4);
    result.port=ntohs(source.sin_port);
    return result;
}
uint64_t nonce() {
    std::random_device random;
    return (uint64_t(random())<<32 | random()) | 1;
}
}
struct PeerUdp::Impl {
    Socket fd=invalid;
#ifdef _WIN32
    bool winsock=false;
#endif
    bool owned=true, handed=false;
    std::vector<PeerEvent> events;
    std::string npid;
    uint64_t user;
    PeerAddress server, local, external;
    Clock::time_point probe{};
    struct Peer {
        PeerAddress address;
        uint64_t challenge=nonce();
        Clock::time_point begin=Clock::now(), sent{}, heard=Clock::now();
        bool active=false, failed=false;
        uint32_t latency_us=0;
    };
    std::map<std::string,Peer> peers;
    ~Impl() {
        if (fd!=invalid && owned) close_socket(fd);
#ifdef _WIN32
        if (winsock) WSACleanup();
#endif
    }
    void send(const void* bytes,size_t size,PeerAddress to) {
        const auto dest=address(to);
        sendto(fd,static_cast<const char*>(bytes),static_cast<int>(size),0,
               reinterpret_cast<const sockaddr*>(&dest),sizeof(dest));
    }
    void control(const std::string& peer,PeerAddress to,unsigned kind,uint64_t challenge) {
        std::array<unsigned char,45> packet{};
        std::memcpy(packet.data(),"TBP1",4);
        packet[4]=static_cast<unsigned char>(kind);
        std::memcpy(packet.data()+5,npid.data(),npid.size());
        std::memcpy(packet.data()+21,peer.data(),peer.size());
        for (unsigned i=0;i<8;++i) packet[37+i]=challenge>>(56-i*8);
        send(packet.data(),packet.size(),to);
    }
    bool consume(const void* data,uint32_t received,PeerAddress source) {
        const auto now=Clock::now();
        const std::span<const unsigned char> packet(static_cast<const unsigned char*>(data),received);
        if (source==server && received==9 && !packet[0] && !packet[1] && !packet[2]) {
            PeerAddress observed;
            std::copy_n(packet.begin()+3,4,observed.ip.begin());
            observed.port=uint16_t(packet[7])<<8 | packet[8];
            if (!(observed==external))
                fprintf(stderr,"[rpcn-peer] mapped=%u.%u.%u.%u:%u\n",observed.ip[0],observed.ip[1],observed.ip[2],observed.ip[3],observed.port);
            external=observed;
            return true;
        }
        if (received!=45 || std::memcmp(packet.data(),"TBP1",4)) return false;
        const std::string sender(reinterpret_cast<const char*>(packet.data()+5),strnlen(reinterpret_cast<const char*>(packet.data()+5),16));
        const std::string target(reinterpret_cast<const char*>(packet.data()+21),strnlen(reinterpret_cast<const char*>(packet.data()+21),16));
        auto found=peers.find(sender);
        if (target!=npid || found==peers.end() || found->second.failed || !(found->second.address==source)) return true;
        auto& peer=found->second;uint64_t challenge=0;
        for (unsigned i=0;i<8;++i) challenge=challenge<<8 | packet[37+i];
        if (packet[4]==1) control(sender,source,2,challenge);
        if (packet[4]!=2 || challenge!=peer.challenge) return true;
        peer.heard=now;
        if (!peer.active) {
            peer.active=true;
            peer.latency_us=static_cast<uint32_t>(std::chrono::duration_cast<std::chrono::microseconds>(now-peer.begin).count());
            events.push_back({sender,true,peer.latency_us});
        }
        return true;
    }

};
PeerUdp::PeerUdp(std::string npid,uint64_t user,std::string host,uint16_t discovery_port,
                 std::string bind_ip,uint16_t port):impl(std::make_unique<Impl>()) {
    if (npid.empty() || npid.size()>16 || !user) throw std::runtime_error("Invalid peer identity");
    impl->npid=std::move(npid);impl->user=user;
#ifdef _WIN32
    WSADATA data{};
    if (WSAStartup(MAKEWORD(2,2),&data)) throw std::runtime_error("Peer Winsock initialization failed");
    impl->winsock=true;
#endif
    addrinfo hints{};hints.ai_family=AF_INET;hints.ai_socktype=SOCK_DGRAM;
    addrinfo* resolved=nullptr;
    if (getaddrinfo(host.c_str(),nullptr,&hints,&resolved)) throw std::runtime_error("Cannot resolve RPCN UDP host");
    impl->server=endpoint(*reinterpret_cast<sockaddr_in*>(resolved->ai_addr));
    impl->server.port=discovery_port;
    freeaddrinfo(resolved);
    // A temporary connected socket determines the actual route's local address.
    Socket route=socket(AF_INET,SOCK_DGRAM,0);
    if (route==invalid) throw std::runtime_error("Cannot create UDP route probe");
    auto remote=address(impl->server);sockaddr_in route_address{};
#ifdef _WIN32
    int size=sizeof(route_address);
#else
    socklen_t size=sizeof(route_address);
#endif
    const bool routed=connect(route,reinterpret_cast<sockaddr*>(&remote),sizeof(remote))==0 &&
        getsockname(route,reinterpret_cast<sockaddr*>(&route_address),&size)==0;
    close_socket(route);
    if (!routed) throw std::runtime_error("Cannot determine RPCN UDP route");
    impl->local=endpoint(route_address);
    sockaddr_in bound{};bound.sin_family=AF_INET;bound.sin_port=htons(port);
    if (inet_pton(AF_INET,bind_ip.c_str(),&bound.sin_addr)!=1) throw std::runtime_error("Invalid UDP bind address");
    if (bound.sin_addr.s_addr) impl->local=endpoint(bound);
    impl->fd=socket(AF_INET,SOCK_DGRAM,0);
    if (impl->fd==invalid || bind(impl->fd,reinterpret_cast<sockaddr*>(&bound),sizeof(bound)))
        throw std::runtime_error("Cannot bind peer UDP socket");
    size=sizeof(bound);
    if (getsockname(impl->fd,reinterpret_cast<sockaddr*>(&bound),&size))
        throw std::runtime_error("Cannot read bound peer UDP port");
    impl->local.port=ntohs(bound.sin_port);
#ifdef _WIN32
    u_long nonblocking=1;
    if (ioctlsocket(impl->fd,FIONBIO,&nonblocking)) throw std::runtime_error("Cannot set peer UDP nonblocking");
#else
    if (fcntl(impl->fd,F_SETFL,O_NONBLOCK)==-1) throw std::runtime_error("Cannot set peer UDP nonblocking");
#endif
}
PeerUdp::~PeerUdp()=default;
PeerAddress PeerUdp::mapped() const { return impl->external; }
PeerAddress PeerUdp::local() const { return impl->local; }
int64_t PeerUdp::take_socket() {
    if (impl->fd==invalid || impl->handed) return -1;
    impl->owned=false;impl->handed=true;
    fprintf(stderr,"[rpcn-peer] UDP socket transferred to guest networking, port=%u\n",impl->local.port);
    return static_cast<int64_t>(impl->fd);
}
void PeerUdp::socket_closed() { impl->fd=invalid; }
bool PeerUdp::usable() const { return impl->fd!=invalid; }
bool PeerUdp::filter(const void* packet,uint32_t size,PeerAddress source) {
    return impl->consume(packet,size,source);
}
std::optional<uint32_t> PeerUdp::activate(const std::string& name,PeerAddress target) {
    if (name.empty() || name.size()>16 || name==impl->npid || !target.port ||
        target.ip==std::array<unsigned char,4>{}) throw std::runtime_error("Invalid peer endpoint");
    if (!impl->peers.contains(name) && impl->peers.size()>=16) throw std::runtime_error("Peer connection limit");
    const auto existing=impl->peers.find(name);
    if (existing!=impl->peers.end() && !existing->second.failed && existing->second.address==target)
        return existing->second.active?std::optional<uint32_t>(existing->second.latency_us):std::nullopt;
    std::erase_if(impl->events,[&](const auto& event){return event.npid==name;});
    impl->peers.insert_or_assign(name,Impl::Peer{target});
    return std::nullopt;
}
void PeerUdp::deactivate(const std::string& name) {
    impl->peers.erase(name);
    std::erase_if(impl->events,[&](const auto& event){return event.npid==name;});
}
std::vector<PeerEvent> PeerUdp::pump() {
    const auto now=Clock::now();
    if (impl->fd!=invalid && now-impl->probe>=1s) {
        std::array<unsigned char,13> registration{1};
        for (unsigned i=0;i<8;++i) registration[1+i]=impl->user>>(i*8);
        std::copy(impl->local.ip.begin(),impl->local.ip.end(),registration.begin()+9);
        impl->send(registration.data(),registration.size(),impl->server);impl->probe=now;
    }
    for (auto& [name,peer]:impl->peers) {
        if (peer.failed) continue;
        if (impl->fd==invalid) {impl->events.push_back({name,false,0});peer.failed=true;continue;}
        if ((!peer.active && now-peer.begin>8s) || (peer.active && now-peer.heard>5s)) {
            impl->events.push_back({name,false,0});peer.failed=true;peer.active=false;continue;
        }
        if (now-peer.sent>=250ms) {
            impl->control(name,peer.address,1,peer.challenge);peer.sent=now;
        }
    }
    for (unsigned budget=0;!impl->handed && impl->fd!=invalid && budget<64;++budget) {
        std::array<unsigned char,2048> packet{};sockaddr_in from{};
#ifdef _WIN32
        int size=sizeof(from);
#else
        socklen_t size=sizeof(from);
#endif
        const int received=recvfrom(impl->fd,reinterpret_cast<char*>(packet.data()),packet.size(),0,
                                    reinterpret_cast<sockaddr*>(&from),&size);
        if (received<0) break;
        const auto source=endpoint(from);
        impl->consume(packet.data(),static_cast<uint32_t>(received),source);
    }
    std::vector<PeerEvent> events;
    events.swap(impl->events);
    return events;
}
}
