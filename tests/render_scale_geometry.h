#pragma once
#include "sdk_overrides/render_scale.h"
#include "sdk_overrides/render_scale_shader.h"
#include <assert.h>

static void check_scale_geometry(void)
{
    const unsigned presets[] = {50, 75, 100, 150, 200, 300};
    const uint32_t scene_w[] = {1280, 1920, 2560, 3840, 5120, 7680};
    const uint32_t scene_h[] = {720, 1080, 1440, 2160, 2880, 4320};
    const uint32_t odd_w[] = {6, 9, 11, 17, 22, 33};
    const uint32_t odd_h[] = {4, 6, 7, 11, 14, 21};
    const tb_scale_size limit = {8192, 8192};
    tb_scale_extent extent;
    for (unsigned i = 0; i < 6; ++i) {
        assert(tb_scale_percent_valid(presets[i]));
        assert(tb_scale_non_native(presets[i]) == (presets[i] != 100));
        assert(tb_scale_extent_make((tb_scale_size){2560, 1440}, presets[i], limit, &extent));
        assert(extent.host.width == scene_w[i] && extent.host.height == scene_h[i]);
        const tb_scale_extent backing = extent;
        float slope = 99;
        assert(tb_scale_depth_slope(extent, 2, &slope));
        assert(slope == 2.0f * presets[i] / 100);
        assert(tb_scale_depth_slope(extent, -2, &slope));
        assert(slope == -2.0f * presets[i] / 100);
        assert(tb_scale_extent_make((tb_scale_size){1280, 720}, presets[i], limit, &extent));
        assert(extent.host.width * 2 == scene_w[i] && extent.host.height * 2 == scene_h[i]);
        uint64_t area = 0;
        /* Six NV3089 tiles, including the 256-wide and 208-high edges.
         * The corresponding source footprints retain the 2x2 sample grid. */
        for (uint32_t y = 0; y < 720; y += 512) {
            uint32_t edge = 0;
            for (uint32_t x = 0; x < 1280; x += 512) {
                const tb_scale_rect tile = {x, y, x == 1024 ? 256 : 512, y == 512 ? 208 : 512};
                tb_scale_rect out, source;
                assert(tb_scale_rect_map(extent, tile, &out) == TB_SCALE_RECT_MAPPED);
                assert(tb_scale_rect_map(backing, (tb_scale_rect){2*x, 2*y, 2*tile.width, 2*tile.height}, &source) == TB_SCALE_RECT_MAPPED);
                assert(out.x == edge && source.x == 2*out.x && source.y == 2*out.y);
                assert(source.width == 2*out.width && source.height == 2*out.height);
                edge = out.x + out.width;
                area += (uint64_t)out.width * out.height;
            }
            assert(edge == extent.host.width);
        }
        assert(area == (uint64_t)extent.host.width * extent.host.height);
        assert(tb_scale_extent_make((tb_scale_size){11, 7}, presets[i], limit, &extent));
        assert(extent.host.width == odd_w[i] && extent.host.height == odd_h[i]);
        area = 0;
        for (uint32_t y = 0; y < 7; ++y) {
            uint32_t edge = 0;
            for (uint32_t x = 0; x < 11; ++x) {
                tb_scale_rect pixel;
                const int result = tb_scale_rect_map(extent, (tb_scale_rect){x, y, 1, 1}, &pixel);
                assert(result == ((pixel.width && pixel.height) ? TB_SCALE_RECT_MAPPED : TB_SCALE_RECT_EMPTY));
                assert(pixel.x == edge);
                edge = pixel.x + pixel.width;
                area += (uint64_t)pixel.width * pixel.height;
            }
            assert(edge == extent.host.width);
        }
        assert(area == (uint64_t)extent.host.width * extent.host.height);
        tb_scale_rect clipped, expected;
        assert(tb_scale_rect_map(extent, (tb_scale_rect){7, 1, 4, 3}, &expected) == TB_SCALE_RECT_MAPPED);
        assert(tb_scale_rect_clip_map(extent, (tb_scale_rect){7, 1, UINT32_MAX, 3}, &clipped) == TB_SCALE_RECT_MAPPED);
        assert(!memcmp(&clipped, &expected, sizeof clipped));
        assert(tb_scale_rect_clip_map(extent, (tb_scale_rect){UINT32_MAX, 1, UINT32_MAX, 3}, &clipped) == TB_SCALE_RECT_EMPTY);
        assert(clipped.x == extent.host.width && clipped.width == 0);
        const float viewport[] = {-1.25f, .5f, 8.5f, 5.25f};
        float mapped[4];
        assert(tb_scale_viewport_map(extent, viewport, mapped));
        assert(mapped[0] == (float)(-1.25 * odd_w[i] / 11));
        assert(mapped[1] == (float)(.5 * odd_h[i] / 7));
        assert(mapped[2] == (float)(8.5 * odd_w[i] / 11));
        assert(mapped[3] == (float)(5.25 * odd_h[i] / 7));
        assert(tb_scale_viewport_fits(mapped,limit,-32768,32767));
        assert(tb_scale_depth_slope(extent, 0, &slope) && slope == 0);
        if (i == 0 || i == 1 || i == 3) assert(!tb_scale_depth_slope(extent, 1, &slope));
    }
    assert(!tb_scale_percent_valid(0) && !tb_scale_percent_valid(49) && !tb_scale_percent_valid(74));
    assert(!tb_scale_percent_valid(76) && !tb_scale_non_native(0));
    assert(!tb_scale_extent_make((tb_scale_size){0, 1}, 150, limit, &extent));
    assert(!tb_scale_extent_make((tb_scale_size){2560, 1440}, 300, (tb_scale_size){4096, 4096}, &extent));
    assert(!tb_scale_extent_make((tb_scale_size){UINT32_MAX, 1}, 300, (tb_scale_size){UINT32_MAX, UINT32_MAX}, &extent));
    assert(!tb_scale_extent_make((tb_scale_size){11, 7}, 175, limit, &extent));
    assert(!tb_scale_extent_make((tb_scale_size){2560,1440},50,(tb_scale_size){1279,720},&extent));
    assert(tb_scale_extent_make((tb_scale_size){1,1},50,limit,&extent));
    assert(extent.host.width==1 && extent.host.height==1);
    assert(tb_scale_extent_make((tb_scale_size){11, 7}, 150, limit, &extent));
    assert(extent.host.width == 17 && extent.host.height == 11);
    tb_scale_rect left, right;
    assert(tb_scale_rect_map(extent, (tb_scale_rect){0, 0, 7, 7}, &left));
    assert(tb_scale_rect_map(extent, (tb_scale_rect){7, 0, 4, 7}, &right));
    assert(left.width == right.x && right.x + right.width == 17 && right.height == 11);
    assert(!tb_scale_rect_map(extent, (tb_scale_rect){UINT32_MAX, 1, 2, 1}, &right));
    assert(tb_scale_extent_make((tb_scale_size){11, 7}, 50, limit, &extent));
    assert(tb_scale_rect_map(extent, (tb_scale_rect){0, 0, 1, 1}, &right) == TB_SCALE_RECT_EMPTY);
    assert(right.width == 0 && right.height == 0);
    assert(tb_scale_rect_map(extent, (tb_scale_rect){1, 1, 1, 1}, &right) == TB_SCALE_RECT_MAPPED);
    assert(tb_scale_rect_map(extent, (tb_scale_rect){11, 7, 0, 0}, &right) == TB_SCALE_RECT_EMPTY);
    assert(tb_scale_rect_map((tb_scale_extent){{11,7},{0,4}}, (tb_scale_rect){0,0,1,1}, &right) == TB_SCALE_RECT_INVALID);
    const float invalid_vp[] = {0, 0, 0, 1};
    float mapped[4];
    assert(!tb_scale_viewport_map(extent, invalid_vp, mapped));
    const float wide[] = {0,0,17,11}, outside[] = {-2,0,8,4};
    assert(!tb_scale_viewport_fits(wide,(tb_scale_size){16,16},-32,32));
    assert(!tb_scale_viewport_fits(outside,limit,-1,32));
    assert(!tb_scale_viewport_fits(wide,limit,-16,16));
    assert(tb_scale_viewport_fits(outside,limit,-32,32));

    char shader[512] = "cbuffer F { float4 fp_alpha; }; input.position.xy = floor(input.position.xy / float2(2, 2)) + 0.5; _rsx_wpos(input.position)";
    char original[sizeof shader];
    memcpy(original, shader, sizeof shader);
    assert(tb_scale_wpos(shader, strlen(shader) + 1) == -1);
    assert(!strcmp(shader, original));
    assert(tb_scale_wpos(shader, sizeof shader) == 1);
    assert(strstr(shader, "float4 fp_scale;"));
    assert(strstr(shader, "fp_scale.x > 0.0 ? fp_scale.x : 1.0"));
    assert(strstr(shader, "fp_scale.y > 0.0 ? fp_scale.y : 1.0"));
    assert(strstr(shader, "/ float2(2, 2)) + 0.5; _rsx_wpos(input.position)"));
    strcpy(shader, "cbuffer F { float4 fp_alpha; float4 fp_dbounds; }; floor(input.position.xy / float2(2, 2)); _rsx_wpos(input.position)");
    assert(tb_scale_wpos(shader, sizeof shader) == 1);
    assert(strstr(shader, "fp_dbounds;") < strstr(shader, "fp_scale;"));
    assert(tb_scale_wpos(NULL, 0) == -1);
    memset(shader, 'x', sizeof shader);
    assert(tb_scale_wpos(shader, sizeof shader) == -1);
    strcpy(shader, "input.tc0.xy; rsx_tex[0].Sample(rsx_samp[0], input.tc0.xy)");
    strcpy(original, shader);
    assert(tb_scale_wpos(shader, sizeof shader) == 0);
    assert(!strcmp(shader, original));
    strcpy(shader, "cbuffer F { float4 fp_alpha; }; floor(input.position.xy / float2(2, 2)); _rsx_wpos(input.position); rsx_tex[0].SampleGrad(rsx_samp[0], input.tc0.xy, ddx(input.tc0.xy), ddy(input.tc0.xy))");
    assert(tb_scale_wpos(shader,sizeof shader)==1);
    assert(strstr(shader,"rsx_tex[0].SampleGrad(rsx_samp[0], input.tc0.xy, ddx(input.tc0.xy), ddy(input.tc0.xy))"));
}
