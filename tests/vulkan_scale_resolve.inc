/* Full title-sized six-tile resolve, not an odd-size geometry surrogate. */
static PFN_vkCmdCopyImageToBuffer resolve_readback_real;
static unsigned resolve_readbacks;
static PFN_vkAllocateMemory resolve_allocate_real;
static PFN_vkFreeMemory resolve_free_real;
static struct { VkDeviceMemory memory; VkDeviceSize bytes; } resolve_memory[2048];
static u64 resolve_live_memory,resolve_peak_memory,resolve_total_memory;
static VKAPI_ATTR VkResult VKAPI_CALL resolve_allocate(VkDevice device,const VkMemoryAllocateInfo* info,
        const VkAllocationCallbacks* callbacks,VkDeviceMemory* memory)
{
    const VkResult result=resolve_allocate_real(device,info,callbacks,memory);
    if (result) return result;
    unsigned slot=0;
    while (slot<2048 && resolve_memory[slot].memory) ++slot;
    assert(slot<2048);
    resolve_memory[slot].memory=*memory;
    resolve_memory[slot].bytes=info->allocationSize;
    resolve_live_memory+=info->allocationSize;
    resolve_total_memory+=info->allocationSize;
    if (resolve_live_memory>resolve_peak_memory) resolve_peak_memory=resolve_live_memory;
    return result;
}
static VKAPI_ATTR void VKAPI_CALL resolve_free(VkDevice device,VkDeviceMemory memory,const VkAllocationCallbacks* callbacks)
{
    for (unsigned i=0;i<2048;++i) if (resolve_memory[i].memory==memory) {
        resolve_live_memory-=resolve_memory[i].bytes;
        resolve_memory[i].memory=VK_NULL_HANDLE;
        break;
    }
    resolve_free_real(device,memory,callbacks);
}
static VKAPI_ATTR void VKAPI_CALL resolve_readback(VkCommandBuffer cb,VkImage image,VkImageLayout layout,
        VkBuffer buffer,uint32_t count,const VkBufferImageCopy* regions)
{
    ++resolve_readbacks;
    resolve_readback_real(cb,image,layout,buffer,count,regions);
}

static u64 resolve_image_bytes(void)
{
    u64 bytes=0;
    for (u32 i=1;i<VK_ENG_MAX_OBJ;++i) if (s_eobj[i].im.img) {
        VkMemoryRequirements r;
        pvkGetImageMemoryRequirements(s_vk.device,s_eobj[i].im.img,&r);
        bytes+=r.size;
    }
    return bytes;
}

