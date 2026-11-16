// Page-protection census of guest accesses to RSX surface memory: page_census.h.
#include "page_census.h"

#if defined(__linux__) && defined(__x86_64__)
#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <mutex>
#include <signal.h>
#include <string>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <time.h>
#include <tuple>
#include <ucontext.h>
#include <unistd.h>

namespace {
constexpr unsigned kRanges = 128, kRing = 1u << 16;
constexpr uintptr_t kPage = 4096;

struct Range {
    std::atomic<int> armed{0};
    uintptr_t lo = 0, hi = 0;            // exact surface bytes
    uintptr_t page_lo = 0, page_hi = 0;  // protected pages
    uint32_t kind = 0, location = 0, offset = 0, w = 0, h = 0, pitch = 0;
};
struct Event {
    uint32_t tid, range, write, inside;
    uintptr_t rip;
};

Range g_ranges[kRanges];
unsigned g_count = 0, g_pending = 0;
struct Pending { const void* host; uint32_t bytes, kind, location, offset, w, h, pitch; } g_next[kRanges];
Event g_ring[kRing];
std::atomic<uint32_t> g_head{0};
uint32_t g_tail = 0;
std::atomic<long> g_walker{0};
std::atomic<uint64_t> g_dropped{0}, g_arms{0};
struct sigaction g_previous;
int g_enabled = -1;
uintptr_t g_exe_base = 0;

long tid() { return syscall(SYS_gettid); }

void chain(int sig, siginfo_t* info, void* context) {
    if (g_previous.sa_flags & SA_SIGINFO) {
        if (g_previous.sa_sigaction) { g_previous.sa_sigaction(sig, info, context); return; }
    } else if (g_previous.sa_handler != SIG_DFL && g_previous.sa_handler != SIG_IGN) {
        g_previous.sa_handler(sig);
        return;
    }
    signal(sig, SIG_DFL);   // re-executing the access now terminates as before
}

void on_fault(int sig, siginfo_t* info, void* context) {
    const uintptr_t at = (uintptr_t)info->si_addr;
    // An armed surface whose protected pages contain the address: record and lift it.
    for (unsigned i = 0; i < kRanges; i++) {
        Range& r = g_ranges[i];
        if (at < r.page_lo || at >= r.page_hi || !r.armed.exchange(0, std::memory_order_acq_rel)) continue;
        const ucontext_t* uc = (const ucontext_t*)context;
        const uint32_t slot = g_head.fetch_add(1, std::memory_order_relaxed);
        if (slot - g_tail < kRing) {
            Event& e = g_ring[slot % kRing];
            e.tid = (uint32_t)tid();
            e.range = i;
            e.write = (uc->uc_mcontext.gregs[REG_ERR] & 2) != 0;
            e.inside = at >= r.lo && at < r.hi;
            e.rip = (uintptr_t)uc->uc_mcontext.gregs[REG_RIP];
        } else {
            g_dropped.fetch_add(1, std::memory_order_relaxed);
        }
        mprotect((void*)r.page_lo, r.page_hi - r.page_lo, PROT_READ | PROT_WRITE);
        return;
    }
    // Lifted by another thread (or by the walker re-arming) in the meantime: retry.
    for (unsigned i = 0; i < kRanges; i++)
        if (at >= g_ranges[i].page_lo && at < g_ranges[i].page_hi) return;
    chain(sig, info, context);
}

std::string comm_of(uint32_t t) {
    char path[64];
    snprintf(path, sizeof path, "/proc/self/task/%u/comm", t);
    FILE* f = fopen(path, "r");
    if (!f) return "exited";
    char name[32] = {0};
    if (!fgets(name, sizeof name, f)) name[0] = 0;
    fclose(f);
    name[strcspn(name, "\n")] = 0;
    return name;
}

struct Key {
    std::string who;
    uint32_t kind, location, offset, w, h, write, inside;
    uintptr_t rip;
    bool operator<(const Key& o) const {
        return std::tie(who, kind, location, offset, w, h, write, inside, rip) <
               std::tie(o.who, o.kind, o.location, o.offset, o.w, o.h, o.write, o.inside, o.rip);
    }
};
std::map<Key, uint64_t> g_totals;
std::map<uint32_t, std::string> g_names;
std::mutex g_report_mutex;
uint64_t g_last_report = 0;

uint64_t now_ns() {
    timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (uint64_t)t.tv_sec * 1000000000ull + (uint64_t)t.tv_nsec;
}

// Walker thread (and exit): fold recorded faults into totals, print periodically.
void collect(bool force) {
    std::lock_guard<std::mutex> lock(g_report_mutex);
    const uint32_t head = g_head.load(std::memory_order_acquire);
    const long walker = g_walker.load(std::memory_order_relaxed);
    for (; g_tail != head; g_tail++) {
        const Event& e = g_ring[g_tail % kRing];
        const Range& r = g_ranges[e.range];
        auto name = g_names.find(e.tid);
        if (name == g_names.end()) name = g_names.emplace(e.tid, comm_of(e.tid)).first;
        const std::string who = (long)e.tid == walker ? "walker" : name->second + "/" + std::to_string(e.tid);
        g_totals[{who, r.kind, r.location, r.offset, r.w, r.h, e.write, e.inside, e.rip - g_exe_base}]++;
    }
    const uint64_t t = now_ns();
    if (!force && t - g_last_report < 5000000000ull) return;
    g_last_report = t;
    fprintf(stderr, "[page-census] arms=%llu dropped=%llu\n", (unsigned long long)g_arms.load(),
            (unsigned long long)g_dropped.load());
    for (const auto& [k, n] : g_totals)
        fprintf(stderr, "[page-census] who=%s kind=%s surface=%u:0x%08X %ux%u %s %s rip=0x%lx count=%llu\n",
                k.who.c_str(), k.kind == TB_CENSUS_DEPTH ? "depth" : "color", k.location, k.offset, k.w, k.h,
                k.write ? "write" : "read", k.inside ? "inside" : "shared-page", (unsigned long)k.rip,
                (unsigned long long)n);
}

void at_exit() { collect(true); }

void install() {
    FILE* maps = fopen("/proc/self/maps", "r");
    if (maps) {   // the executable's first mapping, for addr2line offsets
        char line[512];
        if (fgets(line, sizeof line, maps)) g_exe_base = (uintptr_t)strtoull(line, nullptr, 16);
        fclose(maps);
    }
    struct sigaction action {};
    action.sa_sigaction = on_fault;
    action.sa_flags = SA_SIGINFO | SA_NODEFER;
    sigemptyset(&action.sa_mask);
    sigaction(SIGSEGV, &action, &g_previous);
    atexit(at_exit);
    fprintf(stderr, "[page-census] protecting RSX surface memory between flips (diagnostic)\n");
}
}

