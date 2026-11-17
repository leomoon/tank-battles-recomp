/* Exact Z16 conversion. Initial path waits for completion before reuse. */
#include "../shaders/depth16_spirv.inc"
static struct {
 VkDescriptorSetLayout sl; VkPipelineLayout layout; VkDescriptorPool pool;
 VkDescriptorSet set; VkPipeline pipeline; vk_buffer input,output;
} tb_d16;
static void tb_depth16_shutdown(void) {
 vk_destroy_buffer(&tb_d16.input);vk_destroy_buffer(&tb_d16.output);
 if(tb_d16.pipeline)pvkDestroyPipeline(s_vk.device,tb_d16.pipeline,NULL);
 if(tb_d16.pool)pvkDestroyDescriptorPool(s_vk.device,tb_d16.pool,NULL);
 if(tb_d16.layout)pvkDestroyPipelineLayout(s_vk.device,tb_d16.layout,NULL);
 if(tb_d16.sl)pvkDestroyDescriptorSetLayout(s_vk.device,tb_d16.sl,NULL);
 memset(&tb_d16,0,sizeof tb_d16);
}
static int tb_depth16_init(void) {
 if(tb_d16.pipeline)return 0;
 VkDescriptorSetLayoutBinding bindings[2]={
 {.binding=0,.descriptorType=VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,.descriptorCount=1,.stageFlags=VK_SHADER_STAGE_COMPUTE_BIT},
 {.binding=1,.descriptorType=VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,.descriptorCount=1,.stageFlags=VK_SHADER_STAGE_COMPUTE_BIT}};
 VkDescriptorSetLayoutCreateInfo sc={.sType=VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO,.bindingCount=2,.pBindings=bindings};
 VkPushConstantRange push={VK_SHADER_STAGE_COMPUTE_BIT,0,4};
 if(pvkCreateDescriptorSetLayout(s_vk.device,&sc,NULL,&tb_d16.sl))goto fail;
 VkPipelineLayoutCreateInfo lc={.sType=VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO,.setLayoutCount=1,.pSetLayouts=&tb_d16.sl,.pushConstantRangeCount=1,.pPushConstantRanges=&push};
 if(pvkCreatePipelineLayout(s_vk.device,&lc,NULL,&tb_d16.layout))goto fail;
 VkDescriptorPoolSize size={VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,2};
 VkDescriptorPoolCreateInfo pc={.sType=VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO,.maxSets=1,.poolSizeCount=1,.pPoolSizes=&size};
 if(pvkCreateDescriptorPool(s_vk.device,&pc,NULL,&tb_d16.pool))goto fail;
 VkDescriptorSetAllocateInfo ac={.sType=VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO,.descriptorPool=tb_d16.pool,.descriptorSetCount=1,.pSetLayouts=&tb_d16.sl};
 if(pvkAllocateDescriptorSets(s_vk.device,&ac,&tb_d16.set))goto fail;
 VkShaderModule module=VK_NULL_HANDLE;
 VkShaderModuleCreateInfo mc={.sType=VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO,.codeSize=sizeof tb_depth16_spirv,.pCode=tb_depth16_spirv};
 if(pvkCreateShaderModule(s_vk.device,&mc,NULL,&module))goto fail;
 VkComputePipelineCreateInfo cp={.sType=VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO,.stage={.sType=VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO,.stage=VK_SHADER_STAGE_COMPUTE_BIT,.module=module,.pName="main"},.layout=tb_d16.layout};
 VkResult rc=pvkCreateComputePipelines(s_vk.device,VK_NULL_HANDLE,1,&cp,NULL,&tb_d16.pipeline);
 pvkDestroyShaderModule(s_vk.device,module,NULL);if(rc)goto fail;return 0;
fail:tb_depth16_shutdown();return -1;
}
static vk_buffer* tb_depth16_buffer(vk_buffer* b,VkDeviceSize bytes,VkBufferUsageFlags usage) {
 if(b->buf && b->size>=bytes)return b;
 vk_destroy_buffer(b);
 if(vk_create_device_buffer(b,bytes,usage)){vk_destroy_buffer(b);return NULL;}
 return b;
}
static u32 tb_vk_depth16(void* user,u32 depth,u32 w,u32 h,u32 format) {
 /* Default on for every OS: Iris Pro 580 (Windows) and the Linux hosts pass
  * the exact vulkan_depth16 fixture; Vulkan's correctly rounded float
  * multiply/add keep the quantization portable. TB_DEPTH_GPU=0 selects the
  * reference CPU conversion. */
 const char* enabled=getenv("TB_DEPTH_GPU");
 const int on=!enabled || strcmp(enabled,"0")!=0;
 if(!on || format!=0x92 || !w || !h || w>2048 || h>2048)return 0;
 vk_eng_obj* z=vk_eng_get(depth,VK_ENG_DEPTH);
 if(!z || w>z->w || h>z->h || (s_vk.depth_format!=VK_FORMAT_D32_SFLOAT && s_vk.depth_format!=VK_FORMAT_D32_SFLOAT_S8_UINT))return 0;
 static int compute_queue=-1;
 if(compute_queue<0){u32 count=0;pvkGetPhysicalDeviceQueueFamilyProperties(s_vk.phys,&count,NULL);
  VkQueueFamilyProperties* queues=calloc(count,sizeof *queues);if(!queues)return 0;
  pvkGetPhysicalDeviceQueueFamilyProperties(s_vk.phys,&count,queues);
  compute_queue=s_vk.queue_family<count && (queues[s_vk.queue_family].queueFlags&VK_QUEUE_COMPUTE_BIT);free(queues);}
 if(!compute_queue || tb_depth16_init())return 0;
 const u32 pixels=w*h;const VkDeviceSize packed=((pixels+1u)/2u)*4ull;
 vk_buffer* in=tb_depth16_buffer(&tb_d16.input,(VkDeviceSize)pixels*4,VK_BUFFER_USAGE_TRANSFER_DST_BIT|VK_BUFFER_USAGE_STORAGE_BUFFER_BIT);
 vk_buffer* out=tb_depth16_buffer(&tb_d16.output,packed,VK_BUFFER_USAGE_TRANSFER_SRC_BIT|VK_BUFFER_USAGE_STORAGE_BUFFER_BIT);
 if(!in || !out)return 0;
 u32 handle=vk_eng_alloc();if(!handle)return 0;vk_eng_obj* t=&s_eobj[handle];
 int reused=0;
 for(u32 i=0;i<s_snap_pool_n;i++)
  if(s_snap_pool[i].fmt==VK_FORMAT_R16_UNORM && s_snap_pool[i].w==w && s_snap_pool[i].h==h) {
   t->im=s_snap_pool[i].im;s_snap_pool[i]=s_snap_pool[--s_snap_pool_n];reused=1;break;
  }
 if(!reused && vk_create_image(&t->im,VK_FORMAT_R16_UNORM,w,h,VK_IMAGE_USAGE_SAMPLED_BIT|VK_IMAGE_USAGE_TRANSFER_DST_BIT|VK_IMAGE_USAGE_TRANSFER_SRC_BIT,VK_IMAGE_ASPECT_COLOR_BIT)) {vk_destroy_image(&t->im);memset(t,0,sizeof *t);return 0;}
 /* A sampleable color object must be marked snapshot to be accepted by the
  * engine's texture_release callback. It is never an attachment. */
 t->kind=VK_ENG_COLOR;t->snapshot=1;t->fmt=VK_FORMAT_R16_UNORM;t->bpp=2;t->w=w;t->h=h;
 /* Finish older groups before changing this shared descriptor set. */
 if(vk_begin()){vk_eng_release(user,handle);return 0;}
 VkDescriptorBufferInfo bi[2]={{in->buf,0,(VkDeviceSize)pixels*4},{out->buf,0,packed}};
 VkWriteDescriptorSet writes[2];memset(writes,0,sizeof writes);
 for(unsigned i=0;i<2;i++)writes[i]=(VkWriteDescriptorSet){.sType=VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET,.dstSet=tb_d16.set,.dstBinding=i,.descriptorCount=1,.descriptorType=VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,.pBufferInfo=&bi[i]};
 pvkUpdateDescriptorSets(s_vk.device,2,writes,0,NULL);
 vk_barrier_image(z->im.img,s_vk.depth_aspect,VK_IMAGE_LAYOUT_GENERAL);
 vk_barrier_image(t->im.img,VK_IMAGE_ASPECT_COLOR_BIT,reused?VK_IMAGE_LAYOUT_GENERAL:VK_IMAGE_LAYOUT_UNDEFINED);
 VkBufferImageCopy region={.imageSubresource={VK_IMAGE_ASPECT_DEPTH_BIT,0,0,1},.imageExtent={w,h,1}};
 pvkCmdCopyImageToBuffer(s_vk.cmd,z->im.img,VK_IMAGE_LAYOUT_GENERAL,in->buf,1,&region);
 VkMemoryBarrier barrier={.sType=VK_STRUCTURE_TYPE_MEMORY_BARRIER,.srcAccessMask=VK_ACCESS_TRANSFER_WRITE_BIT,.dstAccessMask=VK_ACCESS_SHADER_READ_BIT};
 pvkCmdPipelineBarrier(s_vk.cmd,VK_PIPELINE_STAGE_TRANSFER_BIT,VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,0,1,&barrier,0,NULL,0,NULL);
 pvkCmdBindPipeline(s_vk.cmd,VK_PIPELINE_BIND_POINT_COMPUTE,tb_d16.pipeline);
 pvkCmdBindDescriptorSets(s_vk.cmd,VK_PIPELINE_BIND_POINT_COMPUTE,tb_d16.layout,0,1,&tb_d16.set,0,NULL);
 pvkCmdPushConstants(s_vk.cmd,tb_d16.layout,VK_SHADER_STAGE_COMPUTE_BIT,0,4,&pixels);
 pvkCmdDispatch(s_vk.cmd,(pixels+127u)/128u,1,1);
 barrier.srcAccessMask=VK_ACCESS_SHADER_WRITE_BIT;barrier.dstAccessMask=VK_ACCESS_TRANSFER_READ_BIT;
 pvkCmdPipelineBarrier(s_vk.cmd,VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,VK_PIPELINE_STAGE_TRANSFER_BIT,0,1,&barrier,0,NULL,0,NULL);
 region.imageSubresource.aspectMask=VK_IMAGE_ASPECT_COLOR_BIT;
 pvkCmdCopyBufferToImage(s_vk.cmd,out->buf,t->im.img,VK_IMAGE_LAYOUT_GENERAL,1,&region);
 vk_barrier_image(t->im.img,VK_IMAGE_ASPECT_COLOR_BIT,VK_IMAGE_LAYOUT_GENERAL);
 if(vk_submit_and_wait()){vk_eng_release(user,handle);return 0;}
 {static int reported;if(!reported){VK_LOG("exact Z16 GPU conversion active (waited, pooled snapshots)\n");reported=1;}}
 return handle;
}
