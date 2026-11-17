/* Definitions for the GPU timestamp hooks declared in vulkan_frame_counters.inc.
 * Included once s_vk is defined. Active only while the frame-budget timeline is
 * enabled; TB_FRAME_GPU_TIMESTAMPS=0 keeps the counters but drops timestamps. */
#define TB_TS_CMDS 16
#define TB_TS_QUERIES 64            /* 2 for the command buffer, 2 per render pass */
static struct {
    VkCommandBuffer cmd;
    VkQueryPool pool;
    u32 used;                       /* queries written in the current recording */
    u32 open_pass;                  /* query index of the open pass's begin, or 0 */
    u32 extent[TB_TS_QUERIES / 2];  /* w<<16|h per pass pair */
    int recording, pending;
} tb_ts[TB_TS_CMDS];
static int tb_ts_state;             /* 0 unchecked, 1 usable, -1 unusable */
static double tb_ts_period;
static uint64_t tb_ts_mask;

static int tb_ts_usable(void)
{
    if (!tb_frame_budget_on()) return 0;
    if (tb_ts_state) return tb_ts_state > 0;
    tb_ts_state = -1;
    const char* off = getenv("TB_FRAME_GPU_TIMESTAMPS");
    if (off && !strcmp(off, "0")) return 0;
    VkPhysicalDeviceProperties props;
    pvkGetPhysicalDeviceProperties(s_vk.phys, &props);
    u32 count = 0;
    pvkGetPhysicalDeviceQueueFamilyProperties(s_vk.phys, &count, NULL);
    VkQueueFamilyProperties* families = calloc(count ? count : 1, sizeof(*families));
    if (!families) return 0;
    pvkGetPhysicalDeviceQueueFamilyProperties(s_vk.phys, &count, families);
    const u32 bits = s_vk.queue_family < count ? families[s_vk.queue_family].timestampValidBits : 0;
    free(families);
    if (!bits || props.limits.timestampPeriod <= 0) {
        fprintf(stderr, "[frame-budget] GPU timestamps unsupported on this queue\n");
        return 0;
    }
    tb_ts_period = props.limits.timestampPeriod;
    tb_ts_mask = bits >= 64 ? UINT64_MAX : (UINT64_C(1) << bits) - 1;
    fprintf(stderr, "[frame-budget] GPU timestamps: period %g ns, %u valid bits\n", tb_ts_period, bits);
    tb_ts_state = 1;
    return 1;
}

static int tb_ts_slot(VkCommandBuffer cmd, int create)
{
    for (int i = 0; i < TB_TS_CMDS; i++)
        if (tb_ts[i].cmd == cmd) return i;
    if (!create) return -1;
    for (int i = 0; i < TB_TS_CMDS; i++) {
        if (tb_ts[i].cmd) continue;
        VkQueryPoolCreateInfo qi = { .sType = VK_STRUCTURE_TYPE_QUERY_POOL_CREATE_INFO,
                                     .queryType = VK_QUERY_TYPE_TIMESTAMP, .queryCount = TB_TS_QUERIES };
        if (pvkCreateQueryPool(s_vk.device, &qi, NULL, &tb_ts[i].pool) != VK_SUCCESS) return -1;
        tb_ts[i].cmd = cmd;
        return i;
    }
    return -1;
}

static uint32_t tb_ts_ns(uint64_t t0, uint64_t t1)
{
    const double ns = (double)((t1 - t0) & tb_ts_mask) * tb_ts_period;
    return ns > 4.0e9 ? UINT32_MAX : (uint32_t)ns;
}

