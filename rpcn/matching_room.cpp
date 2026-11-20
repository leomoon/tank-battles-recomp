#include "matching_data.h"
#include "ppu_recomp.h"

#include <array>
#include <limits>
#include <string_view>

namespace tb::rpcn {
namespace {

using View = std::span<const unsigned char>;

void varint(Bytes& output, uint64_t value) {
    while (value>=128) {
        output.push_back(static_cast<unsigned char>((value&127)|128));
        value >>= 7;
    }
    output.push_back(static_cast<unsigned char>(value));
}

void number(Bytes& output, unsigned field, uint64_t value) {
    if (!value) return;
    varint(output,field*8);
    varint(output,value);
}

void data(Bytes& output, unsigned field, View value) {
    varint(output,field*8+2);
    varint(output,value.size());
    output.insert(output.end(),value.begin(),value.end());
}

void wrapper(Bytes& output, unsigned field, uint64_t value) {
    Bytes inner;
    number(inner,1,value);
    data(output,field,inner);
}

uint64_t varint(View& input) {
    uint64_t result=0;
    for (unsigned shift=0;shift<=63 && !input.empty();shift+=7) {
        const auto byte=input.front();
        input=input.subspan(1);
        if (shift==63 && byte>1) break;
        result|=uint64_t(byte&127)<<shift;
        if (!(byte&128)) return result;
    }
    throw std::runtime_error("Malformed RPCN protobuf integer");
}

struct Field { unsigned id; uint64_t value; View bytes; };

class Fields {
public:
    explicit Fields(View input) {
        while (!input.empty()) {
            if (fields.size()>=1024) throw std::runtime_error("RPCN protobuf field limit exceeded");
            const auto key=varint(input);
            if (key<8 || key>std::numeric_limits<uint32_t>::max())
                throw std::runtime_error("Invalid RPCN protobuf field");
            Field field{static_cast<unsigned>(key>>3),0,{}};
            switch (key&7) {
            case 0:
                field.value=varint(input);
                break;
            case 2: {
                const auto size=varint(input);
                if (size>input.size()) throw std::runtime_error("Truncated RPCN protobuf data");
                field.bytes=input.first(static_cast<size_t>(size));
                input=input.subspan(static_cast<size_t>(size));
                break;
            }
            case 1:
            case 5: {
                const auto size=(key&7)==1?8u:4u;
                if (size>input.size()) throw std::runtime_error("Truncated RPCN protobuf fixed field");
                input=input.subspan(size);
                break;
            }
            default:
                throw std::runtime_error("Invalid RPCN protobuf wire type");
            }
            fields.push_back(field);
        }
    }
    uint64_t value(unsigned id) const {
        const auto it=std::find_if(fields.rbegin(),fields.rend(),[=](const auto& f){return f.id==id;});
        return it==fields.rend()?0:it->value;
    }
    View bytes(unsigned id) const {
        const auto it=std::find_if(fields.rbegin(),fields.rend(),[=](const auto& f){return f.id==id;});
        return it==fields.rend()?View{}:it->bytes;
    }
    uint64_t wrapped(unsigned id) const { return Fields(bytes(id)).value(1); }
    std::vector<View> each(unsigned id, size_t limit=64) const {
        std::vector<View> result;
        for (const auto& f:fields) if (f.id==id) {
            if (result.size()>=limit) throw std::runtime_error("RPCN protobuf repeated field limit exceeded");
            result.push_back(f.bytes);
        }
        return result;
    }
private:
    std::vector<Field> fields;
};

View unpack(View packet) {
    if (packet.size()<4 || read_le(packet.first(4))!=packet.size()-4)
        throw std::runtime_error("Invalid RPCN protobuf packet length");
    return packet.subspan(4);
}

Bytes guest_bytes(uint32_t ptr, uint32_t size, uint32_t limit) {
    if (size>limit || (size && !guest_range(ptr,size)))
        throw std::runtime_error("Invalid Matching2 buffer");
    Bytes bytes(size);
    for (uint32_t i=0;i<size;++i) bytes[i]=vm_read8(ptr+i);
    return bytes;
}

void guest_data(Bytes& dest,unsigned field,uint32_t ptr,uint32_t size,uint32_t limit) {
    if (size) data(dest,field,guest_bytes(ptr,size,limit));
}

void check_array(uint32_t ptr,uint32_t count,uint32_t stride,uint32_t max=64) {
    if (count>max || (count && !guest_range(ptr,uint64_t(count)*stride)))
        throw std::runtime_error("Invalid Matching2 array");
}

Bytes attr(uint32_t at, bool integer) {
    Bytes out;
    wrapper(out,1,vm_read16(at));
    if (integer) number(out,2,vm_read32(at+4));
    else guest_data(out,2,vm_read32(at+4),vm_read32(at+8),1024);
    return out;
}

void attr_array(Bytes& out,unsigned field,uint32_t pointer_count,bool integer=false) {
    const auto ptr=vm_read32(pointer_count), count=vm_read32(pointer_count+4), size=integer?8u:12u;
    check_array(ptr,count,size);
    for (uint32_t i=0;i<count;++i) data(out,field,attr(ptr+size*i,integer));
}

void presence(Bytes& out,unsigned field,uint32_t at) {
    const auto count=vm_read32(at+16);
    if (!count) return;
    Bytes value;
    guest_data(value,1,at,count,16);
    number(value,2,count);
    data(out,field,value);
}

void user_list(Bytes& out,unsigned field,uint32_t pointer_count) {
    const auto ptr=vm_read32(pointer_count), count=vm_read32(pointer_count+4);
    check_array(ptr,count,36);
    for (uint32_t i=0;i<count;++i) {
        auto id=guest_bytes(ptr+36*i,16,16);
        id.erase(std::find(id.begin(),id.end(),0),id.end());
        if (id.empty()) throw std::runtime_error("Empty Matching2 user ID");
        data(out,field,id);
    }
}

Bytes create_request(uint32_t req) {
    Bytes out;
    number(out,1,vm_read32(req));
    number(out,2,vm_read64(req+8));
    number(out,3,vm_read32(req+16));
    number(out,4,vm_read32(req+20));
    attr_array(out,5,req+24);
    attr_array(out,6,req+32,true);
    attr_array(out,7,req+40);
    attr_array(out,8,req+48);
    if (auto ptr=vm_read32(req+56)) guest_data(out,9,ptr,8,8);
    const auto groups=vm_read32(req+60), count=vm_read32(req+64);
    check_array(groups,count,16);
    for (uint32_t i=0;i<count;++i) {
        const auto group=groups+i*16;
        Bytes item;
        number(item,1,vm_read32(group));
        if (vm_read8(group+4)) guest_data(item,2,group+5,8,8);
        number(item,3,vm_read8(group+13)!=0);
        data(out,10,item);
    }
    if (const auto mask=vm_read32(req+68)) {
        if (!guest_range(mask,8)) throw std::runtime_error("Invalid Matching2 slot mask");
        number(out,11,vm_read64(mask));
    }
    user_list(out,12,req+72);
    user_list(out,13,req+80);
    if (auto ptr=vm_read32(req+88)) guest_data(out,14,ptr,8,8);
    attr_array(out,15,req+92);
    wrapper(out,16,vm_read8(req+100));
    if (const auto sig=vm_read32(req+104)) {
        if (!guest_range(sig,8)) throw std::runtime_error("Invalid Matching2 signaling option");
        Bytes item;
        wrapper(item,1,vm_read8(sig));
        wrapper(item,2,vm_read8(sig+1));
        wrapper(item,3,vm_read16(sig+2));
        data(out,17,item);
    }
    return out;
}

Bytes search_request(uint32_t req) {
    Bytes out;
    number(out,1,uint64_t(int64_t(int32_t(vm_read32(req)))));
    number(out,2,vm_read32(req+4));
    number(out,3,vm_read64(req+8));
    for (unsigned i=0;i<4;++i) number(out,4+i,vm_read32(req+16+4*i));
    for (unsigned kind=0;kind<2;++kind) {
        const auto ptr=vm_read32(req+32+kind*8), count=vm_read32(req+36+kind*8);
        const uint32_t stride=kind?16:12;
        check_array(ptr,count,stride);
        for (uint32_t i=0;i<count;++i) {
            Bytes filter;
            wrapper(filter,1,vm_read8(ptr+i*stride));
            data(filter,2,attr(ptr+i*stride+4,kind==0));
            data(out,8+kind,filter);
        }
    }
    const auto ptr=vm_read32(req+48), count=vm_read32(req+52);
    check_array(ptr,count,2);
    for (uint32_t i=0;i<count;++i) wrapper(out,10,vm_read16(ptr+i*2));
    return out;
}

void write_blob(Image& image,uint32_t ptr,View bytes,size_t max) {
    if (bytes.size()>max) throw std::runtime_error("RPCN attribute exceeds guest limit");
    if (!bytes.empty()) {
        auto dest=image.allocate(bytes.size());
        image.copy(dest,bytes);
        image.pointer(ptr,dest);
    }
}

void user_info(Image& image,uint32_t at,const Fields& fields) {
    image.copy(at,fields.bytes(1).first(std::min<size_t>(fields.bytes(1).size(),16)));
    auto out=image.allocate(48+128);
    image.copy(out,fields.bytes(2).first(std::min<size_t>(fields.bytes(2).size(),47)));
    image.copy(out+48,fields.bytes(3).first(std::min<size_t>(fields.bytes(3).size(),127)));
    image.pointer(at+36,out);
    image.pointer(at+40,out+48);
}

void groups(Image& image,uint32_t ptr,const std::vector<View>& fields) {
    if (fields.empty()) return;
    const auto dest=image.allocate(20*fields.size());
    image.pointer(ptr,dest);
    image.write(ptr+4,fields.size(),4);
    for (size_t i=0;i<fields.size();++i) {
        const Fields fields_i(fields[i]);
        const auto at=dest+20*i;
        image.write(at,fields_i.wrapped(1),1);
        image.write(at+1,fields_i.value(2),1);
        const auto label=fields_i.bytes(3);
        image.write(at+2,!label.empty(),1);
        image.copy(at+4,label.first(std::min<size_t>(label.size(),8)));
        image.write(at+12,fields_i.value(4),4);
        image.write(at+16,fields_i.value(5),4);
    }
}

enum class Attribute { binary, integer, member, internal };
void image_attrs(Image& image,uint32_t ptr,const std::vector<View>& fields,Attribute kind) {
    if (fields.empty()) return;
    const bool internal=kind==Attribute::internal || kind==Attribute::member;
    const bool integer=kind==Attribute::integer;
    const uint32_t stride=internal?24:integer?8:12;
    const auto dest=image.allocate(stride*fields.size(),internal?8:4);
    image.pointer(ptr,dest);
    image.write(ptr+4,fields.size(),4);
    for (size_t i=0;i<fields.size();++i) {
        const Fields row(fields[i]);
        const Fields attr_fields=internal?Fields(row.bytes(kind==Attribute::member?2:3)):row;
        uint32_t at=dest+stride*i;
        if (internal) {
            image.write(at,row.value(1),8);
            if (kind==Attribute::internal) image.write(at+8,row.wrapped(2),2);
            at+=kind==Attribute::member?8:12;
        }
        image.write(at,attr_fields.wrapped(1),2);
        if (integer) image.write(at+4,attr_fields.value(2),4);
        else {
            write_blob(image,at+4,attr_fields.bytes(2),1024);
            image.write(at+8,attr_fields.bytes(2).size(),4);
        }
    }
}

uint32_t member(Image& image,const Fields& fields) {
    const auto at=image.allocate(80,8);
    user_info(image,at+4,Fields(fields.bytes(1)));
    image.write(at+48,fields.value(2),8);
    image.write(at+56,fields.value(3),2);
    image.write(at+58,fields.wrapped(4),1);
    if (!fields.bytes(5).empty()) {
        // The room-group pointer references a single group in this member record.
        const Fields group(fields.bytes(5));
        const auto offset=image.allocate(20);
        image.pointer(at+60,offset);
        image.write(offset,group.wrapped(1),1);
        image.write(offset+1,group.value(2),1);
        auto label=group.bytes(3);
        image.write(offset+2,!label.empty(),1);
        image.copy(offset+4,label.first(std::min<size_t>(label.size(),8)));
        image.write(offset+12,group.value(4),4);
        image.write(offset+16,group.value(5),4);
    }
    image.write(at+64,fields.wrapped(6),1);
    image.write(at+68,fields.value(7),4);
    image_attrs(image,at+72,fields.each(8),Attribute::member);
    return at;
}

uint32_t room_internal(RoomResult& result,const Fields& fields,std::string_view npid,uint32_t ptr) {
    auto& image=result.image;
    const auto at=image.allocate(72,8);
    image.pointer(ptr,at);
    result.room=fields.value(4);
    result.owner=fields.wrapped(8);
    result.flags=fields.value(10);
    image.write(at,fields.wrapped(1),2);
    constexpr unsigned sizes[]={4,8,8,8,4};
    constexpr unsigned offsets[]={4,8,16,24,32};
    for (unsigned i=0;i<5;++i) image.write(at+offsets[i],fields.value(i+2),sizes[i]);
    uint32_t previous=at+36;
    for (const auto data:fields.each(7)) {
        const Fields row(data);
        const auto entry=member(image,row);
        image.pointer(previous,entry);
        previous=entry;
        const auto id=static_cast<uint16_t>(row.value(3));
        const auto name=Fields(row.bytes(1)).bytes(1);
        if (std::ranges::equal(name,npid)) {
            image.pointer(at+44,entry);
            result.member=id;
        }
        if (id==result.owner) image.pointer(at+48,entry);
        ++result.count;
    }
    image.write(at+40,result.count,4);
    groups(image,at+52,fields.each(9));
    image.write(at+60,result.flags,4);
    image_attrs(image,at+64,fields.each(11),Attribute::internal);
    return at;
}

void room_external(Image& image,const Fields& fields,uint32_t at) {
    constexpr unsigned offsets[]={4,8,12,14,16,24,32,34,36,38,40};
    constexpr unsigned sizes[]={2,4,2,2,8,8,2,2,2,2,8};
    for (unsigned i=0;i<11;++i) {
        const bool wrapped=i==0 || i==2 || i==3 || (i>=6 && i<=9);
        image.write(at+offsets[i],wrapped?fields.wrapped(i+1):fields.value(i+1),sizes[i]);
    }
    if (!fields.bytes(12).empty()) {
        const auto owner=image.allocate(44);
        image.pointer(at+48,owner);
        user_info(image,owner,Fields(fields.bytes(12)));
    }
    groups(image,at+52,fields.each(13));
    image.write(at+60,fields.value(14),4);
    image_attrs(image,at+64,fields.each(15),Attribute::integer);
    image_attrs(image,at+72,fields.each(16),Attribute::binary);
    image_attrs(image,at+80,fields.each(17),Attribute::binary);
}

void copy_presence(Image& image,uint32_t at,const Fields& fields) {
    const auto size=std::min<uint64_t>({16,fields.value(2),fields.bytes(1).size()});
    image.copy(at,fields.bytes(1).first(static_cast<size_t>(size)));
    image.write(at+16,size,4);
}

}

Bytes frame_protobuf(Bytes communication_id, const Bytes& body) {
    for (unsigned i=0;i<4;++i)
        communication_id.push_back(static_cast<unsigned char>(body.size()>>(i*8)));
    communication_id.insert(communication_id.end(),body.begin(),body.end());
    return communication_id;
}

Bytes room_request(Request command,uint32_t req) {
    size_t size=8;
    switch (command) {
    case Request::create_room: size=108; break;
    case Request::search_room: size=56; break;
    case Request::join_room: size=48; break;
    case Request::leave_room: size=28; break;
    case Request::set_room_data: size=44; break;
    case Request::send_room_message: size=32; break;
    default: break;
    }
    if (!guest_range(req,size)) throw std::runtime_error("Invalid Matching2 request");
    if (command==Request::ping_room_owner) {
        Bytes out;
        const auto room=vm_read64(req);
        for (unsigned i=0;i<8;++i) out.push_back(static_cast<unsigned char>(room>>(i*8)));
        return out;
    }
    if (command==Request::create_room) return create_request(req);
    if (command==Request::search_room) return search_request(req);
    Bytes out;
    number(out,1,vm_read64(req));
    if (command==Request::join_room) {
        if (auto ptr=vm_read32(req+8)) guest_data(out,2,ptr,8,8);
        if (auto ptr=vm_read32(req+12)) guest_data(out,3,ptr,8,8);
        attr_array(out,4,req+16);
        presence(out,5,req+24);
        wrapper(out,6,vm_read8(req+44));
    } else if (command==Request::leave_room) {
        presence(out,2,req+8);
    } else if (command==Request::set_room_data) {
        number(out,2,vm_read32(req+8));
        number(out,3,vm_read32(req+12));
        attr_array(out,4,req+16);
        const auto groups=vm_read32(req+24), group_count=vm_read32(req+28);
        check_array(groups,group_count,3);
        for (uint32_t i=0;i<group_count;++i) {
            Bytes group;
            wrapper(group,1,vm_read8(groups+i*3));
            number(group,2,vm_read8(groups+i*3+1)!=0);
            data(out,5,group);
        }
        if (const auto mask=vm_read32(req+32)) {
            if (!guest_range(mask,8)) throw std::runtime_error("Invalid Matching2 slot mask");
            Bytes packed;
            varint(packed,vm_read64(mask));
            data(out,6,packed);
        }
        const auto ranks=vm_read32(req+36), count=vm_read32(req+40);
        check_array(ranks,count,2);
        for (uint32_t i=0;i<count;++i) wrapper(out,7,vm_read16(ranks+i*2));
    } else if (command==Request::send_room_message) {
        const auto cast=vm_read8(req+8);
        if (cast<1 || cast>3) throw std::runtime_error("Unsupported Matching2 message cast");
        wrapper(out,2,cast);
        if (cast==2) wrapper(out,3,vm_read16(req+12));
        if (cast==3) {
            const auto ptr=vm_read32(req+12), count=vm_read32(req+16);
            check_array(ptr,count,2);
            for (uint32_t i=0;i<count;++i) wrapper(out,3,vm_read16(ptr+i*2));
        }
        guest_data(out,4,vm_read32(req+20),vm_read32(req+24),4096);
        wrapper(out,5,vm_read32(req+28)&0xff);
    }
    return out;
}

RoomResult room_reply(Request command,const Reply& reply,std::string_view npid) {
    if (reply.body.empty()) throw std::runtime_error("Missing RPCN reply status");
    RoomResult result;
    auto& image=result.image;
    const auto body=std::span(reply.body).subspan(1);
    if (command==Request::create_room || command==Request::join_room || command==Request::get_room_data) {
        const auto payload=unpack(body);
        const auto internal=command==Request::get_room_data?payload:Fields(payload).bytes(1);
        if (internal.empty()) throw std::runtime_error("Missing RPCN room data");
        image.allocate(4);
        room_internal(result,Fields(internal),npid,0);
        if (!result.room || !result.member) throw std::runtime_error("Missing RPCN local room member");
    } else if (command==Request::search_room) {
        const Fields fields(unpack(body));
        const auto rooms=fields.each(3);
        image.allocate(16);
        image.write(0,fields.value(1),4);
        image.write(4,fields.value(2),4);
        image.write(8,rooms.size(),4);
        uint32_t previous=12;
        for (const auto room:rooms) {
            const auto offset=image.allocate(88,8);
            room_external(image,Fields(room),offset);
            image.pointer(previous,offset);
            previous=offset;
        }
        result.count=static_cast<uint16_t>(rooms.size());
    } else if (command==Request::ping_room_owner) {
        const Fields fields(unpack(body));
        image.allocate(40,8);
        image.write(0,fields.wrapped(1),2);
        image.write(4,fields.value(2),4);
        result.room=fields.value(3);
        image.write(8,result.room,8);
        image.write(16,fields.value(4),4);
    } else if (command==Request::leave_room) {
        if (body.size()!=8) throw std::runtime_error("Invalid RPCN leave-room reply");
        image.allocate(8,8);
        image.write(0,read_le(body),8);
        result.room=read_le(body);
    }
    return result;
}

RoomNotice room_notice(const Notification& notification,std::string_view npid) {
    RoomNotice output{};
    auto& result=output.result;
    auto& image=result.image;
    const auto body=std::span(notification.body);
    switch (notification.command) {
    case 0: {
        const Fields joined(unpack(body));
        output.room=joined.value(1);
        const Fields update(joined.bytes(2)), row(update.bytes(1));
        output.event=0x1101;
        result.member=row.value(3);
        image.allocate(28);
        image.pointer(0,member(image,row));
        image.write(4,update.wrapped(2),1);
        copy_presence(image,8,Fields(update.bytes(3)));
        break;
    }
    case 1: {
        if (body.size()<8) throw std::runtime_error("Incomplete RPCN leave notification");
        output.room=read_le(body.first(8));
        output.event=0x1102;
        const Fields update(unpack(body.subspan(8))), row(update.bytes(1));
        result.member=row.value(3);
        image.allocate(28);
        image.pointer(0,member(image,row));
        image.write(4,update.wrapped(2),1);
        copy_presence(image,8,Fields(update.bytes(3)));
        break;
    }
    case 2: {
        if (body.size()<8) throw std::runtime_error("Incomplete RPCN room closure");
        output.room=read_le(body.first(8));
        output.event=0x1104;
        const Fields update(unpack(body.subspan(8)));
        image.allocate(28);
        image.write(0,update.wrapped(1),1);
        image.write(4,update.value(2),4);
        copy_presence(image,8,Fields(update.bytes(3)));
        break;
    }
    case 3: {
        if (body.size()<8) throw std::runtime_error("Incomplete RPCN room update");
        output.room=read_le(body.first(8));
        output.event=0x1106;
        const Fields update(unpack(body.subspan(8))), room(update.bytes(1));
        image.allocate(36);
        const auto room_at=room_internal(result,room,npid,0);
        for (unsigned i=0;i<2;++i) {
            const auto offset=image.allocate(4);
            image.write(offset,i?update.value(2):room.value(10),4);
            image.pointer(4+i*4,offset);
        }
        for (unsigned i=0;i<2;++i) {
            const auto offset=image.allocate(8,8);
            image.write(offset,i?update.value(3):room.value(5),8);
            image.pointer(12+i*4,offset);
        }
        // Only the changed entries are listed, pointing into the complete new room image.
        for (const auto [array_at,update_at,stride]:{
                std::array<unsigned,3>{52,20,20},std::array<unsigned,3>{64,28,24}}) {
            const auto count=static_cast<uint32_t>(image.read(room_at+array_at+4,4));
            const auto base=static_cast<uint32_t>(image.read(room_at+array_at,4));
            std::vector<uint32_t> selected;
            for (uint32_t i=0;i<count;++i) {
                const auto entry=base+stride*i;
                const auto id=image.read(entry+(array_at==52?0:12),array_at==52?1:2);
                bool changed=false;
                if (array_at==52) {
                    const auto changed_groups=update.bytes(4);
                    changed=std::find(changed_groups.begin(),changed_groups.end(),id)!=changed_groups.end();
                } else {
                    for (const auto attribute:update.each(5))
                        if (Fields(attribute).value(1)==id) changed=true;
                }
                if (changed) selected.push_back(entry);
            }
            if (selected.empty()) continue;
            const auto refs=image.allocate(selected.size()*4);
            image.pointer(update_at,refs);
            image.write(update_at+4,selected.size(),4);
            for (size_t i=0;i<selected.size();++i) image.pointer(refs+4*i,selected[i]);
        }
        break;
    }
    case 9: {
        if (body.size()<10) throw std::runtime_error("Incomplete RPCN room message");
        output.room=read_le(body.first(8));
        result.member=read_le(body.subspan(8,2));
        output.event=0x2102;
        const Fields message(unpack(body.subspan(10)));
        image.allocate(20);
        image.write(0,message.value(1),1);
        image.write(1,message.wrapped(2),1);
        const auto targets=message.each(3);
        if (!targets.empty()) {
            const auto dst=image.allocate(8);
            image.pointer(4,dst);
            if (message.wrapped(2)==2) image.write(dst,Fields(targets.front()).value(1),2);
            else {
                const auto ids=image.allocate(targets.size()*2);
                image.pointer(dst,ids);
                image.write(dst+4,targets.size(),4);
                for (size_t i=0;i<targets.size();++i) image.write(ids+2*i,Fields(targets[i]).value(1),2);
            }
        }
        if (!message.bytes(4).empty()) {
            const auto user=image.allocate(44);
            user_info(image,user,Fields(message.bytes(4)));
            image.pointer(8,user);
        }
        write_blob(image,12,message.bytes(5),4096);
        image.write(16,message.bytes(5).size(),4);
        break;
    }
    default:
        break;
    }
    return output;
}

PeerAddress signaling_address(const Reply& reply) {
    const auto bytes=std::span(reply.body);
    if (bytes.empty() || bytes[0]) throw std::runtime_error("RPCN peer endpoint unavailable");
    const Fields fields(unpack(bytes.subspan(1)));
    const auto ip=fields.bytes(1);
    const auto port=fields.wrapped(2);
    if (ip.size()!=4 || !port || port>65535)
        throw std::runtime_error("RPCN peer endpoint requires a valid IPv4 address and port");
    PeerAddress result;
    std::copy(ip.begin(),ip.end(),result.ip.begin());
    result.port=static_cast<uint16_t>(port);
    if (result.ip==std::array<unsigned char,4>{}) throw std::runtime_error("RPCN peer has not registered its endpoint");
    return result;
}

}
