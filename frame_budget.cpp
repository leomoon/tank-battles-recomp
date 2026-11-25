// Opt-in frame-budget timeline: see frame_budget.h and scripts/frame_budget.py.
#include "frame_budget.h"
#include "host_services.h"
#include "ps3emu/ps3_ftrace.h"

#include <atomic>
#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <string>
#include <thread>
#include <filesystem>
#if defined(__linux__)
#include <sys/prctl.h>
#include <time.h>
#endif

extern "C" uint8_t* vm_base;
extern "C" uint32_t ppu_hle_inject_base;

namespace {
std::atomic<int> g_enabled{0};
std::atomic<int> g_arm_requested{0};
std::atomic<bool> g_stop{false};
std::thread g_watcher;
void stop_watcher() {g_stop.store(true);if(g_watcher.joinable())g_watcher.join();}
double g_delay = 5.0, g_seconds = 20.0;

void request_arm(int) { g_arm_requested.store(1, std::memory_order_relaxed); }

double env_seconds(const char* name, double fallback, double low, double high) {
    const char* value = getenv(name);
    if (!value || !*value) return fallback;
    const double parsed = strtod(value, nullptr);
    return parsed >= low && parsed <= high ? parsed : fallback;
}

uint32_t guest_put() {
    if (!vm_base) return 0;
    const uint8_t* p = vm_base + ppu_hle_inject_base + 0x2000u;
    return (uint32_t)p[0] << 24 | (uint32_t)p[1] << 16 | (uint32_t)p[2] << 8 | p[3];
}

// Arms the toolkit window on request, then samples the FIFO put register so
// the report can measure how long submitted work waits for the walker.
void watcher() {
#if defined(__linux__)
    prctl(PR_SET_TIMERSLACK, 1UL, 0, 0, 0);
    prctl(PR_SET_NAME, "tb-frame-budget", 0, 0, 0);
#endif
    uint32_t last = 0;
    const char* trigger = getenv("TB_FRAME_BUDGET_TRIGGER");
    while (!g_stop.load(std::memory_order_relaxed)) {
        if (trigger && *trigger) {
            std::error_code error;
            if (std::filesystem::remove(std::filesystem::u8path(trigger), error)) request_arm(0);
        }
        ps3_ft_state* s = &g_ps3_ft;
        if (g_arm_requested.exchange(0, std::memory_order_relaxed) && ps3_ft_on()) {
            const uint64_t start = ps3_ft_now() + (uint64_t)(g_delay * 1e9);
            __atomic_store_n(&s->end, start + (uint64_t)(g_seconds * 1e9), __ATOMIC_RELAXED);
            __atomic_store_n(&s->start, start, __ATOMIC_RELEASE);
            fprintf(stderr, "[frame-budget] window armed: %.1f s after %.1f s\n", g_seconds, g_delay);
        }
        const uint64_t now = ps3_ft_now();
        const int recording = __atomic_load_n(&s->on, __ATOMIC_ACQUIRE) == 2 &&
                              now + 1000000ull >= __atomic_load_n(&s->start, __ATOMIC_ACQUIRE) &&
                              now <= __atomic_load_n(&s->end, __ATOMIC_ACQUIRE);
        if (__atomic_load_n(&s->on, __ATOMIC_ACQUIRE) == 3) return;
        if (!recording) {
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
            continue;
        }
        const uint32_t put = guest_put();
        if (put != last) {
            last = put;
            ps3_ft_span(TB_FT_PUT_ADVANCE, 0, put, 0, now, now);
        }
        std::this_thread::sleep_for(std::chrono::microseconds(10));
    }
}
}

extern "C" void tb_frame_budget_init(void) {
    const char* path = getenv("TB_FRAME_BUDGET");
    if (!path || !*path) return;
    g_delay = env_seconds("TB_FRAME_BUDGET_DELAY", 5.0, 0.0, 600.0);
    g_seconds = env_seconds("TB_FRAME_BUDGET_SECONDS", 20.0, 1.0, 120.0);
    // Far-future window: hooks stay a cheap rejected compare until SIGUSR2.
    const std::string spec = "1000000:" + std::to_string(g_seconds) + ":" + path;
    tb_host_env("PS3_FTRACE", spec.c_str(), 1);
    tb_host_env("PS3_FTRACE_MIN_US", "5", 0);
#ifndef _WIN32
    struct sigaction action {};
    action.sa_handler = request_arm;
    sigemptyset(&action.sa_mask);
    action.sa_flags = SA_RESTART;
    sigaction(SIGUSR2, &action, nullptr);
#endif
    g_enabled.store(1, std::memory_order_release);
    ps3_ft_on();
    // Text ftrace timestamps subtract this base. Keep the exact mapping so
    // title pacing and gameplay telemetry can be aligned without guessing.
    fprintf(stderr, "[frame-budget] base_monotonic_ns=%llu\n",
            (unsigned long long)g_ps3_ft.base);
    g_watcher=std::thread(watcher);
    atexit(stop_watcher);
}

extern "C" int tb_frame_budget_on(void) {
    return g_enabled.load(std::memory_order_relaxed) && ps3_ft_on();
}

extern "C" uint64_t tb_frame_budget_now(void) { return ps3_ft_now(); }

extern "C" thread_local const char* tb_wait_label = nullptr;

extern "C" uint32_t tb_frame_budget_site(const char* label) {
    // Walker-only in practice; a mutex keeps the table sound regardless.
    static std::mutex mutex;
    static const char* sites[256];
    static uint32_t count = 0;
    if (!label) return 0;
    std::lock_guard<std::mutex> lock(mutex);
    for (uint32_t i = 0; i < count; i++)
        if (sites[i] == label || !strcmp(sites[i], label)) return i + 1;
    if (count == 256) return 0;
    sites[count++] = label;
    fprintf(stderr, "[frame-budget] site %u %s\n", count, label);
    return count;
}

extern "C" void tb_frame_budget_span(unsigned kind, uint32_t a, uint64_t t0, uint64_t t1) {
    ps3_ft_span((int)kind, 0, a, 0, t0, t1);
}

extern "C" void tb_frame_budget_mark(unsigned kind, uint32_t a, uint32_t b) {
    if (!g_enabled.load(std::memory_order_relaxed) || __atomic_load_n(&g_ps3_ft.on, __ATOMIC_RELAXED) != 2) return;
    const uint64_t t = ps3_ft_now();
    ps3_ft_span((int)kind, 0, a, b, t, t);
}

// Counters are cumulative: a record carries the running total (mod 2^32) and
// is written at most every 0.5 ms per counter, so a busy counter cannot fill
// the trace. The report differences the last total before each flip.
extern "C" void tb_frame_budget_count(uint32_t id, uint32_t amount) {
    static std::atomic<uint32_t> totals[64];
    static std::atomic<uint64_t> written[64];
    if (id >= 64 || !g_enabled.load(std::memory_order_relaxed) ||
        __atomic_load_n(&g_ps3_ft.on, __ATOMIC_RELAXED) != 2) return;
    const uint32_t total = totals[id].fetch_add(amount, std::memory_order_relaxed) + amount;
    const uint64_t t = ps3_ft_now();
    uint64_t last = written[id].load(std::memory_order_relaxed);
    if (t - last < 500000 || !written[id].compare_exchange_strong(last, t, std::memory_order_relaxed)) return;
    ps3_ft_span(TB_FT_COUNT, 0, id, total, t, t);
}
