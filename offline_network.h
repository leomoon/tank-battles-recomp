#pragma once
#include <cstdint>

void tank_battles_init_offline_network();
void tank_battles_register_offline_network();
void tank_battles_trace_socket(uint32_t nid, uint32_t result, uint32_t lr);
int tank_battles_probe_offline_network();
