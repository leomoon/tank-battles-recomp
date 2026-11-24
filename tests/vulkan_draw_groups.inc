#include <assert.h>
/* Included after the actual generated Vulkan backend, not a fake GPU. */
static const char* tb_test_vs=
"cbuffer V : register(b0) { float4 shift; };\n"
"struct I { float4 p : POSITION; };\n"
"float4 main(I v) : SV_Position { return v.p + shift; }\n";
static const char* tb_test_ps=
"cbuffer F : register(b1) { float4 color; };\n"
"float4 main() : SV_Target0 { return color; }\n";
static void pixel(u32 target,int x,int y,int r,int g,int b)
{
    u8 rgba[4]={0};vk_eng_readback(NULL,target,x,y,1,1,rgba,4);
    if(abs(rgba[0]-r)>1 || abs(rgba[1]-g)>1 || abs(rgba[2]-b)>1) {
        fprintf(stderr,"pixel %d,%d: %u,%u,%u wanted %d,%d,%d\n",x,y,rgba[0],rgba[1],rgba[2],r,g,b);abort();
    }
}
static void draw_color(float r,float g,float b,int left)
{
    const float shift[4]={0,0,0,0},color[4]={r,g,b,1};
    const float vertices[3][4]={{-1,-1,.5,1},{3,-1,.5,1},{-1,3,.5,1}};
    const u32 indices[3]={0,1,2};
    vk_eng_bind_vs_constants(NULL,shift,sizeof shift);
    vk_eng_bind_ps_constants(NULL,color,sizeof color);
    vk_eng_set_scissor(NULL,left,0,8,16);
    vk_eng_draw(NULL,RSX_TOPOLOGY_TRIANGLES,vertices,3,16,indices,3);
}

static void check_polygon_offset(void)
{
    const rsx_vertex_layout_plan layout = { .count = 1, .stride = 16 };
    const rsx_be_render_state state = {
        .color_mask = 0xffffffff, .depth_test = 1, .depth_write = 1,
        .depth_func = 0x201 /* LESS */
    };
    const u32 pipeline = vk_eng_pipeline_create(NULL, tb_test_vs, tb_test_ps,
        &state, &layout, 16, RSX_BE_FMT_R8G8B8A8, 1);
    const u32 color = vk_eng_color_target_create(NULL, RSX_BE_FMT_R8G8B8A8,
        16, 16, NULL, 0);
    const u32 depth = vk_eng_depth_target_create(NULL, 16, 16);
    assert(pipeline && color && depth);
    assert(s_vk_engine_backend.set_depth_bias == tb_vk_set_depth_bias);
    vk_eng_bind_pipeline(NULL, pipeline);
    vk_eng_bind_targets(NULL, &color, 1, depth);

    const struct {
        float units, z;
        u32 bits;
        int expected_green;
    } cases[] = {
        { 0, .5f, 24, 0 },
        { -4, .5f, 24, 1 },
        { 4, .5f, 24, 0 },
        { -2, .50001f, 16, 1 },
        { -2, .50001f, 24, 0 },
        { 0, .5f, 16, 0 }
    };
    for (u32 i = 0; i < sizeof cases / sizeof cases[0]; ++i) {
        vk_eng_clear_depth_stencil(NULL, depth, 1, 1.0f, 0);
        tb_vk_set_depth_bias(NULL, 0, 0, 24);
        draw_color(1, 0, 0, 0);

        const float shift[4] = { 0, 0, cases[i].z - .5f, 0 };
        const float green[4] = { 0, 1, 0, 1 };
        const float vertices[3][4] = {
            { -1, -1, .5f, 1 }, { 3, -1, .5f, 1 }, { -1, 3, .5f, 1 }
        };
        tb_vk_set_depth_bias(NULL, 0, cases[i].units, cases[i].bits);
        vk_eng_bind_vs_constants(NULL, shift, sizeof shift);
        vk_eng_bind_ps_constants(NULL, green, sizeof green);
        vk_eng_draw(NULL, RSX_TOPOLOGY_TRIANGLES, vertices, 3, 16, NULL, 0);
        pixel(color, 2, 8, cases[i].expected_green ? 0 : 255,
            cases[i].expected_green ? 255 : 0, 0);
    }

    /* The slope term must also survive state changes within one draw group. */
    const float vertices[3][4] = {
        { -1, -1, .4f, 1 }, { 3, -1, .8f, 1 }, { -1, 3, .4f, 1 }
    };
    const float shift[4] = { 0, 0, 0, 0 };
    const float red[4] = { 1, 0, 0, 1 }, green[4] = { 0, 1, 0, 1 };
    vk_eng_clear_depth_stencil(NULL, depth, 1, 1.0f, 0);
    tb_vk_set_depth_bias(NULL, 0, 0, 24);
    vk_eng_bind_vs_constants(NULL, shift, sizeof shift);
    vk_eng_bind_ps_constants(NULL, red, sizeof red);
    vk_eng_draw(NULL, RSX_TOPOLOGY_TRIANGLES, vertices, 3, 16, NULL, 0);
    tb_vk_set_depth_bias(NULL, -1, 0, 24);
    vk_eng_bind_ps_constants(NULL, green, sizeof green);
    vk_eng_draw(NULL, RSX_TOPOLOGY_TRIANGLES, vertices, 3, 16, NULL, 0);
    pixel(color, 2, 8, 0, 255, 0);

    tb_vk_set_depth_bias(NULL, 0, 0, 24);
    vk_eng_bind_ps_constants(NULL, red, sizeof red);
    vk_eng_draw(NULL, RSX_TOPOLOGY_TRIANGLES, vertices, 3, 16, NULL, 0);
    pixel(color, 2, 8, 0, 255, 0);
    vk_eng_bind_targets(NULL, NULL, 0, 0);
    vk_eng_color_target_release(NULL, color);
    vk_eng_depth_target_release(NULL, depth);
    vk_eng_pipeline_release(NULL, pipeline);
}

