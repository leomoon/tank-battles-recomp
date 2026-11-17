/* Per-frame Vulkan work counters and GPU timestamps for the frame-budget
 * timeline (frame_budget.h, scripts/frame_budget.py).
 *
 * Included right after the backend declares its run-time loaded entry points.
 * Each wrapped entry point becomes a function-like macro, so every call site in
 * the backend and in the overrides included into it is counted, while the
 * pointer declarations and loader assignments (never followed by "(") are left
 * alone. A macro does not re-expand its own name, and the helpers below call
 * the parenthesized pointer, which also suppresses expansion.
 *
 * Everything is a predictable branch unless TB_FRAME_BUDGET is recording. */
#include "../frame_budget.h"

#define TB_FC(id, n, call) (tb_frame_budget_count((id), (uint32_t)(n)), (call))
#define pvkQueueSubmit(q, n, s, f)           TB_FC(TB_FC_QUEUE_SUBMIT, 1, (pvkQueueSubmit)(q, n, s, f))
#define pvkAllocateMemory(d, i, a, m)        TB_FC(TB_FC_ALLOC_MEMORY, 1, (pvkAllocateMemory)(d, i, a, m))
#define pvkCreateImage(d, i, a, m)           TB_FC(TB_FC_CREATE_IMAGE, 1, (pvkCreateImage)(d, i, a, m))
#define pvkCreateBuffer(d, i, a, m)          TB_FC(TB_FC_CREATE_BUFFER, 1, (pvkCreateBuffer)(d, i, a, m))
#define pvkCmdPipelineBarrier(...)           TB_FC(TB_FC_BARRIER, 1, (pvkCmdPipelineBarrier)(__VA_ARGS__))
#define pvkAllocateDescriptorSets(d, i, s)   TB_FC(TB_FC_DESC_ALLOC, 1, (pvkAllocateDescriptorSets)(d, i, s))
#define pvkUpdateDescriptorSets(d, n, w, c, p) TB_FC(TB_FC_DESC_WRITE, n, (pvkUpdateDescriptorSets)(d, n, w, c, p))
#define pvkCmdClearColorImage(...)           TB_FC(TB_FC_CLEAR_IMAGE, 1, (pvkCmdClearColorImage)(__VA_ARGS__))
#define pvkCmdClearDepthStencilImage(...)    TB_FC(TB_FC_CLEAR_IMAGE, 1, (pvkCmdClearDepthStencilImage)(__VA_ARGS__))
#define pvkCmdCopyImageToBuffer(...)         TB_FC(TB_FC_COPY_IMAGE_TO_BUFFER, 1, (pvkCmdCopyImageToBuffer)(__VA_ARGS__))
#define pvkCmdCopyBufferToImage(...)         TB_FC(TB_FC_COPY_BUFFER_TO_IMAGE, 1, (pvkCmdCopyBufferToImage)(__VA_ARGS__))
#define pvkCmdCopyImage(...)                 TB_FC(TB_FC_COPY_IMAGE, 1, (pvkCmdCopyImage)(__VA_ARGS__))
#define pvkCmdBlitImage(...)                 TB_FC(TB_FC_BLIT_IMAGE, 1, (pvkCmdBlitImage)(__VA_ARGS__))
#define pvkCreateGraphicsPipelines(...)      TB_FC(TB_FC_CREATE_PIPELINE, 1, (pvkCreateGraphicsPipelines)(__VA_ARGS__))
#define pvkCmdDraw(...)                      TB_FC(TB_FC_DRAW, 1, (pvkCmdDraw)(__VA_ARGS__))
#define pvkCmdDrawIndexed(...)               TB_FC(TB_FC_DRAW, 1, (pvkCmdDrawIndexed)(__VA_ARGS__))
#define pvkCreateFramebuffer(d, i, a, f)     TB_FC(TB_FC_CREATE_FRAMEBUFFER, 1, (pvkCreateFramebuffer)(d, i, a, f))
#define pvkCreateShaderModule(d, i, a, m)    TB_FC(TB_FC_CREATE_SHADER, 1, (pvkCreateShaderModule)(d, i, a, m))

/* GPU timestamps: one pair around each command buffer and one around each
 * render pass, read back when the same command buffer is begun again (Vulkan
 * requires it to have completed by then), so no wait is ever added and the
 * submission mode is unchanged. */
static VkResult tb_ts_begin_cmd(VkCommandBuffer cmd, const VkCommandBufferBeginInfo* info);
static VkResult tb_ts_end_cmd(VkCommandBuffer cmd);
static void tb_ts_begin_pass(VkCommandBuffer cmd, const VkRenderPassBeginInfo* info, VkSubpassContents contents);
static void tb_ts_end_pass(VkCommandBuffer cmd);
#define pvkBeginCommandBuffer(c, i)  tb_ts_begin_cmd(c, i)
#define pvkEndCommandBuffer(c)       tb_ts_end_cmd(c)
#define pvkCmdBeginRenderPass(c, i, s) tb_ts_begin_pass(c, i, s)
#define pvkCmdEndRenderPass(c)       tb_ts_end_pass(c)
