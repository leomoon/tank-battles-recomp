/* GPU NV3089 point sampling between two engine colour surfaces (see
 * sdk_overrides/nv3089_gpu.inc and gpu_blit_engine.inc).
 *
 * The guest-memory path takes, for destination pixel (x, y), source texel
 * (ax + kx*x, ay + ky*y). vkCmdBlitImage with nearest filtering cannot be
 * relied on for that at even scales: every sample lands exactly on a texel
 * edge and this driver picks the other side. So the copy is a strided gather
 * with integer indexing instead, all on the GPU and recorded into the open
 * batch: the sampled source rectangle is copied into a buffer, a compute
 * shader (shaders/nv3089_gather.comp) picks the texels, and the result is
 * copied into the destination image. Each submission slot owns its own region
 * of the two buffers, so a batch still in flight is never overwritten;
 * gathers within one batch are ordered by full barriers. tb_vk_blit_exact()
 * checks the whole path once against the CPU rule on real images and the GPU
 * path stays off unless every texel matches. */
#include "../shaders/nv3089_gather_spirv.inc"
#define TB_GATHER_IN  (16u << 20)   /* source texels per slot: a 2560x1440 scene */
#define TB_GATHER_OUT (8u << 20)    /* destination pixels per slot */
#define TB_GATHER_SLOTS 4u          /* one region per submission slot (TB_VK_SLOTS) */
static struct {
    VkDescriptorSetLayout sl; VkPipelineLayout layout; VkDescriptorPool pool;
    VkDescriptorSet set; VkPipeline pipeline; vk_buffer in, out;
    int state;   /* 0 untried, 1 ready, -1 unavailable */
} tb_gather;
static int tb_blit_exact = -1;   /* -1 untested, 0 no, 1 yes */

/* Image-source gather (shaders/nv3089_gather_image.comp) for RGBA8 sources:
 * texelFetch reads only the kept texels, so the whole source rectangle is no
 * longer copied into tb_gather.in first. Set 0 (source image and sampler)
 * comes from the open batch's descriptor pool, so it lives exactly as long as
 * the batch; set 1 is the static output buffer. The once-per-device exactness
 * test checks this path and the buffer path separately, and each runs only if
 * it matched every texel. */
#include "../shaders/nv3089_gather_image_spirv.inc"
#define TB_GATHER_IMAGE_SETS 64u   /* image-source gathers per submission slot */
static struct {
    VkDescriptorSetLayout sl_image, sl_out; VkPipelineLayout layout; VkDescriptorPool pool;
    VkDescriptorPool image_pools[TB_GATHER_SLOTS]; u32 used[TB_GATHER_SLOTS];
    VkDescriptorSet set_out; VkPipeline pipeline;
    int state;   /* 0 untried, 1 ready, -1 unavailable */
} tb_gather_image;

/* The slot's previous batch has completed (vulkan_async_batches.inc): its
 * source-image descriptor sets can be recycled. */
static void tb_gather_slot_reset(u32 slot)
{
    slot %= TB_GATHER_SLOTS;
    if (tb_gather_image.image_pools[slot] && tb_gather_image.used[slot]) {
        pvkResetDescriptorPool(s_vk.device, tb_gather_image.image_pools[slot], 0);
        tb_gather_image.used[slot] = 0;
    }
}
static int tb_gather_mode = 0;   /* 0 automatic, 1 force buffer path, 2 force image path (self-test) */
static int tb_gather_image_exact = 0;

static void tb_gather_image_shutdown(void)
{
    if (tb_gather_image.pipeline) pvkDestroyPipeline(s_vk.device, tb_gather_image.pipeline, NULL);
    if (tb_gather_image.pool) pvkDestroyDescriptorPool(s_vk.device, tb_gather_image.pool, NULL);
    for (u32 i = 0; i < TB_GATHER_SLOTS; i++)
        if (tb_gather_image.image_pools[i]) pvkDestroyDescriptorPool(s_vk.device, tb_gather_image.image_pools[i], NULL);
    if (tb_gather_image.layout) pvkDestroyPipelineLayout(s_vk.device, tb_gather_image.layout, NULL);
    if (tb_gather_image.sl_image) pvkDestroyDescriptorSetLayout(s_vk.device, tb_gather_image.sl_image, NULL);
    if (tb_gather_image.sl_out) pvkDestroyDescriptorSetLayout(s_vk.device, tb_gather_image.sl_out, NULL);
    memset(&tb_gather_image, 0, sizeof tb_gather_image);
}

