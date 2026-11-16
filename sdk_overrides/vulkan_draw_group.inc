/* Port-owned bounded same-target draw groups. The SDK rings give each draw
 * immutable vertex/index/constants/descriptors until the existing fence wait.
 * Reuse a subpass only for attachment operations in rasterization order;
 * sampling a bound attachment, clears, uploads and all consumers stay serial.
 * https://docs.vulkan.org/spec/latest/chapters/primsrast.html#primsrast-order */
static VkFramebuffer tb_group_framebuffer;
static VkRenderPass tb_group_renderpass;
static int tb_group_open;
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
}
