#include "rendering_profile.h"
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <mutex>

namespace {
constexpr const char* labels[] = {"draw", "submit-wait", "shader-translate", "pipeline-request",
    "pipeline-variant", "texture-upload", "surface-present", "window-present", "readback-finish",
    "capture-write", "fifo-drain", "guest-flip-present", "frame-interval"};
constexpr unsigned kinds = sizeof(labels) / sizeof(labels[0]);
struct Sample { std::atomic<uint64_t> n{0}, ns{0}, max{0}, bins[32]{}; } samples[kinds];
static_assert(std::atomic<uint64_t>::is_always_lock_free);
uint64_t now() {
    return std::chrono::duration_cast<std::chrono::nanoseconds>(
        std::chrono::steady_clock::now().time_since_epoch()).count();
}
struct Profile {
    FILE* file = nullptr;
    bool scopes = true;
    uint64_t base = 0, last_report = 0, last_frame = 0;
    unsigned reports = 0;
    std::mutex mutex;
    Profile() {
        const char* mode = getenv("TB_RENDER_PROFILE_MODE");
        scopes = !mode || mode[0] != 'c';
        const char* path = getenv("TB_RENDER_PROFILE");
        if (path && *path) {
            file = fopen(path, "w");
            if (!file) fprintf(stderr, "[render-profile] cannot open requested output\n");
            else base = now();
        }
    }
    ~Profile() { if (file) fclose(file); }
};
Profile& profile() { static Profile value; return value; }
void add(unsigned kind, uint64_t elapsed) {
    auto& s = samples[kind];
    s.n.fetch_add(1, std::memory_order_relaxed);
    s.ns.fetch_add(elapsed, std::memory_order_relaxed);
    auto maximum = s.max.load(std::memory_order_relaxed);
    while (maximum < elapsed && !s.max.compare_exchange_weak(maximum, elapsed, std::memory_order_relaxed)) {}
    unsigned bin = 0;
    for (auto n = elapsed; n > 1 && bin < 31; n >>= 1) ++bin;
    s.bins[bin].fetch_add(1, std::memory_order_relaxed);
}
}
extern "C" uint64_t tb_render_clock(unsigned kind) {
    return kind < kinds && profile().file && profile().scopes ? now() : 0;
}
extern "C" void tb_render_finish(unsigned kind, uint64_t start) {
    if (start && kind < kinds) add(kind, now() - start);
}
extern "C" void tb_render_frame() {
    auto& p = profile();
    if (!p.file) return;
    std::lock_guard<std::mutex> lock(p.mutex);
    const auto t = now();
    if (p.last_frame) add(12, t - p.last_frame);
    p.last_frame = t;
    if (t - p.last_report < 1000000000ull || p.reports >= 120) return;
    p.last_report = t;
    ++p.reports;
    fprintf(p.file, "{\"elapsed_ns\":%llu,\"cumulative\":true,\"scopes_inclusive\":true,\"timings\":{", (unsigned long long)(t - p.base));
    for (unsigned k = 0; k < kinds; ++k) {
        auto& s = samples[k];
        fprintf(p.file, "%s\"%s\":{\"n\":%llu,\"ns\":%llu,\"max_ns\":%llu,\"log2_ns_bins\":[", k ? "," : "", labels[k],
            (unsigned long long)s.n.load(), (unsigned long long)s.ns.load(), (unsigned long long)s.max.load());
        for (unsigned b = 0; b < 32; ++b)
            fprintf(p.file, "%s%llu", b ? "," : "", (unsigned long long)s.bins[b].load());
        fprintf(p.file, "]}");
    }
    fprintf(p.file, "}}\n");
    fflush(p.file);
}