static void tb_gather_shutdown(void)
{
    tb_gather_image_shutdown();
    vk_destroy_buffer(&tb_gather.in); vk_destroy_buffer(&tb_gather.out);
    if (tb_gather.pipeline) pvkDestroyPipeline(s_vk.device, tb_gather.pipeline, NULL);
    if (tb_gather.pool) pvkDestroyDescriptorPool(s_vk.device, tb_gather.pool, NULL);
    if (tb_gather.layout) pvkDestroyPipelineLayout(s_vk.device, tb_gather.layout, NULL);
    if (tb_gather.sl) pvkDestroyDescriptorSetLayout(s_vk.device, tb_gather.sl, NULL);
    memset(&tb_gather, 0, sizeof tb_gather);
}

static int tb_gather_init(void)
{
    if (tb_gather.state) return tb_gather.state > 0;
    tb_gather.state = -1;
    u32 count = 0;
    pvkGetPhysicalDeviceQueueFamilyProperties(s_vk.phys, &count, NULL);
    VkQueueFamilyProperties* queues = calloc(count ? count : 1, sizeof *queues);
    if (!queues) return 0;
    pvkGetPhysicalDeviceQueueFamilyProperties(s_vk.phys, &count, queues);
    const int compute = s_vk.queue_family < count && (queues[s_vk.queue_family].queueFlags & VK_QUEUE_COMPUTE_BIT);
    free(queues);
    if (!compute) return 0;
    VkDescriptorSetLayoutBinding bindings[2] = {
        { .binding = 0, .descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, .descriptorCount = 1, .stageFlags = VK_SHADER_STAGE_COMPUTE_BIT },
        { .binding = 1, .descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, .descriptorCount = 1, .stageFlags = VK_SHADER_STAGE_COMPUTE_BIT } };
    VkDescriptorSetLayoutCreateInfo sc = { .sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO, .bindingCount = 2, .pBindings = bindings };
    VkPushConstantRange push = { VK_SHADER_STAGE_COMPUTE_BIT, 0, 9 * sizeof(u32) };
    VkPipelineLayoutCreateInfo lc = { .sType = VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO, .setLayoutCount = 1,
                                      .pSetLayouts = &tb_gather.sl, .pushConstantRangeCount = 1, .pPushConstantRanges = &push };
    VkDescriptorPoolSize size = { VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, 2 };
    VkDescriptorPoolCreateInfo pc = { .sType = VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO, .maxSets = 1, .poolSizeCount = 1, .pPoolSizes = &size };
    VkShaderModuleCreateInfo mc = { .sType = VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO,
                                    .codeSize = sizeof tb_nv3089_gather_spirv, .pCode = tb_nv3089_gather_spirv };
    VkShaderModule module = VK_NULL_HANDLE;
    if (pvkCreateDescriptorSetLayout(s_vk.device, &sc, NULL, &tb_gather.sl) ||
        pvkCreatePipelineLayout(s_vk.device, &lc, NULL, &tb_gather.layout) ||
        pvkCreateDescriptorPool(s_vk.device, &pc, NULL, &tb_gather.pool)) goto fail;
    VkDescriptorSetAllocateInfo ac = { .sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO, .descriptorPool = tb_gather.pool,
                                       .descriptorSetCount = 1, .pSetLayouts = &tb_gather.sl };
    if (pvkAllocateDescriptorSets(s_vk.device, &ac, &tb_gather.set) ||
        pvkCreateShaderModule(s_vk.device, &mc, NULL, &module)) goto fail;
    VkComputePipelineCreateInfo cp = { .sType = VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO,
        .stage = { .sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO, .stage = VK_SHADER_STAGE_COMPUTE_BIT,
                   .module = module, .pName = "main" }, .layout = tb_gather.layout };
    const VkResult rc = pvkCreateComputePipelines(s_vk.device, VK_NULL_HANDLE, 1, &cp, NULL, &tb_gather.pipeline);
    pvkDestroyShaderModule(s_vk.device, module, NULL);
    if (rc ||
        vk_create_device_buffer(&tb_gather.in, (VkDeviceSize)TB_GATHER_IN * TB_GATHER_SLOTS,
                                VK_BUFFER_USAGE_TRANSFER_DST_BIT | VK_BUFFER_USAGE_STORAGE_BUFFER_BIT) ||
        vk_create_device_buffer(&tb_gather.out, (VkDeviceSize)TB_GATHER_OUT * TB_GATHER_SLOTS,
                                VK_BUFFER_USAGE_TRANSFER_SRC_BIT | VK_BUFFER_USAGE_STORAGE_BUFFER_BIT)) goto fail;
    /* The set never changes: per-slot regions are addressed by push constants. */
    VkDescriptorBufferInfo bi[2] = { { tb_gather.in.buf, 0, VK_WHOLE_SIZE }, { tb_gather.out.buf, 0, VK_WHOLE_SIZE } };
    VkWriteDescriptorSet w[2] = {
        { .sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET, .dstSet = tb_gather.set, .dstBinding = 0, .descriptorCount = 1,
          .descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, .pBufferInfo = &bi[0] },
        { .sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET, .dstSet = tb_gather.set, .dstBinding = 1, .descriptorCount = 1,
          .descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, .pBufferInfo = &bi[1] } };
    pvkUpdateDescriptorSets(s_vk.device, 2, w, 0, NULL);
    tb_gather.state = 1;
    return 1;
fail:
    tb_gather_shutdown();
    tb_gather.state = -1;
    VK_LOG("NV3089 GPU gather unavailable: staying on the CPU path\n");
    return 0;
}

