/* Short Linux fence polling keeps completion and publication synchronous.
 * Both time and probe count are bounded; long GPU work uses the original wait. */
static VkResult tb_fence_wait(VkFence fence)
{
#if defined(__linux__)
    const char* mode=getenv("TB_VK_FENCE_POLL");
    if(mode && !strcmp(mode,"1")) {
        const uint64_t start=tb_depth_cost_now();
        for(unsigned probe=0;probe<256;probe++) {
            VkResult result=pvkGetFenceStatus(s_vk.device,fence);
            if(result!=VK_NOT_READY)return result;
            if((probe&7)==7 && tb_depth_cost_now()-start>=50000)break;
#if defined(__x86_64__) || defined(__i386__)
            __asm__ volatile("pause");
#endif
        }
    }
#endif
    return pvkWaitForFences(s_vk.device,1,&fence,VK_TRUE,UINT64_MAX);
}
