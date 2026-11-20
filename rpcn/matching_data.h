#pragma once

#include "client.h"
#include "peer_udp.h"
#include <algorithm>
#include <span>
#include <stdexcept>

namespace tb::rpcn {

using Bytes = std::vector<unsigned char>;

struct Image {
    Bytes bytes;
    std::vector<uint32_t> pointers;

    uint32_t allocate(size_t size, size_t alignment=4) {
        const auto offset=(bytes.size()+alignment-1)&~(alignment-1);
        if (size>65536 || offset+size>65536) throw std::runtime_error("Matching2 event too large");
        bytes.resize(offset+size);
        return static_cast<uint32_t>(offset);
    }

    void copy(uint32_t offset, std::span<const unsigned char> data) {
        std::copy(data.begin(),data.end(),bytes.begin()+offset);
    }

    void write(uint32_t offset, uint64_t value, unsigned size) {
        for (unsigned i=0; i<size; ++i)
            bytes[offset+i]=static_cast<unsigned char>(value >> (8*(size-i-1)));
    }

    uint64_t read(uint32_t offset,unsigned size) const {
        uint64_t result=0;
        for (unsigned i=0;i<size;++i) result=(result<<8)|bytes.at(offset+i);
        return result;
    }

    void pointer(uint32_t at, uint32_t to) {
        write(at,to,4);
        pointers.push_back(at);
    }
};

bool guest_range(uint64_t address, uint64_t size);
uint64_t read_le(std::span<const unsigned char> bytes);

struct RoomResult {
    Image image;
    uint64_t room=0;
    uint16_t owner=0;
    uint16_t member=0;
    uint16_t count=0;
};

Bytes room_request(Request command, uint32_t guest_request);
Bytes frame_protobuf(Bytes communication_id, const Bytes& body);
RoomResult room_reply(Request command, const Reply& reply, std::string_view npid);

struct RoomNotice {
    uint64_t room;
    uint16_t event;
    RoomResult result;
};

RoomNotice room_notice(const Notification& notification, std::string_view npid);
PeerAddress signaling_address(const Reply& reply);

}
