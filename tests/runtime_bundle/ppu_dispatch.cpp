// Exercise the actual production dispatcher, without a purchased title.
#include "ppu_recomp.h"
#include <cassert>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>

extern "C" {
uint8_t* vm_base = nullptr;
const func_entry function_table[] = {{0, nullptr, nullptr}};
const uint64_t function_table_count = 0;
void ppu_register_function(uint64_t, void (*)(ppu_context*));
void ps3_indirect_call(ppu_context*);
void ps3_indirect_call_to(ppu_context*, uint32_t);
int* ps3_ppu_dispatch_depth_slot();
int* ps3_ppu_vcall_sp_slot();
extern PPU_THREAD_LOCAL void (*g_trampoline_fn)(void*);
}

static constexpr uint32_t target = 0x10000;
void tank_battles_execution_report();
static unsigned calls;
static void leaf(ppu_context* ctx) { ++calls; ++ctx->gpr[3]; }
static void replacement(ppu_context* ctx) { ++calls; ctx->gpr[3] += 2; }
static void tail(void* data) { leaf(static_cast<ppu_context*>(data)); }
static void trampoline(ppu_context*) { g_trampoline_fn = tail; }
static void toc_tail(void* data) {
    auto* ctx = static_cast<ppu_context*>(data);
    assert(ctx->gpr[2] == 0x12345678);
    leaf(ctx);
}
static void toc_trampoline(ppu_context* ctx) {
    ctx->gpr[2] = 0x87654321;
    g_trampoline_fn = toc_tail;
}
static void nested(ppu_context* ctx) {
    assert(*ps3_ppu_dispatch_depth_slot() == 1);
    assert(*ps3_ppu_vcall_sp_slot() == 1);
    ps3_indirect_call_to(ctx, target);
    assert(*ps3_ppu_dispatch_depth_slot() == 1);
}

int main(int argc, char** argv) {
    ppu_context ctx{};
    ctx.ctr = target;
    ctx.gpr[2] = 0x12345678;
    ppu_register_function(target, leaf);
    if (argc == 2 && !std::strcmp(argv[1], "--toc")) {
        ppu_register_function(target + 12, toc_trampoline);
        ps3_indirect_call_to(&ctx, target + 12);
        assert(ctx.gpr[2] == 0x12345678 && ctx.gpr[3] == 1);
        assert(ctx.ctr == target && !g_trampoline_fn);
        return 0;
    }
    if (argc == 2 && !std::strcmp(argv[1], "--recovery")) {
        ctx.ctr = target + 0x1000;
        ctx.gpr[2] = target;
        ps3_indirect_call(&ctx);
        return 0;
    }
    if (argc == 2 && !std::strcmp(argv[1], "--invalid")) {
        ctx.ctr = 0x12345678;
        ctx.gpr[3] = 99;
        ps3_indirect_call(&ctx);
        assert(ctx.gpr[3] == 0); // Diagnostic mode's original invalid-slot behavior.
        return 0;
    }
    if (argc == 2 && !std::strcmp(argv[1], "--null")) {
        ctx.ctr = 0;
        ps3_indirect_call(&ctx);
        return 0;
    }
    if (argc == 2 && !std::strcmp(argv[1], "--depth")) {
        *ps3_ppu_dispatch_depth_slot() = 4001;
        ps3_indirect_call(&ctx);
        assert(calls == 0);
        return 0;
    }
    if (argc == 2 && !std::strcmp(argv[1], "--benchmark")) {
        constexpr unsigned iterations = 10000000;
        for (unsigned i=0; i<10000; ++i) ps3_indirect_call(&ctx);
        auto before = std::chrono::steady_clock::now();
        for (unsigned i=0; i<iterations; ++i) ps3_indirect_call(&ctx);
        double ns = std::chrono::duration<double, std::nano>(std::chrono::steady_clock::now()-before).count();
        std::printf("%.3f ns/resolved call (%u calls)\n", ns/iterations, calls);
        return 0;
    }
    ps3_indirect_call(&ctx);
    assert(ctx.gpr[3] == 1 && calls == 1);
    // A warm cache must notice registrations, including replacement of a hit.
    ppu_register_function(target, replacement);
    ps3_indirect_call(&ctx);
    assert(ctx.gpr[3] == 3 && calls == 2);
    ppu_register_function(target, leaf);
    // Deliberate direct-map collision must never return the previous target.
    ppu_register_function(target + 0x800, replacement);
    ps3_indirect_call_to(&ctx, target + 0x800);
    assert(ctx.gpr[3] == 5 && ctx.ctr == target);
    ps3_indirect_call(&ctx);
    assert(ctx.gpr[3] == 6);
    ppu_register_function(target + 4, trampoline);
    ps3_indirect_call_to(&ctx, target + 4);
    assert(ctx.gpr[3] == 7 && !g_trampoline_fn);
    ppu_register_function(target + 8, nested);
    ps3_indirect_call_to(&ctx, target + 8);
    assert(ctx.gpr[3] == 8 && ctx.ctr == target);
    assert(ctx.gpr[2] == 0x12345678);
    assert(*ps3_ppu_dispatch_depth_slot() == 0 && *ps3_ppu_vcall_sp_slot() == 0);
    tank_battles_execution_report();
    std::puts("PASS: production PPU dispatch, invalidation, collisions, explicit targets and trampolines");
}
