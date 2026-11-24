#include "render_scale_geometry.h"
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

static const float scale_black[] = {0, 0, 0, 1};
static const float scale_triangle[][4] = {
    {-1, -1, .5f, 1}, {3, -1, .5f, 1}, {-1, 3, .5f, 1}
};

static void scale_draw(const float vertices[][4], u32 count, float r, float g, float b, float ratio)
{
    const float alpha[] = {r, g, b, ratio};
    vk_eng_bind_ps_constants(NULL, alpha, sizeof alpha);
    vk_eng_draw(NULL, RSX_TOPOLOGY_TRIANGLES, vertices, count, 16, NULL, 0);
}

static void scale_pixel(u32 target, u32 x, u32 y, unsigned r, unsigned g, unsigned b)
{
    u8 pixel[4] = {0};
    vk_eng_readback(NULL, target, x, y, 1, 1, pixel, 4);
    if (pixel[0] != r || pixel[1] != g || pixel[2] != b) {
        fprintf(stderr, "target %u pixel %u,%u: %u,%u,%u expected %u,%u,%u\n",
                target, x, y, pixel[0], pixel[1], pixel[2], r, g, b);
        abort();
    }
}

static void check_scaled_batch_switches(u32 native, u32 native_z, u32 scaled, u32 scaled_z)
{
    static const char vs[] = "struct I { float4 p : POSITION; }; float4 main(I v) : SV_Position { return v.p; }";
    static const char ps[] = "cbuffer F : register(b1) { float4 color; }; float4 main() : SV_Target0 { return float4(color.rgb, 1); }";
    const rsx_vertex_layout_plan layout = {.count = 1, .stride = 16};
    const rsx_be_render_state state = {.color_mask = 0xffffffff, .depth_func = 0x207};
    const u32 pipe = vk_eng_pipeline_create(NULL, vs, ps, &state, &layout, 16, RSX_BE_FMT_R8G8B8A8, 1);
    assert(pipe);
    vk_eng_bind_pipeline(NULL, pipe);
    vk_eng_set_viewport(NULL, 0, 0, 11, 7);
    vk_eng_submit_and_wait(NULL, RSX_BE_FLUSH_GUEST_REFERENCE);
    // More than two complete batches, alternating attachments for each draw.
    // The old target-switch flush leaves just one draw pending and fails here.
    for (unsigned i = 0; i < 73; ++i) {
        const int right = i & 1;
        vk_eng_set_scissor(NULL, right ? 5 : 0, 0, right ? 6 : 5, 7);
        vk_eng_bind_targets(NULL, &native, 1, native_z);
        scale_draw(scale_triangle, 3, !right, 0, right, 0);
        vk_eng_bind_targets(NULL, &scaled, 1, scaled_z);
        scale_draw(scale_triangle, 3, right, !right, right, .5f);
        assert(s_eb_pending == (2 * (i + 1)) % 64);
        assert(tb_group_mirror_count == (i + 1) % 32);
    }
    scale_pixel(native, 2, 2, 255, 0, 0);
    scale_pixel(native, 8, 2, 0, 0, 255);
    scale_pixel(scaled, 4, 4, 0, 255, 0);
    scale_pixel(scaled, 16, 4, 255, 0, 255);
    // Texture reads of the previous attachment must keep the full dependency.
    static const char sampled_ps[] = "Texture2D rsx_tex[16] : register(t0); SamplerState rsx_samp[16] : register(s0); float4 main() : SV_Target0 { return rsx_tex[0].Sample(rsx_samp[0],float2(.25,.25)); }";
    const u32 sampled_pipe = vk_eng_pipeline_create(NULL, vs, sampled_ps, &state, &layout, 16, RSX_BE_FMT_R8G8B8A8, 1);
    const u32 targets[] = {native, scaled}, depths[] = {native_z, scaled_z};
    const u32 views[] = {vk_eng_surface_view(NULL, native, 0xaae4, 0x85),
                         vk_eng_surface_view(NULL, scaled, 0xaae4, 0x85)};
    assert(sampled_pipe && views[0] && views[1]);
    u32 textures[RSX_MAX_TEXTURES] = {0};
    rsx_be_sampler_desc samplers[RSX_MAX_TEXTURES] = {0};
    vk_eng_set_scissor(NULL, 0, 0, 11, 7);
    for (unsigned i = 0; i < 2; ++i) {
        vk_eng_bind_textures(NULL, textures, samplers, 0);
        vk_eng_bind_pipeline(NULL, pipe);
        vk_eng_bind_targets(NULL, &targets[i], 1, depths[i]);
        scale_draw(scale_triangle, 3, 0, !i, 1, i ? .5f : 0);
        textures[0] = views[i];
        vk_eng_bind_textures(NULL, textures, samplers, 1);
        vk_eng_bind_pipeline(NULL, sampled_pipe);
        vk_eng_bind_targets(NULL, &targets[1-i], 1, depths[1-i]);
        scale_draw(scale_triangle, 3, 0, 0, 0, i ? 0 : .5f);
        assert(s_eb_pending == 1);
        scale_pixel(targets[1-i], i ? 2 : 4, i ? 2 : 4, 0, i ? 0 : 255, 255);
    }
    vk_eng_bind_textures(NULL, textures, samplers, 0);
    // A Native draw may sample a queued mirror without changing its target.
    vk_eng_bind_pipeline(NULL, pipe);
    vk_eng_bind_targets(NULL, &native, 1, native_z);
    scale_draw(scale_triangle, 3, 1, 0, 0, 0);
    vk_eng_bind_targets(NULL, &scaled, 1, scaled_z);
    scale_draw(scale_triangle, 3, 0, 1, 1, .5f);
    assert(tb_group_mirror_count == 1);
    textures[0] = views[1];
    vk_eng_bind_textures(NULL, textures, samplers, 1);
    vk_eng_bind_pipeline(NULL, sampled_pipe);
    vk_eng_bind_targets(NULL, &native, 1, native_z);
    scale_draw(scale_triangle, 3, 0, 0, 0, 0);
    assert(!tb_group_mirror_count && s_eb_pending == 1);
    scale_pixel(native, 2, 2, 0, 255, 255);

    // Mutating a shared asset must replay old mirrors before its upload.
    const u32 asset = vk_eng_texture_create(NULL, RSX_BE_FMT_R8G8B8A8, 1, 1, 1, 1, 0xaae4, 0x85);
    const u8 red[] = {255, 0, 0, 255}, green[] = {0, 255, 0, 255};
    assert(asset);
    vk_eng_texture_upload(NULL, asset, 0, 0, 1, 1, red, 4, 1);
    for (unsigned half = 0; half < 2; ++half) {
        vk_eng_bind_textures(NULL, textures, samplers, 0);
        vk_eng_bind_pipeline(NULL, pipe);
        vk_eng_set_scissor(NULL, half ? 5 : 0, 0, half ? 6 : 5, 7);
        vk_eng_bind_targets(NULL, &native, 1, native_z);
        scale_draw(scale_triangle, 3, 0, 0, 0, 0);
        textures[0] = asset;
        vk_eng_bind_textures(NULL, textures, samplers, 1);
        vk_eng_bind_pipeline(NULL, sampled_pipe);
        vk_eng_bind_targets(NULL, &scaled, 1, scaled_z);
        scale_draw(scale_triangle, 3, 0, 0, 0, .5f);
        assert(tb_group_mirror_count == 1);
        if (!half) {
            vk_eng_texture_upload(NULL, asset, 0, 0, 1, 1, green, 4, 1);
            assert(!tb_group_mirror_count);
        }
    }
    scale_pixel(scaled, 4, 4, 255, 0, 0);
    scale_pixel(scaled, 16, 4, 0, 255, 0);
    vk_eng_bind_textures(NULL, textures, samplers, 0);
    vk_eng_texture_release(NULL, asset);
    vk_eng_texture_release(NULL, views[0]);
    vk_eng_texture_release(NULL, views[1]);
    vk_eng_pipeline_release(NULL, sampled_pipe);
    vk_eng_bind_targets(NULL, NULL, 0, 0);
    vk_eng_pipeline_release(NULL, pipe);
}

