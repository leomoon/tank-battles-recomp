#pragma once
#include <array>
#include <cstdint>
#include <memory>
#include <optional>
#include <string>
#include <vector>

namespace tb::rpcn {
struct PeerAddress {
    std::array<unsigned char,4> ip{};
    uint16_t port=0;
    bool operator==(const PeerAddress&) const = default;
};
struct PeerEvent { std::string npid; bool active; uint32_t latency_us; };
// Serialized by the signaling adapter. After handoff, sysNet owns the socket
// and supplies incoming control packets through filter().
class PeerUdp {
public:
    PeerUdp(std::string npid, uint64_t user, std::string server,
            uint16_t discovery_port=3657, std::string bind_ip="0.0.0.0", uint16_t port=3658);
    ~PeerUdp();
    PeerUdp(const PeerUdp&)=delete;
    PeerUdp& operator=(const PeerUdp&)=delete;
    std::optional<uint32_t> activate(const std::string& peer, PeerAddress address);
    void deactivate(const std::string& peer);
    std::vector<PeerEvent> pump();
    PeerAddress mapped() const;
    PeerAddress local() const;
    int64_t take_socket();
    void socket_closed();
    bool usable() const;
    bool filter(const void* packet, uint32_t size, PeerAddress source);
private:
    struct Impl;
    std::unique_ptr<Impl> impl;
};
}