static void check_resolve(unsigned percent)
{
    tb_vk_scale_reset();
    s_vk.eng_active=1;
    memset(resolve_memory,0,sizeof resolve_memory);
    resolve_live_memory=resolve_peak_memory=resolve_total_memory=0;
    resolve_allocate_real=pvkAllocateMemory; resolve_free_real=pvkFreeMemory;
    pvkAllocateMemory=resolve_allocate; pvkFreeMemory=resolve_free;
    tb_scale_layout group={0};
    group.count=3;
    group.depth=(tb_scale_surface){0,0x1000000,10240,2,1,{2560,1440}};
    u32 colors[3],displays[3];
    const u32 depth=vk_eng_depth_target_create(NULL,2560,1440);
    assert(depth);
    for (u32 i=0;i<3;++i) {
        group.routes[i].color=(tb_scale_surface){0,0x2000000+i*0x1000000,10240,RSX_BE_FMT_R8G8B8A8,2+i,{2560,1440}};
        group.routes[i].display=(tb_scale_surface){0,0x5000000+i*0x400000,5120,RSX_BE_FMT_R8G8B8A8,5+i,{1280,720}};
        colors[i]=vk_eng_color_target_create(NULL,RSX_BE_FMT_R8G8B8A8,2560,1440,NULL,0);
        displays[i]=vk_eng_color_target_create(NULL,RSX_BE_FMT_R8G8B8A8,1280,720,NULL,0);
        assert(colors[i] && displays[i]);
    }
    const u64 native_bytes=resolve_image_bytes();
    tb_vk_scale_prepare(&group,percent);
    tb_vk_scale_attach(group.depth,depth,1);
    for (u32 i=0;i<3;++i) {
        tb_vk_scale_attach(group.routes[i].color,colors[i],0);
        tb_vk_scale_attach(group.routes[i].display,displays[i],0);
    }
    const int scaled=percent!=100;
    if (!scaled) assert(tb_vk_blit_exact());
    const u32 dw=1280*percent/100,dh=720*percent/100;
    u8* pixels=malloc((size_t)dw*dh*16);
    u8* result=malloc((size_t)dw*dh*4);
    assert(pixels && result);
    for (u32 route=0;route<3;++route) {
        const u32 scene=scaled?tb_scaled.color[route].handle:colors[route];
        const u32 display=scaled?tb_scaled.display[route].handle:displays[route];
        for (u32 y=0;y<dh*2;++y) for (u32 x=0;x<dw*2;++x) {
            u8* p=pixels+((size_t)y*dw*2+x)*4;
            p[0]=(x/2%120)*2+(x%2)*16;
            p[1]=(y/2%110)*2+(y%2)*16;
            p[2]=route*60; p[3]=255;
        }
        vk_eng_color_target_write(NULL,scene,0,0,dw*2,dh*2,pixels,dw*8);
        if (scaled) {
            tb_vk_scale_wrote(scene);
            tb_scaled.active_color=scene;
            tb_scaled.rasterized=1;
        }
        for (u32 y=0;y<720;y+=512) for (u32 x=0;x<1280;x+=512) {
            const u32 w=x==1024?256:512,h=y==512?208:512;
            if (scaled) {
                assert(tb_vk_scale_resolve(group.routes[route].color,group.routes[route].display,
                    (tb_scale_rect){x*2,y*2,w*2,h*2},(tb_scale_rect){x,y,w,h}));
                assert(!tb_vk_scale_scanout(group.routes[route].display));
            } else assert(tb_vk_surface_blit(scene,x*2+1,y*2+1,2,2,display,x,y,w,h));
        }
        if (scaled) {
            tb_vk_scale_publish(group.routes[route].display,1);
            assert(tb_vk_scale_scanout(group.routes[route].display)==display);
            assert(!s_eobj[colors[route]].im.img && !s_eobj[displays[route]].im.img);
        }
        vk_eng_readback(NULL,display,0,0,dw,dh,result,dw*4);
        /* Linear's centre phase averages exactly the four texels in each
         * physical 2x2 footprint; Native preserves the second-texel gather. */
        for (u32 y=0;y<dh;++y) for (u32 x=0;x<dw;++x) {
            const u8* p=result+((size_t)y*dw+x)*4;
            assert(p[0]==(x%120)*2+(scaled?8:16));
            assert(p[1]==(y%110)*2+(scaled?8:16));
            assert(p[2]==route*60 && p[3]==255);
        }
        if (scaled) {
            const u8 blue[]={0,0,255,255};
            vk_eng_color_target_write(NULL,displays[route],640,360,1,1,blue,4);
            assert(tb_vk_scale_scanout(group.routes[route].display)==display);
            /* A one-guest-pixel patch may map empty at reduced sizes. */
            tb_scale_rect patch;
            if (tb_scale_rect_map(tb_scaled.display[route].extent,(tb_scale_rect){640,360,1,1},&patch)==TB_SCALE_RECT_MAPPED)
                scale_pixel(display,patch.x,patch.y,0,0,255);
            scale_pixel(display,dw-1,dh-1,(dw-1)%120*2+8,(dh-1)%110*2+8,route*60);
        }
        tb_eng_complete();
        resolve_readback_real=pvkCmdCopyImageToBuffer;
        pvkCmdCopyImageToBuffer=resolve_readback;
        resolve_readbacks=0;
        s_vk.eng_active=1; s_vk.present_split=1;
        if (scaled) assert(tb_vk_scale_present(displays[route],display));
        else vk_eng_present(NULL,display);
        assert(s_vk.present_inflight);
        /* Guest buffer reuse must not change the submitted present copy. */
        const float red[]={1,0,0,1};
        vk_eng_clear_color(NULL,display,red);
        rsx_vulkan_backend_present_finish();
        assert(!resolve_readbacks && !tb_present_readback()->ptr);
        assert(tb_present_extent().width==dw && tb_present_extent().height==dh);
        const VkExtent2D saved_drawable=s_vk.sc_extent;
        s_vk.sc_extent=(VkExtent2D){1920,1200};
        vk_rect fit=vk_window_fit();
        assert(fit.x==0 && fit.y==60 && fit.w==1920 && fit.h==1080);
        s_vk.sc_extent=(VkExtent2D){1024,768};
        fit=vk_window_fit();
        assert(fit.x==0 && fit.y==96 && fit.w==1024 && fit.h==576);
        s_vk.sc_extent=saved_drawable;
        assert(rsx_vulkan_backend_presented_center()!=0xffff0000u);
        assert(resolve_readbacks==1); /* explicit diagnostics only */
        pvkCmdCopyImageToBuffer=resolve_readback_real;
        vk_destroy_buffer(tb_present_readback());
        printf("present %u%% route %u: %ux%u, copy=%llu bytes, normal readback=0\n",
            percent,route,dw,dh,(unsigned long long)dw*dh*4);
    }
    /* Explicit frame dumps still receive the full selected image. */
    const u32 last=scaled?tb_scaled.display[2].handle:displays[2];
    s_vk.dump_path="unused-step5-fixture"; s_vk.dump_every=UINT32_MAX;
    if (scaled) assert(tb_vk_scale_present(displays[2],last));
    else vk_eng_present(NULL,last);
    /* Capture policy belongs to the submitted frame, even when toggled
     * before the present worker completes it. */
    s_vk.dump_path=NULL;
    rsx_vulkan_backend_present_finish();
    assert(tb_present_readback()->size==(VkDeviceSize)dw*dh*4);
    assert(tb_present_captured);
    assert(rsx_vulkan_backend_presented_center()==0xffff0000u);
    const u8* captured=tb_present_readback()->ptr;
    for (size_t i=0;i<(size_t)dw*dh;++i)
        assert(captured[4*i]==255 && captured[4*i+1]==0 && captured[4*i+2]==0 && captured[4*i+3]==255);
    vk_destroy_buffer(tb_present_readback());
    if (scaled) assert(tb_vk_scale_present(displays[2],last));
    else vk_eng_present(NULL,last);
    setenv("TB_FRAME_HISTORY","fixture",1);
    rsx_vulkan_backend_present_finish();
    assert(!tb_present_captured && !tb_present_readback()->ptr);
    assert(rsx_vulkan_backend_presented_center()==0xffff0000u);
    assert(tb_present_readback()->size==4);
    unsetenv("TB_FRAME_HISTORY");
    vk_destroy_buffer(tb_present_readback());
    if (scaled) {
        /* Give depth an owner too, then check Native memory really disappeared. */
        const float green[]={0,1,0,1};
        tb_vk_scale_clear(group.routes[0].color,group.depth,(tb_scale_rect){0,0,2560,1440},
            TB_SCALE_CLEAR_COLOR|TB_SCALE_CLEAR_DEPTH|TB_SCALE_CLEAR_STENCIL,green,1,0);
        const u64 live=resolve_image_bytes();
        printf("attachments %u%%: Native=%llu live=%llu bytes\n",percent,
            (unsigned long long)native_bytes,(unsigned long long)live);
        printf("allocation diagnostic %u%%: peak=%llu allocated-total=%llu current=%llu bytes (fixture-owned allocations)\n",
            percent,(unsigned long long)resolve_peak_memory,(unsigned long long)resolve_total_memory,
            (unsigned long long)resolve_live_memory);
        if (percent<100) assert(live<native_bytes);
        assert(!s_eobj[depth].im.img);
        assert(tb_vk_scale_resolve(group.routes[0].color,group.routes[0].display,
            (tb_scale_rect){0,0,1024,1024},(tb_scale_rect){0,0,512,512}));
        tb_vk_scale_publish(group.routes[0].display,0);
        assert(!tb_vk_scale_scanout(group.routes[0].display));
        assert(tb_vk_scale_resolve(group.routes[0].color,group.routes[0].display,
            (tb_scale_rect){0,0,1024,1024},(tb_scale_rect){0,0,512,512}));
        tb_vk_scale_wrote(tb_scaled.color[0].handle); /* producer changes mid-resolve */
        assert(!tb_vk_scale_resolve(group.routes[0].color,group.routes[0].display,
            (tb_scale_rect){1024,0,1024,1024},(tb_scale_rect){512,0,512,512}));
        assert(tb_scale_rejected && !tb_vk_scale_scanout(group.routes[0].display));
    }
    tb_vk_scale_reset();
    for (u32 i=0;i<3;++i) { vk_eng_color_target_release(NULL,colors[i]); vk_eng_color_target_release(NULL,displays[i]); }
    vk_eng_depth_target_release(NULL,depth);
    free(pixels); free(result);
    /* Overlapping copies snapshot the source before writing the destination. */
    u8 row[8*4];
    for (u32 x=0;x<8;++x) { row[x*4]=x*20; row[x*4+1]=row[x*4+2]=0; row[x*4+3]=255; }
    const u32 overlap=vk_eng_color_target_create(NULL,RSX_BE_FMT_R8G8B8A8,8,1,row,32);
    assert(overlap);
    for (u32 x=0;x<8;++x) scale_pixel(overlap,x,0,x*20,0,0);
    assert(tb_vk_color_blit(overlap,(tb_scale_rect){0,0,6,1},overlap,(tb_scale_rect){2,0,6,1},VK_FILTER_NEAREST));
    for (u32 x=0;x<8;++x) scale_pixel(overlap,x,0,(x<2?x:x-2)*20,0,0);
    vk_eng_color_target_release(NULL,overlap);
    pvkAllocateMemory=resolve_allocate_real; pvkFreeMemory=resolve_free_real;
    s_vk.eng_active=0; s_vk.present_split=0;
}
