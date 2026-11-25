/* Owned by the present path. The next guest frame may write its display
 * surfaces while the host is presenting or resizing the completed frame. */
static struct {
    vk_image image;
    vk_buffer readback;
    VkExtent2D extent, failed_extent;
    int active, written;
} tb_present_scaled;
/* Protected by present_mu. Describes the submitted frame, not a later
 * change to the diagnostic environment while its copy is in flight. */
static int tb_present_captured;

/* Present images are never used by the engine's async draw batches. Free
 * only these resources after the present fence rather than flushing the
 * walker's command state from the window thread. */
static void tb_present_destroy_image(vk_image* image)
{
    if (image->view) pvkDestroyImageView(s_vk.device, image->view, NULL);
    if (image->img) pvkDestroyImage(s_vk.device, image->img, NULL);
    if (image->mem) pvkFreeMemory(s_vk.device, image->mem, NULL);
    memset(image, 0, sizeof *image);
}

static void tb_present_destroy_buffer(vk_buffer* buffer)
{
    if (buffer->ptr) pvkUnmapMemory(s_vk.device,buffer->mem);
    if (buffer->buf) pvkDestroyBuffer(s_vk.device,buffer->buf,NULL);
    if (buffer->mem) pvkFreeMemory(s_vk.device,buffer->mem,NULL);
    memset(buffer,0,sizeof *buffer);
}

static void tb_present_image_shutdown(void)
{
    tb_present_destroy_image(&tb_present_scaled.image);
    tb_present_destroy_buffer(&tb_present_scaled.readback);
    memset(&tb_present_scaled, 0, sizeof tb_present_scaled);
}

/* The register-file engine draws into its own attachments. Once its first
 * owned presentation target exists, the fixed-function framebuffer and its
 * full-size attachments have no consumers. Native engine presents use the
 * same owned-image path if the scale subsequently falls back. */
static void tb_present_retire_base(void)
{
    if (s_vk.framebuffer) {
        pvkDestroyFramebuffer(s_vk.device,s_vk.framebuffer,NULL);
        s_vk.framebuffer=VK_NULL_HANDLE;
    }
    tb_present_destroy_image(&s_vk.depth);
    tb_present_destroy_image(&s_vk.color);
    tb_present_destroy_buffer(&s_vk.readback);
}

static VkExtent2D tb_present_extent(void)
{
    return tb_present_scaled.active ? tb_present_scaled.extent : (VkExtent2D){s_vk.width, s_vk.height};
}

static const vk_image* tb_present_image(void)
{
    return tb_present_scaled.active ? &tb_present_scaled.image : &s_vk.color;
}

static int tb_present_full_capture(void)
{
    const char* history = getenv("TB_FRAME_HISTORY");
    return s_vk.dump_path || (history && *history);
}

static vk_buffer* tb_present_readback(void)
{
    return tb_present_scaled.active
        ? &tb_present_scaled.readback : &s_vk.readback;
}

static int tb_present_prepare_readback(VkDeviceSize bytes)
{
    vk_buffer* buffer = tb_present_readback();
    if (buffer->ptr && buffer->size >= bytes) return 1;
    tb_present_destroy_buffer(buffer);
    return !vk_create_host_buffer(buffer, bytes, VK_BUFFER_USAGE_TRANSFER_DST_BIT);
}

/* Diagnostics opt in to a host transfer. Ordinary presentation never reads
 * pixels back. This runs with present_mu held, after its fence completed.
 * Iris 580 returns stale pixels if this copy shares the image-copy command
 * buffer. A separate diagnostic submission leaves ordinary presents alone. */
