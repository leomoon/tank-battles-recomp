/* Original regression: registered scanout exists in guest memory, not as a draw target. */
static void test_registered_scanout_memory(void)
{
    engine_up();
    draw_triangle();
    const u32 drawing = stub.bound_rt[0];
    const u32 scanout = 0x80000;
    u8* bytes = vm_base + GUEST_LOCAL_EA + scanout;
    for (unsigned n=0;n<16;n++) {
        bytes[n*4]=0x80;bytes[n*4+1]=0x10;bytes[n*4+2]=0x20;bytes[n*4+3]=0x30;
    }
    rsx_draw_engine_set_display_buffer(0,RSX_LOCATION_LOCAL,scanout,16,4,4);
    memset(&g_written,0,sizeof g_written);
    rsx_draw_engine_present_buffer(0);
    CHECK(presented_surface != drawing,"scanout-only flip does not fall back to unfinished draw target");
    CHECK(g_written.n==1 && g_written.surface==presented_surface,"scanout guest bytes initialize the selected backend target");
    CHECK(g_written.first[0]==0x10 && g_written.first[1]==0x20 && g_written.first[2]==0x30 && g_written.first[3]==0x80,"scanout bytes preserve A8R8G8B8 to RGBA conversion");
    const u32 selected = presented_surface;
    bytes[1]=0x70;
    rsx_draw_engine_guest_wrote(RSX_LOCATION_LOCAL,scanout,16,4,4);
    CHECK(g_written.n==2 && g_written.surface==selected && g_written.first[0]==0x70,"later 2D transfer updates the same registered scanout");
    draw_triangle();
    rsx_draw_engine_present();
    CHECK(presented_surface==selected,"later draw-target binding does not change the selected scanout");
    engine_down();
}

static void test_texture_write_within_frame(void)
{
    engine_up();
    bind_texture_unit(RSX_LOCATION_MAIN,TEX_OFFSET);
    memset(vm_base+TEX_OFFSET,0x11,4*4*4);
    draw_triangle();
    CHECK(stub.n_texture_upload==1,"initial texture upload before an in-frame transfer");
    memset(vm_base+TEX_OFFSET+16,0x22,4);
    rsx_draw_engine_guest_wrote(RSX_LOCATION_MAIN,TEX_OFFSET+16,4,1,1);
    draw_triangle();
    CHECK(stub.n_texture_upload==2,"partial 2D write inside a cached texture is visible in the same frame");
    draw_triangle();
    CHECK(stub.n_texture_upload==2,"unchanged subsequent draw reuses the refreshed texture");
    engine_down();
}

static void test_guest_flip_trace_scope(void)
{
    if (!getenv("TB_RSX_TRACE")) return;
    engine_up();
    rsx_draw_engine_note_flip(0);
    for (u32 i=0;i<3;i++) {
        draw_triangle();
        rsx_draw_engine_present_buffer(0);
        rsx_draw_engine_present_buffer(0);
        draw_triangle();
        rsx_draw_engine_note_flip(0);
    }
    engine_down();
}
