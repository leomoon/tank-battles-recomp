/* Scaled Vulkan resources for an observed scene layout. The scaled raster
 * attachments stay outside the engine's guest address and alias tables. */
#include "render_scale.h"

static int tb_vk_color_blit(u32 source, tb_scale_rect source_rect, u32 dest,
                           tb_scale_rect dest_rect, VkFilter filter);

typedef struct {
    u32 handle;
    tb_scale_extent extent;
    u32 native;
    u64 serial, native_serial;
    int authoritative;
} tb_vk_scale_image;

typedef struct {
    tb_scale_layout layout;
    unsigned percent;
    u32 active_color;
    unsigned clear_mask;
    int stencil_known;
    int rasterized;
    tb_scale_size viewport_limit;
    float viewport_min, viewport_max;
    tb_vk_scale_image depth;
    tb_vk_scale_image color[TB_SCALE_MAX_ROUTES];
    tb_vk_scale_image display[TB_SCALE_MAX_ROUTES];
    u64 display_ready[TB_SCALE_MAX_ROUTES]; /* published destination serial */
    u64 resolve_source[TB_SCALE_MAX_ROUTES];
} tb_vk_scale_bank;

static tb_vk_scale_bank tb_scaled;
static int tb_scale_rejected;
static unsigned tb_scale_initial_percent;
static int tb_scale_internal;
static int tb_vk_scale_materialize(tb_vk_scale_image* image);
static int tb_vk_scale_restore(void);

__attribute__((weak)) int tb_settings_render_scale(void) { return 100; }
__attribute__((weak)) void tb_render_scale_fallback(void) {}

int tb_vk_scale_requested(void)
{
    if (s_vk.device && !tb_scale_initial_percent)
        tb_scale_initial_percent = (unsigned)tb_settings_render_scale();
    return !s_vk.device || tb_scale_rejected ? 100 : tb_settings_render_scale();
}

void tb_vk_scale_measure_present(unsigned effective_percent, unsigned width, unsigned height)
{
    /* Publish explicit zeroes as well as increments during diagnostics, so an
     * unused scaled/depth path is distinguishable from an older trace schema.
     * Recording is armed later than startup; do not cache a one-time flag. */
    if (tb_frame_budget_on())
        for (unsigned id=TB_FC_LOGICAL_DRAW; id<=TB_FC_FXAA_TEXELS; ++id)
            tb_frame_budget_count(id, 0);
    if (!tb_scale_initial_percent) tb_scale_initial_percent = (unsigned)tb_settings_render_scale();
    const int scaled = effective_percent != 100;
    tb_frame_budget_count(scaled ? TB_FC_SCALE_PRESENT_SCALED : TB_FC_SCALE_PRESENT_NATIVE, 1);
    if (tb_scale_initial_percent != 100 && !scaled)
        tb_frame_budget_count(TB_FC_SCALE_FALLBACK_PRESENT, 1);
    tb_frame_budget_mark(TB_FT_SCALE_STATE, tb_scale_initial_percent, effective_percent);
    if (scaled)
        tb_frame_budget_mark(TB_FT_SCALE_SCENE, tb_scaled.depth.extent.host.width, tb_scaled.depth.extent.host.height);
    tb_frame_budget_mark(TB_FT_SCALE_OUTPUT, width, height);
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
    if (tb_scaled.percent) {
        if (!tb_vk_scale_restore()) {
            VK_LOG("cannot reconstruct authoritative render-scale contents during retirement\n");
            abort();
        }
        tb_vk_scale_release(&tb_scaled);
    }
}

static void tb_vk_scale_reset(void)
{
    tb_vk_scale_discard();
    tb_scale_rejected = 0;
}

static void tb_vk_scale_fail(unsigned percent, const char* reason)
{
    tb_frame_budget_count(TB_FC_SCALE_RESOURCE_REJECT, 1);
    tb_vk_scale_discard();
    tb_scale_rejected = 1;
    VK_LOG("render scale %u%% unavailable (%s); using Native resolution\n", percent, reason);
    tb_render_scale_fallback();
}

