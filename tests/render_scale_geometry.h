#pragma once
#include "sdk_overrides/render_scale.h"
#include "sdk_overrides/render_scale_shader.h"
#include <assert.h>

static void check_scale_geometry(void)
{
    const unsigned presets[] = {100, 150, 200, 300};
    const uint32_t scene_w[] = {2560, 3840, 5120, 7680};
    const uint32_t scene_h[] = {1440, 2160, 2880, 4320};
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

    char shader[256] = "input.position.xy = floor(input.position.xy / float2(2, 2)) + 0.5; _rsx_wpos(input.position)";
    char original[sizeof shader];
    memcpy(original, shader, sizeof shader);
    assert(tb_scale_wpos(shader, strlen(shader) + 1) == -1);
    assert(!strcmp(shader, original));
    assert(tb_scale_wpos(shader, sizeof shader) == 1);
    assert(strstr(shader, "fp_alpha.w > 0.0 ? fp_alpha.w : 1.0"));
    assert(strstr(shader, "/ float2(2, 2)) + 0.5; _rsx_wpos(input.position)"));
    strcpy(shader, "input.tc0.xy; rsx_tex[0].Sample(rsx_samp[0], input.tc0.xy)");
    strcpy(original, shader);
    assert(tb_scale_wpos(shader, sizeof shader) == 0);
    assert(!strcmp(shader, original));
}
