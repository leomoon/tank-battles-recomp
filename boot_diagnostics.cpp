#include "boot_diagnostics.h"
#include "stubs.h"
#include "execution_policy_api.h"
#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <thread>
#include <chrono>
#include "host_services.h"
#include "ppu_recomp.h"
#ifndef _WIN32
#include <signal.h>
#include <unistd.h>
#include <cerrno>
#include <initializer_list>
#endif
#include <cerrno>

static_assert(std::atomic<uint32_t>::is_always_lock_free);
static_assert(std::atomic<const char*>::is_always_lock_free);
struct Snapshot {
    std::atomic<uint32_t> sequence{0}, seen{0}, cia{0}, lr{0}, nid{0};
};
static Snapshot snapshots[64];
static uint32_t trace_nid;
static std::atomic<uint32_t> traced_calls{0};
static std::atomic<const char*> milestone{"process start"};
static std::atomic<bool> stop_requests{false};
static std::thread request_watcher;
static void stop_request_watcher() {
    stop_requests.store(true);
    if(request_watcher.joinable()) request_watcher.join();
}

// Only lock-free atomics and async-signal-safe writes are used by the handler.
// Never wait on an interrupted writer: inconsistent snapshots are omitted.
static void write_text(const char* s) {
    size_t n = 0; while (s[n]) ++n;
    tb_host_write(s, n);
}
static void write_hex(uint32_t value) {
    char text[11] = "0x00000000";
    const char* digits = "0123456789ABCDEF";
    for (unsigned i=0; i<8; ++i) text[9-i] = digits[(value >> (i*4)) & 15];
    write_text(text);
}
void tank_battles_report(const char* reason) {
    const int saved_errno = errno;
    tank_battles_execution_report();
    tank_battles_vulkan_report();
    write_text("[diagnostic] reason="); write_text(reason);
    write_text(" milestone="); write_text(milestone.load()); write_text("\n");
    write_text("[diagnostic] Last observed HLE boundaries, not a live guest-PC sample:\n");
    for (unsigned i=0; i<64; ++i) {
        auto& s = snapshots[i]; const auto before = s.sequence.load();
        if (!s.seen.load()) continue;
        if (before & 1) { write_text("[diagnostic] snapshot busy\n"); continue; }
        const auto cia=s.cia.load(), lr=s.lr.load(), nid=s.nid.load();
        if (before != s.sequence.load()) { write_text("[diagnostic] snapshot changed\n"); continue; }
        write_text("[diagnostic] guest_thread="); write_hex(i);
        write_text(" cia="); write_hex(cia);
        write_text(" lr="); write_hex(lr);
        write_text(" hle_nid="); write_hex(nid); write_text("\n");
    }
    errno = saved_errno;
}
#ifndef _WIN32
static void report_signal(int number) {
    tank_battles_report(number == SIGUSR1 ? "snapshot requested" : "fatal signal");
    if (number != SIGUSR1) {
        struct sigaction action{};
        action.sa_handler = SIG_DFL;
        sigemptyset(&action.sa_mask);
        sigaction(number, &action, nullptr);
        kill(getpid(), number); // Restore the original signal exit semantics.
    }
}
#endif
#ifdef _WIN32
static LONG WINAPI report_exception(EXCEPTION_POINTERS*) {
    tank_battles_report("fatal Windows exception");
    return EXCEPTION_CONTINUE_SEARCH;
}
#endif
void tank_battles_diagnostics_init() {
    if (const char* value = getenv("TB_TRACE_NID")) trace_nid = static_cast<uint32_t>(strtoul(value, nullptr, 0));
#ifdef _WIN32
    SetUnhandledExceptionFilter(report_exception);
#else
    struct sigaction action{};
    action.sa_handler = report_signal;
    sigemptyset(&action.sa_mask);
    action.sa_flags = SA_RESTART;
    for (int number : {SIGUSR1, SIGSEGV, SIGBUS, SIGILL, SIGFPE, SIGABRT})
        if (sigaction(number, &action, nullptr) != 0)
            fprintf(stderr, "[diagnostic] could not install signal handler %d\n", number);
#endif
    if (const char* path=getenv("TB_DIAGNOSTIC_TRIGGER");path && *path) {
        const auto trigger=std::filesystem::u8path(path);
        request_watcher=std::thread([trigger] {
            while(!stop_requests.load()) {
                std::error_code error;
                if(std::filesystem::remove(trigger,error)) tank_battles_report("snapshot requested");
                std::this_thread::sleep_for(std::chrono::milliseconds(50));
            }
        });
        atexit(stop_request_watcher);
    }
}
void tank_battles_milestone(const char* name) {
    milestone.store(name); // Caller supplies static-lifetime strings only.
    fprintf(stderr, "[milestone] %s\n", name);
}
extern "C" void tb_sdk_ps3_hle_call(uint32_t, ppu_context*);
extern "C" void ps3_hle_call(uint32_t nid, ppu_context* context) {
    if (context->thread_id < 64) {
        auto& s = snapshots[context->thread_id];
        s.sequence.fetch_add(1);
        s.cia.store(static_cast<uint32_t>(context->cia));
        s.lr.store(static_cast<uint32_t>(context->lr));
        s.nid.store(nid);
        s.seen.store(1);
        s.sequence.fetch_add(1);
    }
    const bool trace = trace_nid && nid == trace_nid && traced_calls.fetch_add(1) < 32;
    if (trace) fprintf(stderr, "[port-hle] enter nid=0x%08X lr=0x%08X r3=0x%016llX\n",
                       nid, static_cast<uint32_t>(context->lr), (unsigned long long)context->gpr[3]);
    if (!tank_battles_dispatch_override(nid, context))
        tb_sdk_ps3_hle_call(nid, context);
    if (trace) fprintf(stderr, "[port-hle] return nid=0x%08X r3=0x%016llX\n",
                       nid, (unsigned long long)context->gpr[3]);
}