static void check_scaled_raster(const tb_scale_layout* group)
{
    static const char vs[] =
        "struct I { float4 p : POSITION; }; float4 main(I v) : SV_Position { return v.p; }";
    char ps[512] =
        "cbuffer F : register(b1) { float4 fp_alpha; };"
        "struct I { float4 position : SV_Position; };"
        "float4 _rsx_wpos(float4 p) { return p; }"
        "float4 main(I input) : SV_Target0 {"
        "  float4 p = _rsx_wpos(input.position);"
        "  p.xy = floor(input.position.xy / float2(2, 2)) + 0.5;"
        "  return float4(fp_alpha.r, fp_alpha.g * (p.x >= 1.5), fp_alpha.b, 1);"
        "}";
    assert(tb_scale_wpos(ps, sizeof ps) == 1);
    const rsx_vertex_layout_plan layout = {.count = 1, .stride = 16};
    rsx_be_render_state state = {
        .color_mask = 0xffffffff, .depth_test = 1, .depth_write = 1, .depth_func = 0x201
    };
    const u32 pipeline = vk_eng_pipeline_create(NULL, vs, ps, &state, &layout, 16, RSX_BE_FMT_R8G8B8A8, 1);
    const int stencil = !!(s_vk.depth_aspect & VK_IMAGE_ASPECT_STENCIL_BIT);
    state.stencil_enable = stencil;
    state.s_func = 0x202; // EQUAL
    state.s_func_mask = 0xff;
    state.depth_bounds_test = s_vk.depth_bounds_ok;
    const u32 depth_stencil = vk_eng_pipeline_create(NULL, vs, ps, &state, &layout, 16, RSX_BE_FMT_R8G8B8A8, 1);
    const u32 native = vk_eng_color_target_create(NULL, RSX_BE_FMT_R8G8B8A8, 11, 7, NULL, 0);
    const u32 native_z = vk_eng_depth_target_create(NULL, 11, 7);
    assert(pipeline && depth_stencil && native && native_z);

    // With this offset viewport, x spans guest pixels [2.10, 2.40].
    // Native has no sample there. The 200% image has a sample at 2.25.
    const float sliver[][4] = {
        {-.475f, -1, .5f, 1}, {-.4f, -1, .5f, 1}, {-.4f, 1, .5f, 1},
        {-.475f, -1, .5f, 1}, {-.4f, 1, .5f, 1}, {-.475f, 1, .5f, 1}
    };
    vk_eng_bind_pipeline(NULL, pipeline);
    vk_eng_set_viewport(NULL, 0, 1, 8, 5);
    vk_eng_set_scissor(NULL, 2, 2, 4, 2);
    vk_eng_bind_targets(NULL, &native, 1, native_z);
    vk_eng_clear_color(NULL, native, scale_black);
    vk_eng_clear_depth_stencil(NULL, native_z, RSX_BE_CLEAR_DEPTH, 1, 0);
    scale_draw(sliver, 6, 1, 0, 0, 0);
    u8 baseline[11 * 7 * 4];
    vk_eng_readback(NULL, native, 0, 0, 11, 7, baseline, 44);
    for (unsigned i = 0; i < sizeof baseline; ++i)
        assert(baseline[i] == (i % 4 == 3 ? 255 : 0));

    const tb_scale_surface color = group->routes[0].color, depth = group->depth;
    const tb_scale_rect whole = {0, 0, 11, 7};
    tb_scale_binding binding;
    assert(!tb_vk_scale_bindings(color, depth, 0, &binding));
    tb_vk_scale_clear(color, depth, whole, TB_SCALE_CLEAR_COLOR | TB_SCALE_CLEAR_DEPTH, scale_black, 1, 0);
    assert(!tb_vk_scale_bindings(color, depth, 1, &binding));
    assert(!tb_vk_scale_bindings(color, depth, 0, &binding));
    const unsigned all_clear = TB_SCALE_CLEAR_COLOR | TB_SCALE_CLEAR_DEPTH | TB_SCALE_CLEAR_STENCIL;
    tb_vk_scale_clear(color, depth, whole, all_clear, scale_black, 1, 3);
    assert(tb_vk_scale_bindings(color, depth, stencil, &binding));
    vk_eng_bind_targets(NULL, &binding.color, 1, binding.depth);
    scale_draw(sliver, 6, 1, 0, 0, .5f);
    for (u32 y = 3; y <= 8; ++y) {
        scale_pixel(binding.color, 4, y, y >= 4 && y < 8 ? 255 : 0, 0, 0);
        scale_pixel(binding.color, 5, y, 0, 0, 0);
    }

    const tb_scale_surface display = group->routes[0].display;
    const u32 scaled_display = tb_scaled.display[0].handle;
    const u32 native_display = vk_eng_color_target_create(NULL, RSX_BE_FMT_R8G8B8A8, 6, 4, NULL, 0);
    const float blue[] = {0, 0, 1, 1};
    assert(native_display);
    vk_eng_clear_color(NULL, native_display, scale_black);
    vk_eng_clear_color(NULL, scaled_display, scale_black);
    assert(tb_vk_scale_resolve(color, display, (tb_scale_rect){0, 0, 6, 7}, (tb_scale_rect){0, 0, 3, 4}));
    assert(!tb_vk_scale_scanout(display));
    assert(tb_vk_scale_resolve(color, display, (tb_scale_rect){6, 0, 5, 7}, (tb_scale_rect){3, 0, 3, 4}));
    tb_vk_scale_publish(display, 1);
    assert(tb_vk_scale_scanout(display) == scaled_display);
    tb_scale_surface obsolete = display;
    ++obsolete.generation;
    assert(!tb_vk_scale_scanout(obsolete));

    assert(tb_vk_scale_present(native_display, scaled_display));
    assert(tb_present_scaled.active && !tb_present_scaled.readback.ptr);
    setenv("TB_FRAME_HISTORY", "fixture", 1); // Request full output backing.
    assert(tb_vk_scale_present(native_display, scaled_display));
    const VkExtent2D output = tb_present_extent();
    assert(output.width == 12 && output.height == 8 && tb_present_scaled.readback.ptr);
    const u8* frame = tb_present_readback()->ptr;
    unsigned detail = 0;
    for (u32 i = 0; i < output.width * output.height; ++i) detail += frame[i * 4] != 0;
    assert(detail > 0); // The black Native image cannot produce this detail at any scale.
    scale_pixel(native_display, 2, 2, 0, 0, 0);

    // Replay a pitched guest upload without overwriting adjacent scaled pixels.
    const u8 guest_rows[] = {0, 0, 255, 255, 17, 17, 17, 17,
                             0, 255, 0, 255, 18, 18, 18, 18};
    const tb_scale_rect patch = {3, 1, 1, 2};
    vk_eng_color_target_write(NULL, native_display, patch.x, patch.y, patch.width, patch.height, guest_rows, 8);
    assert(tb_vk_scale_patch(display, native_display, patch, 0));
    scale_pixel(scaled_display, 6, 3, 0, 0, 255);
    scale_pixel(scaled_display, 7, 4, 0, 255, 0);
    scale_pixel(scaled_display, 8, 4, 0, 0, 0);
    scale_pixel(scaled_display, 2, 7, 0, 0, 0);

    // The overlapping transfer reads a snapshot. The source's lower row
    // must stay green even after its first row overwrites that destination.
    assert(tb_vk_color_blit(scaled_display, patch, scaled_display, (tb_scale_rect){3, 2, 1, 2}, VK_FILTER_NEAREST));
    scale_pixel(scaled_display, 6, 4, 0, 0, 255);
    scale_pixel(scaled_display, 6, 6, 0, 255, 0);
    assert(tb_vk_color_blit(scaled_display, (tb_scale_rect){3, 2, 1, 2},
                           native_display, (tb_scale_rect){0, 0, 1, 2}, VK_FILTER_LINEAR));
    scale_pixel(native_display, 0, 0, 0, 0, 255);
    scale_pixel(native_display, 0, 1, 0, 255, 0);

    tb_vk_scale_publish(display, 0);
    assert(!tb_vk_scale_scanout(display));
    vk_eng_clear_color(NULL, native_display, blue);
    vk_eng_present(NULL, native_display);
    assert(!tb_present_scaled.active && s_vk.last_center == 255);
    unsetenv("TB_FRAME_HISTORY");
    tb_present_image_shutdown();
    vk_eng_color_target_release(NULL, native_display);

    // Preserve guest fragment coordinates, stencil reference and depth bounds.
    vk_eng_set_viewport(NULL, 1, 0, 10, 7);
    vk_eng_set_scissor(NULL, 1, 0, UINT32_MAX, 7);
    vk_eng_bind_pipeline(NULL, depth_stencil);
    vk_eng_set_depth_bounds(NULL, 0, 1);
    vk_eng_set_stencil_ref(NULL, 2);
    scale_draw(scale_triangle, 3, 0, 0, 1, .5f);
    if (stencil) scale_pixel(binding.color, 6, 2, 0, 0, 0);
    tb_vk_scale_clear(color, depth, whole, all_clear, scale_black, 1, 3);
    vk_eng_set_stencil_ref(NULL, 3);
    scale_draw(scale_triangle, 3, 0, 1, 1, .5f);
    scale_pixel(binding.color, 3, 2, 0, 0, 255); // guest x = 1.75
    scale_pixel(binding.color, 4, 2, 0, 255, 255); // guest x = 2.25
    scale_pixel(binding.color, 21, 2, 0, 255, 255);
    scale_pixel(binding.color, 1, 2, 0, 0, 0);
    scale_draw(scale_triangle, 3, 1, 0, 0, .5f);
    scale_pixel(binding.color, 4, 2, 0, 255, 255); // equal depth fails LESS
    if (s_vk.depth_bounds_ok) {
        tb_vk_scale_clear(color, depth, (tb_scale_rect){2, 1, 1, 1},
                          TB_SCALE_CLEAR_DEPTH, scale_black, .75f, 0);
        vk_eng_set_depth_bounds(NULL, 0, .65f);
        scale_draw(scale_triangle, 3, 1, 0, 0, .5f);
        scale_pixel(binding.color, 4, 2, 0, 255, 255);
        vk_eng_set_depth_bounds(NULL, 0, 1);
        scale_draw(scale_triangle, 3, 1, 0, 0, .5f);
        scale_pixel(binding.color, 4, 2, 255, 0, 0);
        scale_pixel(binding.color, 6, 2, 0, 255, 255);
    }

    // At 10 guest pixels the slope is .02 per pixel. At 200% it is
    // .01 per host pixel, so -1.5 must move depth by .03, not .015.
    const float ramp[][4] = {{-1, -1, .3f, 1}, {3, -1, .7f, 1}, {-1, 3, .3f, 1}};
    const float raised[][4] = {{-1, -1, .322f, 1}, {3, -1, .722f, 1}, {-1, 3, .322f, 1}};
    tb_vk_scale_clear(color, depth, whole, all_clear, scale_black, 1, 3);
    vk_eng_bind_pipeline(NULL, pipeline);
    tb_vk_set_depth_bias(NULL, 0, 0, 24);
    scale_draw(ramp, 3, 1, 0, 0, .5f);
    tb_vk_set_depth_bias(NULL, -1.5f, 0, 24);
    scale_draw(raised, 3, 0, 1, 0, .5f);
    scale_pixel(binding.color, 6, 2, 0, 255, 0);
    tb_vk_set_depth_bias(NULL, 0, 0, 24);

    // A Native pass can immediately follow the scene and uses guest extents.
    vk_eng_bind_targets(NULL, &native, 1, native_z);
    vk_eng_readback(NULL, native, 0, 0, 11, 7, baseline, 44);
    for (unsigned i = 0; i < sizeof baseline; ++i)
        assert(baseline[i] == (i % 4 == 3 ? 255 : 0));
    vk_eng_bind_pipeline(NULL, pipeline);
    vk_eng_set_scissor(NULL, 0, 0, 11, 7);
    scale_draw(scale_triangle, 3, 0, 1, 1, 0);
    scale_pixel(native, 1, 2, 0, 0, 255);
    scale_pixel(native, 2, 2, 0, 255, 255);
    check_scaled_batch_switches(native, native_z, binding.color, binding.depth);
    vk_eng_bind_targets(NULL, NULL, 0, 0);
    vk_eng_pipeline_release(NULL, depth_stencil);
    vk_eng_pipeline_release(NULL, pipeline);
    vk_eng_depth_target_release(NULL, native_z);
    vk_eng_color_target_release(NULL, native);
}

