#pragma once
#include <stdint.h>
#include "rendering_profile.h"
#ifdef __cplusplus
extern "C" {
#endif
void tb_rsx_texture_capture(uint32_t location, uint32_t offset, uint32_t format, uint32_t width, uint32_t height, uint32_t pitch, const void* raw, uint32_t raw_bytes, const void* decoded, uint32_t row_bytes, uint32_t rows);
void tb_vulkan_event(unsigned kind, uint32_t a, uint32_t b, uint32_t c);
void tb_execution_event(unsigned kind, uint32_t address, uint32_t image, uint32_t detail);
#ifdef __cplusplus
}
void tank_battles_vulkan_report();
void tank_battles_execution_report();
int tank_battles_execution_probe();
#endif
