/* A transfer may change any part of a cached texture between two draws in
 * one frame. Overlap, not just a texture's start address, invalidates it. */
static void eng_textures_touch(u32 location, u32 offset, u32 bytes)
{
    const u64 end = (u64)offset + bytes;
    for (u32 i = 0; i < g.n_textures; i++) {
        eng_texture* e = &g.textures[i];
        const u32 span = e->depth > 1
            ? eng_texture_span_3d(e->format,e->width,e->height,e->depth,e->levels,e->pitch)
            : eng_texture_span(e->format,e->width,e->height,e->levels,e->pitch,e->cubemap);
        if (e->location == location && (u64)e->offset < end && (u64)offset < (u64)e->offset + span) {
            e->last_hash_frame = g.frames - 1u;
            e->last_full_frame = g.frames - ENG_TEXHASH_FULL_EVERY;
            e->rewritten = 1;
        }
    }
}

void tb_rsx_memory_written(u32 location, u32 offset, u32 bytes)
{
    if (g.ready && bytes) eng_textures_touch(location,offset,bytes);
}
