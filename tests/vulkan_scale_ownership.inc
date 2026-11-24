/* Actual backend ownership: stale Native images are intentional poison.
 * Guest reads, partial writes and recovery must observe the newer owner. */
static PFN_vkCmdCopyBufferToImage ownership_real_upload;
static unsigned ownership_uploads;
static PFN_vkCmdDraw ownership_real_draw;
static unsigned ownership_draws;
static VKAPI_ATTR void VKAPI_CALL ownership_draw(VkCommandBuffer command, uint32_t vertices, uint32_t instances,
        uint32_t first, uint32_t instance)
{
    ++ownership_draws;
    ownership_real_draw(command,vertices,instances,first,instance);
}

static u8 ownership_stencil(u32 depth)
{
    const vk_eng_obj* image=vk_eng_get(depth,VK_ENG_DEPTH);
    vk_buffer* stage=vk_stage_get(&s_rb_stage,4,VK_BUFFER_USAGE_TRANSFER_DST_BIT);
    assert(image && stage && !vk_begin());
    vk_barrier_image(image->im.img,s_vk.depth_aspect,VK_IMAGE_LAYOUT_GENERAL);
    const VkBufferImageCopy region={.imageSubresource={VK_IMAGE_ASPECT_STENCIL_BIT,0,0,1},.imageExtent={1,1,1}};
    pvkCmdCopyImageToBuffer(s_vk.cmd,image->im.img,VK_IMAGE_LAYOUT_GENERAL,stage->buf,1,&region);
    const VkBufferMemoryBarrier barrier={.sType=VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER,
        .srcAccessMask=VK_ACCESS_TRANSFER_WRITE_BIT,.dstAccessMask=VK_ACCESS_HOST_READ_BIT,
        .srcQueueFamilyIndex=VK_QUEUE_FAMILY_IGNORED,.dstQueueFamilyIndex=VK_QUEUE_FAMILY_IGNORED,
        .buffer=stage->buf,.size=VK_WHOLE_SIZE};
    pvkCmdPipelineBarrier(s_vk.cmd,VK_PIPELINE_STAGE_TRANSFER_BIT,VK_PIPELINE_STAGE_HOST_BIT,0,0,NULL,1,&barrier,0,NULL);
    assert(!vk_submit_and_wait());
    vk_invalidate(stage);
    return *(const u8*)stage->ptr;
}
static VKAPI_ATTR void VKAPI_CALL ownership_upload(VkCommandBuffer command, VkBuffer src,
        VkImage dst, VkImageLayout layout, uint32_t count, const VkBufferImageCopy* regions)
{
    ++ownership_uploads;
    ownership_real_upload(command,src,dst,layout,count,regions);
}