static int tb_gather_image_init(void)
{
    if (tb_gather_image.state) return tb_gather_image.state > 0;
    tb_gather_image.state = -1;
    if (!s_vk.sampler) return 0;
    VkDescriptorSetLayoutBinding image_bindings[2] = {
        { .binding = 0, .descriptorType = VK_DESCRIPTOR_TYPE_SAMPLED_IMAGE, .descriptorCount = 1, .stageFlags = VK_SHADER_STAGE_COMPUTE_BIT },
        { .binding = 1, .descriptorType = VK_DESCRIPTOR_TYPE_SAMPLER, .descriptorCount = 1, .stageFlags = VK_SHADER_STAGE_COMPUTE_BIT } };
    VkDescriptorSetLayoutBinding out_binding = { .binding = 0, .descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,
                                                 .descriptorCount = 1, .stageFlags = VK_SHADER_STAGE_COMPUTE_BIT };
    VkDescriptorSetLayoutCreateInfo si = { .sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO, .bindingCount = 2, .pBindings = image_bindings };
    VkDescriptorSetLayoutCreateInfo so = { .sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO, .bindingCount = 1, .pBindings = &out_binding };
    if (pvkCreateDescriptorSetLayout(s_vk.device, &si, NULL, &tb_gather_image.sl_image) ||
        pvkCreateDescriptorSetLayout(s_vk.device, &so, NULL, &tb_gather_image.sl_out)) goto fail;
    VkDescriptorSetLayout layouts[2] = { tb_gather_image.sl_image, tb_gather_image.sl_out };
    VkPushConstantRange push = { VK_SHADER_STAGE_COMPUTE_BIT, 0, 7 * sizeof(u32) };
    VkPipelineLayoutCreateInfo lc = { .sType = VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO, .setLayoutCount = 2,
                                      .pSetLayouts = layouts, .pushConstantRangeCount = 1, .pPushConstantRanges = &push };
    VkDescriptorPoolSize size = { VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, 1 };
    VkDescriptorPoolCreateInfo pc = { .sType = VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO, .maxSets = 1, .poolSizeCount = 1, .pPoolSizes = &size };
    if (pvkCreatePipelineLayout(s_vk.device, &lc, NULL, &tb_gather_image.layout) ||
        pvkCreateDescriptorPool(s_vk.device, &pc, NULL, &tb_gather_image.pool)) goto fail;
    VkDescriptorPoolSize image_sizes[2] = { { VK_DESCRIPTOR_TYPE_SAMPLED_IMAGE, TB_GATHER_IMAGE_SETS },
                                            { VK_DESCRIPTOR_TYPE_SAMPLER, TB_GATHER_IMAGE_SETS } };
    VkDescriptorPoolCreateInfo ipc = { .sType = VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO, .maxSets = TB_GATHER_IMAGE_SETS,
                                       .poolSizeCount = 2, .pPoolSizes = image_sizes };
    for (u32 i = 0; i < TB_GATHER_SLOTS; i++)
        if (pvkCreateDescriptorPool(s_vk.device, &ipc, NULL, &tb_gather_image.image_pools[i])) goto fail;
    VkDescriptorSetAllocateInfo ac = { .sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO, .descriptorPool = tb_gather_image.pool,
                                       .descriptorSetCount = 1, .pSetLayouts = &tb_gather_image.sl_out };
    VkShaderModuleCreateInfo mc = { .sType = VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO,
                                    .codeSize = sizeof tb_nv3089_gather_image_spirv, .pCode = tb_nv3089_gather_image_spirv };
    VkShaderModule module = VK_NULL_HANDLE;
    if (pvkAllocateDescriptorSets(s_vk.device, &ac, &tb_gather_image.set_out) ||
        pvkCreateShaderModule(s_vk.device, &mc, NULL, &module)) goto fail;
    VkComputePipelineCreateInfo cp = { .sType = VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO,
        .stage = { .sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO, .stage = VK_SHADER_STAGE_COMPUTE_BIT,
                   .module = module, .pName = "main" }, .layout = tb_gather_image.layout };
    const VkResult rc = pvkCreateComputePipelines(s_vk.device, VK_NULL_HANDLE, 1, &cp, NULL, &tb_gather_image.pipeline);
    pvkDestroyShaderModule(s_vk.device, module, NULL);
    if (rc) goto fail;
    VkDescriptorBufferInfo bi = { tb_gather.out.buf, 0, VK_WHOLE_SIZE };
    VkWriteDescriptorSet w = { .sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET, .dstSet = tb_gather_image.set_out, .dstBinding = 0,
                               .descriptorCount = 1, .descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, .pBufferInfo = &bi };
    pvkUpdateDescriptorSets(s_vk.device, 1, &w, 0, NULL);
    tb_gather_image.state = 1;
    return 1;
fail:
    tb_gather_image_shutdown();
    tb_gather_image.state = -1;
    VK_LOG("NV3089 image-source gather unavailable: using the buffer gather\n");
    return 0;
}