extern "C" int tb_page_census_on(void) {
    if (g_enabled < 0) {
        const char* e = getenv("TB_PAGE_CENSUS");
        g_enabled = e && *e && strcmp(e, "0") != 0;
        if (g_enabled) install();
    }
    return g_enabled;
}

extern "C" void tb_page_census_walker(void) { g_walker.store(tid(), std::memory_order_relaxed); }

extern "C" void tb_page_census_begin(void) { g_pending = 0; }

extern "C" void tb_page_census_add(const void* host, uint32_t bytes, uint32_t kind, uint32_t location,
                                   uint32_t offset, uint32_t w, uint32_t h, uint32_t pitch) {
    if (!host || !bytes || g_pending == kRanges) return;
    g_next[g_pending++] = {host, bytes, kind, location, offset, w, h, pitch};
}

extern "C" void tb_page_census_commit(void) {
    if (!tb_page_census_on()) return;
    collect(false);
    for (unsigned i = 0; i < g_count; i++) {   // lift the previous set
        Range& r = g_ranges[i];
        if (r.armed.exchange(0, std::memory_order_acq_rel))
            mprotect((void*)r.page_lo, r.page_hi - r.page_lo, PROT_READ | PROT_WRITE);
    }
    for (unsigned i = 0; i < g_pending; i++) {
        Range& r = g_ranges[i];
        const Pending& p = g_next[i];
        r.lo = (uintptr_t)p.host;
        r.hi = r.lo + p.bytes;
        r.page_lo = r.lo & ~(kPage - 1);
        r.page_hi = (r.hi + kPage - 1) & ~(kPage - 1);
        r.kind = p.kind; r.location = p.location; r.offset = p.offset; r.w = p.w; r.h = p.h; r.pitch = p.pitch;
        if (mprotect((void*)r.page_lo, r.page_hi - r.page_lo, PROT_NONE) == 0)
            r.armed.store(1, std::memory_order_release);
    }
    // Slots past the new set keep their old bounds disarmed: a fault racing
    // the re-arm still finds them and retries rather than chaining.
    if (g_pending > g_count) g_count = g_pending;
    g_arms.fetch_add(1, std::memory_order_relaxed);
}
#else
extern "C" int tb_page_census_on(void) { return 0; }
extern "C" void tb_page_census_walker(void) {}
extern "C" void tb_page_census_begin(void) {}
extern "C" void tb_page_census_add(const void*, uint32_t, uint32_t, uint32_t, uint32_t, uint32_t, uint32_t, uint32_t) {}
extern "C" void tb_page_census_commit(void) {}
#endif
