/* Reserved Vulkan resources for an observed scene layout. The live renderer
 * keeps its native handles until draw and resolve both support scaled images.
 * Reservations never enter the draw engine's guest address or alias tables. */
#include "render_scale.h"

typedef struct {
    u32 handle;
    tb_scale_extent extent;
} tb_vk_scale_image;

typedef struct {
    tb_scale_layout layout;
    unsigned percent;
    tb_vk_scale_image depth;
    tb_vk_scale_image color[TB_SCALE_MAX_ROUTES];
    tb_vk_scale_image display[TB_SCALE_MAX_ROUTES];
} tb_vk_scale_bank;

static tb_vk_scale_bank tb_scaled;
static int tb_scale_rejected;

__attribute__((weak)) int tb_settings_render_scale(void) { return 100; }
__attribute__((weak)) void tb_render_scale_fallback(void) {}

int tb_vk_scale_requested(void)
{
    return !s_vk.device || tb_scale_rejected ? 100 : tb_settings_render_scale();
}

static void tb_vk_scale_release(tb_vk_scale_bank* bank)
{
    for (u32 i = 0; i < bank->layout.count; ++i) {
        vk_eng_color_target_release(NULL, bank->display[i].handle);
        vk_eng_color_target_release(NULL, bank->color[i].handle);
    }
    vk_eng_depth_target_release(NULL, bank->depth.handle);
    memset(bank, 0, sizeof *bank);
}

void tb_vk_scale_discard(void)
{
    if (tb_scaled.percent) tb_vk_scale_release(&tb_scaled);
}

static void tb_vk_scale_reset(void)
{
    tb_vk_scale_discard();
    tb_scale_rejected = 0;
}

static void tb_vk_scale_fail(unsigned percent, const char* reason)
{
    tb_vk_scale_discard();
    tb_scale_rejected = 1;
    VK_LOG("render scale %u%% unavailable (%s); using Native resolution\n", percent, reason);
    tb_render_scale_fallback();
}

void tb_vk_scale_unavailable(void)
{
    const int percent = tb_vk_scale_requested();
    if (percent > 100) tb_vk_scale_fail((unsigned)percent, "scene layout not recognized");
}

static int tb_vk_scale_same_size(tb_scale_size a, tb_scale_size b)
{
    return a.width == b.width && a.height == b.height;
}

static tb_scale_size tb_vk_scale_limit(void)
{
    VkPhysicalDeviceProperties properties;
    pvkGetPhysicalDeviceProperties(s_vk.phys, &properties);
    const VkPhysicalDeviceLimits* limits = &properties.limits;
    const u32 dimension = limits->maxImageDimension2D;
    tb_scale_size limit = {
        limits->maxFramebufferWidth < dimension ? limits->maxFramebufferWidth : dimension,
        limits->maxFramebufferHeight < dimension ? limits->maxFramebufferHeight : dimension
    };
    const VkFormat formats[] = {VK_FORMAT_R8G8B8A8_UNORM, s_vk.depth_format};
    const VkImageUsageFlags usages[] = {
        VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT | VK_IMAGE_USAGE_SAMPLED_BIT |
            VK_IMAGE_USAGE_TRANSFER_SRC_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT,
        VK_IMAGE_USAGE_DEPTH_STENCIL_ATTACHMENT_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT
    };
    for (unsigned i = 0; i < 2; ++i) {
        VkImageFormatProperties supported;
        if (pvkGetPhysicalDeviceImageFormatProperties(s_vk.phys, formats[i], VK_IMAGE_TYPE_2D,
                VK_IMAGE_TILING_OPTIMAL, usages[i], 0, &supported) != VK_SUCCESS ||
            !(supported.sampleCounts & VK_SAMPLE_COUNT_1_BIT)) return (tb_scale_size){0};
        if (supported.maxExtent.width < limit.width) limit.width = supported.maxExtent.width;
        if (supported.maxExtent.height < limit.height) limit.height = supported.maxExtent.height;
    }
    return limit;
}

static int tb_vk_scale_disjoint(const tb_scale_layout* layout)
{
    const tb_scale_surface* surfaces[1 + 2 * TB_SCALE_MAX_ROUTES] = {&layout->depth};
    for (u32 i = 0; i < layout->count; ++i) {
        surfaces[1 + 2*i] = &layout->routes[i].color;
        surfaces[2 + 2*i] = &layout->routes[i].display;
    }
    for (u32 i = 0; i <= 2 * layout->count; ++i) {
        const tb_scale_surface* a = surfaces[i];
        const u64 end = (u64)a->offset + (u64)a->pitch * a->size.height;
        if (!a->generation || !a->pitch || end > (1ull << 32)) return 0;
        for (u32 j = 0; j < i; ++j) {
            const tb_scale_surface* b = surfaces[j];
            const u64 other_end = (u64)b->offset + (u64)b->pitch * b->size.height;
            if (a->location == b->location && a->offset < other_end && b->offset < end) return 0;
        }
    }
    return 1;
}