/* The image-source dispatch, recorded after the shared prologue. Returns 0
 * when its per-batch descriptor set cannot be had (the buffer path runs). */
static int tb_gather_image_dispatch(VkCommandBuffer cb, vk_eng_obj* s, u32 sx, u32 sy, u32 kx, u32 ky,
                                    u32 slot, u32 dw, u32 dh)
{
    if (!s->im.view) return 0;
    /* Serial submission waits for every gather, so its pool can be reset now;
     * asynchronous slots reset theirs when the slot is reused. */
    if (!tb_async_active()) tb_gather_slot_reset(slot);
    if (tb_gather_image.used[slot] >= TB_GATHER_IMAGE_SETS) return 0;
    VkDescriptorSet set = VK_NULL_HANDLE;
    VkDescriptorSetAllocateInfo ac = { .sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO,
                                       .descriptorPool = tb_gather_image.image_pools[slot],
                                       .descriptorSetCount = 1, .pSetLayouts = &tb_gather_image.sl_image };
    if (pvkAllocateDescriptorSets(s_vk.device, &ac, &set) != VK_SUCCESS) return 0;
    tb_gather_image.used[slot]++;
    VkDescriptorImageInfo image = { .imageView = s->im.view, .imageLayout = VK_IMAGE_LAYOUT_GENERAL };
    VkDescriptorImageInfo sampler = { .sampler = s_vk.sampler };
    VkWriteDescriptorSet w[2] = {
        { .sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET, .dstSet = set, .dstBinding = 0, .descriptorCount = 1,
          .descriptorType = VK_DESCRIPTOR_TYPE_SAMPLED_IMAGE, .pImageInfo = &image },
        { .sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET, .dstSet = set, .dstBinding = 1, .descriptorCount = 1,
          .descriptorType = VK_DESCRIPTOR_TYPE_SAMPLER, .pImageInfo = &sampler } };
    pvkUpdateDescriptorSets(s_vk.device, 2, w, 0, NULL);
    VkDescriptorSet sets[2] = { set, tb_gather_image.set_out };
    pvkCmdBindPipeline(cb, VK_PIPELINE_BIND_POINT_COMPUTE, tb_gather_image.pipeline);
    pvkCmdBindDescriptorSets(cb, VK_PIPELINE_BIND_POINT_COMPUTE, tb_gather_image.layout, 0, 2, sets, 0, NULL);
    const u32 shape[7] = { slot * (TB_GATHER_OUT / 4u), dw, dh, kx, ky, sx, sy };
    pvkCmdPushConstants(cb, tb_gather_image.layout, VK_SHADER_STAGE_COMPUTE_BIT, 0, sizeof shape, shape);
    pvkCmdDispatch(cb, (dw + 7u) / 8u, (dh + 7u) / 8u, 1);
    return 1;
}

