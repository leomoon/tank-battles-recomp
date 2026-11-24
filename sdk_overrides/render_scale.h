#pragma once

#include <stdint.h>

#define TB_SCALE_MAX_ROUTES 8

typedef struct {
    uint32_t width, height;
} tb_scale_size;

typedef struct {
    tb_scale_size guest, host;
} tb_scale_extent;

typedef struct {
    uint32_t x, y, width, height;
} tb_scale_rect;

typedef struct {
    uint32_t location, offset, pitch, format, generation;
    tb_scale_size size;
} tb_scale_surface;

typedef struct {
    tb_scale_surface color, display;
} tb_scale_route;

typedef struct {
    tb_scale_surface depth;
    uint32_t count;
    tb_scale_route routes[TB_SCALE_MAX_ROUTES];
} tb_scale_layout;

static inline int tb_scale_extent_make(tb_scale_size guest, unsigned percent, tb_scale_size limit,
                                       tb_scale_extent* extent)
{
    if (!guest.width || !guest.height ||
        (percent != 100 && percent != 150 && percent != 200 && percent != 300)) return 0;
    const uint64_t width = ((uint64_t)guest.width * percent + 99) / 100;
    const uint64_t height = ((uint64_t)guest.height * percent + 99) / 100;
    if (width > limit.width || height > limit.height) return 0;
    *extent = (tb_scale_extent){guest, {(uint32_t)width, (uint32_t)height}};
    return 1;
}

/* Map both edges on the allocation's actual ratio. Adjacent partial regions
 * share the same edge, including when an odd extent was rounded up. */
static inline int tb_scale_rect_map(tb_scale_extent extent, tb_scale_rect guest, tb_scale_rect* host)
{
    if (!extent.guest.width || !extent.guest.height || !guest.width || !guest.height ||
        (uint64_t)guest.x + guest.width > extent.guest.width ||
        (uint64_t)guest.y + guest.height > extent.guest.height) return 0;
    const uint32_t x = (uint64_t)guest.x * extent.host.width / extent.guest.width;
    const uint32_t y = (uint64_t)guest.y * extent.host.height / extent.guest.height;
    const uint32_t right = (uint64_t)(guest.x + guest.width) * extent.host.width / extent.guest.width;
    const uint32_t bottom = (uint64_t)(guest.y + guest.height) * extent.host.height / extent.guest.height;
    *host = (tb_scale_rect){x, y, right - x, bottom - y};
    return host->width && host->height;
}

int tb_vk_scale_requested(void);
void tb_vk_scale_measure_present(unsigned effective_percent, unsigned width, unsigned height);
void tb_vk_scale_discard(void);
void tb_vk_scale_prepare(const tb_scale_layout* layout, unsigned percent);
void tb_vk_scale_unavailable(void);

/* Read/mirror bindings use the guest identity, never an engine slot or a
 * Vulkan handle that might have been recycled for a different allocation. */
typedef struct {
    uint32_t color, depth, percent;
} tb_scale_binding;
enum {
    TB_SCALE_CLEAR_COLOR = 1,
    TB_SCALE_CLEAR_DEPTH = 2,
    TB_SCALE_CLEAR_STENCIL = 4
};

void tb_vk_scale_clear(tb_scale_surface color, tb_scale_surface depth, tb_scale_rect rect,
                       unsigned mask, const float rgba[4], float z, unsigned stencil);
int tb_vk_scale_bindings(tb_scale_surface color, tb_scale_surface depth, int stencil,
                        tb_scale_binding* binding);
void tb_vk_scale_skip_frame(void);
void tb_vk_scale_capture(tb_scale_surface color, const char* path);
int tb_vk_scale_resolve(tb_scale_surface color, tb_scale_surface display,
                        tb_scale_rect source, tb_scale_rect dest);
void tb_vk_scale_publish(tb_scale_surface display, int complete);
uint32_t tb_vk_scale_scanout(tb_scale_surface display);
int tb_vk_scale_patch(tb_scale_surface surface, uint32_t native, tb_scale_rect rect, int pending);
int tb_vk_scale_present(uint32_t native, uint32_t scaled);
