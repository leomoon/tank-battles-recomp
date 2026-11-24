/* Window-only FXAA. Reuse the present command buffer and keep the source
 * frame untouched, including its guest readbacks and diagnostic captures. */
#include "../shaders/fxaa_spirv.inc"
static struct {
    int state;
    VkDescriptorSetLayout set_layout;
    VkPipelineLayout layout;
    VkDescriptorPool pool;
    VkDescriptorSet set;
    VkPipeline pipeline;
    VkSampler sampler;
    vk_image output;
    VkExtent2D extent;
    VkImageView source;
    int written;
} tb_fxaa;

static void tb_fxaa_shutdown(void)
{
    tb_present_destroy_image(&tb_fxaa.output);
    if (tb_fxaa.pipeline) pvkDestroyPipeline(s_vk.device, tb_fxaa.pipeline, NULL);
    if (tb_fxaa.sampler) pvkDestroySampler(s_vk.device, tb_fxaa.sampler, NULL);
    if (tb_fxaa.pool) pvkDestroyDescriptorPool(s_vk.device, tb_fxaa.pool, NULL);
    if (tb_fxaa.layout) pvkDestroyPipelineLayout(s_vk.device, tb_fxaa.layout, NULL);
    if (tb_fxaa.set_layout) pvkDestroyDescriptorSetLayout(s_vk.device, tb_fxaa.set_layout, NULL);
    memset(&tb_fxaa, 0, sizeof tb_fxaa);
}

static int tb_fxaa_init(void)
{
    if (tb_fxaa.state) return tb_fxaa.state > 0;
    VkShaderModule module = VK_NULL_HANDLE;
    u32 queue_count = 0;
    pvkGetPhysicalDeviceQueueFamilyProperties(s_vk.phys, &queue_count, NULL);
    VkQueueFamilyProperties* queues = calloc(queue_count, sizeof *queues);
    if (!queues) goto fail;
    pvkGetPhysicalDeviceQueueFamilyProperties(s_vk.phys, &queue_count, queues);
    const int compute = s_vk.queue_family < queue_count &&
        (queues[s_vk.queue_family].queueFlags & VK_QUEUE_COMPUTE_BIT);
    free(queues);
    VkFormatProperties format;
    pvkGetPhysicalDeviceFormatProperties(s_vk.phys, VK_FORMAT_R8G8B8A8_UNORM, &format);
    const VkFormatFeatureFlags required = VK_FORMAT_FEATURE_SAMPLED_IMAGE_FILTER_LINEAR_BIT |
        VK_FORMAT_FEATURE_STORAGE_IMAGE_BIT;
    if (!compute || (format.optimalTilingFeatures & required) != required) goto fail;

    const VkDescriptorSetLayoutBinding bindings[2] = {
        { .binding = 0, .descriptorType = VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER,
          .descriptorCount = 1, .stageFlags = VK_SHADER_STAGE_COMPUTE_BIT },
        { .binding = 1, .descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_IMAGE,
          .descriptorCount = 1, .stageFlags = VK_SHADER_STAGE_COMPUTE_BIT }
    };
    const VkDescriptorSetLayoutCreateInfo sc = {
        .sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO,
        .bindingCount = 2, .pBindings = bindings
    };
    if (pvkCreateDescriptorSetLayout(s_vk.device, &sc, NULL, &tb_fxaa.set_layout)) goto fail;
    const VkPipelineLayoutCreateInfo lc = {
        .sType = VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO,
        .setLayoutCount = 1, .pSetLayouts = &tb_fxaa.set_layout
    };
    if (pvkCreatePipelineLayout(s_vk.device, &lc, NULL, &tb_fxaa.layout)) goto fail;
    const VkDescriptorPoolSize sizes[2] = {
        { VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER, 1 },
        { VK_DESCRIPTOR_TYPE_STORAGE_IMAGE, 1 }
    };
    const VkDescriptorPoolCreateInfo pc = {
        .sType = VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO,
        .maxSets = 1, .poolSizeCount = 2, .pPoolSizes = sizes
    };
    if (pvkCreateDescriptorPool(s_vk.device, &pc, NULL, &tb_fxaa.pool)) goto fail;
    const VkDescriptorSetAllocateInfo ac = {
        .sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO,
        .descriptorPool = tb_fxaa.pool, .descriptorSetCount = 1,
        .pSetLayouts = &tb_fxaa.set_layout
    };
    if (pvkAllocateDescriptorSets(s_vk.device, &ac, &tb_fxaa.set)) goto fail;
    const VkSamplerCreateInfo sampler = {
        .sType = VK_STRUCTURE_TYPE_SAMPLER_CREATE_INFO,
        .magFilter = VK_FILTER_LINEAR, .minFilter = VK_FILTER_LINEAR,
        .mipmapMode = VK_SAMPLER_MIPMAP_MODE_NEAREST,
        .addressModeU = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE,
        .addressModeV = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE,
        .addressModeW = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE
    };
    if (pvkCreateSampler(s_vk.device, &sampler, NULL, &tb_fxaa.sampler)) goto fail;
    const VkShaderModuleCreateInfo mc = {
        .sType = VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO,
        .codeSize = sizeof tb_fxaa_spirv, .pCode = tb_fxaa_spirv
    };
    if (pvkCreateShaderModule(s_vk.device, &mc, NULL, &module)) goto fail;
    const VkComputePipelineCreateInfo pipeline = {
        .sType = VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO,
        .stage = { .sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO,
            .stage = VK_SHADER_STAGE_COMPUTE_BIT, .module = module, .pName = "main" },
        .layout = tb_fxaa.layout
    };
    const VkResult result = pvkCreateComputePipelines(s_vk.device, VK_NULL_HANDLE,
        1, &pipeline, NULL, &tb_fxaa.pipeline);
    pvkDestroyShaderModule(s_vk.device, module, NULL);
    module = VK_NULL_HANDLE;
    if (result) goto fail;
    tb_fxaa.state = 1;
    return 1;
fail:
    if (module) pvkDestroyShaderModule(s_vk.device, module, NULL);
    tb_fxaa_shutdown();
    tb_fxaa.state = -1;
    VK_LOG("optional FXAA unavailable on this device\n");
    return 0;
}

