/* Owned by the present path. The next guest frame may write its display
 * surfaces while the host is presenting or resizing the completed frame. */
static struct {
    vk_image image;
    vk_buffer readback;
    VkExtent2D extent, failed_extent;
    int active, written;
} tb_present_scaled;

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

static void tb_present_image_shutdown(void)
{
    tb_present_destroy_image(&tb_present_scaled.image);
    vk_destroy_buffer(&tb_present_scaled.readback);
    memset(&tb_present_scaled, 0, sizeof tb_present_scaled);
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
    return tb_present_scaled.active && tb_present_full_capture()
        ? &tb_present_scaled.readback : &s_vk.readback;
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
    if (tb_present_full_capture() && !tb_present_scaled.readback.ptr &&
        vk_create_host_buffer(&tb_present_scaled.readback, (VkDeviceSize)w * h * 4,
                              VK_BUFFER_USAGE_TRANSFER_DST_BIT)) {
        goto fail;
    }
    return 1;
fail:
    tb_present_image_shutdown();
    tb_present_scaled.failed_extent = (VkExtent2D){w, h};
    VK_LOG("scaled presentation target unavailable at %ux%u; using Native resolution\n", w, h);
    return 0;
}
