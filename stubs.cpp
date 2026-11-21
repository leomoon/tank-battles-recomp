/* Original port hooks; no game code. */
#include "stubs.h"
#include "hle_inventory.h"
#include "content_paths.h"
#include "entitlement.h"
#include "offline_network.h"
#include <cstdlib>
#include <cstdio>
#include <vector>
#ifdef TB_DIAGNOSTIC_TESTS
#include "ppu_recomp.h"
#endif

extern "C" void ps3_load_prx_modules(void) {}

extern "C" int ps3_hle_has(uint32_t);
extern "C" void ps3_hle_register_ctx(uint32_t, const char*, tank_battles_override_fn);
extern "C" void tb_sdk_ps3_hle_call(uint32_t, ppu_context*);
struct Override { uint32_t nid; uint32_t alias; };
static std::vector<Override> overrides;

void tank_battles_register_override(uint32_t nid, tank_battles_override_fn handler)
{
    if (!handler) std::abort();
    static uint32_t next_alias = 0x54420000u;
    while (ps3_hle_has(next_alias)) ++next_alias;
    const uint32_t alias = next_alias++;
    tank_battles_record_override(nid, alias);
    // Dispatch through the SDK under a private NID to preserve its TOC guard,
    // callback pumping, ABI adaptation and in-flight tracking.
    ps3_hle_register_ctx(alias, "Tank Battles port override", handler);
    if (!ps3_hle_has(alias)) {
        std::fprintf(stderr, "[hle] Could not register override 0x%08x (alias 0x%08x)\n", nid, alias);
        std::abort();
    }
    for (auto& entry : overrides)
        if (entry.nid == nid) { entry.alias = alias; return; }
    overrides.push_back({nid, alias});
}
bool tank_battles_dispatch_override(uint32_t nid, ppu_context* context)
{
    for (const auto& entry : overrides)
        if (entry.nid == nid) {
            tb_sdk_ps3_hle_call(entry.alias, context);
            return true;
        }
    return false;
}
#ifdef TB_DIAGNOSTIC_TESTS
static constexpr uint32_t test_nid = 0x1CF98800u; // cellPadInit, already in the generic HLE table.
static void shared_test_handler(ppu_context* context) { context->gpr[3] = 0xBAD; }
static void test_override(ppu_context* context) { context->gpr[3] = 0x54424F4Bu; context->gpr[2] = 0; }
extern "C" void ps3_hle_call(uint32_t, ppu_context*);
bool tank_battles_verify_test_override()
{
    ppu_context context{};
    context.gpr[1] = 0x0FEFFF00;
    context.gpr[2] = 0x12345678;
    context.cia = 0x10200;
    context.lr = 0x10204;
    ps3_hle_call(test_nid, &context);
    const bool ok = context.gpr[3] == 0x54424F4Bu && context.gpr[2] == 0x12345678 &&
                    vm_read64(context.gpr[1] + 40) == 0x12345678;
    fprintf(stderr, "[override-test] %s\n", ok ? "PASS" : "FAIL");
    return ok;
}
#endif
void tank_battles_register_text_conversion();
void tank_battles_register_overrides()
{
    tank_battles_register_text_conversion();
    tank_battles_register_asset_overrides();
    tank_battles_register_entitlement_overrides();
    tank_battles_register_offline_network();
#ifdef TB_DIAGNOSTIC_TESTS
    ps3_hle_register_ctx(test_nid, "synthetic existing shared handler", shared_test_handler);
    tank_battles_register_override(test_nid, test_override);
#endif
}
