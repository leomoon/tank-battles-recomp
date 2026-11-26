// Title diagnostics and an explicit developer pacing experiment. This object
// belongs to the title executable so
// old runtime bundles and game-free runtime fixtures need no new ABI symbols.
#include "ppu_recomp.h"
#include "host_services.h"
#include "online_pacing_recovery.h"
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

namespace {
struct Event {
    uint64_t ns=0, previous_us=0, guest_us=0, tx=0, rx=0, sequence=0, io_begin_ns=0;
    uint32_t kind=0, object=0, interval_bits=0, correction=0, flags=0, lr=0, result=0, polls=0, socket=0;
    double deadline_guest_us=0, observed_float_us=0;
};
struct Trace {
    std::atomic<bool> active{false}, stop{false};
    std::atomic<uint64_t> dropped{0}, tx{0}, rx{0};
    std::mutex mutex;
    std::vector<Event> events;
    std::thread worker;
    std::string path,trigger,role="unspecified";
    bool clock_only=false;
    uint64_t start=0,end=0;
    Trace() {
        const char* value=getenv("TB_PACING_TRACE");
        if (!value || !*value) return;
        path=value;
        value=getenv("TB_PACING_TRIGGER");
        if (!value || !*value) return; // Explicit gameplay trigger is mandatory.
        trigger=value;
        value=getenv("TB_PACING_CLOCK_ONLY");clock_only=value && std::string(value)=="1";
        value=getenv("TB_PACING_ROLE");
        if(value && (std::string(value)=="host" || std::string(value)=="invited")) role=value;
        double seconds=20;
        if((value=getenv("TB_PACING_SECONDS"))) {
            char* tail=nullptr;double parsed=strtod(value,&tail);
            if(tail && !*tail && parsed>=1 && parsed<=120) seconds=parsed;
        }
        events.reserve(32768);
        worker=std::thread([this,seconds] {
            while(!stop.load(std::memory_order_relaxed)) {
                std::error_code ec;
                if(!start && std::filesystem::remove(std::filesystem::path(std::u8string(trigger.begin(),trigger.end())),ec)) {
                    start=tb_host_now();end=start+uint64_t(seconds*1e9);
                    active.store(true,std::memory_order_release);
                }
                if(start && tb_host_now()>=end) break;
                std::this_thread::sleep_for(std::chrono::milliseconds(10));
            }
            active.store(false,std::memory_order_release);
            if(start) flush();
        });
    }
    void shutdown() {active.store(false,std::memory_order_release);stop.store(true);if(worker.joinable())worker.join();}
    void flush() {
        std::lock_guard guard(mutex);
        auto filename=std::filesystem::path(std::u8string(path.begin(),path.end()));
#ifdef _WIN32
        FILE* file=_wfopen(filename.c_str(),L"wb");
#else
        FILE* file=fopen(filename.c_str(),"wb");
#endif
        if(!file) {fprintf(stderr,"[pacing] cannot write diagnostic trace\n");return;}
        fprintf(file,"{\"schema\":1,\"clock\":\"host_monotonic_ns\",\"role\":\"%s\",\"start_ns\":%llu,\"end_ns\":%llu,\"complete\":%s,\"capacity\":32768,\"dropped\":%llu,\"events\":%zu}\n",role.c_str(),(unsigned long long)start,(unsigned long long)end,tb_host_now()>=end?"true":"false",(unsigned long long)dropped.load(),events.size());
        for(const auto& e:events) {
            char deadline[40],observed[40];
            if(std::isfinite(e.deadline_guest_us))snprintf(deadline,sizeof(deadline),"%.9g",e.deadline_guest_us);else snprintf(deadline,sizeof(deadline),"null");
            if(std::isfinite(e.observed_float_us))snprintf(observed,sizeof(observed),"%.9g",e.observed_float_us);else snprintf(observed,sizeof(observed),"null");
            fprintf(file,"{\"ns\":%llu,\"kind\":%u,\"object\":%u,\"previous_guest_us\":%llu,\"observed_guest_us\":%llu,\"interval_bits\":%u,\"correction_u32\":%u,\"flags\":%u,\"deadline_guest_us\":%s,\"observed_float_us\":%s,\"polls\":%u,\"tx\":%llu,\"rx\":%llu,\"local_sequence\":%llu,\"io_begin_ns\":%llu,\"socket\":%u,\"result_u32\":%u,\"lr\":%u}\n",(unsigned long long)e.ns,e.kind,e.object,(unsigned long long)e.previous_us,(unsigned long long)e.guest_us,e.interval_bits,e.correction,e.flags,deadline,observed,e.polls,(unsigned long long)e.tx,(unsigned long long)e.rx,(unsigned long long)e.sequence,(unsigned long long)e.io_begin_ns,e.socket,e.result,e.lr);
        }
        fclose(file);
    }
    void push(Event e) {
        e.ns=tb_host_now();
        if(e.ns<start || e.ns>end) return;
        e.tx=tx.load(std::memory_order_relaxed);e.rx=rx.load(std::memory_order_relaxed);
        std::unique_lock guard(mutex,std::try_to_lock);
        if(!guard.owns_lock()) {++dropped;return;}
        if(!active.load(std::memory_order_acquire))return;
        if(events.size()==32768) {++dropped;return;}
        events.push_back(e);
    }
};
Trace& trace() {
    // Guest threads can still unwind during exit. Keep storage alive until the
    // OS reclaims it, but stop/join the writer before normal process teardown.
    static Trace* value=[] {auto* p=new Trace;std::atexit([] {trace().shutdown();});return p;}();
    return *value;
}
thread_local uint32_t polls=0;
thread_local Event io[2];
bool valid(uint64_t p) {return vm_base && p>=0x10000 && p<=0xfffffe00ull;}

struct RecoveryExperiment {
    bool enabled=false;
    std::mutex mutex;
    uint32_t queue=0;
    uint64_t role_ids=0;
    bool role_known=false,unsupported=false;
    tb_pacing_recovery::State state;
    RecoveryExperiment() {
        const char* option=getenv("TB_EXPERIMENTAL_ONLINE_PACING");
        enabled=option && std::string(option)=="stale-phase";
        if(enabled)fprintf(stderr,"[online pacing] experimental stale-phase recovery enabled\n");
    }
};
RecoveryExperiment& recovery() {static auto* value=new RecoveryExperiment;return *value;}
}

