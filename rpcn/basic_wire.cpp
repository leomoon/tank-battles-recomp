#include "basic.h"
#include <algorithm>
#include <stdexcept>

namespace tb::rpcn {
namespace {
using Bytes=std::vector<unsigned char>;
using View=std::span<const unsigned char>;
void integer(Bytes& out,uint64_t n) {
    while (n>=128) { out.push_back((n&127)|128); n>>=7; }
    out.push_back(n);
}
void number(Bytes& out,unsigned field,uint64_t n) { integer(out,field*8); integer(out,n); }
void data(Bytes& out,unsigned field,View bytes) {
    integer(out,field*8+2); integer(out,bytes.size()); out.insert(out.end(),bytes.begin(),bytes.end());
}
View bytes(const std::string& s) { return {reinterpret_cast<const unsigned char*>(s.data()),s.size()}; }
uint64_t integer(View& input) {
    uint64_t n=0;
    for (unsigned shift=0;shift<64;shift+=7) {
        if (input.empty()) throw std::runtime_error("Truncated invitation integer");
        const auto b=input.front(); input=input.subspan(1);
        if (shift==63 && b>1) throw std::runtime_error("Overflowed invitation integer");
        n|=uint64_t(b&127)<<shift;
        if (!(b&128)) return n;
    }
    throw std::runtime_error("Invalid invitation integer");
}
void text(std::string& out,View input,size_t limit) {
    if (input.size()>limit || std::find(input.begin(),input.end(),0)!=input.end())
        throw std::runtime_error("Invalid invitation text");
    out.assign(input.begin(),input.end());
}
}

std::vector<unsigned char> encode_basic_message(const BasicMessage& msg,const std::vector<std::string>& recipients) {
    if (msg.communication_id.size()!=12 || msg.type!=3 || msg.subtype>1 || msg.id ||
        (msg.features&0x7ff8) || msg.data.empty() || msg.data.size()>1024 ||
        msg.subject.size()>128 || msg.body.size()>512 || recipients.empty() || recipients.size()>16)
        throw std::runtime_error("Unsupported invitation fields");
    Bytes message;
    data(message,1,bytes(msg.communication_id)); number(message,2,msg.id);
    for (auto [field,value]:{std::pair{3,unsigned(msg.type)},std::pair{4,unsigned(msg.subtype)}}) {
        Bytes wrapped; number(wrapped,1,value); data(message,field,wrapped);
    }
    number(message,5,msg.features); data(message,6,bytes(msg.subject));
    data(message,7,bytes(msg.body)); data(message,8,msg.data);
    Bytes request; data(request,1,message);
    for (const auto& npid:recipients) {
        if (npid.size()<3 || npid.size()>16 || npid.find_first_not_of(
            "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")!=std::string::npos)
            throw std::runtime_error("Invalid invitation recipient");
        data(request,2,bytes(npid));
    }
    Bytes framed;
    for (unsigned i=0;i<4;++i) framed.push_back(request.size()>>(8*i));
    framed.insert(framed.end(),request.begin(),request.end());
    return framed;
}

BasicMessage decode_basic_notification(View input) {
    BasicMessage msg;
    const auto end=std::find(input.begin(),input.end(),0);
    if (end==input.end()) throw std::runtime_error("Missing invitation sender");
    const auto name_size=static_cast<size_t>(end-input.begin());
    text(msg.sender,input.first(name_size),16); input=input.subspan(name_size+1);
    if (msg.sender.size()<3 || input.size()<4) throw std::runtime_error("Invalid invitation envelope");
    uint32_t size=0;
    for (unsigned i=0;i<4;++i) size|=uint32_t(input[i])<<(8*i);
    input=input.subspan(4);
    if (size!=input.size() || size>4096) throw std::runtime_error("Invalid invitation size");
    msg.type=0xffff;
    unsigned fields=0;
    while (!input.empty()) {
        if (++fields>32) throw std::runtime_error("Invitation field limit exceeded");
        const auto key=integer(input),field=key>>3,wire=key&7;
        if (!field) throw std::runtime_error("Invalid invitation field");
        uint64_t value=0; View payload;
        if (wire==0) value=integer(input);
        else if (wire==2) {
            const auto length=integer(input);
            if (length>input.size()) throw std::runtime_error("Truncated invitation field");
            payload=input.first(length); input=input.subspan(length);
        } else throw std::runtime_error("Unsupported invitation wire type");
        if (field==1 && wire==2) text(msg.communication_id,payload,12);
        else if (field==2 && wire==0) msg.id=value;
        else if ((field==3 || field==4) && wire==2) {
            if (payload.empty()) { if (field==3) msg.type=0; else msg.subtype=0; continue; }
            if (integer(payload)!=8) throw std::runtime_error("Invalid invitation type wrapper");
            value=integer(payload);
            if (value>65535 || !payload.empty()) throw std::runtime_error("Invalid invitation type");
            if (field==3) msg.type=value; else msg.subtype=value;
        } else if (field==5 && wire==0) {
            if (value>UINT32_MAX) throw std::runtime_error("Invalid invitation flags");
            msg.features=value;
        } else if (field==6 && wire==2) text(msg.subject,payload,128);
        else if (field==7 && wire==2) text(msg.body,payload,512);
        else if (field==8 && wire==2) {
            if (payload.size()>1024) throw std::runtime_error("Invitation attachment limit exceeded");
            msg.data.assign(payload.begin(),payload.end());
        }
    }
    if (msg.communication_id.size()!=12 || msg.type!=3 || msg.subtype>1 || msg.data.empty())
        throw std::runtime_error("Unsupported invitation message");
    return msg;
}
}
