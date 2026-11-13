#include "execution_policy_api.h"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <set>
#include <unistd.h>

namespace {
struct Trace {
    const char* directory = getenv("TB_RESOURCE_TRACE");
    std::mutex mutex;
    FILE* events = nullptr;
    FILE* payload = nullptr;
    std::set<uint32_t> media;
    uint64_t bytes = 0;
    unsigned records = 0;
    bool limited = false;
    Trace() {
        if (!directory || !*directory) return;
        char path[4096];
        snprintf(path,sizeof path,"%s/reads.jsonl",directory); events=fopen(path,"w");
        snprintf(path,sizeof path,"%s/reads.bin",directory); payload=fopen(path,"wb");
        snprintf(path,sizeof path,"%s/media-nids.txt",directory);
        if (FILE* f=fopen(path,"r")) {
            unsigned nid; while(fscanf(f,"%x",&nid)==1)media.insert(nid); fclose(f);
        }
    }
    ~Trace(){if(events)fclose(events);if(payload)fclose(payload);}
};
Trace& trace(){static Trace instance;return instance;}
}
extern "C" void tb_resource_read(int host_fd, uint64_t offset, const void* data, uint64_t size, uint32_t ea)
{
    auto& t=trace();if(!t.events || !t.payload || !size)return;
    char proc[64],path[4096];snprintf(proc,sizeof proc,"/proc/self/fd/%d",host_fd);
    auto n=readlink(proc,path,sizeof(path)-1);if(n<=0)return;path[n]=0;
    const char* base=strrchr(path,'/');base=base?base+1:path;
    if(strcmp(base,"assets_images.edat.dec") && strcmp(base,"assets_common.edat.dec") &&
       strcmp(base,"assets_ps3.edat.dec") && strcmp(base,"assets_sound.edat.dec"))return;
    std::lock_guard<std::mutex> lock(t.mutex);
    if(t.records>=65536 || size>(96ull<<20)-t.bytes) {
        if(!t.limited){fprintf(t.events,"{\"trace_limit\":true}\n");fflush(t.events);t.limited=true;}
        return;
    }
    auto saved=fwrite(data,1,size,t.payload);
    fprintf(t.events,"{\"archive\":\"%s\",\"offset\":%llu,\"bytes\":%zu,\"capture_offset\":%llu,\"guest_buffer\":%u}\n",
            base,(unsigned long long)offset,saved,(unsigned long long)t.bytes,ea);
    t.bytes+=saved;++t.records;fflush(t.payload);fflush(t.events);
}
extern "C" void tb_resource_hle(uint32_t nid)
{
    auto& t=trace();if(!t.events || !t.media.count(nid))return;
    std::lock_guard<std::mutex> lock(t.mutex);
    if(t.records++<65536) {fprintf(t.events,"{\"media_nid\":%u}\n",nid);fflush(t.events);}
}

extern "C" void tb_input_sample(uint32_t kind, uint32_t port, uint32_t buttons)
{
    static const bool enabled=getenv("TB_INPUT_TRACE")!=nullptr;
    if(!enabled || kind>1 || port>3)return;
    auto& t=trace();if(!t.events)return;
    std::lock_guard<std::mutex> lock(t.mutex);
    static uint32_t last[2][4];static bool seen[2][4];static unsigned n;
    if(n>=128 || (seen[kind][port] && last[kind][port]==buttons))return;
    seen[kind][port]=true;last[kind][port]=buttons;++n;
    fprintf(t.events,"{\"input_kind\":%u,\"port\":%u,\"buttons\":%u}\n",kind,port,buttons);fflush(t.events);
}
