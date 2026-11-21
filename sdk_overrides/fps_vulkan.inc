static uint64_t tb_completed_guest_serial;
/* Window-only overlay: no guest render target, readback or shader is changed. */
static vk_buffer tb_fps_buffer;
static VkFormat tb_fps_swap_format;

static void tb_fps_window_overlay(VkCommandBuffer cb,VkImage image)
{
    if (tb_fps_swap_format!=VK_FORMAT_R8G8B8A8_UNORM && tb_fps_swap_format!=VK_FORMAT_B8G8R8A8_UNORM &&
        tb_fps_swap_format!=VK_FORMAT_R8G8B8A8_SRGB && tb_fps_swap_format!=VK_FORMAT_B8G8R8A8_SRGB) return;
    if (!tb_fps_buffer.buf) {
        if (vk_create_host_buffer(&tb_fps_buffer,TB_OVERLAY_BUFFER_SIZE+TB_NOTIFICATION_BUFFER_SIZE,VK_BUFFER_USAGE_TRANSFER_SRC_BIT) || !tb_fps_buffer.coherent) {
            vk_destroy_buffer(&tb_fps_buffer);return;
        }
    }
    const int bgra=tb_fps_swap_format==VK_FORMAT_B8G8R8A8_UNORM || tb_fps_swap_format==VK_FORMAT_B8G8R8A8_SRGB;
    tb_overlay_region regions[2];
    const int visible[2]={
        tb_draw_overlay(tb_fps_buffer.ptr,s_vk.sc_extent.width,s_vk.sc_extent.height,bgra,&regions[0]),
        tb_draw_notification_toast((char*)tb_fps_buffer.ptr+TB_OVERLAY_BUFFER_SIZE,
            s_vk.sc_extent.width,s_vk.sc_extent.height,bgra,&regions[1])};
    VkImageMemoryBarrier barrier={
        .sType=VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER,
        .srcAccessMask=VK_ACCESS_TRANSFER_WRITE_BIT,.dstAccessMask=VK_ACCESS_TRANSFER_WRITE_BIT,
        .oldLayout=VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,.newLayout=VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,
        .srcQueueFamilyIndex=VK_QUEUE_FAMILY_IGNORED,.dstQueueFamilyIndex=VK_QUEUE_FAMILY_IGNORED,
        .image=image,.subresourceRange={VK_IMAGE_ASPECT_COLOR_BIT,0,1,0,1}};
    for (unsigned layer=0;layer<2;++layer) {
        if (!visible[layer]) continue;
        const tb_overlay_region region=regions[layer];
        pvkCmdPipelineBarrier(cb,VK_PIPELINE_STAGE_TRANSFER_BIT,VK_PIPELINE_STAGE_TRANSFER_BIT,0,0,NULL,0,NULL,1,&barrier);
        const VkBufferImageCopy copy={.bufferOffset=layer?TB_OVERLAY_BUFFER_SIZE:0,
            .imageSubresource={VK_IMAGE_ASPECT_COLOR_BIT,0,0,1},
            .imageOffset={(int32_t)region.x,(int32_t)region.y,0},.imageExtent={region.width,region.height,1}};
        pvkCmdCopyBufferToImage(cb,tb_fps_buffer.buf,image,VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,1,&copy);
    }
}