static void check_fxaa(int scaled)
{
    if (scaled) {
        assert(tb_present_prepare_scaled(25, 19));
        tb_present_scaled.active = 1;
    }
    const VkImage source = tb_present_image()->img;
    const VkExtent2D extent = tb_present_extent();
    const u32 pixels = extent.width * extent.height;
    const size_t bytes = pixels * 4;
    vk_buffer upload = {0}, readback = {0};
    assert(!vk_create_host_buffer(&upload, bytes, VK_BUFFER_USAGE_TRANSFER_SRC_BIT));
    assert(!vk_create_host_buffer(&readback, bytes * 2, VK_BUFFER_USAGE_TRANSFER_DST_BIT));
    assert(upload.coherent);
    u8* pattern = upload.ptr;
    for (u32 y = 0; y < extent.height; ++y) {
        for (u32 x = 0; x < extent.width; ++x) {
            const size_t offset = (y * extent.width + x) * 4;
            memset(pattern + offset, x > y ? 255 : 0, 3);
            pattern[offset + 3] = 81;
        }
    }
    assert(!vk_begin());
    vk_barrier_image(source, VK_IMAGE_ASPECT_COLOR_BIT, scaled ? VK_IMAGE_LAYOUT_UNDEFINED : VK_IMAGE_LAYOUT_GENERAL);
    VkBufferImageCopy region = {
        .imageSubresource = { VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1 },
        .imageExtent = { extent.width, extent.height, 1 }
    };
    pvkCmdCopyBufferToImage(s_vk.cmd, upload.buf, source,
        VK_IMAGE_LAYOUT_GENERAL, 1, &region);
    assert(tb_fxaa_window_source(s_vk.cmd) == source);
    if (!scaled) assert(!tb_fxaa.state); /* Off allocates no FXAA resources. */
    setenv("TB_FXAA", "1", 1);
    tb_settings_note_antialiasing(1);
    const VkImage filtered = tb_fxaa_window_source(s_vk.cmd);
    assert(filtered && filtered != source);
    assert(tb_fxaa.extent.width == extent.width && tb_fxaa.extent.height == extent.height);
    pvkCmdCopyImageToBuffer(s_vk.cmd, filtered, VK_IMAGE_LAYOUT_GENERAL,
        readback.buf, 1, &region);
    region.bufferOffset = bytes;
    pvkCmdCopyImageToBuffer(s_vk.cmd, source, VK_IMAGE_LAYOUT_GENERAL,
        readback.buf, 1, &region);
    const VkBufferMemoryBarrier host = {
        .sType = VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER,
        .srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT,
        .dstAccessMask = VK_ACCESS_HOST_READ_BIT,
        .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
        .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
        .buffer = readback.buf, .size = VK_WHOLE_SIZE
    };
    pvkCmdPipelineBarrier(s_vk.cmd, VK_PIPELINE_STAGE_TRANSFER_BIT,
        VK_PIPELINE_STAGE_HOST_BIT, 0, 0, NULL, 1, &host, 0, NULL);
    assert(!vk_submit_and_wait());
    vk_invalidate(&readback);
    const u8* result = readback.ptr;
    assert(!memcmp(result + bytes, pattern, bytes));
    u32 smoothed = 0;
    for (u32 i = 0; i < pixels; ++i) {
        smoothed += result[i * 4] > 0 && result[i * 4] < 255;
        assert(result[i * 4 + 3] == 81);
    }
    assert(smoothed > 0);
    assert(result[(extent.width - 1) * 4] == 255);
    assert(result[(pixels - extent.width) * 4] == 0);
    unsetenv("TB_FXAA");
    tb_settings_note_antialiasing(0);
    assert(tb_fxaa_window_source(VK_NULL_HANDLE) == source);

    vk_destroy_buffer(&upload);
    vk_destroy_buffer(&readback);
    if (scaled) tb_present_image_shutdown();
    puts("PASS: optional FXAA smooths edges and preserves alpha and source bytes at the actual frame size");
}
int main(void)
{
    setenv("VK_DRAW_BATCH","0",1);
    setenv("PS3RECOMP_VK_GUEST_PROGRAMS","1",1);
    assert(vk_init_all(16,16,"draw groups fixture")==0);assert(vk_eng_init(NULL,16,16)==0);
    rsx_vertex_layout_plan layout={.count=1,.stride=16};
    rsx_be_render_state state={.color_mask=0xffffffff,.depth_func=0x207};
    u32 pipe=vk_eng_pipeline_create(NULL,tb_test_vs,tb_test_ps,&state,&layout,16,RSX_BE_FMT_R8G8B8A8,1);assert(pipe);
    u32 a=vk_eng_color_target_create(NULL,RSX_BE_FMT_R8G8B8A8,16,16,NULL,0);
    u32 b=vk_eng_color_target_create(NULL,RSX_BE_FMT_R8G8B8A8,16,16,NULL,0);assert(a&&b);
    vk_eng_bind_pipeline(NULL,pipe);vk_eng_bind_targets(NULL,&a,1,0);
    draw_color(1,0,0,0);draw_color(0,1,0,8);
    assert(tb_group_open && s_eb_pending==2 && s_eb.sets==2);
    pixel(a,2,8,255,0,0);pixel(a,10,8,0,255,0);
    /* Distinct vertex/index slices: each primitive covers a different half. */
    const float left[3][4]={{-1,-1,.5,1},{0,-1,.5,1},{-1,3,.5,1}};
    const float right[3][4]={{0,-1,.5,1},{1,-1,.5,1},{1,3,.5,1}};
    const float cyan[4]={0,1,1,1},yellow[4]={1,1,0,1};const u32 triangle[3]={0,1,2};
    vk_eng_set_scissor(NULL,0,0,16,16);
    vk_eng_bind_ps_constants(NULL,cyan,sizeof cyan);vk_eng_draw(NULL,RSX_TOPOLOGY_TRIANGLES,left,3,16,triangle,3);
    vk_eng_bind_ps_constants(NULL,yellow,sizeof yellow);vk_eng_draw(NULL,RSX_TOPOLOGY_TRIANGLES,right,3,16,triangle,3);
    assert(s_eb_pending==2);pixel(a,1,4,0,255,255);pixel(a,14,4,255,255,0);

    draw_color(1,0,0,0);
    s_eb.vb_head=VK_EB_VB_BYTES-8;s_eb.ib_head=VK_EB_IB_BYTES-8;s_eb.ub_head=VK_EB_UB_BYTES-8;
    draw_color(0,1,0,8);assert(s_eb_pending==1 && s_eb.sets==1);
    pixel(a,2,8,255,0,0);pixel(a,10,8,0,255,0);
    /* Mutate the caller's constants and force the ring/pool boundary. */
    for(int i=0;i<140;i++)draw_color(i&1,!(i&1),0,(i&1)?0:8);
    vk_eng_submit_and_wait(NULL,RSX_BE_FLUSH_GUEST_REFERENCE);
    assert(!tb_group_open && !s_eb_pending && !s_eb.sets);
    pixel(a,2,8,255,0,0);pixel(a,10,8,0,255,0);
    draw_color(0,0,1,0);vk_eng_bind_targets(NULL,&b,1,0);draw_color(1,1,0,8);
    /* Attachment switches end the pass, retaining both immutable draws in
     * the same bounded submission instead of forcing a fence boundary. */
    assert(s_eb_pending==2);pixel(a,2,8,0,0,255);pixel(b,10,8,255,255,0);
    if (tb_async_active()) {
        /* An idle walker hands the open batch to the GPU without waiting. */
        vk_eng_bind_targets(NULL,&a,1,0);draw_color(0,1,0,0);assert(s_eb_pending==1);
        tb_vk_idle_submit();assert(!s_eb_pending && tb_async_inflight());
        pixel(a,2,8,0,255,0);
    }
    /* A partial upload has to end a pending draw pass before reusing staging;
     * with asynchronous batches it joins the batch through the staging ring. */
    u32 texture=vk_eng_texture_create(NULL,RSX_BE_FMT_R8G8B8A8,2,2,1,1,0xAAE4,0x85);assert(texture);
    vk_eng_bind_targets(NULL,&a,1,0);draw_color(1,0,1,0);
    u8 texels[16]={255};vk_eng_texture_upload(NULL,texture,0,0,2,1,texels,8,1);
    assert(!tb_group_open && (tb_async_active() ? s_eb_pending==2 : !s_eb_pending));pixel(a,2,8,255,0,255);
    /* The same descriptors must retain different texture bindings per draw. */
    const char* textured_ps="Texture2D rsx_tex[16] : register(t0); SamplerState rsx_samp[16] : register(s0); float4 main() : SV_Target0 { return rsx_tex[0].Sample(rsx_samp[0],float2(.25,.25)); }";
    u32 texpipe=vk_eng_pipeline_create(NULL,tb_test_vs,textured_ps,&state,&layout,16,RSX_BE_FMT_R8G8B8A8,1);assert(texpipe);
    u32 redtex=vk_eng_texture_create(NULL,RSX_BE_FMT_R8G8B8A8,1,1,1,1,0xAAE4,0x85);
    u32 greentex=vk_eng_texture_create(NULL,RSX_BE_FMT_R8G8B8A8,1,1,1,1,0xAAE4,0x85);assert(redtex&&greentex);
    const u8 red[4]={255,0,0,255},green[4]={0,255,0,255};
    vk_eng_texture_upload(NULL,redtex,0,0,1,1,red,4,1);vk_eng_texture_upload(NULL,greentex,0,0,1,1,green,4,1);
    u32 textures[RSX_MAX_TEXTURES]={redtex};rsx_be_sampler_desc samplers[RSX_MAX_TEXTURES]={0};
    const u32 texture_setup=s_eb_pending;
    vk_eng_bind_pipeline(NULL,texpipe);vk_eng_bind_textures(NULL,textures,samplers,1);draw_color(0,0,0,0);
    textures[0]=greentex;vk_eng_bind_textures(NULL,textures,samplers,1);draw_color(0,0,0,8);
    assert(s_eb_pending==texture_setup+2);pixel(a,2,8,255,0,0);pixel(a,10,8,0,255,0);
    /* Uploading over a sampled texture completes the earlier draw first. */
    draw_color(0,0,0,0);vk_eng_texture_upload(NULL,greentex,0,0,1,1,red,4,1);draw_color(0,0,0,8);
    pixel(a,2,8,0,255,0);pixel(a,10,8,255,0,0);
    vk_eng_bind_textures(NULL,textures,samplers,0);vk_eng_bind_pipeline(NULL,pipe);
    /* Start a pending copy, then reuse its source for a new draw group. */
    s_vk.eng_active=1;s_vk.present_split=1;
    vk_eng_present(NULL,a);assert(s_vk.present_inflight);
    draw_color(0,1,1,0);rsx_vulkan_backend_present_finish();
    assert(rsx_vulkan_backend_presented_center()==0xffff0000u);
    s_vk.eng_active=0;s_vk.present_split=0;
    pixel(a,2,8,0,255,255);
    {   /* NV3089 on the GPU: the 2:1 resolve from texel 1, inside an open batch,
         * equals the guest point-sampling rule texel for texel. */
        enum { SW = 64, SH = 32 };
        static u8 seed[SW*SH*4], out[(SW/2)*(SH/2)*4];
        for (u32 i = 0; i < SW*SH; i++) { seed[i*4]=(u8)i; seed[i*4+1]=(u8)(i>>8); seed[i*4+2]=(u8)(i*29); seed[i*4+3]=(u8)~i; }
        const u32 src=vk_eng_color_target_create(NULL,RSX_BE_FMT_R8G8B8A8,SW,SH,seed,SW*4);
        const u32 dst=vk_eng_color_target_create(NULL,RSX_BE_FMT_R8G8B8A8,SW/2,SH/2,NULL,0);assert(src&&dst);
        s_vk.eng_active=1;
        assert(tb_vk_blit_exact());                       /* the one-time device check */
        draw_color(1,0,0,0);                              /* something pending in the batch */
        const u32 w=(SW-1)/2,h=(SH-1)/2;
        assert(tb_vk_surface_blit(src,1,1,2,2,dst,0,0,w,h));
        assert(tb_async_active() ? s_eb_pending>=2 : !s_eb_pending);
        vk_eng_readback(NULL,dst,0,0,w,h,out,w*4);
        for (u32 y=0;y<h;y++) for (u32 x=0;x<w;x++)
            assert(!memcmp(out+(y*w+x)*4,seed+((1+2*y)*SW+1+2*x)*4,4));
        assert(!tb_vk_surface_blit(src,0,0,1,1,src,0,0,1,1));     /* no self copies */
        assert(!tb_vk_surface_blit(src,2,1,2,2,dst,0,0,w+1,h));   /* last texel 2+2*31 is past the source edge */
        s_vk.eng_active=0;
        vk_eng_release(NULL,src);vk_eng_release(NULL,dst);
    }
    draw_color(1,0,0,0);vk_eng_color_target_release(NULL,a);
    assert(!tb_group_open && !s_eb_pending);
    check_polygon_offset();
    check_fxaa(0);
    check_fxaa(1);
    tb_fxaa_shutdown();
    vk_eng_shutdown(NULL);vk_guest_shutdown();
    vk_destroy_image(&s_vk.color);vk_destroy_image(&s_vk.depth);
    vk_destroy_buffer(&s_vk.readback);
    pvkDeviceWaitIdle(s_vk.device);pvkDestroyDevice(s_vk.device,NULL);pvkDestroyInstance(s_vk.instance,NULL);dlclose(s_vk.lib);
    puts("PASS: real Vulkan draw groups preserve per-draw state, ring reset, target switches, uploads, readback, pending present and release");return 0;
}