// Kept separate from the read-only recorder. Exact opt-in is mandatory, and
// lifecycle hooks drop all provenance before the next session can use it.
extern "C" void tb_pacing_experiment(ppu_context* c,unsigned op) {
    auto& experiment=recovery();
    if(!experiment.enabled)return;
    std::lock_guard guard(experiment.mutex);
    if(op==15 || op==11) {
        experiment.queue=0;experiment.state={};experiment.role_known=false;experiment.unsupported=false;
        return;
    }
    if(op==14) {
        const uint64_t ids=(uint64_t(uint32_t(c->gpr[29]))<<32)|uint32_t(c->gpr[3]);
        if(experiment.role_known && ids!=experiment.role_ids) {
            experiment.queue=0;experiment.state={};experiment.unsupported=false;
        }
        experiment.role_ids=ids;experiment.role_known=true;
        return;
    }
    if(experiment.unsupported)return;
    const uint32_t queue=uint32_t(op==8 || op==2?c->gpr[28]:c->gpr[30]);
    if(!valid(queue))return;
    if(!experiment.queue)experiment.queue=queue;
    if(queue!=experiment.queue) {
        experiment.state={};experiment.unsupported=true;return;
    }
    auto& state=experiment.state;
    if(op==7) {
        if(!valid(c->gpr[6]))return;
        state.sample(vm_read32(c->gpr[6]+0xc),uint32_t(c->gpr[5]),vm_read32(queue+0x20));
    } else if(op==8)state.select(vm_read32(queue+0x20),vm_read32(queue+0x24));
    else if(op==2)state.publish(uint32_t(c->gpr[11]),vm_read32(queue+0x24));
    else if(op==12) {
        // This exact title path has already requested recovery for a newer
        // host frame. Unknown role/state or untracked estimates fall back.
        if(c->gpr[3]!=0 || !vm_read8(queue+0x14) || c->gpr[2]<0x12534 ||
           !valid(c->gpr[2]-0x2534))return;
        const uint32_t field=vm_read32(c->gpr[2]-0x2534);
        if(!valid(field) || field<0x10124 || !vm_read8(field-0x124+0x121))return;
        const uint32_t before=vm_read32(field);
        const uint32_t origin=state.field.frame;
        const uint32_t pending=vm_read32(queue+0x20),retained=vm_read32(queue+0x24);
        if(!state.recover(uint32_t(c->gpr[31]),uint32_t(c->gpr[28]),before,
                          pending,retained))return;
        vm_write32(queue+0x20,0);vm_write32(queue+0x24,0);vm_write32(field,0);
        auto& recorder=trace();
        if(recorder.active.load(std::memory_order_acquire) && !recorder.clock_only) {
            Event event;event.kind=14;event.object=queue;
            event.sequence=uint32_t(c->gpr[31]);event.previous_us=uint32_t(c->gpr[28]);
            event.correction=before;event.guest_us=origin;
            event.interval_bits=pending;event.result=retained;recorder.push(event);
        }
    }
}

