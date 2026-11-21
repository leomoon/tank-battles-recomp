#pragma once
#include <stdint.h>
#include "rendering_profile.h"
#ifdef __cplusplus
extern "C" {
#endif
void tb_rsx_memory_written(uint32_t location, uint32_t offset, uint32_t bytes);
void tb_scanout_sample(uint32_t buffer, uint32_t surface, uint32_t offset);
void tb_frame_history(const void* rgba, uint32_t width, uint32_t height, uint32_t present);
void tb_rsx_transfer_trace(uint32_t subchannel, uint32_t method, uint32_t arg);
void tb_frame_order(uint32_t buffer, uint32_t offset, uint64_t write_serial, uint32_t present);
void tb_frame_event(uint32_t kind,uint32_t a,uint32_t b,uint32_t c,uint32_t d);
void tb_fps_guest_flip(void);
uint64_t tb_fps_guest_serial(void);
void tb_fps_present(void);
void tb_fps_toggle(void);
#define TB_OVERLAY_MAX_SCALE 6u
#define TB_OVERLAY_BUFFER_SIZE (224u * 134u * TB_OVERLAY_MAX_SCALE * TB_OVERLAY_MAX_SCALE * 4u)
typedef struct tb_overlay_region { uint32_t x, y, width, height; } tb_overlay_region;
// Writes a tightly packed rectangle into TB_OVERLAY_BUFFER_SIZE bytes of storage.
int tb_draw_overlay(void* pixels,uint32_t width,uint32_t height,int bgra,tb_overlay_region* region);
void tb_keyboard_down(uint32_t scancode);
uint32_t tb_keyboard_take(void);
uint32_t tb_keyboard_peek(void);
void tb_keyboard_ack(uint32_t mask);
void tb_input_sample(uint32_t kind, uint32_t port, uint32_t buttons);
void tb_resource_read(int host_fd, uint64_t offset, const void* data, uint64_t size, uint32_t ea);
void tb_resource_hle(uint32_t nid);
void tb_rsx_texture_capture(uint32_t location, uint32_t offset, uint32_t format, uint32_t width, uint32_t height, uint32_t pitch, const void* raw, uint32_t raw_bytes, const void* decoded, uint32_t row_bytes, uint32_t rows);
void tb_vulkan_event(unsigned kind, uint32_t a, uint32_t b, uint32_t c);
void tb_execution_event(unsigned kind, uint32_t address, uint32_t image, uint32_t detail);
#ifdef __cplusplus
}
void tank_battles_vulkan_report();
void tank_battles_execution_report();
int tank_battles_execution_probe();
#endif
