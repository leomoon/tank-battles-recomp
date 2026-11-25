/* Exact depth/stencil and partial-clear checks at every supported preset.
 * Use an even guest extent so all presets have an exact isotropic ratio. */
static void check_depth_stencil(unsigned percent)
{
    tb_vk_scale_reset();
    tb_scale_layout group={0};
    group.count=1;
    group.depth=(tb_scale_surface){0,0x1000,48,2,1,{12,8}};
    group.routes[0].color=(tb_scale_surface){0,0x2000,48,RSX_BE_FMT_R8G8B8A8,2,{12,8}};
    group.routes[0].display=(tb_scale_surface){0,0x3000,24,RSX_BE_FMT_R8G8B8A8,3,{6,4}};
    const u32 native=vk_eng_color_target_create(NULL,RSX_BE_FMT_R8G8B8A8,12,8,NULL,0);
    const u32 native_z=vk_eng_depth_target_create(NULL,12,8);
    assert(native && native_z);
    tb_vk_scale_prepare(&group,percent);
    const u32 color=percent==100?native:tb_scaled.color[0].handle;
    const u32 depth=percent==100?native_z:tb_scaled.depth.handle;
    const u32 w=s_eobj[color].w,h=s_eobj[color].h;
    const int stencil=!!(s_vk.depth_aspect & VK_IMAGE_ASPECT_STENCIL_BIT);
    static const char vs[]="float4 main(float4 p:POSITION):SV_Position { return p; }";
    static const char ps[]="cbuffer C:register(b1) { float4 color; }; float4 main():SV_Target0 { return float4(color.rgb,1); }";
    const rsx_vertex_layout_plan layout={.count=1,.stride=16};
    rsx_be_render_state state={.color_mask=0xffffffff,.depth_test=1,.depth_write=1,.depth_func=0x201,
        .stencil_enable=stencil,.s_func=0x202,.s_func_mask=0xff};
    const u32 pipeline=vk_eng_pipeline_create(NULL,vs,ps,&state,&layout,16,RSX_BE_FMT_R8G8B8A8,1);
    assert(pipeline);
    vk_eng_bind_targets(NULL,&color,1,depth);
    vk_eng_bind_pipeline(NULL,pipeline);
    vk_eng_bind_textures(NULL,NULL,NULL,0);
    vk_eng_set_viewport(NULL,0,0,12,8);
    vk_eng_set_scissor(NULL,0,0,12,8);
    tb_vk_set_depth_bias(NULL,0,0,24);
    vk_eng_clear_color(NULL,color,scale_black);
    vk_eng_clear_depth_stencil(NULL,depth,RSX_BE_CLEAR_DEPTH|RSX_BE_CLEAR_STENCIL,1,3);
    vk_eng_set_stencil_ref(NULL,3);
    scale_draw(scale_triangle,3,0,1,0,percent!=100);
    scale_pixel(color,w/2,h/2,0,255,0);
    scale_draw(scale_triangle,3,1,0,0,percent!=100); /* equal depth fails LESS */
    scale_pixel(color,w/2,h/2,0,255,0);
    const float near_triangle[][4]={{-1,-1,.25f,1},{3,-1,.25f,1},{-1,3,.25f,1}};
    if (stencil) {
        vk_eng_set_stencil_ref(NULL,2);
        scale_draw(near_triangle,3,1,0,0,percent!=100);
        scale_pixel(color,w/2,h/2,0,255,0);
    }
    vk_eng_set_stencil_ref(NULL,3);
    scale_draw(near_triangle,3,0,0,1,percent!=100);
    /* Restore only the left half's depth/stencil; subsequent geometry must
     * pass there and fail on the untouched half, at every mapped pixel. */
    if (percent==100) {
        VkRenderPass pass=vk_eng_render_pass(s_eobj[color].fmt,1);
        VkFramebuffer fb=vk_eng_framebuffer(pass,&color,1,s_eobj[depth].im.view,depth,w,h);
        assert(fb && !vk_begin());
        vk_barrier_image(s_eobj[depth].im.img,s_vk.depth_aspect,VK_IMAGE_LAYOUT_GENERAL);
        const VkRenderPassBeginInfo begin={.sType=VK_STRUCTURE_TYPE_RENDER_PASS_BEGIN_INFO,.renderPass=pass,
            .framebuffer=fb,.renderArea={{0,0},{w,h}}};
        const VkClearAttachment clear={.aspectMask=s_vk.depth_aspect,.clearValue.depthStencil={1,7}};
        const VkClearRect rect={.rect={{0,0},{w/2,h}},.layerCount=1};
        pvkCmdBeginRenderPass(s_vk.cmd,&begin,VK_SUBPASS_CONTENTS_INLINE);
        pvkCmdClearAttachments(s_vk.cmd,1,&clear,1,&rect);
        pvkCmdEndRenderPass(s_vk.cmd);
        assert(!vk_submit_and_wait());
    } else tb_vk_scale_clear(group.routes[0].color,group.depth,(tb_scale_rect){0,0,6,8},
        TB_SCALE_CLEAR_DEPTH|TB_SCALE_CLEAR_STENCIL,scale_black,1,7);
    vk_eng_set_stencil_ref(NULL,7);
    scale_draw(scale_triangle,3,1,0,0,percent!=100);
    u8 pixels[36*24*4];
    vk_eng_readback(NULL,color,0,0,w,h,pixels,w*4);
    for (u32 y=0;y<h;++y) for (u32 x=0;x<w;++x) {
        const u8* p=pixels+(y*w+x)*4;
        assert(p[0]==(x<w/2?255:0) && p[1]==0 && p[2]==(x<w/2?0:255) && p[3]==255);
    }
    vk_eng_bind_targets(NULL,NULL,0,0);
    vk_eng_pipeline_release(NULL,pipeline);
    tb_vk_scale_reset();
    vk_eng_color_target_release(NULL,native);
    vk_eng_depth_target_release(NULL,native_z);
    printf("PASS: %u%% depth LESS, stencil reference and partial depth/stencil clear\n",percent);
}