/* The previous recording of this command buffer has completed: report it. */
static void tb_ts_collect(int i)
{
    if (!tb_ts[i].pending) return;
    tb_ts[i].pending = 0;
    uint64_t ticks[TB_TS_QUERIES];
    const u32 used = tb_ts[i].used;
    if (used < 2 || pvkGetQueryPoolResults(s_vk.device, tb_ts[i].pool, 0, used, sizeof(ticks), ticks,
                                           sizeof(uint64_t), VK_QUERY_RESULT_64_BIT) != VK_SUCCESS)
        return;
    tb_frame_budget_mark(TB_FT_GPU_CMD, 0, tb_ts_ns(ticks[0], ticks[1]));
    for (u32 q = 2; q + 1 < used; q += 2)
        tb_frame_budget_mark(TB_FT_GPU_PASS, tb_ts[i].extent[q / 2], tb_ts_ns(ticks[q], ticks[q + 1]));
}

static VkResult tb_ts_begin_cmd(VkCommandBuffer cmd, const VkCommandBufferBeginInfo* info)
{
    const VkResult r = (pvkBeginCommandBuffer)(cmd, info);
    const int on = r == VK_SUCCESS && tb_ts_usable();
    const int i = tb_ts_slot(cmd, on);
    if (i < 0) return r;
    if (on) tb_ts_collect(i);
    tb_ts[i].pending = 0;
    tb_ts[i].recording = on;
    tb_ts[i].used = 0;
    tb_ts[i].open_pass = 0;
    if (!on) return r;
    pvkCmdResetQueryPool(cmd, tb_ts[i].pool, 0, TB_TS_QUERIES);
    pvkCmdWriteTimestamp(cmd, VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT, tb_ts[i].pool, 0);
    tb_ts[i].used = 2;              /* query 1 is written at end */
    return r;
}

static VkResult tb_ts_end_cmd(VkCommandBuffer cmd)
{
    const int i = tb_ts_slot(cmd, 0);
    if (i >= 0 && tb_ts[i].recording) {
        if (tb_ts[i].open_pass) tb_ts[i].used = tb_ts[i].open_pass;   /* unbalanced: drop it */
        pvkCmdWriteTimestamp(cmd, VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT, tb_ts[i].pool, 1);
        tb_ts[i].recording = 0;
        tb_ts[i].pending = 1;
    }
    return (pvkEndCommandBuffer)(cmd);
}

static void tb_ts_begin_pass(VkCommandBuffer cmd, const VkRenderPassBeginInfo* info, VkSubpassContents contents)
{
    tb_frame_budget_count(TB_FC_RENDER_PASS, 1);
    const int i = tb_ts_slot(cmd, 0);
    if (i >= 0 && tb_ts[i].recording && !tb_ts[i].open_pass && tb_ts[i].used + 2 <= TB_TS_QUERIES) {
        const u32 q = tb_ts[i].used;
        tb_ts[i].extent[q / 2] = (info->renderArea.extent.width & 0xFFFFu) << 16 | (info->renderArea.extent.height & 0xFFFFu);
        pvkCmdWriteTimestamp(cmd, VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT, tb_ts[i].pool, q);
        tb_ts[i].open_pass = q;
    }
    (pvkCmdBeginRenderPass)(cmd, info, contents);
}

static void tb_ts_end_pass(VkCommandBuffer cmd)
{
    (pvkCmdEndRenderPass)(cmd);
    const int i = tb_ts_slot(cmd, 0);
    if (i >= 0 && tb_ts[i].recording && tb_ts[i].open_pass) {
        pvkCmdWriteTimestamp(cmd, VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT, tb_ts[i].pool, tb_ts[i].open_pass + 1);
        tb_ts[i].used = tb_ts[i].open_pass + 2;
        tb_ts[i].open_pass = 0;
    }
}

static void tb_ts_shutdown(void)
{
    for (int i = 0; i < TB_TS_CMDS; i++) {
        if (tb_ts[i].pool) pvkDestroyQueryPool(s_vk.device, tb_ts[i].pool, NULL);
        tb_ts[i].pool = VK_NULL_HANDLE;
        tb_ts[i].cmd = VK_NULL_HANDLE;
    }
    tb_ts_state = 0;
}
