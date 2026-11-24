/* Port-owned bounded same-target draw groups. The SDK rings give each draw
 * immutable vertex/index/constants/descriptors until the existing fence wait.
 * Reuse a subpass only for attachment operations in rasterization order;
 * sampling a bound attachment, clears, uploads and all consumers stay serial.
 * https://docs.vulkan.org/spec/latest/chapters/primsrast.html#primsrast-order */
static VkFramebuffer tb_group_framebuffer;
static VkRenderPass tb_group_renderpass;
static int tb_group_open;
static int tb_group_scaled;
static u32 tb_group_color, tb_group_depth, tb_group_targets;
typedef struct tb_group_draw {
    VkFramebuffer framebuffer;
    VkRenderPass renderpass;
    VkExtent2D extent;
    VkPipeline pipeline;
    VkPipelineLayout layout;
    VkDescriptorSet descriptors;
    VkBuffer vertices, indices;
    VkDeviceSize vertex_offset, index_offset;
    VkViewport viewport;
    VkRect2D scissor;
    u32 vertex_count, index_count, stencil, color, depth;
    float depth_bias, depth_slope, depth_min, depth_max;
} tb_group_draw;
static tb_group_draw tb_group_mirrors[64];
static u32 tb_group_mirror_count;

static void tb_group_emit(const tb_group_draw* draw)
{
    pvkCmdBindPipeline(s_vk.cmd,VK_PIPELINE_BIND_POINT_GRAPHICS,draw->pipeline);
    pvkCmdSetViewport(s_vk.cmd,0,1,&draw->viewport);
    pvkCmdSetScissor(s_vk.cmd,0,1,&draw->scissor);
    pvkCmdSetStencilReference(s_vk.cmd,VK_STENCIL_FACE_FRONT_AND_BACK,draw->stencil);
    pvkCmdSetDepthBias(s_vk.cmd,draw->depth_bias,0,draw->depth_slope);
    if(s_vk.depth_bounds_ok)pvkCmdSetDepthBounds(s_vk.cmd,draw->depth_min,draw->depth_max);
    pvkCmdBindDescriptorSets(s_vk.cmd,VK_PIPELINE_BIND_POINT_GRAPHICS,
        draw->layout,0,1,&draw->descriptors,0,NULL);
    pvkCmdBindVertexBuffers(s_vk.cmd,0,1,&draw->vertices,&draw->vertex_offset);
    if(draw->index_count) {
        pvkCmdBindIndexBuffer(s_vk.cmd,draw->indices,draw->index_offset,VK_INDEX_TYPE_UINT32);
        pvkCmdDrawIndexed(s_vk.cmd,draw->index_count,1,0,0,0);
    } else pvkCmdDraw(s_vk.cmd,draw->vertex_count,1,0,0);
}

static int tb_group_mirror_reads(u32 handle)
{
    for(u32 i=0;i<tb_group_mirror_count;++i)
        if(handle==tb_group_mirrors[i].color || handle==tb_group_mirrors[i].depth)return 1;
    return 0;
}
/* Read once: this is asked several times per draw, and getenv was about 6% of
 * the walker's samples once the GPU waits were gone. */
static int tb_group_on(void)
{
    static int on=-1;
    if(on<0){const char* mode=getenv("TB_VK_SUBMIT_MODE");on=!mode || strcmp(mode,"grouped")==0;}
    return on;
}
static void tb_group_end(void)
{
    if(tb_group_open)pvkCmdEndRenderPass(s_vk.cmd);
    tb_group_open=0;tb_group_framebuffer=VK_NULL_HANDLE;tb_group_renderpass=VK_NULL_HANDLE;
    tb_group_scaled=0;
    tb_group_color=tb_group_depth=tb_group_targets=0;
    if(!tb_group_mirror_count)return;
    /* All descriptors and ring slices live until the same batch fence. Replay
     * mirrors before any upload, clear, consumer or slot reuse can mutate them.
     * They share one scaled framebuffer, with no attachment texture feedback. */
    const VkMemoryBarrier barrier={
        .sType=VK_STRUCTURE_TYPE_MEMORY_BARRIER,
        .srcAccessMask=VK_ACCESS_MEMORY_WRITE_BIT,
        .dstAccessMask=VK_ACCESS_MEMORY_READ_BIT|VK_ACCESS_MEMORY_WRITE_BIT
    };
    pvkCmdPipelineBarrier(s_vk.cmd,VK_PIPELINE_STAGE_ALL_COMMANDS_BIT,
        VK_PIPELINE_STAGE_ALL_COMMANDS_BIT,0,1,&barrier,0,NULL,0,NULL);
    const tb_group_draw* first=&tb_group_mirrors[0];
    const VkRenderPassBeginInfo begin={
        .sType=VK_STRUCTURE_TYPE_RENDER_PASS_BEGIN_INFO,
        .renderPass=first->renderpass,.framebuffer=first->framebuffer,
        .renderArea={{0,0},first->extent}
    };
    pvkCmdBeginRenderPass(s_vk.cmd,&begin,VK_SUBPASS_CONTENTS_INLINE);
    for(u32 i=0;i<tb_group_mirror_count;++i)tb_group_emit(&tb_group_mirrors[i]);
    pvkCmdEndRenderPass(s_vk.cmd);
    tb_group_mirror_count=0;
}
