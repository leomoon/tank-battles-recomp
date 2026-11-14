/* Sparse, opt-in GPU timestamps for the serialized general command buffer.
 * Every 256th submission is sampled; no extra GPU waits are introduced. */
#include <time.h>
#include <sys/stat.h>
static VkQueryPool tb_gpu_pool;
static FILE* tb_gpu_file;
static int tb_gpu_checked,tb_gpu_active;
static unsigned tb_gpu_bits;
static uint64_t tb_gpu_submissions,tb_gpu_samples;
static double tb_gpu_period;
static void tb_gpu_begin(void)
{
    tb_gpu_active=0;
    if(!tb_gpu_checked) {
        tb_gpu_checked=1;
        const char* path=getenv("TB_GPU_TIMING");
        if(!path || !*path)return;
        VkPhysicalDeviceProperties props;pvkGetPhysicalDeviceProperties(s_vk.phys,&props);
        u32 count=0;pvkGetPhysicalDeviceQueueFamilyProperties(s_vk.phys,&count,NULL);
        VkQueueFamilyProperties* families=calloc(count,sizeof(*families));
        if(!families)return;
        pvkGetPhysicalDeviceQueueFamilyProperties(s_vk.phys,&count,families);
        if(s_vk.queue_family<count)tb_gpu_bits=families[s_vk.queue_family].timestampValidBits;
        free(families);tb_gpu_period=props.limits.timestampPeriod;
        if(!tb_gpu_bits || tb_gpu_period<=0){fprintf(stderr,"[gpu-timing] timestamps unsupported\n");return;}
        VkQueryPoolCreateInfo info={.sType=VK_STRUCTURE_TYPE_QUERY_POOL_CREATE_INFO,.queryType=VK_QUERY_TYPE_TIMESTAMP,.queryCount=2};
        if(pvkCreateQueryPool(s_vk.device,&info,NULL,&tb_gpu_pool)!=VK_SUCCESS)return;
        tb_gpu_file=fopen(path,"w");
        if(tb_gpu_file)fchmod(fileno(tb_gpu_file),0644);
        fprintf(stderr,"[gpu-timing] timestamp period=%g ns valid_bits=%u; every 256th serialized command\n",tb_gpu_period,tb_gpu_bits);
    }
    if(!tb_gpu_file || ++tb_gpu_submissions%256 || tb_gpu_samples>=30000)return;
    tb_gpu_active=1;
    pvkCmdResetQueryPool(s_vk.cmd,tb_gpu_pool,0,2);
    pvkCmdWriteTimestamp(s_vk.cmd,VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT,tb_gpu_pool,0);
}
static void tb_gpu_end(void)
{
    if(tb_gpu_active)pvkCmdWriteTimestamp(s_vk.cmd,VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT,tb_gpu_pool,1);
}
static void tb_gpu_result(void)
{
    if(!tb_gpu_active)return;
    uint64_t ticks[2];
    if(pvkGetQueryPoolResults(s_vk.device,tb_gpu_pool,0,2,sizeof(ticks),ticks,sizeof(uint64_t),VK_QUERY_RESULT_64_BIT)!=VK_SUCCESS) {
        fprintf(stderr,"[gpu-timing] query unavailable after existing submit fence\n");return;
    }
    const uint64_t mask=tb_gpu_bits==64?UINT64_MAX:(UINT64_C(1)<<tb_gpu_bits)-1;
    const double ns=((ticks[1]-ticks[0])&mask)*tb_gpu_period;
    struct timespec now;clock_gettime(CLOCK_MONOTONIC,&now);
    fprintf(tb_gpu_file,"{\"monotonic_ns\":%llu,\"submission\":%llu,\"gpu_ns\":%.3f}\n",
            (unsigned long long)((uint64_t)now.tv_sec*1000000000u+now.tv_nsec),(unsigned long long)tb_gpu_submissions,ns);
    if(++tb_gpu_samples%32==0)fflush(tb_gpu_file);
}
static void tb_gpu_shutdown(void)
{
    if(tb_gpu_pool)pvkDestroyQueryPool(s_vk.device,tb_gpu_pool,NULL);
    if(tb_gpu_file)fclose(tb_gpu_file);
    tb_gpu_file=NULL;tb_gpu_pool=VK_NULL_HANDLE;tb_gpu_checked=0;
}
