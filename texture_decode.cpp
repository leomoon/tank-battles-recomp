#include "texture_decode.h"
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <mutex>
#include <array>
#include <filesystem>
#include <string>

namespace {
uint64_t now() {
    return std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now().time_since_epoch()).count();
}
struct DecodeProfile {
    struct Row { u32 format,w,h,pitch,dst_pitch; int swizzled,argb; uint64_t calls=0,pixels=0,bytes=0,ns=0; };
    const char* path=std::getenv("TB_TEXTURE_DECODE_PROFILE");
    std::mutex mutex;
    std::array<Row,128> rows{};
    unsigned count=0,reports=0;
    uint64_t last=0;
    FILE* file=nullptr;
    void add(const rsx_tex_layout& tl,u32 w,u32 h,u32 pitch,int argb,uint64_t ns) {
        std::lock_guard<std::mutex> lock(mutex);
        unsigned i=0;
        for(;i<count;i++) {const auto& r=rows[i];if(r.format==tl.base_format && r.w==w && r.h==h && r.pitch==tl.row_bytes && r.dst_pitch==pitch && r.swizzled==tl.swizzled && r.argb==argb)break;}
        if(i==count) {if(count==rows.size())return;rows[count++]={tl.base_format,w,h,tl.row_bytes,pitch,tl.swizzled,argb};}
        auto& r=rows[i];r.calls++;r.pixels+=uint64_t(w)*h;r.bytes+=uint64_t(tl.dst_row_bytes)*tl.rows;r.ns+=ns;
        const uint64_t t=now();
        if(t-last>=1000000000ull && reports<600) {last=t;flush();}
    }
    void flush() {
        if(!file) {file=std::fopen(path,"w");if(!file)return;
            std::error_code error;std::filesystem::permissions(path,std::filesystem::perms::owner_read|std::filesystem::perms::owner_write|std::filesystem::perms::group_read|std::filesystem::perms::others_read,std::filesystem::perm_options::replace,error);}
        std::fprintf(file,"{\"report\":%u,\"monotonic_ns\":%llu,\"rows\":[",reports++,(unsigned long long)now());
        for(unsigned i=0;i<count;i++) {const auto& r=rows[i];
            std::fprintf(file,"%s{\"format\":%u,\"width\":%u,\"height\":%u,\"source_pitch\":%u,\"destination_pitch\":%u,\"swizzled\":%d,\"argb\":%d,\"calls\":%llu,\"pixels\":%llu,\"bytes\":%llu,\"ns\":%llu}",i?",":"",r.format,r.w,r.h,r.pitch,r.dst_pitch,r.swizzled,r.argb,(unsigned long long)r.calls,(unsigned long long)r.pixels,(unsigned long long)r.bytes,(unsigned long long)r.ns);}
        std::fprintf(file,"]}\n");std::fflush(file);
    }
    ~DecodeProfile(){if(file){if(reports<600)flush();std::fclose(file);}}
};
// A bounded diagnostic registry lives through all worker shutdown. Periodic
// snapshots are flushed; no static destructor races a late renderer worker.
DecodeProfile& profile(){static auto* value=new DecodeProfile;return *value;}
}
extern "C" void rsx_texture_decode(void* dst,u32 pitch,const u8* src,u32 w,u32 h,const rsx_tex_layout* tl,int argb) {
    auto& p=profile();const bool record=p.path && *p.path && tl && dst && src && w && h;
    const auto start=record?now():0;
    static const bool specialized=[] {const char* mode=std::getenv("TB_TEXTURE_DECODE_MODE");return !mode || std::string(mode)=="specialized";}();
    if(!specialized || !tb_texture_decode_specialized(dst,pitch,src,w,h,tl,argb))
        tb_texture_decode_reference(dst,pitch,src,w,h,tl,argb);
    if(record)p.add(*tl,w,h,pitch,argb,now()-start);
}
