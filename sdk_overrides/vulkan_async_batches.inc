/* Asynchronous draw-batch submission (TB_VK_ASYNC, default on with batching).
 *
 * vk_eng_flush used to submit the open batch and wait for the GPU before the
 * walker could record anything else, because the vertex/index/uniform/staging
 * rings and the descriptor pool were reset for reuse the moment it returned.
 * Every batch boundary (a full batch, a render-target switch, a present, a
 * serial upload) therefore stalled the walker on the GPU.
 *
 * Here a batch is submitted with the fence of its own slot and the walker goes
 * on recording into the next slot. Each of the TB_VK_SLOTS slots owns a
 * command buffer, a fence, a descriptor pool and one equal partition of every
 * ring, so nothing a submitted batch reads is written or reset until that
 * slot's fence has signalled; reusing a slot waits for it (bounded
 * back-pressure, at most TB_VK_SLOTS batches in flight).
 *
 * GPU ordering is unchanged: one queue, submissions in recording order, and
 * every batch, serial command buffer and present begins with a full
 * ALL_COMMANDS barrier. Completion is still waited for wherever the CPU needs
 * it:
 *   - serial work (uploads, readbacks, snapshots) submits and waits as before,
 *     and begins with a full barrier, so its fence implies everything earlier;
 *   - destroying an image, buffer, framebuffer or pipeline waits for every
 *     batch in flight first (tb_eng_complete), as it waited for the open one;
 *   - the engine's submit_and_wait stays fully synchronous (its contract), as
 *     does shutdown.
 * TB_VK_ASYNC=0 restores the serial flush; GPU timestamp profiling
 * (TB_GPU_TIMING) also keeps it, since it reads results after each wait. */
#define TB_VK_SLOTS 4
static struct {
    VkCommandBuffer cmd;
    VkFence fence;
    VkDescriptorPool pool;
    int inflight;
} tb_slot[TB_VK_SLOTS];
static u32 tb_cur;
static int tb_async = -1;   /* -1 undecided, 0 serial, 1 async */

static VkDescriptorPool tb_async_pool(void)
{
    VkDescriptorPoolSize ps[3] = {
        { VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER, 2 * VK_EB_MAX_SETS },
        { VK_DESCRIPTOR_TYPE_SAMPLED_IMAGE, RSX_MAX_TEXTURES * VK_EB_MAX_SETS },
        { VK_DESCRIPTOR_TYPE_SAMPLER, RSX_MAX_TEXTURES * VK_EB_MAX_SETS },
    };
    VkDescriptorPoolCreateInfo pci = { .sType = VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO,
                                       .maxSets = VK_EB_MAX_SETS, .poolSizeCount = 3, .pPoolSizes = ps };
    VkDescriptorPool pool = VK_NULL_HANDLE;
    return pvkCreateDescriptorPool(s_vk.device, &pci, NULL, &pool) == VK_SUCCESS ? pool : VK_NULL_HANDLE;
}

/* Decided once, when batching comes up and before any ring slice is taken, so
 * every slice already lies in the partition of the slot that records it. */
static void tb_async_init(void)
{
    if (tb_async >= 0) return;
    tb_async = 0;
    const char* mode = getenv("TB_VK_ASYNC");
    if ((mode && !strcmp(mode, "0")) || getenv("TB_GPU_TIMING")) {
        VK_LOG("draw batches: serial submission\n");
        return;
    }
    tb_slot[0].cmd = s_vk.cmd; tb_slot[0].fence = s_vk.fence; tb_slot[0].pool = s_eb.pool;
    VkCommandBufferAllocateInfo cai = { .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO,
                                        .commandPool = s_vk.pool, .level = VK_COMMAND_BUFFER_LEVEL_PRIMARY,
                                        .commandBufferCount = 1 };
    VkFenceCreateInfo fci = { .sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO };
    for (u32 i = 1; i < TB_VK_SLOTS; i++) {
        tb_slot[i].pool = tb_async_pool();
        if (!tb_slot[i].pool || pvkAllocateCommandBuffers(s_vk.device, &cai, &tb_slot[i].cmd) != VK_SUCCESS ||
            pvkCreateFence(s_vk.device, &fci, NULL, &tb_slot[i].fence) != VK_SUCCESS) {
            VK_LOG("draw batches: no objects for asynchronous slots, staying serial\n");
            return;
        }
    }
    tb_cur = 0;
    tb_async = 1;
    VK_LOG("draw batches: asynchronous submission, %d slots\n", TB_VK_SLOTS);
}

