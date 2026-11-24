/* Exercise the real Vulkan allocator, framebuffer cache, views and rollback. */
#include <assert.h>

static PFN_vkAllocateMemory real_allocate_memory;
static unsigned allocations_before_failure;

static VKAPI_ATTR VkResult VKAPI_CALL fail_allocation(VkDevice device, const VkMemoryAllocateInfo* info,
                                                     const VkAllocationCallbacks* callbacks, VkDeviceMemory* memory)
{
    if (allocations_before_failure-- == 0) return VK_ERROR_OUT_OF_DEVICE_MEMORY;
    return real_allocate_memory(device, info, callbacks, memory);
}

static unsigned live_objects(void)
{
    unsigned count = 0;
    for (u32 i = 1; i < VK_ENG_MAX_OBJ; ++i) count += s_eobj[i].kind != VK_ENG_FREE;
    return count;
}

static void check_scaled_clear(u32 handle, tb_scale_extent extent)
{
    const tb_scale_size host = extent.host;
    const float red[] = {1, 0, 0, 1};
    vk_eng_clear_color(NULL, handle, red);
    u8 pixel[4];
    vk_eng_readback(NULL, handle, host.width - 1, host.height - 1, 1, 1, pixel, 4);
    assert(pixel[0] == 255 && pixel[1] == 0 && pixel[2] == 0 && pixel[3] == 255);
}

