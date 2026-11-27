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
#include <cstring>
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
    bool clock_only=false, frame_only=false;
    uint64_t start=0,end=0;
    Trace() {
        const char* value=getenv("TB_PACING_TRACE");
        if (!value || !*value) return;
        path=value;
        value=getenv("TB_PACING_TRIGGER");
        if (!value || !*value) return; // Explicit gameplay trigger is mandatory.
        trigger=value;
        value=getenv("TB_PACING_CLOCK_ONLY");clock_only=value && std::string(value)=="1";
        value=getenv("TB_PACING_FRAME_ONLY");frame_only=value && std::string(value)=="1";
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
        if(frame_only && e.kind!=1 && e.kind!=2 && e.kind!=3 && e.kind!=9 && e.kind!=11 && e.kind!=14 && e.kind!=15 && e.kind!=16 && e.kind!=17 && e.kind!=18 && e.kind!=19 && e.kind!=20 && e.kind!=21 && e.kind!=22 && e.kind!=6 && e.kind!=7 && e.kind!=8 && e.kind!=12)return;
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
    const bool enabled=[] {
        // Ordinary players use the validated helper. This developer control
        // selects the original path for diagnostics, never a user setting.
        const char* value=getenv("TB_ONLINE_PACING");
        const bool on=!value || !*value || std::string(value)!="original";
        if(value && *value && std::string(value)!="original" && std::string(value)!="recovery-render")
            fprintf(stderr,"[online pacing] unknown comparison option; using recovery rendering\n");
        fprintf(stderr,"[online pacing] %s\n",on?"guarded recovery rendering enabled":"original rendering selected");
        return on;
    }();
    std::mutex mutex;
    tb_pacing_recovery::RenderState state;
};
RecoveryExperiment& recovery() {static auto* value=new RecoveryExperiment;return *value;}
}

extern "C" void tb_pacing_experiment(ppu_context* c,unsigned op) {
    auto& experiment=recovery();
    if(!experiment.enabled)return;
    std::lock_guard guard(experiment.mutex);
    auto& state=experiment.state;
    if(op==15 || op==11) {state.reset();return;}
    if(op==14) {state.role(uint32_t(c->gpr[29]),uint32_t(c->gpr[3]));return;}
    if(op==13) {
        if(!valid(c->gpr[30]) || !valid(c->gpr[3])) {state.pending=false;return;}
        state.observe(uint32_t(c->gpr[30]),uint32_t(c->gpr[3]),uint32_t(c->gpr[31]),
                      uint32_t(c->gpr[28]),tb_host_now(),vm_read8(c->gpr[30]+0x14)!=0);
    } else if(op==12) {
        if(c->gpr[3]!=0) {state.pending=false;return;}
        state.request(uint32_t(c->gpr[30]),uint32_t(c->gpr[31]),uint32_t(c->gpr[28]),tb_host_now());
    } else if(op==19) {
        if(!valid(c->gpr[3])) {state.pending=false;return;}
        state.advance(uint32_t(c->gpr[3]),vm_read32(c->gpr[3]+0x20));
    } else if(op==17) {
        if(!valid(c->gpr[7]) || c->gpr[7]<0x10122 || !valid(state.manager) || !valid(state.queue)) {
            state.pending=false;return;
        }
        const uint32_t pacer=uint32_t(c->gpr[7])-0x122;
        // Verify the frame advanced by the original title call, then updated.
        // A request cannot render a later update or survive supersession.
        const bool active=vm_read8(state.queue+0x14) && vm_read8(pacer+0x121);
        auto& diagnostic=trace();
        if(diagnostic.active.load(std::memory_order_acquire) && c->gpr[29]==1) {
            Event event;event.kind=19;event.object=state.manager;event.sequence=state.remote;
            event.previous_us=state.local;event.guest_us=vm_read32(state.manager+0x20);
            event.flags=unsigned(state.pending) | unsigned(state.unsupported)<<1 | unsigned(state.seen)<<2 | unsigned(state.invited())<<3 | unsigned(state.advanced)<<4;
            event.result=uint32_t(tb_host_now()-state.observed_ns);event.interval_bits=state.queue;
            diagnostic.push(event);
        }
        if(!state.consume(vm_read32(state.manager+0x20),tb_host_now(),active,
                          c->gpr[29]==1 && vm_read8(pacer+0x122)==1))return;
        c->gpr[29]=0; // Only this loaded render decision changes.
        auto& recorder=trace();
        if(recorder.active.load(std::memory_order_acquire)) {
            Event event;event.kind=16;event.object=pacer;event.sequence=state.remote;
            event.previous_us=state.local;event.correction=vm_read32(pacer+0x124);
            recorder.push(event);
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
// 20/21/22: adaptive interval maximum, output and member timing input.
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
        e.socket=uint32_t(c->gpr[30]);e.result=vm_read32(c->gpr[30]);
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
    } else if(op==20) {
        if(!valid(c->gpr[30]) || !valid(c->gpr[2]-0x255c))return;
        const uint32_t local_average=vm_read32(c->gpr[2]-0x255c);
        const uint32_t target=vm_read32(c->gpr[2]-0x2550);
        if(!valid(local_average) || !valid(target))return;
        e.kind=20;e.object=uint32_t(c->gpr[30]);e.observed_float_us=c->fpr[11];
        e.interval_bits=vm_read32(local_average);e.result=vm_read32(target);
        e.correction=vm_read32(c->gpr[2]-0x2548);
    } else if(op==21) {
        if(!valid(c->gpr[29]))return;
        e.kind=21;e.object=uint32_t(c->gpr[29]);e.interval_bits=vm_read32(e.object+0x40);
    } else if(op==22) {
        if(!valid(c->gpr[10]))return;
        e.kind=22;e.object=uint32_t(c->gpr[10]);e.interval_bits=vm_read32(e.object+0x10);
        e.sequence=vm_read32(e.object+0xc);e.flags=vm_read8(e.object+0x15);
    } else if(op==18) {
        e.kind=18;e.object=uint32_t(c->gpr[3]);
        const float delta=float(c->fpr[1]);memcpy(&e.interval_bits,&delta,sizeof(delta));
    } else if(op==17) {
        if(!valid(c->gpr[7]) || c->gpr[7]<0x10122)return;
        e.kind=17;e.object=uint32_t(c->gpr[7])-0x122;e.flags=uint32_t(c->gpr[29]);
        e.correction=vm_read32(e.object+0x124);
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