static int tb_present_capture_pixels(int full)
{
    const VkExtent2D extent = tb_present_extent();
    vk_buffer* readback = tb_present_readback();
    const VkDeviceSize bytes = full ? (VkDeviceSize)extent.width*extent.height*4u : 4u;
    {
        if (!tb_present_prepare_readback(bytes)) return 0;
        VkCommandBuffer cb = s_vk.present_cmd;
        if (pvkResetCommandBuffer(cb, 0)) return 0;
        const VkCommandBufferBeginInfo begin = {.sType=VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO,
            .flags=VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT};
        if (pvkBeginCommandBuffer(cb, &begin)) return 0;
        vk_cmd_after_everything(cb);
        vk_barrier_image_on(cb, tb_present_image()->img, VK_IMAGE_ASPECT_COLOR_BIT, VK_IMAGE_LAYOUT_GENERAL);
        VkBufferImageCopy copy = {.imageSubresource={VK_IMAGE_ASPECT_COLOR_BIT,0,0,1},
            .imageOffset={(int32_t)(extent.width/2),(int32_t)(extent.height/2),0},.imageExtent={1,1,1}};
        if (full) { copy.imageOffset=(VkOffset3D){0,0,0}; copy.imageExtent=(VkExtent3D){extent.width,extent.height,1}; }
        pvkCmdCopyImageToBuffer(cb, tb_present_image()->img, VK_IMAGE_LAYOUT_GENERAL, readback->buf, 1, &copy);
        const VkBufferMemoryBarrier host={.sType=VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER,
            .srcAccessMask=VK_ACCESS_TRANSFER_WRITE_BIT,.dstAccessMask=VK_ACCESS_HOST_READ_BIT,
            .srcQueueFamilyIndex=VK_QUEUE_FAMILY_IGNORED,.dstQueueFamilyIndex=VK_QUEUE_FAMILY_IGNORED,
            .buffer=readback->buf,.size=VK_WHOLE_SIZE};
        pvkCmdPipelineBarrier(cb,VK_PIPELINE_STAGE_TRANSFER_BIT,VK_PIPELINE_STAGE_HOST_BIT,0,0,NULL,1,&host,0,NULL);
        if (pvkEndCommandBuffer(cb)) return 0;
        const VkSubmitInfo submit={.sType=VK_STRUCTURE_TYPE_SUBMIT_INFO,.commandBufferCount=1,.pCommandBuffers=&cb};
        if (pvkResetFences(s_vk.device,1,&s_vk.present_fence)) return 0;
        if (vk_queue_submit(&submit,s_vk.present_fence)) return 0;
        if (pvkWaitForFences(s_vk.device,1,&s_vk.present_fence,VK_TRUE,UINT64_MAX)) return 0;
        tb_frame_budget_count(TB_FC_PRESENT_READBACK_BYTES,(u32)bytes);
    }
    vk_invalidate(readback);
    return 1;
}

static u32 tb_present_probe_center(void)
{
    if (!s_vk.presented) return 0;
    if (!tb_present_captured && !tb_present_capture_pixels(0)) return 0;
    const VkExtent2D extent=tb_present_extent();
    const u8* pixels=tb_present_readback()->ptr;
    const size_t c=tb_present_captured?((size_t)(extent.height/2)*extent.width+extent.width/2)*4:0;
    return 0xff000000u | ((u32)pixels[c]<<16) | ((u32)pixels[c+1]<<8) | pixels[c+2];
}

/* Runs with present_mu held after completion of the previous frame. */
static int tb_present_prepare_scaled(u32 w, u32 h)
{
    if (tb_present_scaled.failed_extent.width == w && tb_present_scaled.failed_extent.height == h) return 0;
    if (!tb_present_scaled.image.img || tb_present_scaled.extent.width != w ||
        tb_present_scaled.extent.height != h) {
        tb_present_image_shutdown();
        if (vk_create_image(&tb_present_scaled.image, VK_FORMAT_R8G8B8A8_UNORM, w, h,
                VK_IMAGE_USAGE_TRANSFER_DST_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT | VK_IMAGE_USAGE_SAMPLED_BIT,
                VK_IMAGE_ASPECT_COLOR_BIT)) {
            goto fail;
        }
        tb_present_scaled.extent = (VkExtent2D){w, h};
    }
    const VkDeviceSize bytes=(VkDeviceSize)w*h*4;
    if (tb_present_full_capture() && tb_present_scaled.readback.size<bytes) {
        tb_present_destroy_buffer(&tb_present_scaled.readback);
        if (vk_create_host_buffer(&tb_present_scaled.readback,bytes,VK_BUFFER_USAGE_TRANSFER_DST_BIT)) goto fail;
    }
    return 1;
fail:
    tb_present_image_shutdown();
    tb_present_scaled.failed_extent = (VkExtent2D){w, h};
    VK_LOG("scaled presentation target unavailable at %ux%u; using Native resolution\n", w, h);
    return 0;
}