int main(void)
{
    const unsigned presets[] = {100, 150, 200, 300};
    const u32 scene_w[] = {2560, 3840, 5120, 7680};
    const u32 scene_h[] = {1440, 2160, 2880, 4320};
    const tb_scale_size limit = {8192, 8192};
    tb_scale_extent extent;
    for (unsigned i = 0; i < 4; ++i) {
        assert(tb_scale_extent_make((tb_scale_size){2560, 1440}, presets[i], limit, &extent));
        assert(extent.host.width == scene_w[i] && extent.host.height == scene_h[i]);
        assert(tb_scale_extent_make((tb_scale_size){1280, 720}, presets[i], limit, &extent));
        assert(extent.host.width * 2 == scene_w[i] && extent.host.height * 2 == scene_h[i]);
    }
    assert(!tb_scale_extent_make((tb_scale_size){0, 1}, 150, limit, &extent));
    assert(!tb_scale_extent_make((tb_scale_size){2560, 1440}, 300, (tb_scale_size){4096, 4096}, &extent));
    assert(!tb_scale_extent_make((tb_scale_size){UINT32_MAX, 1}, 300, (tb_scale_size){UINT32_MAX, UINT32_MAX}, &extent));
    assert(!tb_scale_extent_make((tb_scale_size){11, 7}, 175, limit, &extent));
    assert(tb_scale_extent_make((tb_scale_size){11, 7}, 150, limit, &extent));
    assert(extent.host.width == 17 && extent.host.height == 11);
    tb_scale_rect left, right;
    assert(tb_scale_rect_map(extent, (tb_scale_rect){0, 0, 7, 7}, &left));
    assert(tb_scale_rect_map(extent, (tb_scale_rect){7, 0, 4, 7}, &right));
    assert(left.width == right.x && right.x + right.width == 17 && right.height == 11);
    assert(!tb_scale_rect_map(extent, (tb_scale_rect){UINT32_MAX, 1, 2, 1}, &right));

    assert(!vk_init_all(16, 16, "render scale allocation fixture"));
    assert(!vk_eng_init(NULL, 16, 16));
    const u32 native = vk_eng_color_target_create(NULL, RSX_BE_FMT_R8G8B8A8, 33, 21, NULL, 0);
    const u32 native_depth = vk_eng_depth_target_create(NULL, 33, 21);
    assert(native && native_depth);
    const unsigned baseline = live_objects();
    tb_scale_layout group = {0};
    group.count = 3;
    group.depth = (tb_scale_surface){0, 0x1000, 44, 2, 1, {11, 7}};
    for (u32 i = 0; i < group.count; ++i) {
        group.routes[i].color = (tb_scale_surface){0, 0x2000+i*0x1000, 44, RSX_BE_FMT_R8G8B8A8, 2+i, {11, 7}};
        group.routes[i].display = (tb_scale_surface){0, 0x5000+i*0x1000, 24, RSX_BE_FMT_R8G8B8A8, 5+i, {6, 4}};
    }
    tb_vk_scale_prepare(&group, 100);
    assert(!tb_scaled.percent && live_objects() == baseline);
    for (unsigned i = 1; i < 4; ++i) {
        tb_vk_scale_prepare(&group, presets[i]);
        assert(tb_scaled.percent == presets[i] && live_objects() == baseline + 7);
        const u32 color = tb_scaled.color[0].handle;
        const u32 depth = tb_scaled.depth.handle;
        const u32 display = tb_scaled.display[0].handle;
        VkImage image = s_eobj[color].im.img;
        assert(s_eobj[color].guest_size.width == 11 && s_eobj[color].guest_size.height == 7);
        assert(s_eobj[display].w == (6 * presets[i] + 99) / 100 && s_eobj[display].h == 4 * presets[i] / 100);
        const u32 w = s_eobj[color].w, h = s_eobj[color].h;
        assert(w == (11 * presets[i] + 99) / 100 && h == (7 * presets[i] + 99) / 100);
        assert(s_eobj[depth].w == w && s_eobj[depth].h == h);
        tb_vk_scale_prepare(&group, presets[i]);
        assert(tb_scaled.color[0].handle == color && s_eobj[color].im.img == image);
        VkRenderPass pass = vk_eng_render_pass(s_eobj[color].fmt, 1);
        const u32 count = s_e2.fb_count;
        VkFramebuffer fb = vk_eng_framebuffer(pass, &color, 1, s_eobj[depth].im.view, depth, w, h);
        assert(fb && s_e2.fb_count == count);
        assert(!vk_eng_framebuffer(pass, &color, 1, s_eobj[depth].im.view, depth, w - 1, h));
        assert(!vk_eng_framebuffer(pass, &color, 1, s_eobj[native_depth].im.view, native_depth, w, h));
        assert(!vk_eng_framebuffer(pass, &native, 1, s_eobj[depth].im.view, depth, w, h));
        check_scaled_clear(color, tb_scaled.color[0].extent);
        const u32 view = vk_eng_surface_view(NULL, color, 0xaae4, 0x85);
        const u32 copy = vk_eng_color_snapshot(NULL, color);
        assert(view && copy && s_eobj[view].w == w && s_eobj[copy].w == w);
        assert(s_eobj[view].guest_size.width == 11 && s_eobj[copy].scale_percent == presets[i]);
        assert(vk_eng_surface_view(NULL, color, 0xaae4, 0x85) == view);
        vk_eng_texture_release(NULL, copy);
        ++group.routes[0].color.generation;
        tb_vk_scale_prepare(&group, presets[i]);
        assert(tb_scaled.layout.routes[0].color.generation == group.routes[0].color.generation);
        assert(live_objects() == baseline + 7);
    }

    tb_vk_scale_discard();
    assert(live_objects() == baseline);
    const u32 old_offset = group.routes[0].display.offset;
    group.routes[0].display.offset = group.routes[0].color.offset + 32;
    tb_vk_scale_prepare(&group, 150);
    assert(tb_scale_rejected && !tb_scaled.percent && live_objects() == baseline);
    group.routes[0].display.offset = old_offset;
    tb_vk_scale_reset();
    group.routes[0].color.size.width++;
    tb_vk_scale_prepare(&group, 150);
    assert(tb_scale_rejected && !tb_scaled.percent && live_objects() == baseline);
    group.routes[0].color.size.width--;
    tb_vk_scale_reset();
    real_allocate_memory = pvkAllocateMemory;
    allocations_before_failure = 4; // fail after the depth, scene/display pair and another scene image
    pvkAllocateMemory = fail_allocation;
    tb_vk_scale_prepare(&group, 150);
    pvkAllocateMemory = real_allocate_memory;
    assert(tb_scale_rejected && !tb_scaled.percent && live_objects() == baseline && s_e2.fb_count == 0);
    assert(vk_eng_get(native, VK_ENG_COLOR) && vk_eng_get(native_depth, VK_ENG_DEPTH));
    check_scaled_clear(native, (tb_scale_extent){{33, 21}, {33, 21}});
    tb_vk_scale_reset();
    tb_vk_scale_prepare(&group, 200);
    assert(tb_scaled.percent == 200);
    vk_eng_shutdown(NULL);
    assert(live_objects() == 0 && s_e2.fb_count == 0 && !tb_scaled.percent);
    puts("PASS: preset and odd extents, compatible framebuffers, clears, cache identity, partial allocation rollback and Native fallback");
    return 0;
}