/* Destination (dx, dy, dw, dh) takes source texels (sx + kx*i, sy + ky*j). */
static int tb_vk_gather_record(u32 src, u32 sx, u32 sy, u32 kx, u32 ky,
                               u32 dst, u32 dx, u32 dy, u32 dw, u32 dh)
{
    vk_eng_obj* s = vk_eng_get(src, VK_ENG_COLOR);
    vk_eng_obj* d = vk_eng_get(dst, VK_ENG_COLOR);
    if (!s || !d || s == d || s->fmt != d->fmt || s->bpp != 4 || !kx || !ky || !dw || !dh) return 0;
    const u32 cw = (dw - 1u) * kx + 1u, ch = (dh - 1u) * ky + 1u;   /* the sampled source rectangle */
    if (sx + cw > s->w || sy + ch > s->h || dx + dw > d->w || dy + dh > d->h) return 0;
    if ((u64)cw * ch * 4u > TB_GATHER_IN || (u64)dw * dh * 4u > TB_GATHER_OUT || !tb_gather_init()) return 0;
    const u32 slot = tb_async_active() ? tb_cur % TB_GATHER_SLOTS : 0;
    const int batched = tb_async_record();
    if (batched ? (!s_eb_pending && vk_begin()) : vk_begin()) return 0;
    VkCommandBuffer cb = s_vk.cmd;
    vk_cmd_after_everything(cb);                      /* earlier draws into the source, earlier gathers */
    vk_barrier_image(s->im.img, VK_IMAGE_ASPECT_COLOR_BIT, VK_IMAGE_LAYOUT_GENERAL);
    const int image_path = s->fmt == VK_FORMAT_R8G8B8A8_UNORM && tb_gather_mode != 1 &&
                           (tb_gather_mode == 2 || tb_gather_image_exact) && tb_gather_image_init();
    if (!(image_path && tb_gather_image_dispatch(cb, s, sx, sy, kx, ky, slot, dw, dh))) {
        if (tb_gather_mode == 2) return 0;   /* self-test of the image path only */
        VkBufferImageCopy in = { .bufferOffset = (VkDeviceSize)slot * TB_GATHER_IN, .bufferRowLength = cw,
                                 .imageSubresource = { VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1 },
                                 .imageOffset = { (int32_t)sx, (int32_t)sy, 0 }, .imageExtent = { cw, ch, 1 } };
        pvkCmdCopyImageToBuffer(cb, s->im.img, VK_IMAGE_LAYOUT_GENERAL, tb_gather.in.buf, 1, &in);
        vk_cmd_after_everything(cb);
        pvkCmdBindPipeline(cb, VK_PIPELINE_BIND_POINT_COMPUTE, tb_gather.pipeline);
        pvkCmdBindDescriptorSets(cb, VK_PIPELINE_BIND_POINT_COMPUTE, tb_gather.layout, 0, 1, &tb_gather.set, 0, NULL);
        const u32 shape[9] = { slot * (TB_GATHER_IN / 4u), cw, slot * (TB_GATHER_OUT / 4u), dw, dh, kx, ky, 0, 0 };
        pvkCmdPushConstants(cb, tb_gather.layout, VK_SHADER_STAGE_COMPUTE_BIT, 0, sizeof shape, shape);
        pvkCmdDispatch(cb, (dw + 7u) / 8u, (dh + 7u) / 8u, 1);
    }
    vk_cmd_after_everything(cb);
    vk_barrier_image(d->im.img, VK_IMAGE_ASPECT_COLOR_BIT, VK_IMAGE_LAYOUT_GENERAL);
    VkBufferImageCopy out = { .bufferOffset = (VkDeviceSize)slot * TB_GATHER_OUT, .bufferRowLength = dw,
                              .imageSubresource = { VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1 },
                              .imageOffset = { (int32_t)dx, (int32_t)dy, 0 }, .imageExtent = { dw, dh, 1 } };
    pvkCmdCopyBufferToImage(cb, tb_gather.out.buf, d->im.img, VK_IMAGE_LAYOUT_GENERAL, 1, &out);
    /* The result, visible to whatever draws, samples or copies it next. */
    vk_barrier_image(d->im.img, VK_IMAGE_ASPECT_COLOR_BIT, VK_IMAGE_LAYOUT_GENERAL);
    if (batched) vk_eb_recorded();
    else if (vk_submit_and_wait()) return 0;
    return 1;
}

