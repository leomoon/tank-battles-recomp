/* The Native draw and all guest-facing texture state remain authoritative
 * until the resolve and guest readback paths can consume the scaled scene. */
static int tb_scale_sample_alias(u32 location, u32 offset, u64 bytes)
{
    for (u32 i = 0; i < TB_SCALE_MAX_ROUTES; ++i) {
        const tb_scale_resolve* r = &tb_scale_scene.proven[i];
        if (tb_scale_overlaps(r->route.color, location, offset, bytes) ||
            tb_scale_overlaps(r->route.display, location, offset, bytes) ||
            tb_scale_overlaps(r->depth, location, offset, bytes)) return 1;
    }
    return 0;
}

static int tb_scale_samples_native(u32 fragment_mask, u32 vertex_mask)
{
    for (u32 unit = 0; unit < RSX_DSP_NUM_TEXTURES; ++unit) {
        if (!(fragment_mask & (1u << unit))) continue;
        rsx_dsp_texture t;
        rsx_dsp_get_texture(&g.rsx, unit, &t);
        u32 bytes = t.depth > 1 ? eng_texture_span_3d(t.format, t.width, t.height, t.depth, t.mipmaps, t.pitch)
                                : eng_texture_span(t.format, t.width, t.height, t.mipmaps, t.pitch, t.cubemap);
        if (tb_scale_sample_alias(t.location, t.offset, bytes ? bytes : 1)) return 0;
    }
    for (u32 unit = 0; unit < RSX_DSP_NUM_VERTEX_TEXTURES; ++unit) {
        if (!(vertex_mask & (1u << unit))) continue;
        rsx_dsp_vertex_texture t;
        rsx_dsp_get_vertex_texture(&g.rsx, unit, &t);
        u32 bytes = t.depth > 1 ? eng_texture_span_3d(t.format, t.width, t.height, t.depth, t.mipmaps, t.pitch)
                                : eng_texture_span(t.format, t.width, t.height, t.mipmaps, t.pitch, t.cubemap);
        if (tb_scale_sample_alias(t.location, t.offset, bytes ? bytes : 1)) return 0;
    }
    return 1;
}

static int tb_scale_mirror_binding(u32 target, u32 zslot, u32 n_targets, const rsx_be_render_state* rs,
                                    u32 tex_mask, u32 vtex_mask, tb_scale_binding* binding)
{
    if (!tb_scale_non_native(tb_scale_scene.percent) || n_targets != 1 || zslot == ENG_INVALID ||
        !tb_scale_scene.draws[target].color.generation) return 0;
    if (!tb_vk_scale_bindings(tb_scale_color_id(&g.surfaces[target]),
                               tb_scale_depth_id(&g.zdepths[zslot]), rs->stencil_enable, binding)) return 0;
    if (s_dbz.unit || !tb_scale_samples_native(tex_mask, vtex_mask)) {
        tb_frame_budget_count(TB_FC_SCALE_SKIP_ALIAS, 1);
        tb_vk_scale_skip_frame();
        return 0;
    }
    return 1;
}

static void tb_scale_mirror_clear(const rsx_dsp_surface* sf, u32 target, u32 n_targets, u32 mask)
{
    if (!tb_scale_non_native(tb_scale_scene.percent) || n_targets != 1 ||
        sf->color_target != CELL_GCM_SURFACE_TARGET_1 || sf->aa_sx != 2 || sf->aa_sy != 2) return;
    u32 zslot = 0;
    for (; zslot < g.n_zdepths; ++zslot) {
        const eng_zdepth* depth = &g.zdepths[zslot];
        if (depth->handle && depth->location == sf->zeta_location && depth->offset == sf->zeta_offset)
            break;
    }
    if (zslot == g.n_zdepths) return;
    const tb_scale_surface color = tb_scale_color_id(&g.surfaces[target]);
    const tb_scale_surface depth = tb_scale_depth_id(&g.zdepths[zslot]);
    if (!tb_scale_scene_pair(sf, color, depth)) return;
    u32 rc[4];
    if (!eng_clear_rect(sf, color.size.width, color.size.height, rc)) return;

    const u32 channels = RSX_CLEAR_COLOR_R | RSX_CLEAR_COLOR_G | RSX_CLEAR_COLOR_B | RSX_CLEAR_COLOR_A;
    // Match the current Vulkan clear policy for the selected channels.
    const unsigned clear = ((mask & channels) ? TB_SCALE_CLEAR_COLOR : 0) |
                           ((mask & RSX_CLEAR_DEPTH) ? TB_SCALE_CLEAR_DEPTH : 0) |
                           ((mask & RSX_CLEAR_STENCIL) ? TB_SCALE_CLEAR_STENCIL : 0);
    if (!clear) return;
    const u32 rgba = rsx_dsp_clear_color(&g.rsx);
    const float values[] = {
        ((rgba >> 16) & 255u) / 255.0f, ((rgba >> 8) & 255u) / 255.0f,
        (rgba & 255u) / 255.0f, ((rgba >> 24) & 255u) / 255.0f
    };
    const u32 zs = rsx_dsp_reg(&g.rsx, M_ZSTENCIL_CLEAR);
    tb_vk_scale_clear(color, depth, (tb_scale_rect){rc[0], rc[1], rc[2], rc[3]}, clear, values,
                       (float)(zs >> 8) / 16777215.0f, zs & 255u);
}
