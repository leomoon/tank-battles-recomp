// Exercise production entitlement handlers with synthetic VM/dispatch only.
#include "ppu_recomp.h"
#include "entitlement.h"
#include "stubs.h"
#include <array>
#include <cstdlib>
#include <utility>

std::size_t ppu_vm_size = 0x04000000;
static std::array<uint8_t, 128> guest{};
static std::array<std::pair<uint32_t, tank_battles_override_fn>, 2> handlers{};
static unsigned registered = 0;
uint8_t vm_read8(uint64_t address) { return guest.at(address - 0x02000000); }
void vm_write8(uint64_t address, uint8_t value) { guest.at(address - 0x02000000) = value; }
void tank_battles_register_override(uint32_t nid, tank_battles_override_fn handler) {
    handlers.at(registered++) = {nid, handler};
}
extern "C" void ps3_hle_call(uint32_t nid, ppu_context* context) {
    for (const auto& [id, handler] : handlers) {
        if (id == nid) { handler(context); return; }
    }
    std::abort();
}
int main() {
    tank_battles_register_entitlement_overrides();
    if (registered != 2) return 2;
    return tank_battles_probe_entitlement();
}
