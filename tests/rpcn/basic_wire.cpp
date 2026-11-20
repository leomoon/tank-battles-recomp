#include "rpcn/basic.h"
#include <cassert>
#include <cstdio>
#include <stdexcept>
using Bytes=std::vector<unsigned char>;
int main() {
    tb::rpcn::BasicMessage original;
    original.communication_id="NPWR00718_00";original.features=1;
    original.data=Bytes(24);original.data[7]=56;
    const auto request=tb::rpcn::encode_basic_message(original,{"PlayerB"});
    // SendMessageRequest.message is a length-delimited protobuf field.
    assert(request[4]==10 && request[5]<128);
    Bytes notification={'P','l','a','y','e','r','A',0};
    const unsigned size=request[5];
    for (unsigned i=0;i<4;++i) notification.push_back(size>>(8*i));
    notification.insert(notification.end(),request.begin()+6,request.begin()+6+size);
    const auto decoded=tb::rpcn::decode_basic_notification(notification);
    assert(decoded.sender=="PlayerA" && decoded.communication_id==original.communication_id);
    assert(decoded.type==3 && decoded.subtype==0 && decoded.features==1 && decoded.data==original.data);
    // Every truncation must reject the notification, including inside its attachment.
    for (size_t i=0;i<notification.size();++i) {
        bool rejected=false;
        try { tb::rpcn::decode_basic_notification(std::span(notification).first(i)); }
        catch (const std::runtime_error&) { rejected=true; }
        assert(rejected);
    }
    auto invalid=original;invalid.data.resize(1025);
    bool rejected=false;
    try {tb::rpcn::encode_basic_message(invalid,{"PlayerB"});}
    catch(const std::runtime_error&) {rejected=true;}
    assert(rejected);
    puts("PASS: RPCN invitation fields, exact attachment and malformed/truncated payloads");
}
