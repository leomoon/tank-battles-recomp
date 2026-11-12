#pragma once
#include <stdint.h>
struct ppu_context;
using tank_battles_override_fn = void (*)(ppu_context*);
// Register only during startup, before any guest threads are dispatched.
void tank_battles_register_overrides();
void tank_battles_register_override(uint32_t nid, tank_battles_override_fn handler);
bool tank_battles_dispatch_override(uint32_t nid, ppu_context* context);
#ifdef TB_DIAGNOSTIC_TESTS
bool tank_battles_verify_test_override();
#endif