/* Once per device: the whole path against the CPU rule on distinct texels. */
static int tb_vk_blit_exact(void)
{
    if (tb_blit_exact >= 0) return tb_blit_exact;
    tb_blit_exact = 0;
    enum { SW = 96, SH = 40 };
    static u8 seed[SW * SH * 4], out[SW * SH * 4];
    for (u32 i = 0; i < SW * SH; i++) {
        seed[i * 4] = (u8)i; seed[i * 4 + 1] = (u8)(i >> 8); seed[i * 4 + 2] = (u8)(i * 37); seed[i * 4 + 3] = 0xA5;
    }
    const u32 src = vk_eng_color_target_create(NULL, RSX_BE_FMT_R8G8B8A8, SW, SH, seed, SW * 4);
    const u32 dst = vk_eng_color_target_create(NULL, RSX_BE_FMT_R8G8B8A8, SW, SH, NULL, 0);
    /* {scale, first texel}: the title's resolve is k = 2 from texel 1. Each GPU
     * path is checked on its own: 1 = buffer gather, 2 = image-source gather. */
    static const u32 cases[][2] = { { 1, 0 }, { 1, 3 }, { 2, 1 }, { 2, 2 }, { 3, 1 }, { 4, 2 } };
    int exact[3] = { 0, 0, 0 };
    for (int mode = 1; mode <= 2; mode++) {
        int ok = src && dst;
        tb_gather_mode = mode;
        for (u32 n = 0; ok && n < 6; n++) {
            const u32 k = cases[n][0], a = cases[n][1];
            const u32 dw = (SW - a - 1) / k + 1, dh = (SH - a - 1) / k + 1;
            memset(out, 0, sizeof out);
            ok = tb_vk_gather_record(src, a, a, k, k, dst, 1, 2, dw - 1, dh - 2);
            if (!ok) break;
            vk_eng_readback(NULL, dst, 1, 2, dw - 1, dh - 2, out, (dw - 1) * 4);
            for (u32 y = 0; ok && y < dh - 2; y++)
                for (u32 x = 0; ok && x < dw - 1; x++)
                    ok = !memcmp(out + (y * (dw - 1) + x) * 4, seed + ((a + k * y) * SW + a + k * x) * 4, 4);
        }
        exact[mode] = ok;
    }
    tb_gather_mode = 0;
    if (src) vk_eng_release(NULL, src);
    if (dst) vk_eng_release(NULL, dst);
    tb_blit_exact = exact[1];
    tb_gather_image_exact = exact[1] && exact[2];
    VK_LOG("NV3089 GPU point sampling %s\n", exact[1] ? "matches the guest-memory path exactly" : "differs from the guest-memory path: staying on the CPU");
    if (exact[1]) VK_LOG("NV3089 image-source gather %s\n", exact[2] ? "matches exactly: active" : "differs: using the buffer gather");
    return tb_blit_exact;
}

/* Engine entry (gpu_blit_engine.inc): 1 when the copy was recorded. */
int tb_vk_surface_blit(u32 src, u32 sx, u32 sy, u32 kx, u32 ky, u32 dst, u32 dx, u32 dy, u32 dw, u32 dh)
{
    if (!tb_vk_scale_native(src)) return 0;
    tb_vk_scale_native_mutation(dst,"point transfer into an authoritative attachment");
    if (!s_vk.device || !s_vk.eng_active || !tb_vk_blit_exact()) return 0;
    return tb_vk_gather_record(src, sx, sy, kx, ky, dst, dx, dy, dw, dh);
}

