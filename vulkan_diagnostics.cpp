#include "execution_policy_api.h"
#include <atomic>
#include <cstdio>
#include "host_services.h"

namespace {
constexpr const char* names[] = {
    "backend-ready", "shader-module", "guest-draw", "window-present", "pipeline",
    "texture-upload", "surface-flip", "event-pump", "window-event"
};
constexpr unsigned count = sizeof(names) / sizeof(names[0]);
std::atomic<uint32_t> counts[count];
static_assert(std::atomic<uint32_t>::is_always_lock_free);
}

extern "C" void tb_vulkan_event(unsigned kind, uint32_t a, uint32_t b, uint32_t c)
{
    if (kind >= count) return;
    const auto n = counts[kind].fetch_add(1, std::memory_order_relaxed) + 1;
    if (n <= (kind == 8 ? 32u : 8u))
        fprintf(stderr, "[vulkan-event] %s a=%u b=%u c=%u count=%u\n", names[kind], a, b, c, n);
}

void tank_battles_vulkan_report()
{
    // Called by the signal diagnostic handler. Avoid stdio and allocations.
    for (unsigned k = 0; k < count; ++k) {
        constexpr char prefix[] = "[vulkan-count] ";
        tb_host_write( prefix, sizeof(prefix) - 1);
        size_t length = 0;
        while (names[k][length]) ++length;
        tb_host_write( names[k], length);
        char value[] = "=0x00000000\n";
        constexpr char digits[] = "0123456789ABCDEF";
        const auto n = counts[k].load(std::memory_order_relaxed);
        for (unsigned j = 0; j < 8; ++j)
            value[10 - j] = digits[(n >> (4 * j)) & 15];
        tb_host_write( value, sizeof(value) - 1);
    }
}