static void check_ownership(tb_scale_layout group, unsigned percent)
{
    tb_vk_scale_reset();
    const float red[]={1,0,0,1},green[]={0,1,0,1};
    u32 color=vk_eng_color_target_create(NULL,RSX_BE_FMT_R8G8B8A8,11,7,NULL,0);
    const u32 depth=vk_eng_depth_target_create(NULL,11,7);
    const u32 display=vk_eng_color_target_create(NULL,RSX_BE_FMT_R8G8B8A8,6,4,NULL,0);
    assert(color && depth && display);
    vk_eng_clear_color(NULL,color,red);
    vk_eng_clear_depth_stencil(NULL,depth,RSX_BE_CLEAR_DEPTH|RSX_BE_CLEAR_STENCIL,1,7);
    tb_vk_scale_prepare(&group,percent);
    tb_vk_scale_attach(group.routes[0].color,color,0);
    tb_vk_scale_attach(group.depth,depth,1);
    tb_vk_scale_attach(group.routes[0].display,display,0);
    tb_scale_binding selected={0};
    assert(tb_vk_scale_use(group.routes[0].color,group.depth,&selected)==(percent!=100));
    const u32 target=percent==100?color:selected.color;
    const u32 zeta=percent==100?depth:selected.depth;
    static const char vs[]="float4 main(float4 p:POSITION):SV_Position { return p; }";
    static const char ps[]="cbuffer C:register(b1) { float4 color; }; float4 main():SV_Target0 { return float4(color.rgb,1); }";
    const rsx_vertex_layout_plan layout={.count=1,.stride=16};
    const rsx_be_render_state state={.color_mask=0xffffffff,.depth_func=0x207};
    const u32 pipeline=vk_eng_pipeline_create(NULL,vs,ps,&state,&layout,16,RSX_BE_FMT_R8G8B8A8,1);
    assert(pipeline);
    vk_eng_bind_targets(NULL,&target,1,zeta);
    vk_eng_bind_pipeline(NULL,pipeline);
    vk_eng_bind_textures(NULL,NULL,NULL,0);
    vk_eng_set_viewport(NULL,0,0,11,7);
    vk_eng_set_scissor(NULL,0,0,11,7);
    tb_vk_set_depth_bias(NULL,0,0,24);
    ownership_real_draw=pvkCmdDraw;
    pvkCmdDraw=ownership_draw;
    ownership_draws=0;
    for (unsigned draw=0;draw<145;++draw) scale_draw(scale_triangle,3,0,1,0,percent!=100);
    scale_pixel(target,0,0,0,255,0);
    assert(ownership_draws==145); /* more than two batches, no hidden replay */
    pvkCmdDraw=ownership_real_draw;
    if (percent==100) {
        assert(!tb_scaled.percent && tb_vk_scale_sample(color)==color);
    } else {
        tb_vk_scale_image* owned=tb_scale_native_image(color);
        assert(owned && owned->authoritative && owned->serial>owned->native_serial);
        assert(!s_eobj[color].im.img); /* stable guest handle, no Native allocation */
        ++tb_scale_internal;
        assert(tb_scale_native_storage(color));
        vk_eng_clear_color(NULL,color,red); /* poison a diagnostic Native representation */
        scale_pixel(color,0,0,255,0,0);
        --tb_scale_internal;
        const u32 view=vk_eng_surface_view(NULL,color,0xaae4,0x85);
        const u32 snapshot=vk_eng_color_snapshot(NULL,color);
        assert(view && snapshot && s_eobj[view].surface==target);
        assert(s_eobj[snapshot].w==s_eobj[target].w && s_eobj[snapshot].guest_size.width==11);
        scale_pixel(snapshot,0,0,0,255,0);
        assert(owned->serial>owned->native_serial); /* aliases do not materialize */
        ownership_real_upload=pvkCmdCopyBufferToImage;
        pvkCmdCopyBufferToImage=ownership_upload;
        ownership_uploads=0;
        scale_pixel(color,0,0,0,255,0);
        assert(ownership_uploads==1 && owned->native_serial==owned->serial);
        scale_pixel(color,10,6,0,255,0);
        assert(ownership_uploads==1); /* cached full-image materialized region */
        pvkCmdCopyBufferToImage=ownership_real_upload;

        const u32 host_w=s_eobj[target].w, host_h=s_eobj[target].h;
        u8 ramp[33*21*4], reconstructed[11*7*4];
        for (u32 y=0;y<host_h;++y) for (u32 x=0;x<host_w;++x) {
            u8* pixel=ramp+(y*host_w+x)*4;
            pixel[0]=(u8)x; pixel[1]=(u8)y; pixel[2]=0; pixel[3]=255;
        }
        vk_eng_color_target_write(NULL,target,0,0,host_w,host_h,ramp,host_w*4);
        tb_vk_scale_wrote(target);
        vk_eng_readback(NULL,color,0,0,11,7,reconstructed,44);
        for (u32 y=0;y<7;++y) for (u32 x=0;x<11;++x) {
            const u8* pixel=reconstructed+(y*11+x)*4;
            assert(pixel[0]==(2*x+1)*host_w/22 && pixel[1]==(2*y+1)*host_h/14 && pixel[2]==0 && pixel[3]==255);
        }

        static const char sampled_ps[]="Texture2D rsx_tex[16]:register(t0); SamplerState rsx_samp[16]:register(s0); float4 main():SV_Target0 { return rsx_tex[0].Sample(rsx_samp[0],float2(.75,.75)); }";
        const u32 sampled_pipeline=vk_eng_pipeline_create(NULL,vs,sampled_ps,&state,&layout,16,RSX_BE_FMT_R8G8B8A8,1);
        assert(sampled_pipeline);
        tb_vk_scale_clear(group.routes[0].color,group.depth,(tb_scale_rect){0,0,11,7},TB_SCALE_CLEAR_COLOR,red,1,0);
        vk_eng_bind_pipeline(NULL,sampled_pipeline);
        u32 textures[RSX_MAX_TEXTURES]={snapshot};
        rsx_be_sampler_desc samplers[RSX_MAX_TEXTURES]={0};
        vk_eng_bind_textures(NULL,textures,samplers,1);
        scale_draw(scale_triangle,3,0,0,0,1);
        scale_pixel(target,0,0,0,255,0); /* feedback samples the earlier immutable image */
        vk_eng_bind_textures(NULL,NULL,NULL,0);
        vk_eng_bind_pipeline(NULL,pipeline);
        vk_eng_pipeline_release(NULL,sampled_pipeline);

        tb_vk_scale_clear(group.routes[0].color,group.depth,(tb_scale_rect){0,0,11,7},
            TB_SCALE_CLEAR_COLOR|TB_SCALE_CLEAR_DEPTH|TB_SCALE_CLEAR_STENCIL,green,.375f,9);
        const u8 blue[3*3*4]={0,0,255,255,0,0,255,255,0,0,255,255,
            0,0,255,255,0,0,255,255,0,0,255,255,0,0,255,255,0,0,255,255,0,0,255,255};
        vk_eng_color_target_write(NULL,color,2,2,3,3,blue,12);
        assert(!s_eobj[color].im.img); /* temporary upload representation retired */
        tb_scale_rect patch;
        assert(tb_scale_rect_map(owned->extent,(tb_scale_rect){2,2,3,3},&patch)==TB_SCALE_RECT_MAPPED);
        scale_pixel(target,patch.x,patch.y,0,0,255);
        scale_pixel(target,s_eobj[target].w-1,s_eobj[target].h-1,0,255,0);
        assert(owned->serial>owned->native_serial);
        /* An immutable feedback snapshot survives later writes to its source. */
        scale_pixel(snapshot,0,0,0,255,0);
        float observed_depth=-1;
        assert(!vk_eng_depth_readback(NULL,depth,10,6,1,1,&observed_depth,4));
        assert(observed_depth==.375f && tb_scaled.depth.native_serial==tb_scaled.depth.serial);
        if (s_vk.depth_aspect & VK_IMAGE_ASPECT_STENCIL_BIT)
            assert(ownership_stencil(depth)==9 && ownership_stencil(zeta)==9);
        assert(tb_vk_scale_resolve(group.routes[0].color,group.routes[0].display,
            (tb_scale_rect){0,0,11,7},(tb_scale_rect){0,0,6,4}));
        tb_vk_scale_publish(group.routes[0].display,1);
        assert(tb_scaled.display[0].authoritative && tb_vk_scale_scanout(group.routes[0].display));
        /* Unsupported operations after scaled draws must reconstruct every
         * current representation before allowing Native continuation. */
        const u8 depth_write[]={0x80,0,0,20};
        assert(tb_vk_scale_depth_upload(depth,(tb_scale_rect){0,0,1,1},depth_write,4));
        assert(!vk_eng_depth_readback(NULL,depth,0,0,1,1,&observed_depth,4) && observed_depth==8388608.0f/16777215.0f);
        assert(!vk_eng_depth_readback(NULL,depth,10,6,1,1,&observed_depth,4) && observed_depth==.375f);
        if (s_vk.depth_aspect & VK_IMAGE_ASPECT_STENCIL_BIT) assert(ownership_stencil(depth)==20);
        assert(tb_scale_rejected && !owned->authoritative && tb_vk_scale_sample(color)==color);
        scale_pixel(color,10,6,0,255,0);
        assert(!tb_vk_scale_use(group.routes[0].color,group.depth,&selected));
        const u32 native_view=vk_eng_surface_view(NULL,color,0xaae4,0x85);
        assert(native_view && s_eobj[native_view].surface==color);
        vk_eng_clear_color(NULL,color,red);
        scale_pixel(color,0,0,255,0,0);
        scale_pixel(target,0,0,0,255,0);
        vk_eng_texture_release(NULL,snapshot);

        /* Replacing a Native allocation retires the old generation only after
         * preserving every other authoritative attachment (including stencil). */
        tb_vk_scale_reset();
        tb_vk_scale_prepare(&group,percent);
        tb_vk_scale_attach(group.routes[0].color,color,0);
        tb_vk_scale_attach(group.depth,depth,1);
        tb_vk_scale_attach(group.routes[0].display,display,0);
        assert(tb_vk_scale_use(group.routes[0].color,group.depth,&selected));
        tb_vk_scale_clear(group.routes[0].color,group.depth,(tb_scale_rect){0,0,11,7},
            TB_SCALE_CLEAR_COLOR|TB_SCALE_CLEAR_DEPTH|TB_SCALE_CLEAR_STENCIL,green,.625f,12);
        vk_eng_color_target_release(NULL,color);
        assert(tb_scale_rejected && !tb_scaled.color[0].native);
        color=vk_eng_color_target_create(NULL,RSX_BE_FMT_R8G8B8A8,11,7,NULL,0);
        assert(color);
        ++group.routes[0].color.generation;
        tb_vk_scale_attach(group.routes[0].color,color,0);
        assert(!tb_vk_scale_use(group.routes[0].color,group.depth,&selected));
        assert(!vk_eng_depth_readback(NULL,depth,0,0,1,1,&observed_depth,4) && observed_depth==.625f);
        if (s_vk.depth_aspect & VK_IMAGE_ASPECT_STENCIL_BIT) assert(ownership_stencil(depth)==12);
    }
    vk_eng_bind_targets(NULL,NULL,0,0);
    vk_eng_pipeline_release(NULL,pipeline);
    tb_vk_scale_discard();
    vk_eng_color_target_release(NULL,color);
    vk_eng_color_target_release(NULL,display);
    vk_eng_depth_target_release(NULL,depth);
    tb_vk_scale_reset();
}