static int tb_async_active(void) { return tb_async == 1; }

/* A clear or texture upload about to be recorded into the open batch rather
 * than submitted and waited for on its own: neither has a CPU consumer, and
 * the batch's staging ring keeps upload rows alive until its fence. Transfer
 * commands cannot sit inside a render pass, so an open draw group ends here;
 * the next draw starts a new one behind a full barrier. */
static int tb_async_record(void)
{
    if (tb_async != 1) return 0;
    if (tb_group_open) tb_group_end();
    return 1;
}

static int tb_async_inflight(void)
{
    if (tb_async != 1) return 0;
    for (u32 i = 0; i < TB_VK_SLOTS; i++)
        if (tb_slot[i].inflight) return 1;
    return 0;
}

static void tb_slot_wait(u32 i)
{
    if (!tb_slot[i].inflight) return;
    tb_fence_wait(tb_slot[i].fence);
    tb_slot[i].inflight = 0;
}

/* The ring window the current slot may hand out: [lo, lo + part). */
static void tb_ring_window(VkDeviceSize cap, VkDeviceSize* lo, VkDeviceSize* hi)
{
    if (tb_async != 1) { *lo = 0; *hi = cap; return; }
    const VkDeviceSize part = cap / TB_VK_SLOTS;
    *lo = part * tb_cur;
    *hi = *lo + part;
}

/* vk_eng_flush's submission when asynchronous; returns 0 to take the serial
 * path instead. The caller has already cleared s_eb_pending. */
static int tb_async_flush(void)
{
    if (tb_async != 1) return 0;
    TB_RENDER_SCOPE(1);
    tb_group_end();
    if (pvkEndCommandBuffer(s_vk.cmd) != VK_SUCCESS) { VK_LOG("vkEndCommandBuffer (batch) failed\n"); return 1; }
    VkSubmitInfo si = { .sType = VK_STRUCTURE_TYPE_SUBMIT_INFO,
                        .commandBufferCount = 1, .pCommandBuffers = &s_vk.cmd };
    pvkResetFences(s_vk.device, 1, &s_vk.fence);
    if (vk_queue_submit(&si, s_vk.fence) != VK_SUCCESS) { VK_LOG("vkQueueSubmit (batch) failed\n"); return 1; }
    tb_slot[tb_cur].inflight = 1;
    s_eb.flushes++;
    if (tb_frame_budget_on()) {
        const uint64_t t = tb_frame_budget_now();
        tb_frame_budget_span(TB_FT_BATCH_SUBMIT, tb_wait_label && !strcmp(tb_wait_label, "async:idle"), t, t);
    }

    /* The next slot: wait for its previous batch, then its rings and pool are free. */
    tb_cur = (tb_cur + 1) % TB_VK_SLOTS;
    tb_wait_label = "async:slot-reuse";
    tb_slot_wait(tb_cur);
    s_vk.cmd = tb_slot[tb_cur].cmd;
    s_vk.fence = tb_slot[tb_cur].fence;
    s_eb.pool = tb_slot[tb_cur].pool;
    pvkResetDescriptorPool(s_vk.device, s_eb.pool, 0);   /* its sets belonged to a finished batch */
    s_eb.sets = 0;
    s_eb.vb_head = s_eb.ib_head = s_eb.ub_head = s_eb.sb_head = 0;
    return 1;
}

/* The walker found nothing to drain: hand the GPU what is recorded instead
 * of leaving it in the open batch until the next boundary, so GPU work
 * overlaps the guest building the rest of the frame. Never waits unless the
 * next slot is still in flight. Walker thread only, like all recording. */
void tb_vk_idle_submit(void)
{
    if (tb_async != 1 || !s_eb_pending) return;
    tb_wait_label = "async:idle";
    vk_eng_flush();
    tb_wait_label = NULL;
}

/* Everything recorded so far submitted and finished: before destroying an
 * object a batch may reference, and wherever the engine requires completion. */
static void tb_eng_complete(void)
{
    const char* label = tb_wait_label;
    if (s_eb_pending) vk_eng_flush();
    tb_wait_label = label;
    if (tb_async != 1) return;
    for (u32 i = 0; i < TB_VK_SLOTS; i++) tb_slot_wait(i);
}