int main(void)
{
    check_scale_geometry();
    const unsigned presets[] = {100, 150, 200, 300};
    setenv("PS3RECOMP_VK_GUEST_PROGRAMS", "1", 1);
    assert(!vk_init_all(16, 16, "render scale fixture"));
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
        if (presets[i] == 150) {
            const VkRect2D clipped = tb_vk_scale_scissor(&s_eobj[color], (u32[]){7, 1, UINT32_MAX, 3});
            assert(clipped.offset.x == 10 && clipped.offset.y == 1 &&
                   clipped.extent.width == 7 && clipped.extent.height == 5);
            tb_vk_scale_clear(group.routes[0].color, group.depth, (tb_scale_rect){7, 1, 4, 3},
                              TB_SCALE_CLEAR_COLOR, scale_black, 1, 0);
            scale_pixel(color, 9, 1, 255, 0, 0);
            scale_pixel(color, 10, 1, 0, 0, 0);
            scale_pixel(color, 16, 5, 0, 0, 0);
            scale_pixel(color, 16, 6, 255, 0, 0);
        }
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
    check_scaled_raster(&group);
    vk_eng_shutdown(NULL);
    assert(live_objects() == 0 && s_e2.fb_count == 0 && !tb_scaled.percent);
    puts("PASS: presets, pre-resolve detail, viewport/scissor, WPOS, depth, stencil, partial transfers, aliases and scaled presentation");
    return 0;
}
