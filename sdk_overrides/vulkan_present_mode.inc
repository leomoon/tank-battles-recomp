/* Swapchain present mode for the port's window.
 *
 * The guest paces itself on flips and the 60 Hz vblank beat; the swapchain
 * should not add a second, host-side vsync wait on top of that. FIFO makes
 * vkAcquireNextImageKHR/vkQueuePresentKHR block until the display consumes an
 * image, so a present that lands just after a refresh stalls whoever calls it.
 * MAILBOX shows the newest finished frame at the next refresh without tearing
 * and never blocks the caller. TB_VK_PRESENT_MODE=fifo keeps the original mode
 * for A/B comparison; FIFO is also the fallback when the surface lacks MAILBOX. */
static VkPresentModeKHR tb_present_mode(void)
{
    static VkPresentModeKHR chosen = (VkPresentModeKHR)-1;
    if ((int)chosen != -1) return chosen;
    chosen = VK_PRESENT_MODE_FIFO_KHR;
    const char* forced = getenv("TB_VK_PRESENT_MODE");
    if (forced && !strcmp(forced, "fifo")) {
        VK_LOG("window present mode FIFO (forced)\n");
        return chosen;
    }
    VkPresentModeKHR modes[16];
    u32 n = 16;
    if (!pvkGetPhysicalDeviceSurfacePresentModesKHR ||
        pvkGetPhysicalDeviceSurfacePresentModesKHR(s_vk.phys, s_vk.surface, &n, modes) < 0) n = 0;
    for (u32 i = 0; i < n; i++)
        if (modes[i] == VK_PRESENT_MODE_MAILBOX_KHR) chosen = VK_PRESENT_MODE_MAILBOX_KHR;
    VK_LOG("window present mode %s\n", chosen == VK_PRESENT_MODE_MAILBOX_KHR ? "MAILBOX" : "FIFO (no MAILBOX)");
    return chosen;
}
