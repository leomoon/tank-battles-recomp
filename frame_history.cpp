#include "execution_policy_api.h"
#include <chrono>
#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <mutex>
#include <vector>

namespace {
using Clock = std::chrono::steady_clock;
struct History {
    const char* directory = std::getenv("TB_FRAME_HISTORY");
    unsigned limit = 96, saved = 0;
    double after = 0;
    Clock::time_point start = Clock::now();
    std::mutex lock;
    uint32_t buffer = 0, surface = 0, offset = 0;
    History() {
        if (const char* e = std::getenv("TB_FRAME_HISTORY_LIMIT")) {
            unsigned value = std::strtoul(e, nullptr, 10);
            limit = value > 240 ? 240 : value;
        }
        if (const char* e = std::getenv("TB_FRAME_HISTORY_AFTER")) after = std::atof(e);
    }
};
History& history() { static History h; return h; }
}
extern "C" void tb_scanout_sample(uint32_t buffer, uint32_t surface, uint32_t offset)
{
    auto& h = history();
    if (!h.directory || !*h.directory) return;
    std::lock_guard<std::mutex> guard(h.lock);
    h.buffer = buffer; h.surface = surface; h.offset = offset;
}
extern "C" void tb_frame_order(uint32_t buffer,uint32_t offset,uint64_t serial,uint32_t present)
{
    static const char* path=std::getenv("TB_FRAME_ORDER_TRACE");
    if (!path || !*path) return;
    static unsigned count=0;
    if (count++>=12000) return;
    if (FILE* f=std::fopen(path,"a")) {
        const auto ns=std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now()-history().start).count();
        std::fprintf(f,"{\"kind\":\"present\",\"present\":%u,\"elapsed_ns\":%lld,\"buffer\":%u,\"offset\":%u,\"write_serial\":%llu}\n",
            present,static_cast<long long>(ns),buffer,offset,static_cast<unsigned long long>(serial));
        std::fclose(f);
    }
}
extern "C" void tb_frame_event(uint32_t kind,uint32_t a,uint32_t b,uint32_t c,uint32_t d)
{
    static const char* path=std::getenv("TB_FRAME_ORDER_TRACE");
    if (!path || !*path) return;
    static std::atomic<unsigned> count{0};
    if (count.fetch_add(1,std::memory_order_relaxed)>=120000) return;
    if (FILE* f=std::fopen(path,"a")) {
        const auto ns=std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now()-history().start).count();
        std::fprintf(f,"{\"kind\":\"%s\",\"elapsed_ns\":%lld,\"buffer\":%u,\"b\":%u,\"c\":%u,\"d\":%u}\n",
            kind==0?"flip-request":kind==1?"flip-select":"scanout-write",static_cast<long long>(ns),a,b,c,d);
        std::fclose(f);
    }
}
extern "C" void tb_frame_history(const void* pixels, uint32_t width, uint32_t height, uint32_t present)
{
    auto& h = history();
    if (!h.directory || !*h.directory) return;
    std::lock_guard<std::mutex> guard(h.lock);
    double elapsed = std::chrono::duration<double>(Clock::now() - h.start).count();
    if (h.saved >= h.limit || elapsed < h.after || !width || !height) return;
    // Bound each diagnostic frame as well as the number retained.
    if (uint64_t(width) * height > 4096ull * 4096) return;
    const auto* rgba = static_cast<const unsigned char*>(pixels);
    std::vector<unsigned char> rgb(size_t(width) * height * 3);
    for (size_t i = 0; i < size_t(width) * height; ++i)
        for (unsigned c = 0; c < 3; ++c) rgb[i * 3 + c] = rgba[i * 4 + c];
    char path[4096];
    std::snprintf(path, sizeof path, "%s/frame-%04u.ppm", h.directory, h.saved);
    FILE* f = std::fopen(path, "wb");
    bool ok = f && std::fprintf(f, "P6\n%u %u\n255\n", width, height) > 0;
    if (f) {
        ok = (std::fwrite(rgb.data(), 1, rgb.size(), f) == rgb.size()) && ok;
        ok = (std::fclose(f) == 0) && ok;
    }
    std::snprintf(path, sizeof path, "%s/frames.jsonl", h.directory);
    if (FILE* metadata = std::fopen(path, "a")) {
        std::fprintf(metadata, "{\"index\":%u,\"present\":%u,\"elapsed_seconds\":%.6f,\"buffer\":%u,\"surface\":%u,\"offset\":%u,\"width\":%u,\"height\":%u,\"complete\":%s}\n",
                     h.saved, present, elapsed, h.buffer, h.surface, h.offset, width, height, ok ? "true" : "false");
        std::fclose(metadata);
    }
    ++h.saved;
}
