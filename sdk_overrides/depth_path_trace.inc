/* Bounded diagnostics only. No guest bytes or texture payloads are recorded. */
static int tb_depth_trace_enabled(void)
{
    static int enabled=-1;
    if(enabled<0)enabled=getenv("TB_DEPTH_PATH_TRACE")!=NULL;
    return enabled;
}
static void tb_depth_trace(u32 slot,const rsx_dsp_texture* t)
{
    static unsigned reports;
    if(!tb_depth_trace_enabled() || reports++>=64)return;
    const eng_zdepth* z=&g.zdepths[slot];
    fprintf(stderr,"[depth-path] frame=%u slot=%u producer=%u:0x%08X zfmt=%u size=%ux%u used=%ux%u pitch=%u serial=%llu dirty=%d consumer=fmt0x%X %ux%u pitch=%u mip=%u cube=%u dim=%u backend-convert=%d backend-view=%d force-guest=%d\n",
        g.frames,slot,z->location,z->offset,z->zfmt,z->w,z->h,z->used_w,z->used_h,z->pitch,
        (unsigned long long)z->depth_serial,z->guest_dirty,t->format,t->width,t->height,t->pitch,
        t->mipmaps,t->cubemap,t->dimension,g.be->depth_as_color!=NULL,g.be->surface_view!=NULL,s_depth_via_guest);
}
