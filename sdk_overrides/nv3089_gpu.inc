/* TB_GPU_RESOLVE (default on): run an NV3089 scaled-image blit between two
 * draw-engine colour surfaces on the GPU instead of reading the source back
 * into guest memory, copying on the CPU and uploading the destination again.
 * This title resolves its 2560x1440 scene into the 1280x720 display buffer
 * that way, about six 512x512 tiles and 14.7 MB of readback per frame.
 *
 * Only the cases the CPU path below treats as a plain 4-byte copy qualify, at
 * an integer scale with whole-texel origins; the GPU gather picks exactly
 * the texels the CPU loop would (checked on the device once,
 * vulkan_surface_blit.inc). Anything else -- other formats, the NV309E
 * swizzled pyramid, fractional steps, clipped-away blits, sources or
 * destinations that are not engine surfaces -- takes the original path.
 * TB_GPU_RESOLVE=0 restores it everywhere. */
/* The draw engine's (gpu_blit_engine.inc); FIFO-only fixtures have none. */
__attribute__((weak)) int rsx_draw_engine_gpu_blit(u32 sloc, u32 soff, u32 spitch, u32 sx, u32 sy, u32 kx, u32 ky,
                                                   u32 dloc, u32 doff, u32 dpitch, u32 dx, u32 dy, u32 dw, u32 dh)
{
    (void)sloc; (void)soff; (void)spitch; (void)sx; (void)sy; (void)kx; (void)ky;
    (void)dloc; (void)doff; (void)dpitch; (void)dx; (void)dy; (void)dw; (void)dh;
    return 0;
}

static int tb_gpu_resolve_on(void)
{
    static int on = -1;
    if (on < 0) { const char* e = getenv("TB_GPU_RESOLVE"); on = !e || strcmp(e, "0") != 0; }
    return on;
}

static int tb_nv3089_gpu(u32 in_pitch, u32 dst_pitch, u32 out_x, u32 out_y, u32 out_w, u32 out_h)
{
    if (!tb_gpu_resolve_on() || !rsx_draw_engine_enabled() || rsx_live_draw_enabled()) return 0;
    const u32 f = s_nv3089.fmt & 0xFFu;
    if (f != 3u && f != 0xDu) return 0;                                   /* 4-byte source pixels */
    if (s_gcm2d.color_fmt == 4u || s_gcm2d.color_fmt == 1u) return 0;     /* 2- or 1-byte destination */
    {   /* the NV309E swizzled pyramid branch */
        const u32 l2w = (s_nv309e.fmt >> 16) & 0xFF, l2h = (s_nv309e.fmt >> 24) & 0xFF;
        if (f == 3u && s_nv309e.active && s_nv309e.offset && (1u << l2w) == out_w && (1u << l2h) == out_h) return 0;
    }
    if ((s_nv3089.ds_dx & 0xFFFFFu) || (s_nv3089.dt_dy & 0xFFFFFu)) return 0;
    const u32 kx = s_nv3089.ds_dx >> 20, ky = s_nv3089.dt_dy >> 20;
    if (kx < 1u || kx > 8u || ky < 1u || ky > 8u) return 0;
    const u32 u0 = s_nv3089.in_uv & 0xFFFF, v0 = s_nv3089.in_uv >> 16;   /* 12.4 */
    if ((u0 & 15u) || (v0 & 15u)) return 0;
    const u32 ax = u0 >> 4, ay = v0 >> 4;
    /* The clip, as the CPU path applies it (destination coordinates). */
    const u32 cx0 = s_nv3089.clip_pt & 0xFFFF, cy0 = s_nv3089.clip_pt >> 16;
    const u32 cx1 = (s_nv3089.clip_sz & 0xFFFF) ? cx0 + (s_nv3089.clip_sz & 0xFFFF) : 0xFFFFFFFFu;
    const u32 cy1 = (s_nv3089.clip_sz >> 16) ? cy0 + (s_nv3089.clip_sz >> 16) : 0xFFFFFFFFu;
    const u32 x0 = out_x > cx0 ? out_x : cx0, y0 = out_y > cy0 ? out_y : cy0;
    const u32 x1 = (u64)out_x + out_w < cx1 ? out_x + out_w : cx1;
    const u32 y1 = (u64)out_y + out_h < cy1 ? out_y + out_h : cy1;
    if (x1 <= x0 || y1 <= y0) return 0;
    /* Texels the CPU loop reads: a + k * (x - out), all inside the source image. */
    const u32 fx = ax + kx * (x0 - out_x), fy = ay + ky * (y0 - out_y);
    const u32 lx = ax + kx * (x1 - 1u - out_x), ly = ay + ky * (y1 - 1u - out_y);
    const u32 in_w = s_nv3089.in_sz & 0xFFFF, in_h = s_nv3089.in_sz >> 16;
    if ((in_w && lx >= in_w) || (in_h && ly >= in_h)) return 0;
    const u32 sloc = s_nv3089.src_dma != 0xFEED0001u ? RSX_LOCATION_LOCAL : RSX_LOCATION_MAIN;
    const u32 dloc = s_gcm2d.dst_dma != 0xFEED0001u ? RSX_LOCATION_LOCAL : RSX_LOCATION_MAIN;
    if (!rsx_draw_engine_gpu_blit(sloc, s_nv3089.in_off, in_pitch, fx, fy, kx, ky,
                                  dloc, s_gcm2d.dst_offset, dst_pitch, x0, y0, x1 - x0, y1 - y0))
        return 0;
    /* The frame-order trace sees a write into a display buffer as the CPU path reports it. */
    const u32 written = s_gcm2d.dst_offset + y0 * dst_pitch + x0 * 4u;
    for (u32 i = 0; i < CELL_GCM_MAX_DISPLAY_BUFFER_NUM; ++i) {
        const CellGcmDisplayInfo* b = &s_display_buffers[i];
        if (s_display_buffer_set[i] && dloc == RSX_LOCATION_LOCAL && written >= b->offset &&
            (u64)written < (u64)b->offset + (u64)b->pitch * b->height)
            tb_frame_event(2, i, written, x1 - x0, y1 - y0);
    }
    return 1;
}
