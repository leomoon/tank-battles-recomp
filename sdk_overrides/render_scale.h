#pragma once

#include <stdint.h>
#include <float.h>

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

static inline int tb_scale_percent_valid(unsigned percent)
{
    return percent == 50 || percent == 75 || percent == 100 ||
           percent == 150 || percent == 200 || percent == 300;
}

static inline int tb_scale_non_native(unsigned percent)
{
    return tb_scale_percent_valid(percent) && percent != 100;
}

static inline int tb_scale_extent_make(tb_scale_size guest, unsigned percent, tb_scale_size limit,
                                       tb_scale_extent* extent)
{
    if (!extent || !guest.width || !guest.height || !tb_scale_percent_valid(percent)) return 0;
    const uint64_t width = ((uint64_t)guest.width * percent + 99) / 100;
    const uint64_t height = ((uint64_t)guest.height * percent + 99) / 100;
    if (width > limit.width || height > limit.height) return 0;
    *extent = (tb_scale_extent){guest, {(uint32_t)width, (uint32_t)height}};
    return 1;
}

enum { TB_SCALE_RECT_INVALID = 0, TB_SCALE_RECT_MAPPED = 1, TB_SCALE_RECT_EMPTY = 2 };

/* Map both edges on the allocation's actual ratio. Adjacent partial regions
 * share the same edge, including when an odd extent was rounded up. EMPTY is
 * a valid no-op, including a nonempty guest region with no host pixels. */
static inline int tb_scale_rect_map(tb_scale_extent extent, tb_scale_rect guest, tb_scale_rect* host)
{
    if (!host || !extent.guest.width || !extent.guest.height || !extent.host.width || !extent.host.height ||
        (uint64_t)guest.x + guest.width > extent.guest.width ||
        (uint64_t)guest.y + guest.height > extent.guest.height) return 0;
    const uint32_t x = (uint64_t)guest.x * extent.host.width / extent.guest.width;
    const uint32_t y = (uint64_t)guest.y * extent.host.height / extent.guest.height;
    const uint32_t right = (uint64_t)(guest.x + guest.width) * extent.host.width / extent.guest.width;
    const uint32_t bottom = (uint64_t)(guest.y + guest.height) * extent.host.height / extent.guest.height;
    *host = (tb_scale_rect){x, y, right - x, bottom - y};
    return host->width && host->height ? TB_SCALE_RECT_MAPPED : TB_SCALE_RECT_EMPTY;
}

/* Clip before mapping. Use subtraction rather than overflowing x + width. */
static inline int tb_scale_rect_clip_map(tb_scale_extent extent, tb_scale_rect rect, tb_scale_rect* host)
{
    const uint32_t x = rect.x < extent.guest.width ? rect.x : extent.guest.width;
    const uint32_t y = rect.y < extent.guest.height ? rect.y : extent.guest.height;
    const uint32_t w = extent.guest.width - x, h = extent.guest.height - y;
    return tb_scale_rect_map(extent, (tb_scale_rect){x, y,
        rect.width < w ? rect.width : w, rect.height < h ? rect.height : h}, host);
}

static inline int tb_scale_viewport_map(tb_scale_extent extent, const float guest[4], float host[4])
{
    if (!guest || !host || !extent.guest.width || !extent.guest.height ||
        !extent.host.width || !extent.host.height || !(guest[2] > 0) || !(guest[3] > 0)) return 0;
    float mapped[4];
    for (unsigned i = 0; i < 4; ++i) {
        const double value = (double)guest[i] * (i & 1 ? extent.host.height : extent.host.width) /
                             (i & 1 ? extent.guest.height : extent.guest.width);
        if (!(value >= -FLT_MAX && value <= FLT_MAX)) return 0;
        mapped[i] = (float)value;
    }
    if (!(mapped[2] > 0) || !(mapped[3] > 0)) return 0;
    for (unsigned i = 0; i < 4; ++i) host[i] = mapped[i];
    return 1;
}

static inline int tb_scale_depth_slope(tb_scale_extent extent, float guest, float* host)
{
    if (!host || !extent.guest.width || !extent.guest.height || !extent.host.width || !extent.host.height ||
        !(guest >= -FLT_MAX && guest <= FLT_MAX)) return 0;
    /* Vulkan exposes one slope factor. Unequal rounded X/Y ratios cannot
     * preserve max(abs(dz/dx), abs(dz/dy)) for every primitive: use Native
     * for a nonzero slope instead of silently over/under-biasing it. */
    if (guest && (uint64_t)extent.host.width * extent.guest.height !=
                 (uint64_t)extent.host.height * extent.guest.width) return 0;
    const double value = (double)guest * extent.host.width / extent.guest.width;
    if (!(value >= -FLT_MAX && value <= FLT_MAX)) return 0;
    *host = (float)value;
    return 1;
}

static inline int tb_scale_viewport_fits(const float host[4], tb_scale_size maximum, float lower, float upper)
{
    return host && host[2] > 0 && host[2] <= maximum.width && host[3] > 0 && host[3] <= maximum.height &&
        host[0] >= lower && host[1] >= lower &&
        (double)host[0] + host[2] <= upper && (double)host[1] + host[3] <= upper;
}

int tb_vk_scale_requested(void);
void tb_vk_scale_measure_present(unsigned effective_percent, unsigned width, unsigned height);
void tb_vk_scale_discard(void);
void tb_vk_scale_prepare(const tb_scale_layout* layout, unsigned percent);
void tb_vk_scale_unavailable(void);
void tb_vk_scale_attach(tb_scale_surface surface, uint32_t native, int depth);
int tb_vk_scale_native(uint32_t native);
uint32_t tb_vk_scale_sample(uint32_t native);
void tb_vk_scale_fallback(const char* reason);
void tb_vk_scale_releasing(uint32_t native);
int tb_vk_scale_draw_ready(uint32_t color);
void tb_vk_scale_wrote(uint32_t scaled);
void tb_vk_scale_native_mutation(uint32_t native, const char* reason);
int tb_vk_scale_uploaded(uint32_t native, tb_scale_rect rect);
int tb_vk_scale_tracked(uint32_t native);
int tb_vk_scale_depth_upload(uint32_t native, tb_scale_rect rect, const uint8_t* packed, uint32_t pitch);

/* Read/mirror bindings use the guest identity, never an engine slot or a
 * Vulkan handle that might have been recycled for a different allocation. */
typedef struct {
    uint32_t color, depth, percent;
    float guest_per_host_x, guest_per_host_y;
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
int tb_vk_scale_use(tb_scale_surface color, tb_scale_surface depth, tb_scale_binding* binding);
void tb_vk_scale_skip_frame(void);
void tb_vk_scale_capture(tb_scale_surface color, const char* path);
int tb_vk_scale_resolve(tb_scale_surface color, tb_scale_surface display,
                        tb_scale_rect source, tb_scale_rect dest);
void tb_vk_scale_publish(tb_scale_surface display, int complete);
uint32_t tb_vk_scale_scanout(tb_scale_surface display);
int tb_vk_scale_patch(tb_scale_surface surface, uint32_t native, tb_scale_rect rect, int pending);
int tb_vk_scale_present(uint32_t native, uint32_t scaled);
