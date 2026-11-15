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
    assert(s_eb_pending==1);pixel(a,2,8,0,0,255);pixel(b,10,8,255,255,0);
    /* A partial upload has to end a pending draw pass before reusing staging. */
    u32 texture=vk_eng_texture_create(NULL,RSX_BE_FMT_R8G8B8A8,2,2,1,1,0xAAE4,0x85);assert(texture);
    vk_eng_bind_targets(NULL,&a,1,0);draw_color(1,0,1,0);
    u8 texels[16]={255};vk_eng_texture_upload(NULL,texture,0,0,2,1,texels,8,1);
    assert(!tb_group_open && !s_eb_pending);pixel(a,2,8,255,0,255);
    /* The same descriptors must retain different texture bindings per draw. */
    const char* textured_ps="Texture2D rsx_tex[16] : register(t0); SamplerState rsx_samp[16] : register(s0); float4 main() : SV_Target0 { return rsx_tex[0].Sample(rsx_samp[0],float2(.25,.25)); }";
    u32 texpipe=vk_eng_pipeline_create(NULL,tb_test_vs,textured_ps,&state,&layout,16,RSX_BE_FMT_R8G8B8A8,1);assert(texpipe);
    u32 redtex=vk_eng_texture_create(NULL,RSX_BE_FMT_R8G8B8A8,1,1,1,1,0xAAE4,0x85);
    u32 greentex=vk_eng_texture_create(NULL,RSX_BE_FMT_R8G8B8A8,1,1,1,1,0xAAE4,0x85);assert(redtex&&greentex);
    const u8 red[4]={255,0,0,255},green[4]={0,255,0,255};
    vk_eng_texture_upload(NULL,redtex,0,0,1,1,red,4,1);vk_eng_texture_upload(NULL,greentex,0,0,1,1,green,4,1);
    u32 textures[RSX_MAX_TEXTURES]={redtex};rsx_be_sampler_desc samplers[RSX_MAX_TEXTURES]={0};
    vk_eng_bind_pipeline(NULL,texpipe);vk_eng_bind_textures(NULL,textures,samplers,1);draw_color(0,0,0,0);
    textures[0]=greentex;vk_eng_bind_textures(NULL,textures,samplers,1);draw_color(0,0,0,8);
    assert(s_eb_pending==2);pixel(a,2,8,255,0,0);pixel(a,10,8,0,255,0);
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
    draw_color(1,0,0,0);vk_eng_color_target_release(NULL,a);
    assert(!tb_group_open && !s_eb_pending);
    vk_eng_shutdown(NULL);vk_guest_shutdown();
    vk_destroy_image(&s_vk.color);vk_destroy_image(&s_vk.depth);
    vk_destroy_buffer(&s_vk.readback);
    pvkDeviceWaitIdle(s_vk.device);pvkDestroyDevice(s_vk.device,NULL);pvkDestroyInstance(s_vk.instance,NULL);dlclose(s_vk.lib);
    puts("PASS: real Vulkan draw groups preserve per-draw state, ring reset, target switches, uploads, readback, pending present and release");return 0;
}