void tb_vk_scale_unavailable(void)
{
    const int percent = tb_vk_scale_requested();
    if (tb_scale_non_native((unsigned)percent)) tb_vk_scale_fail((unsigned)percent, "scene layout not recognized");
}

static int tb_vk_scale_same_size(tb_scale_size a, tb_scale_size b)
{
    return a.width == b.width && a.height == b.height;
}

static tb_scale_size tb_vk_scale_limit(tb_vk_scale_bank* bank)
{
    VkPhysicalDeviceProperties properties;
    pvkGetPhysicalDeviceProperties(s_vk.phys, &properties);
    const VkPhysicalDeviceLimits* limits = &properties.limits;
    bank->viewport_limit = (tb_scale_size){limits->maxViewportDimensions[0], limits->maxViewportDimensions[1]};
    bank->viewport_min = limits->viewportBoundsRange[0];
    bank->viewport_max = limits->viewportBoundsRange[1];
    const u32 dimension = limits->maxImageDimension2D;
    tb_scale_size limit = {
        limits->maxFramebufferWidth < dimension ? limits->maxFramebufferWidth : dimension,
        limits->maxFramebufferHeight < dimension ? limits->maxFramebufferHeight : dimension
    };
    if (limits->maxViewportDimensions[0] < limit.width) limit.width = limits->maxViewportDimensions[0];
    if (limits->maxViewportDimensions[1] < limit.height) limit.height = limits->maxViewportDimensions[1];
    const VkFormat formats[] = {VK_FORMAT_R8G8B8A8_UNORM, s_vk.depth_format};
    VkFormatProperties color;
    pvkGetPhysicalDeviceFormatProperties(s_vk.phys, formats[0], &color);
    const VkFormatFeatureFlags blit = VK_FORMAT_FEATURE_BLIT_SRC_BIT | VK_FORMAT_FEATURE_BLIT_DST_BIT |
        VK_FORMAT_FEATURE_SAMPLED_IMAGE_FILTER_LINEAR_BIT;
    if ((color.optimalTilingFeatures & blit) != blit) return (tb_scale_size){0};
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
        image->serial=1; /* the allocator initialized every pixel */
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
    bank->depth.serial=1;
    zeta->guest_size = bank->depth.extent.guest;
    zeta->scale_percent = bank->percent;
    for (u32 i = 0; i < bank->layout.count; ++i) {
        if (!tb_vk_scale_color(&bank->color[i], &bank->layout.routes[i].color, bank->percent) ||
            !tb_vk_scale_color(&bank->display[i], &bank->layout.routes[i].display, bank->percent)) return 0;
        const u32 color = bank->color[i].handle;
        const tb_scale_extent extent = bank->color[i].extent;
        tb_scale_rect area;
        if (tb_scale_rect_map(extent, (tb_scale_rect){0, 0, extent.guest.width, extent.guest.height}, &area) != TB_SCALE_RECT_MAPPED)
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
    if (!tb_vk_scale_plan(&candidate, tb_vk_scale_limit(&candidate))) {
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

static tb_scale_extent tb_vk_scale_extent(const vk_eng_obj* image)
{
    const tb_scale_size host = {image->w, image->h};
    return (tb_scale_extent){image->scale_percent ? image->guest_size : host, host};
}

static VkRect2D tb_vk_scale_scissor(const vk_eng_obj* image, const u32 scissor[4])
{
    tb_scale_rect host;
    const tb_scale_rect clip = {scissor[0], scissor[1], scissor[2], scissor[3]};
    if (tb_scale_rect_clip_map(tb_vk_scale_extent(image), clip, &host) == TB_SCALE_RECT_INVALID)
        return (VkRect2D){0};
    return (VkRect2D){{(int32_t)host.x, (int32_t)host.y}, {host.width, host.height}};
}

static u32 tb_vk_scale_find_color(tb_scale_surface id)
{
    for (u32 i = 0; i < tb_scaled.layout.count; ++i)
        if (!memcmp(&id, &tb_scaled.layout.routes[i].color, sizeof id))
            return tb_scaled.color[i].handle;
    return 0;
}

static u32 tb_vk_scale_display_index(tb_scale_surface id)
{
    for (u32 i = 0; i < tb_scaled.layout.count; ++i)
        if (!memcmp(&id, &tb_scaled.layout.routes[i].display, sizeof id)) return i;
    return TB_SCALE_MAX_ROUTES;
}

#include "vulkan_scale_owner.inc"

int tb_vk_scale_resolve(tb_scale_surface color, tb_scale_surface display,
                        tb_scale_rect source, tb_scale_rect dest)
{
    const u32 route = tb_vk_scale_display_index(display);
    if (route == TB_SCALE_MAX_ROUTES) return 0;
    tb_scaled.display_ready[route] = 0;
    const u32 handle = tb_vk_scale_find_color(color);
    if (tb_scale_rejected || !tb_scaled.rasterized || !handle || handle != tb_scaled.active_color ||
        handle != tb_scaled.color[route].handle) return 0;
    const u64 serial=tb_scaled.color[route].serial;
    if (tb_scaled.resolve_source[route] && tb_scaled.resolve_source[route]!=serial) {
        tb_vk_scale_fallback("scene changed during tiled resolve");
        return 0;
    }
    tb_scaled.resolve_source[route]=serial;
    tb_vk_scale_image* output=&tb_scaled.display[route];
    if (output->native && !output->authoritative) {
        if (!tb_scale_copy(output->native,output->handle)) {
            tb_vk_scale_fallback("display initialization");
            return 0;
        }
        output->authoritative=1;
    }
    /* The proven title layout is exactly 2:1 at every preset, including all
     * six clipped tile edges. Linear samples each physical 2x2 footprint at
     * its centre (equal four-texel weights), without crossing tile boundaries.
     * Native continues through the exact NV3089 second-texel gather. */
    const int ok = tb_vk_color_blit(handle, source, tb_scaled.display[route].handle, dest, VK_FILTER_LINEAR);
    if (ok) {
        tb_vk_scale_wrote(tb_scaled.display[route].handle);
        tb_scale_rect host;
        if (tb_scale_rect_map(output->extent,dest,&host)==TB_SCALE_RECT_MAPPED)
            tb_frame_budget_count(TB_FC_SCALE_RESOLVE_BYTES,host.width*host.height*4u);
    }
    else tb_vk_scale_fallback("scaled resolve rejected");
    return ok;
}

void tb_vk_scale_publish(tb_scale_surface display, int complete)
{
    const u32 route = tb_vk_scale_display_index(display);
    if (route != TB_SCALE_MAX_ROUTES) {
        tb_scaled.display_ready[route] = complete && !tb_scale_rejected &&
            tb_scaled.resolve_source[route]==tb_scaled.color[route].serial
            ? tb_scaled.display[route].serial : 0;
        tb_scaled.resolve_source[route]=0;
    }
}

u32 tb_vk_scale_scanout(tb_scale_surface display)
{
    const u32 route = tb_vk_scale_display_index(display);
    return route != TB_SCALE_MAX_ROUTES && tb_scaled.display_ready[route] &&
        tb_scaled.display_ready[route]==tb_scaled.display[route].serial ? tb_scaled.display[route].handle : 0;
}

int tb_vk_scale_patch(tb_scale_surface surface, u32 native, tb_scale_rect rect, int pending)
{
    tb_vk_scale_image* owned=tb_scale_native_image(native);
    if (owned && owned->authoritative) return 1; /* upload callback applied the changed region */
    if (tb_scale_rejected) return 0;
    const u32 route = tb_vk_scale_display_index(surface);
    if (route != TB_SCALE_MAX_ROUTES) {
        if (!pending && !tb_scaled.display_ready[route]) return 1;
        return tb_vk_color_blit(native, rect, tb_scaled.display[route].handle, rect, VK_FILTER_NEAREST);
    }
    const u32 color = tb_vk_scale_find_color(surface);
    if (!color || color != tb_scaled.active_color) return 1;
    return tb_vk_color_blit(native, rect, color, rect, VK_FILTER_NEAREST);
}

void tb_vk_scale_skip_frame(void)
{
    tb_scaled.active_color = 0;
    tb_scaled.clear_mask = 0;
    tb_scaled.stencil_known = 0;
    tb_scaled.rasterized = 0;
}

int tb_vk_scale_bindings(tb_scale_surface color, tb_scale_surface depth, int stencil,
                        tb_scale_binding* binding)
{
    if (!tb_scaled.active_color || memcmp(&depth, &tb_scaled.layout.depth, sizeof depth) ||
        tb_vk_scale_find_color(color) != tb_scaled.active_color ||
        (tb_scaled.clear_mask & (TB_SCALE_CLEAR_COLOR | TB_SCALE_CLEAR_DEPTH)) !=
            (TB_SCALE_CLEAR_COLOR | TB_SCALE_CLEAR_DEPTH)) return 0;
    if (stencil && !tb_scaled.stencil_known) {
        tb_frame_budget_count(TB_FC_SCALE_SKIP_STENCIL, 1);
        tb_vk_scale_skip_frame();
        return 0;
    }
    const tb_scale_extent extent = tb_scaled.depth.extent;
    *binding = (tb_scale_binding){tb_scaled.active_color, tb_scaled.depth.handle, tb_scaled.percent,
        (float)extent.guest.width / extent.host.width, (float)extent.guest.height / extent.host.height};
    return 1;
}

void tb_vk_scale_clear(tb_scale_surface color, tb_scale_surface depth, tb_scale_rect rect,
                       unsigned mask, const float rgba[4], float z, unsigned stencil)
{
    if (!tb_scaled.percent || memcmp(&depth, &tb_scaled.layout.depth, sizeof depth)) return;
    const u32 color_handle = tb_vk_scale_find_color(color);
    if (!color_handle) return;
    const vk_eng_obj* image = vk_eng_get(color_handle, VK_ENG_COLOR);
    const vk_eng_obj* depth_image = vk_eng_get(tb_scaled.depth.handle, VK_ENG_DEPTH);
    if (!image || !depth_image) return;
    const tb_scale_extent extent = tb_vk_scale_extent(image);
    tb_scale_rect host;
    if (tb_scale_rect_map(extent, rect, &host) != TB_SCALE_RECT_MAPPED) return;

    if (color_handle != tb_scaled.active_color) {
        tb_scaled.active_color = color_handle;
        tb_scaled.clear_mask = 0;
        tb_scaled.rasterized = 0;
    }
    VkClearAttachment attachments[2] = {0};
    unsigned count = 0;
    if (mask & TB_SCALE_CLEAR_COLOR) {
        attachments[count].aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
        memcpy(attachments[count++].clearValue.color.float32, rgba, sizeof(float) * 4);
    }
    VkImageAspectFlags aspects = 0;
    if (mask & TB_SCALE_CLEAR_DEPTH) aspects |= VK_IMAGE_ASPECT_DEPTH_BIT;
    if (mask & TB_SCALE_CLEAR_STENCIL) aspects |= s_vk.depth_aspect & VK_IMAGE_ASPECT_STENCIL_BIT;
    if (aspects) {
        attachments[count].aspectMask = aspects;
        attachments[count++].clearValue.depthStencil = (VkClearDepthStencilValue){z, stencil};
    }
    if (!count) return;
    VkRenderPass pass = vk_eng_render_pass(image->fmt, 1);
    VkFramebuffer fb = pass ? vk_eng_framebuffer(pass, &color_handle, 1, depth_image->im.view,
                                                  tb_scaled.depth.handle, image->w, image->h) : VK_NULL_HANDLE;
    if (!fb || vk_begin()) {
        tb_vk_scale_skip_frame();
        return;
    }
    vk_barrier_image(image->im.img, VK_IMAGE_ASPECT_COLOR_BIT, VK_IMAGE_LAYOUT_GENERAL);
    vk_barrier_image(depth_image->im.img, s_vk.depth_aspect, VK_IMAGE_LAYOUT_GENERAL);
    const VkRenderPassBeginInfo begin = {
        .sType = VK_STRUCTURE_TYPE_RENDER_PASS_BEGIN_INFO, .renderPass = pass, .framebuffer = fb,
        .renderArea = {{0, 0}, {image->w, image->h}}
    };
    const VkClearRect clear = {
        .rect = {{(int32_t)host.x, (int32_t)host.y}, {host.width, host.height}}, .layerCount = 1
    };
    pvkCmdBeginRenderPass(s_vk.cmd, &begin, VK_SUBPASS_CONTENTS_INLINE);
    pvkCmdClearAttachments(s_vk.cmd, count, attachments, 1, &clear);
    pvkCmdEndRenderPass(s_vk.cmd);
    if (vk_submit_and_wait()) {
        tb_vk_scale_skip_frame();
        return;
    }
    if (mask & TB_SCALE_CLEAR_COLOR) tb_vk_scale_wrote(color_handle);
    if (mask & (TB_SCALE_CLEAR_DEPTH|TB_SCALE_CLEAR_STENCIL)) tb_vk_scale_wrote(tb_scaled.depth.handle);
    if (!host.x && !host.y && host.width == image->w && host.height == image->h) {
        tb_scaled.clear_mask |= mask;
        if (mask & TB_SCALE_CLEAR_STENCIL) tb_scaled.stencil_known = 1;
        if (mask & TB_SCALE_CLEAR_COLOR) tb_scaled.rasterized = 0;
    }
    if (tb_scale_host_image(color_handle)->authoritative) tb_scaled.rasterized=1;
}

static void tb_scale_capture_image(u32 handle,const char* path)
{
    FILE* existing = fopen(path, "rb");
    if (existing) { fclose(existing); return; }
    const vk_eng_obj* image = vk_eng_get(handle, VK_ENG_COLOR);
    if (!image || image->bpp != 4) return;
    const size_t pitch = (size_t)image->w * 4;
    u8* pixels = calloc(image->h, pitch);
    if (!pixels) return;
    vk_eng_readback(NULL, handle, 0, 0, image->w, image->h, pixels, (u32)pitch);
    FILE* file = fopen(path, "wb");
    if (file) {
        const size_t count = (size_t)image->w * image->h;
        for (size_t i = 0; i < count; ++i) memmove(pixels + 3*i, pixels + 4*i, 3);
        fprintf(file, "P6\n%u %u\n255\n", image->w, image->h);
        const int written = fwrite(pixels, 3, count, file) == count;
        if (fclose(file) || !written) remove(path);
    }
    free(pixels);
}

void tb_vk_scale_capture(tb_scale_surface color,const char* path)
{
    if (!tb_scaled.rasterized || tb_vk_scale_find_color(color)!=tb_scaled.active_color ||
        (tb_scaled.clear_mask & (TB_SCALE_CLEAR_COLOR|TB_SCALE_CLEAR_DEPTH))!=
            (TB_SCALE_CLEAR_COLOR|TB_SCALE_CLEAR_DEPTH)) return;
    tb_scale_capture_image(tb_scaled.active_color,path);
    for (u32 i=0;i<tb_scaled.layout.count;++i) if (tb_scaled.color[i].handle==tb_scaled.active_color) {
        const u32 display=tb_vk_scale_scanout(tb_scaled.layout.routes[i].display);
        char output[4096];
        const size_t length=strlen(path);
        if (display && length>4 && length<sizeof output-13) {
            snprintf(output,sizeof output,"%.*s-resolve.ppm",(int)(length-4),path);
            tb_scale_capture_image(display,output);
        }
    }
}