static int tb_vk_scale_plan(tb_vk_scale_bank* bank, tb_scale_size limit)
{
    const tb_scale_layout* layout = &bank->layout;
    if (layout->count == 0 || layout->count > TB_SCALE_MAX_ROUTES || !tb_vk_scale_disjoint(layout) ||
        !tb_scale_extent_make(layout->depth.size, bank->percent, limit, &bank->depth.extent)) return 0;
    for (u32 i = 0; i < layout->count; ++i) {
        const tb_scale_route* route = &layout->routes[i];
        if (route->color.format != RSX_BE_FMT_R8G8B8A8 || route->display.format != RSX_BE_FMT_R8G8B8A8 ||
            !tb_vk_scale_same_size(route->color.size, layout->depth.size) ||
            !tb_scale_extent_make(route->color.size, bank->percent, limit, &bank->color[i].extent) ||
            !tb_scale_extent_make(route->display.size, bank->percent, limit, &bank->display[i].extent)) return 0;
    }
    return 1;
}

static u32 tb_vk_scale_color(tb_vk_scale_image* image, const tb_scale_surface* surface, unsigned percent)
{
    const tb_scale_size host = image->extent.host;
    image->handle = vk_eng_color_target_create(NULL, (rsx_be_format)surface->format,
                                               host.width, host.height, NULL, 0);
    if (image->handle) {
        s_eobj[image->handle].guest_size = image->extent.guest;
        s_eobj[image->handle].scale_percent = percent;
    }
    return image->handle;
}

static int tb_vk_scale_allocate(tb_vk_scale_bank* bank)
{
    const tb_scale_size depth = bank->depth.extent.host;
    bank->depth.handle = vk_eng_depth_target_create(NULL, depth.width, depth.height);
    vk_eng_obj* zeta = vk_eng_get(bank->depth.handle, VK_ENG_DEPTH);
    if (!zeta) return 0;
    zeta->guest_size = bank->depth.extent.guest;
    zeta->scale_percent = bank->percent;
    for (u32 i = 0; i < bank->layout.count; ++i) {
        if (!tb_vk_scale_color(&bank->color[i], &bank->layout.routes[i].color, bank->percent) ||
            !tb_vk_scale_color(&bank->display[i], &bank->layout.routes[i].display, bank->percent)) return 0;
        const u32 color = bank->color[i].handle;
        const tb_scale_extent extent = bank->color[i].extent;
        tb_scale_rect area;
        if (!tb_scale_rect_map(extent, (tb_scale_rect){0, 0, extent.guest.width, extent.guest.height}, &area))
            return 0;
        VkRenderPass pass = vk_eng_render_pass(s_eobj[color].fmt, 1);
        if (!pass || !vk_eng_framebuffer(pass, &color, 1, zeta->im.view, bank->depth.handle,
                                        area.width, area.height)) return 0;
    }
    return 1;
}

void tb_vk_scale_prepare(const tb_scale_layout* layout, unsigned percent)
{
    if (percent == 100) { tb_vk_scale_discard(); return; }
    if (tb_scale_rejected) return;
    if (tb_scaled.percent == percent && !memcmp(&tb_scaled.layout, layout, sizeof *layout)) return;

    // The existing native images, bindings and contents stay available on every failure path.
    tb_vk_scale_discard();
    tb_vk_scale_bank candidate = {0};
    candidate.layout = *layout;
    candidate.percent = percent;
    if (!tb_vk_scale_plan(&candidate, tb_vk_scale_limit())) {
        tb_vk_scale_fail(percent, "incompatible attachments or Vulkan dimension limits");
        return;
    }
    if (!tb_vk_scale_allocate(&candidate)) {
        tb_vk_scale_release(&candidate);
        tb_vk_scale_fail(percent, "Vulkan resource allocation failed");
        return;
    }
    tb_scaled = candidate;
    VK_LOG("render scale %u%% resources staged: %ux%u scene, %ux%u resolve, %u routes\n",
           percent, candidate.depth.extent.host.width, candidate.depth.extent.host.height,
           candidate.display[0].extent.host.width, candidate.display[0].extent.host.height, layout->count);
}
