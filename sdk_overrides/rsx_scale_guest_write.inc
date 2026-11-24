/* Native 100% retains the SDK's policy. A tracked non-Native zeta receives
 * guest writes only after coherent reconstruction and permanent Native
 * continuation. Color rows use the ordinary upload and mapped patch path. */
static int tb_scale_depth_guest_write(u32 location,u32 offset,u32 pitch,u32 w,u32 h)
{
    for (u32 i=0;i<g.n_zdepths;++i) {
        eng_zdepth* z=&g.zdepths[i];
        if (z->location!=location || !z->pitch || z->zfmt!=2 || offset<z->offset ||
            (h>1 && z->pitch!=pitch) || !tb_vk_scale_tracked(z->handle)) continue;
        const u64 relative=(u64)offset-z->offset;
        const u32 x=(relative%z->pitch)/4, y=(u32)(relative/z->pitch);
        if (relative%4 || y>=z->h || x>=z->w || w>z->w-x || h>z->h-y) continue;
        int newer_color=0;
        for (u32 c=0;c<g.n_surfaces;++c) {
            const eng_surface* color=&g.surfaces[c];
            if (color->location==location && color->color_serial>z->depth_serial &&
                (u64)offset<(u64)color->offset+(u64)color->pitch*color->h &&
                color->offset<(u64)offset+(u64)(h-1)*pitch+w*4ull) newer_color=1;
        }
        if (newer_color) continue;
        u8* rows=eng_coherence_buffer(w*h*4);
        if (!rows) { tb_vk_scale_fallback("depth write staging allocation"); abort(); }
        for (u32 row=0;row<h;++row) {
            const u8* src=eng_guest_linear(location,offset+row*pitch,w*4);
            if (!src) { tb_vk_scale_fallback("depth write guest range"); abort(); }
            memcpy(rows+(size_t)row*w*4,src,w*4);
        }
        if (!tb_vk_scale_depth_upload(z->handle,(tb_scale_rect){x,y,w,h},rows,w*4)) abort();
        z->depth_serial=++g.write_serial;
        z->guest_dirty=1; /* Only the changed region is current in guest memory. */
        z->snapshot_valid=0; z->had_write=1;
        return 1;
    }
    return 0;
}

/* One-row byte transfers carry their byte stride, which is not necessarily
 * the containing render target's pitch. Resolve that pitch independently. */
static u32 tb_scale_byte_pitch(u32 location,u32 offset,u32 fallback_pitch)
{
    const eng_surface* best=NULL;
    for (u32 i=0;i<g.n_surfaces;++i) {
        const eng_surface* s=&g.surfaces[i];
        if (!s->handle || s->location!=location || !s->pitch || offset<s->offset) continue;
        const u64 relative=(u64)offset-s->offset;
        if (relative/s->pitch>=s->h || relative%s->pitch>=(u64)s->w*eng_transfer_bpp(s->fmt)) continue;
        if (!best || s->offset>best->offset) best=s;
    }
    return best?best->pitch:fallback_pitch;
}

void tb_rsx_guest_read_bytes(u32 location,u32 offset,u32 bytes)
{
    if (!g.ready || !bytes) return;
    const u32 pitch=tb_scale_byte_pitch(location,offset,bytes);
    rsx_draw_engine_guest_read(location,offset,pitch,(bytes+3u)/4u,1);
}

/* NV0039 knows the exact byte count. Expand its notification to aligned pixels
 * after it has preserved the surrounding bytes via guest_read. */
void tb_rsx_guest_wrote_bytes(u32 location,u32 offset,u32 bytes)
{
    if (!bytes || (u64)offset+bytes>0x100000000ull) return;
    const u32 aligned=offset&~3u;
    const u32 pixels=(bytes+(offset&3u)+3u)/4u;
    const u32 pitch=tb_scale_byte_pitch(location,aligned,pixels*4);
    rsx_draw_engine_guest_wrote(location,aligned,pitch,pixels,1);
}
