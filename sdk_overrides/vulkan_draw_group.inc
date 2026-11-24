/* Port-owned bounded same-target draw groups. The SDK rings give each draw
 * immutable vertex/index/constants/descriptors until the existing fence wait.
 * Reuse a subpass only for attachment operations in rasterization order;
 * sampling a bound attachment, clears, uploads and all consumers stay serial.
 * https://docs.vulkan.org/spec/latest/chapters/primsrast.html#primsrast-order */
static VkFramebuffer tb_group_framebuffer;
static VkRenderPass tb_group_renderpass;
static int tb_group_open;
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
    u32 vertex_count, index_count, stencil, color, depth, render_scale;
    float depth_bias, depth_slope, depth_min, depth_max;
} tb_group_draw;

static void tb_group_emit(const tb_group_draw* draw)
{
    /* Count at physical emission, rather than enqueue time.
     * Auxiliary backend draws remain in TB_FC_DRAW, outside these engine counts. */
    tb_frame_budget_count(draw->render_scale
        ? TB_FC_ENGINE_SCALED_DRAW : TB_FC_ENGINE_NATIVE_DRAW, 1);
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
    tb_group_color=tb_group_depth=tb_group_targets=0;
}