/* Convert each end from its own guest extent. The Native NV3089 path
 * above keeps its exact integer samples. The display resolve filters the
 * scaled scene at the scaled output grid. Native uploads use nearest. */
static int tb_vk_color_rect(const vk_eng_obj* image, tb_scale_rect guest, tb_scale_rect* host)
{
    const tb_scale_size size = {image->w, image->h};
    return tb_scale_rect_map((tb_scale_extent){image->scale_percent ? image->guest_size : size, size},
                             guest, host);
}

static void tb_vk_record_color_blit(VkCommandBuffer cb, VkImage src, tb_scale_rect s,
                                   VkImage dst, tb_scale_rect d, VkFilter filter)
{
    vk_barrier_image_on(cb, src, VK_IMAGE_ASPECT_COLOR_BIT, VK_IMAGE_LAYOUT_GENERAL);
    vk_barrier_image_on(cb, dst, VK_IMAGE_ASPECT_COLOR_BIT, VK_IMAGE_LAYOUT_GENERAL);
    const VkImageBlit blit = {
        .srcSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1},
        .srcOffsets = {{(int32_t)s.x, (int32_t)s.y, 0},
                       {(int32_t)(s.x+s.width), (int32_t)(s.y+s.height), 1}},
        .dstSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1},
        .dstOffsets = {{(int32_t)d.x, (int32_t)d.y, 0},
                       {(int32_t)(d.x+d.width), (int32_t)(d.y+d.height), 1}}
    };
    pvkCmdBlitImage(cb, src, VK_IMAGE_LAYOUT_GENERAL, dst, VK_IMAGE_LAYOUT_GENERAL, 1, &blit, filter);
}

static int tb_vk_color_blit(u32 source, tb_scale_rect source_rect, u32 dest,
                           tb_scale_rect dest_rect, VkFilter filter)
{
    const vk_eng_obj* src = vk_eng_get(source, VK_ENG_COLOR);
    const vk_eng_obj* dst = vk_eng_get(dest, VK_ENG_COLOR);
    tb_scale_rect s, d;
    if (!src || !dst || src->fmt != VK_FORMAT_R8G8B8A8_UNORM || dst->fmt != src->fmt) return 0;
    const int source_mapping = tb_vk_color_rect(src, source_rect, &s);
    const int dest_mapping = tb_vk_color_rect(dst, dest_rect, &d);
    if (source_mapping == TB_SCALE_RECT_INVALID || dest_mapping == TB_SCALE_RECT_INVALID) return 0;
    if (dest_mapping == TB_SCALE_RECT_EMPTY) return 1; /* valid no-op; no GPU command */
    if (source_mapping == TB_SCALE_RECT_EMPTY) return 0; /* reconstruction requires a nonempty source */

    vk_image snapshot = {0};
    const int alias = src->im.img == dst->im.img;
    if (alias && vk_create_image(&snapshot, src->fmt, s.width, s.height,
            VK_IMAGE_USAGE_TRANSFER_SRC_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT | VK_IMAGE_USAGE_SAMPLED_BIT,
            VK_IMAGE_ASPECT_COLOR_BIT)) {
        vk_destroy_image(&snapshot);
        return 0;
    }
    const int batched = !alias && (vk_eb_active() || tb_async_record());
    int ok = 0;
    if (batched ? (!s_eb_pending && vk_begin()) : vk_begin()) goto done;
    tb_group_end();
    VkImage from = src->im.img;
    if (alias) {
        const tb_scale_rect whole = {0, 0, s.width, s.height};
        vk_barrier_image(snapshot.img, VK_IMAGE_ASPECT_COLOR_BIT, VK_IMAGE_LAYOUT_UNDEFINED);
        tb_vk_record_color_blit(s_vk.cmd, from, s, snapshot.img, whole, VK_FILTER_NEAREST);
        from = snapshot.img;
        s = whole;
    }
    tb_vk_record_color_blit(s_vk.cmd, from, s, dst->im.img, d, filter);
    if (batched) { vk_eb_recorded(); ok = 1; }
    else ok = !vk_submit_and_wait();
done:
    if (alias) vk_destroy_image(&snapshot);
    return ok;
}
