#include "content_paths.h"
#include "ppu_recomp.h"
#include "ps3emu/nid.h"
#include "cellFs.h"
#include <algorithm>
#include <array>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <string>
extern "C" {
void ps3_hle_call(uint32_t, ppu_context*);
int64_t sys_fs_open(ppu_context*);
int64_t sys_fs_read(ppu_context*);
int64_t sys_fs_lseek(ppu_context*);
int64_t sys_fs_fstat(ppu_context*);
int64_t sys_fs_close(ppu_context*);
const char* cellGame_get_title_id();
int cellfs_translate_path(const char*, char*, size_t);
void sys_fs_translate_path(const char*, char*, int);
extern uint8_t* vm_base;
}
namespace fs = std::filesystem;
static constexpr uint32_t path_ea=0x02000000, key_ea=0x02001000, fd_ea=0x02001100;
static constexpr uint32_t buffer_ea=0x02002000, count_ea=0x02003000, stat_ea=0x02004000;
static int64_t invoke(unsigned layer, unsigned op, ppu_context& c) {
    static const char* names[]={"cellFsOpen","cellFsRead","cellFsLseek","cellFsFstat","cellFsClose"};
    if (!layer) { ps3_hle_call(ps3_compute_nid(names[op]), &c); return static_cast<int64_t>(c.gpr[3]); }
    if (layer==1) {
        static int64_t (*calls[])(ppu_context*)={sys_fs_open,sys_fs_read,sys_fs_lseek,sys_fs_fstat,sys_fs_close};
        return calls[op](&c);
    }
    switch (op) {
    case 0: return cellFsOpen((const char*)(uintptr_t)c.gpr[3], (s32)c.gpr[4],
                              (CellFsFd*)(uintptr_t)c.gpr[5], nullptr, 0);
    case 1: return cellFsRead((CellFsFd)c.gpr[3], (void*)(uintptr_t)c.gpr[4],
                              c.gpr[5], (u64*)(uintptr_t)c.gpr[6]);
    case 2: return cellFsLseek((CellFsFd)c.gpr[3], (s64)c.gpr[4], (s32)c.gpr[5],
                               (u64*)(uintptr_t)c.gpr[6]);
    case 3: return cellFsFstat((CellFsFd)c.gpr[3], (CellFsStat*)(uintptr_t)c.gpr[4]);
    default: return cellFsClose((CellFsFd)c.gpr[3]);
    }
}
static bool verify_file(const std::string& guest, const fs::path& reference, ppu_context& c) {
    if (guest.size()>=1024) return false;
    std::memcpy(vm_base+path_ea,guest.c_str(),guest.size()+1);
    const auto size=fs::file_size(reference);
    for (unsigned layer=0; layer<3; ++layer) {
        c.gpr[3]=path_ea; c.gpr[4]=0; c.gpr[5]=fd_ea; c.gpr[6]=c.gpr[7]=0;
        if (invoke(layer,0,c)!=0) { fprintf(stderr,"[asset-probe] open failed layer=%u path=%s\n",layer,guest.c_str()); return false; }
        const auto fd=vm_read32(fd_ea);
        bool ok=true;
        c.gpr[3]=fd; c.gpr[4]=stat_ea;
        ok = invoke(layer,3,c)==0 && vm_read64(stat_ea+0x24)==size;
        std::ifstream stream(reference,std::ios::binary);
        for (auto offset : std::array<uint64_t,3>{0,size/2,size>64?size-64:0}) {
            const uint64_t wanted=std::min<uint64_t>(64,size-offset);
            c.gpr[3]=fd; c.gpr[4]=offset; c.gpr[5]=0; c.gpr[6]=count_ea;
            ok = (invoke(layer,2,c)==0 && vm_read64(count_ea)==offset) && ok;
            c.gpr[3]=fd; c.gpr[4]=buffer_ea; c.gpr[5]=wanted; c.gpr[6]=count_ea;
            ok = (invoke(layer,1,c)==0 && vm_read64(count_ea)==wanted) && ok;
            char expected[64]{}; stream.clear();stream.seekg(offset);stream.read(expected,wanted);
            ok = !memcmp(expected,vm_base+buffer_ea,wanted) && ok;
        }
        c.gpr[3]=fd;c.gpr[4]=0;c.gpr[5]=2;c.gpr[6]=count_ea;
        ok = (invoke(layer,2,c)==0 && vm_read64(count_ea)==size) && ok;
        c.gpr[3]=fd;c.gpr[4]=buffer_ea;c.gpr[5]=8;c.gpr[6]=count_ea;
        ok = (invoke(layer,1,c)==0 && vm_read64(count_ea)==0) && ok;
        c.gpr[3]=fd;ok = (invoke(layer,4,c)==0) && ok;
        fprintf(stderr,"[asset-probe] %s layer=%u size=%llu path=%s\n",ok?"PASS":"FAIL",layer,
                (unsigned long long)size,guest.c_str());
        if (!ok) return false;
    }
    return true;
}
static bool verify_writable(ppu_context& c) {
    const char* token=getenv("TB_PATH_PROBE_TOKEN");
    if (!token) return true;
    for (const char* p=token;*p;++p)
        if (!( (*p>='a'&&*p<='z') || (*p>='0'&&*p<='9') || *p=='-')) return false;
    // Linux fopen(directory,"rb") succeeds, but PS3 file opens must report EISDIR.
    fs::create_directories(getenv("PS3_HDD1_ROOT"));
    const char directory[]="/dev_hdd1/";
    memcpy(vm_base+path_ea,directory,sizeof directory);
    for(unsigned layer=0;layer<3;++layer) {
        constexpr uint32_t sentinel=0xABCD1234;
        vm_write32(fd_ea,sentinel);
        c.gpr[3]=path_ea;c.gpr[4]=0;c.gpr[5]=fd_ea;c.gpr[6]=c.gpr[7]=0;
        auto status=invoke(layer,0,c);
        bool ok=static_cast<uint32_t>(status)==CELL_EISDIR && vm_read32(fd_ea)==sentinel;
        if(status==0){c.gpr[3]=vm_read32(fd_ea);invoke(layer,4,c);}
        if(!ok){fprintf(stderr,"[asset-probe] directory open failed layer=%u status=0x%08X\n",layer,(uint32_t)status);return false;}
    }
    fprintf(stderr,"[asset-probe] directory opens return EISDIR in all three layers\n");
    const std::string name=std::string("port-path-check-")+token+".bin";
    const std::array<std::pair<std::string,fs::path>,2> paths={{
        {"/dev_hdd1/"+name,fs::path(getenv("PS3_HDD1_ROOT"))/name},
        {"/dev_hdd0/home/00000001/"+name,fs::path(getenv("PS3_HDD0_ROOT"))/"home/00000001"/name}}};
    for (const auto& [guest,host]: paths) {
        if (fs::exists(host)) return false;
        fs::create_directories(host.parent_path());
        std::memcpy(vm_base+path_ea,guest.c_str(),guest.size()+1);
        c.gpr[3]=path_ea;c.gpr[4]=CELL_FS_O_RDWR|CELL_FS_O_CREAT|CELL_FS_O_EXCL;c.gpr[5]=fd_ea;c.gpr[6]=c.gpr[7]=0;
        if (invoke(0,0,c)!=0) return false;
        const auto fd=vm_read32(fd_ea);
        const char payload[]="Original port filesystem test.";
        std::memcpy(vm_base+buffer_ea,payload,sizeof payload);
        c.gpr[3]=fd;c.gpr[4]=buffer_ea;c.gpr[5]=sizeof payload;c.gpr[6]=count_ea;
        ps3_hle_call(ps3_compute_nid("cellFsWrite"),&c);
        bool ok=c.gpr[3]==0 && vm_read64(count_ea)==sizeof payload;
        c.gpr[3]=fd;ok=(invoke(0,4,c)==0)&&ok;
        char generic[1200],raw[1200];cellfs_translate_path(guest.c_str(),generic,sizeof generic);
        sys_fs_translate_path(guest.c_str(),raw,sizeof raw);
        ok=fs::equivalent(host,generic)&&fs::equivalent(host,raw)&&ok;
        if (ok) ok=verify_file(guest,host,c);
        fs::remove(host);
        if (!ok) return false;
    }
    return true;
}
int tank_battles_probe_assets() {
    const char* manifest=getenv("TB_ASSET_PROBE");
    if (!manifest) return 1;
    unsigned char key[16];
    if (fread(key,1,sizeof key,stdin)!=sizeof key) { fprintf(stderr,"[asset-probe] Expected transient key bytes on stdin\n");return 1; }
    memcpy(vm_base+key_ea,key,sizeof key);memset(key,0,sizeof key);
    ppu_context c{};c.thread_id=62;c.gpr[1]=0x03FFF000;
    std::ifstream input(manifest);std::string line;unsigned checked=0;
    while (std::getline(input,line)) {
        const auto tab=line.find('\t');if (tab==std::string::npos)return 1;
        const auto guest=line.substr(0,tab);const fs::path reference=line.substr(tab+1);
        if (guest.size()>=1024) return 1;
        memcpy(vm_base+path_ea,guest.c_str(),guest.size()+1);
        c.gpr[3]=key_ea;c.gpr[4]=path_ea;
        ps3_hle_call(ps3_compute_nid("sceNpDrmIsAvailable"),&c);
        if (c.gpr[3]!=0 || !verify_file(guest,reference,c)) return 1;
        ++checked;
    }
    memset(vm_base+key_ea,0,16);
    if (!checked || !verify_writable(c)) return 1;
    fprintf(stderr,"[asset-probe] ALL PASS files=%u title=%s\n",checked,cellGame_get_title_id());
    return 0;
}
