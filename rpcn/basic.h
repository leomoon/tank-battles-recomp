#pragma once
#include "client.h"
#include <span>

namespace tb::rpcn {
struct BasicMessage {
    std::string communication_id, sender, subject, body;
    uint64_t id=0;
    uint16_t type=3, subtype=0;
    uint32_t features=0;
    std::vector<unsigned char> data;
};
std::vector<unsigned char> encode_basic_message(const BasicMessage&, const std::vector<std::string>& recipients);
BasicMessage decode_basic_notification(std::span<const unsigned char>);
void register_basic();
void pump_basic(bool system_dialog=false,bool room_exited=false);
void reset_basic();
}