// op 0: pacing function entry; 1: original floating comparison, 2: correction
// store completed; 3/4: sendto entry/exit; 5/6: recvfrom entry/exit;
// 7: received frame snapshot subtraction; 8: peak selection;
// 9: constructed numeric frame header; 10: completed pacing-field reset;
// 11: original network-clock calibration completed;
// 12: recovery phase requested; 13: incoming/local frame pair;
// 14: title role-comparison identifiers.
extern "C" void tb_pacing_hook(ppu_context* c,unsigned op) {
    auto& t=trace();
    if(!t.active.load(std::memory_order_acquire))return;
    if(t.clock_only && op!=11)return;
    if(op==0) {polls=0;return;}
    Event e;
    if(op==1) {
        ++polls;
        const bool waiting=(c->cr>>7)&1;
        if(polls!=1 && waiting)return;
        if(!valid(c->gpr[31]) || !valid(c->gpr[30]))return;
        e.kind=waiting?1:2;e.object=uint32_t(c->gpr[31]);e.polls=polls;
        e.previous_us=vm_read64(e.object+0x110);e.guest_us=c->gpr[3];
        e.interval_bits=vm_read32(c->gpr[30]+4);e.correction=uint32_t(c->gpr[11]);
        e.flags=uint32_t(vm_read8(e.object+0x120))<<24 | uint32_t(vm_read8(e.object+0x121))<<16 | uint32_t(vm_read8(e.object+0x122))<<8 | vm_read8(e.object+0x12d);
        e.deadline_guest_us=c->fpr[0];e.observed_float_us=c->fpr[1];
    } else if(op==2) {
        e.kind=3;e.object=uint32_t(c->gpr[31])-0x124;e.correction=uint32_t(c->gpr[11]);
    } else if(op>=3 && op<=6) {
        unsigned direction=(op-3)/2;
        if(op%2) {
            io[direction]=Event{};io[direction].io_begin_ns=tb_host_now();
            io[direction].socket=uint32_t(c->gpr[3]);io[direction].lr=uint32_t(c->lr);
            return;
        }
        e=io[direction];e.kind=direction?5:4;e.result=uint32_t(c->gpr[3]);
        e.sequence=direction?++t.rx:++t.tx;
    } else if(op==7) {
        if(!valid(c->gpr[6]))return;
        e.kind=6;e.object=uint32_t(c->gpr[30]);
        e.sequence=vm_read32(c->gpr[6]+0xc);
        e.previous_us=uint32_t(c->gpr[4]);e.guest_us=uint32_t(c->gpr[26]);
        e.correction=uint32_t(c->gpr[5]);
    } else if(op==8) {
        if(!valid(c->gpr[28]))return;
        e.kind=7;e.object=uint32_t(c->gpr[28]);
        e.previous_us=vm_read32(e.object+0x24);e.guest_us=vm_read32(e.object+0x20);
        e.correction=uint32_t(c->gpr[0]);
    } else if(op==9) {
        if(!valid(c->gpr[28]))return;
        e.kind=8;e.object=uint32_t(c->gpr[28]);
        e.sequence=vm_read32(e.object);e.guest_us=vm_read32(e.object+4);
        e.previous_us=vm_read32(e.object+8);
    } else if(op==10) {
        if(!valid(c->gpr[31]))return;
        e.kind=9;e.object=uint32_t(c->gpr[31]);e.correction=vm_read32(e.object+0x124);
        e.previous_us=vm_read64(e.object+0x110);
        e.flags=uint32_t(vm_read8(e.object+0x122))<<16 | uint32_t(vm_read8(e.object+0x12c))<<8 | vm_read8(e.object+0x12d);
    } else if(op==11) {
        if(!valid(c->gpr[3]) || !valid(c->gpr[30]))return;
        e.kind=10;e.object=uint32_t(c->gpr[30]);e.socket=uint32_t(c->gpr[3]);
        e.previous_us=c->gpr[22];e.guest_us=c->gpr[31];e.sequence=uint32_t(c->gpr[28]);
        e.interval_bits=uint32_t(c->gpr[24]);e.result=uint32_t(c->gpr[25]);e.correction=uint32_t(c->gpr[23]);
    } else if(op==12) {
        e.kind=11;e.object=uint32_t(c->gpr[30]);
        e.sequence=uint32_t(c->gpr[31]);e.previous_us=uint32_t(c->gpr[28]);
        e.correction=uint32_t(c->gpr[31]-c->gpr[28]);
    } else if(op==13) {
        if(!valid(c->gpr[3]))return;
        e.kind=12;e.object=uint32_t(c->gpr[30]);
        e.sequence=uint32_t(c->gpr[31]);e.previous_us=uint32_t(c->gpr[28]);
        e.guest_us=uint32_t(c->gpr[26]);e.interval_bits=vm_read32(c->gpr[3]+0x10);
    } else if(op==14) {
        e.kind=13;e.object=uint32_t(c->gpr[31]);
        e.sequence=uint32_t(c->gpr[29]);e.result=uint32_t(c->gpr[3]);e.flags=c->gpr[7]==0;
        if(valid(c->gpr[1]+0xa0))e.lr=uint32_t(vm_read64(c->gpr[1]+0xa0));
    } else return;
    t.push(e);
}
