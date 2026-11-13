#pragma once
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
void tb_vulkan_event(unsigned kind, uint32_t a, uint32_t b, uint32_t c);
void tb_execution_event(unsigned kind, uint32_t address, uint32_t image, uint32_t detail);
#ifdef __cplusplus
}
void tank_battles_vulkan_report();
void tank_battles_execution_report();
int tank_battles_execution_probe();
#endif
