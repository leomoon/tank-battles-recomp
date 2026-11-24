/* NV3089 between two engine colour surfaces, on the GPU (nv3089_gpu.inc).
 *
 * The source scene exists on the GPU; the guest-memory path read it back,
 * blitted it on the CPU and pushed the result into the destination surface.
 * Here both ends stay on the GPU. The destination then holds a GPU-only write
 * exactly as after a draw into it: guest_dirty is set, so the engine's own
 * consumers that need guest bytes (eng_surface_to_guest, transfers through
 * rsx_draw_engine_guest_read) still materialize them first. The page census
 * (analysis/readback-census) found no guest reader of these surfaces. */
__attribute__((weak)) int tb_vk_surface_blit(u32 src, u32 sx, u32 sy, u32 kx, u32 ky,
                                             u32 dst, u32 dx, u32 dy, u32 dw, u32 dh)
{
    (void)src; (void)sx; (void)sy; (void)kx; (void)ky; (void)dst; (void)dx; (void)dy; (void)dw; (void)dh;
    return 0;
}

static u32 tb_blit_surface(u32 location, u32 base, u32 pitch, u32 x, u32 y, u32 w, u32 h, u32* sx, u32* sy)
{
    u32 xb = 0, row = 0;
    const u32 slot = eng_surface_at(location, base + y * pitch + x * 4u, pitch, &xb, &row);
    if (slot == ENG_INVALID) return ENG_INVALID;
    const eng_surface* s = &g.surfaces[slot];
    if (s->fmt != RSX_BE_FMT_R8G8B8A8 || xb % 4u || xb / 4u + w > s->w || row + h > s->h) return ENG_INVALID;
    *sx = xb / 4u;
    *sy = row;
    return slot;
}

/* Destination pixel (dx + i, dy + j) takes source texel (sx + kx*i, sy + ky*j);
 * coordinates in pixels relative to each side's base offset. 1 when done. */
int rsx_draw_engine_gpu_blit(u32 sloc, u32 soff, u32 spitch, u32 sx, u32 sy, u32 kx, u32 ky,
                             u32 dloc, u32 doff, u32 dpitch, u32 dx, u32 dy, u32 dw, u32 dh)
{
    if (!g.ready || !kx || !ky || !dw || !dh) return 0;
    const u32 sw = (dw - 1u) * kx + 1u, sh = (dh - 1u) * ky + 1u;
    u32 ssx, ssy, dsx, dsy;
    const u32 ss = tb_blit_surface(sloc, soff, spitch, sx, sy, sw, sh, &ssx, &ssy);
    const u32 ds = tb_blit_surface(dloc, doff, dpitch, dx, dy, dw, dh, &dsx, &dsy);
    /* A source never rendered on the GPU holds its truth in guest memory. */
    if (ss == ENG_INVALID || ds == ENG_INVALID || ss == ds || !g.surfaces[ss].color_serial) return 0;
    if (!tb_vk_surface_blit(g.surfaces[ss].handle, ssx, ssy, kx, ky, g.surfaces[ds].handle, dsx, dsy, dw, dh))
        return 0;
    tb_scale_note_resolve(ss, ds, ssx, ssy, dsx, dsy, kx, ky, dw, dh);
    eng_surface* d = &g.surfaces[ds];
    d->color_serial = ++g.write_serial;
    d->guest_dirty = 1;
    d->last_use = g.surfaces[ss].last_use = g.frames;
    /* Textures cached over the destination bytes must not outlive the write. */
    tb_rsx_memory_written(dloc, doff + dy * dpitch + dx * 4u, (dh - 1u) * dpitch + dw * 4u);
    return 1;
}
