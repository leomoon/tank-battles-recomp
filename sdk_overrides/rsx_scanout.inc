/* A registered display buffer can be written entirely by 2D transfers. It
 * still needs its own backend surface; the currently bound draw target can
 * belong to the next frame or an intermediate pass without the UI. */
static u32 tb_scanout_surface(const eng_display_buffer* d)
{
    if (!d->pitch || (u64)d->width * 4 > d->pitch ||
        (u64)d->pitch * d->height > 0xFFFFFFFFu || !g.be->color_target_write) {
        fprintf(stderr, "[rsx engine] invalid scanout geometry or missing upload support\n");
        return ENG_INVALID;
    }
    const u32 slot = eng_surface_get(d->location, d->offset, d->width, d->height,
                                     RSX_BE_FMT_R8G8B8A8, d->pitch, 0x101);
    if (slot == ENG_INVALID) {
        fprintf(stderr, "[rsx engine] scanout surface creation failed\n");
        return ENG_INVALID;
    }
    g.surfaces[slot].pitch = d->pitch;
    // Local VRAM is not seeded by eng_surface_get. Import the completed image
    // once; subsequent 2D writes use the existing coherence notifications.
    rsx_draw_engine_guest_wrote(d->location, d->offset, d->pitch, d->width, d->height);
    return slot;
}