static int tb_fxaa_bind_source(const vk_image* source, VkExtent2D extent)
{
    if (tb_fxaa.extent.width != extent.width || tb_fxaa.extent.height != extent.height) {
        tb_present_destroy_image(&tb_fxaa.output);
        tb_fxaa.written = 0;
        tb_fxaa.source = VK_NULL_HANDLE;
        tb_fxaa.extent = extent;
        if (vk_create_image(&tb_fxaa.output, VK_FORMAT_R8G8B8A8_UNORM, extent.width, extent.height,
                VK_IMAGE_USAGE_STORAGE_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT,
                VK_IMAGE_ASPECT_COLOR_BIT)) {
            tb_present_destroy_image(&tb_fxaa.output);
            VK_LOG("optional FXAA target unavailable at %ux%u\n", extent.width, extent.height);
        } else VK_LOG("optional FXAA ready (%ux%u), F4 toggles antialiasing\n", extent.width, extent.height);
    }
    if (!tb_fxaa.output.img) return 0;
    if (tb_fxaa.source == source->view) return 1;
    const VkDescriptorImageInfo images[2] = {
        { tb_fxaa.sampler, source->view, VK_IMAGE_LAYOUT_GENERAL },
        { VK_NULL_HANDLE, tb_fxaa.output.view, VK_IMAGE_LAYOUT_GENERAL }
    };
    const VkWriteDescriptorSet writes[2] = {
        { .sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET,
          .dstSet = tb_fxaa.set, .dstBinding = 0, .descriptorCount = 1,
          .descriptorType = VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER, .pImageInfo = &images[0] },
        { .sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET,
          .dstSet = tb_fxaa.set, .dstBinding = 1, .descriptorCount = 1,
          .descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_IMAGE, .pImageInfo = &images[1] }
    };
    pvkUpdateDescriptorSets(s_vk.device, 2, writes, 0, NULL);
    tb_fxaa.source = source->view;
    return 1;
}

static VkImage tb_fxaa_window_source(VkCommandBuffer cb)
{
    const vk_image* source = tb_present_image();
    const VkExtent2D extent = tb_present_extent();
    if (!tb_settings_antialiasing() || !tb_fxaa_init() || !tb_fxaa_bind_source(source, extent)) return source->img;
    vk_barrier_image_on(cb, source->img, VK_IMAGE_ASPECT_COLOR_BIT, VK_IMAGE_LAYOUT_GENERAL);
    vk_barrier_image_on(cb, tb_fxaa.output.img, VK_IMAGE_ASPECT_COLOR_BIT,
        tb_fxaa.written ? VK_IMAGE_LAYOUT_GENERAL : VK_IMAGE_LAYOUT_UNDEFINED);
    pvkCmdBindPipeline(cb, VK_PIPELINE_BIND_POINT_COMPUTE, tb_fxaa.pipeline);
    pvkCmdBindDescriptorSets(cb, VK_PIPELINE_BIND_POINT_COMPUTE, tb_fxaa.layout,
        0, 1, &tb_fxaa.set, 0, NULL);
    pvkCmdDispatch(cb, (extent.width + 7) / 8, (extent.height + 7) / 8, 1);
    vk_barrier_image_on(cb, tb_fxaa.output.img, VK_IMAGE_ASPECT_COLOR_BIT, VK_IMAGE_LAYOUT_GENERAL);
    tb_fxaa.written = 1;
    return tb_fxaa.output.img;
}
