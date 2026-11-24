/* One physical image owns each proven surface generation. Native handles stay
 * stable for the guest engine, but are reconstructed only for an observer.
 * Full-image serials describe the materialized region; a partial write never
 * makes the untouched Native pixels valid. FIFO/render-thread ownership. */
static tb_vk_scale_image* tb_scale_native_image(u32 native)
{
    if (!native || tb_scale_internal) return NULL;
    if (tb_scaled.depth.native == native) return &tb_scaled.depth;
    for (u32 i=0;i<tb_scaled.layout.count;++i) {
        if (tb_scaled.color[i].native == native) return &tb_scaled.color[i];
        if (tb_scaled.display[i].native == native) return &tb_scaled.display[i];
    }
    return NULL;
}

static tb_vk_scale_image* tb_scale_host_image(u32 handle)
{
    if (!handle) return NULL;
    if (tb_scaled.depth.handle == handle) return &tb_scaled.depth;
    for (u32 i=0;i<tb_scaled.layout.count;++i) {
        if (tb_scaled.color[i].handle == handle) return &tb_scaled.color[i];
        if (tb_scaled.display[i].handle == handle) return &tb_scaled.display[i];
    }
    return NULL;
}

/* Keep the guest handle/identity, but allocate its Native representation only
 * when an observer or a partial upload needs it. Contents become valid only
 * after the materialization serial is published. */
static int tb_scale_native_storage(u32 native)
{
    if (!native || native>=VK_ENG_MAX_OBJ) return 0;
    vk_eng_obj* o=&s_eobj[native];
    if (o->im.img) return 1;
    const int color=o->kind==VK_ENG_COLOR;
    if (!color && o->kind!=VK_ENG_DEPTH) return 0;
    const VkImageAspectFlags aspect=color?VK_IMAGE_ASPECT_COLOR_BIT:s_vk.depth_aspect;
    const VkImageUsageFlags usage=VK_IMAGE_USAGE_TRANSFER_SRC_BIT|VK_IMAGE_USAGE_TRANSFER_DST_BIT|
        (color?(VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT|VK_IMAGE_USAGE_SAMPLED_BIT):VK_IMAGE_USAGE_DEPTH_STENCIL_ATTACHMENT_BIT);
    if (vk_create_image(&o->im,o->fmt,o->w,o->h,usage,aspect)) {
        vk_destroy_image(&o->im);
        return 0;
    }
    if (vk_begin()) return 0;
    vk_barrier_image(o->im.img,aspect,VK_IMAGE_LAYOUT_UNDEFINED);
    if (vk_submit_and_wait()) return 0;
    for (u32 i=1;color && i<VK_ENG_MAX_OBJ;++i) {
        vk_eng_obj* view=&s_eobj[i];
        if (view->kind==VK_ENG_VIEW && view->surface==native && !view->im.view &&
            vk_eng_make_view(o->im.img,o->fmt,1,vk_remap(view->remap,view->rsx_fmt),0,&view->im.view)) return 0;
    }
    return 1;
}

static void tb_scale_retire_native(tb_vk_scale_image* image)
{
    if (tb_scale_internal || !image->native || !image->authoritative) return;
    vk_eng_obj* o=&s_eobj[image->native];
    if (!o->im.img) return;
    tb_eng_complete(); /* Native views/framebuffers may belong to older batches. */
    vk_eng_drop_framebuffers();
    for (u32 i=1;i<VK_ENG_MAX_OBJ;++i)
        if (s_eobj[i].kind==VK_ENG_VIEW && s_eobj[i].surface==image->native)
            vk_destroy_image(&s_eobj[i].im);
    VkMemoryRequirements requirements;
    pvkGetImageMemoryRequirements(s_vk.device,o->im.img,&requirements);
    tb_frame_budget_count(TB_FC_SCALE_RETIRED_BYTES,requirements.size);
    vk_destroy_image(&o->im);
    image->native_serial=0;
    /* Initial imports and observers can grow these to Native dimensions. */
    vk_destroy_buffer(&s_rb_stage);
    vk_destroy_buffer(&s_up_stage);
}

/* D32 and stencil are reconstructed independently with center-point nearest
 * sampling. Transfer staging handles devices without depth/stencil blits.
 * This waited path is for initialization, observers and recovery, never the
 * ordinary scene draw/clear/resolve path. Preserve stencil bytes for continued
 * rendering; the guest ABI's existing depth export packs a zero stencil byte. */
static int tb_scale_copy(u32 source, u32 destination)
{
    const int color=vk_eng_get(source,VK_ENG_COLOR)!=NULL;
    const vk_eng_obj* src=vk_eng_get(source,color?VK_ENG_COLOR:VK_ENG_DEPTH);
    const vk_eng_obj* dst=vk_eng_get(destination,color?VK_ENG_COLOR:VK_ENG_DEPTH);
    if (!src || !dst || !tb_scale_native_storage(source) || !tb_scale_native_storage(destination) || src->fmt!=dst->fmt ||
        (color?src->fmt!=VK_FORMAT_R8G8B8A8_UNORM:
          (src->fmt!=VK_FORMAT_D32_SFLOAT && src->fmt!=VK_FORMAT_D32_SFLOAT_S8_UINT))) return 0;
    const VkImageAspectFlags aspects=color?VK_IMAGE_ASPECT_COLOR_BIT:s_vk.depth_aspect;
    tb_eng_complete();
    for (unsigned stencil=0;stencil<2;++stencil) {
        if (stencil && !(aspects & VK_IMAGE_ASPECT_STENCIL_BIT)) break;
        const u32 bpp=stencil?1:4;
        const VkImageAspectFlags aspect=color?VK_IMAGE_ASPECT_COLOR_BIT:
            (stencil?VK_IMAGE_ASPECT_STENCIL_BIT:VK_IMAGE_ASPECT_DEPTH_BIT);
        vk_buffer* read=vk_stage_get(&s_rb_stage,(VkDeviceSize)src->w*src->h*bpp,VK_BUFFER_USAGE_TRANSFER_DST_BIT);
        vk_buffer* write=vk_stage_get(&s_up_stage,(VkDeviceSize)dst->w*dst->h*bpp,VK_BUFFER_USAGE_TRANSFER_SRC_BIT);
        if (!read || !write || !write->coherent || vk_begin()) return 0;
        vk_barrier_image(src->im.img,aspects,VK_IMAGE_LAYOUT_GENERAL);
        const VkBufferImageCopy out={.imageSubresource={aspect,0,0,1},.imageExtent={src->w,src->h,1}};
        pvkCmdCopyImageToBuffer(s_vk.cmd,src->im.img,VK_IMAGE_LAYOUT_GENERAL,read->buf,1,&out);
        const VkBufferMemoryBarrier barrier={.sType=VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER,
            .srcAccessMask=VK_ACCESS_TRANSFER_WRITE_BIT,.dstAccessMask=VK_ACCESS_HOST_READ_BIT,
            .srcQueueFamilyIndex=VK_QUEUE_FAMILY_IGNORED,.dstQueueFamilyIndex=VK_QUEUE_FAMILY_IGNORED,
            .buffer=read->buf,.size=VK_WHOLE_SIZE};
        pvkCmdPipelineBarrier(s_vk.cmd,VK_PIPELINE_STAGE_TRANSFER_BIT,VK_PIPELINE_STAGE_HOST_BIT,
            0,0,NULL,1,&barrier,0,NULL);
        if (vk_submit_and_wait()) return 0;
        vk_invalidate(read);
        for (u32 y=0;y<dst->h;++y) for (u32 x=0;x<dst->w;++x) {
            const u32 sx=(u64)(2*x+1)*src->w/(2ull*dst->w);
            const u32 sy=(u64)(2*y+1)*src->h/(2ull*dst->h);
            memcpy((u8*)write->ptr+((size_t)y*dst->w+x)*bpp,
                   (const u8*)read->ptr+((size_t)sy*src->w+sx)*bpp,bpp);
        }
        if (vk_begin()) return 0;
        vk_barrier_image(dst->im.img,aspects,VK_IMAGE_LAYOUT_GENERAL);
        const VkBufferImageCopy in={.imageSubresource={aspect,0,0,1},.imageExtent={dst->w,dst->h,1}};
        pvkCmdCopyBufferToImage(s_vk.cmd,write->buf,dst->im.img,VK_IMAGE_LAYOUT_GENERAL,1,&in);
        if (vk_submit_and_wait()) return 0;
    }
    return 1;
}

static int tb_vk_scale_materialize(tb_vk_scale_image* image)
{
    if (!image->native || !image->authoritative || image->native_serial==image->serial) return 1;
    if (!tb_scale_copy(image->handle,image->native)) return 0;
    image->native_serial=image->serial;
    tb_frame_budget_count(TB_FC_SCALE_MATERIALIZE,1);
    return 1;
}

static int tb_vk_scale_restore(void)
{
    if (tb_scale_internal) return 1;
    ++tb_scale_internal;
    int ok=tb_vk_scale_materialize(&tb_scaled.depth);
    for (u32 i=0;i<tb_scaled.layout.count;++i) {
        ok=tb_vk_scale_materialize(&tb_scaled.color[i]) && ok;
        ok=tb_vk_scale_materialize(&tb_scaled.display[i]) && ok;
    }
    --tb_scale_internal;
    return ok;
}

void tb_vk_scale_attach(tb_scale_surface id, u32 native, int depth)
{
    tb_vk_scale_image* image=NULL;
    if (depth && !memcmp(&id,&tb_scaled.layout.depth,sizeof id)) image=&tb_scaled.depth;
    for (u32 i=0;!depth && i<tb_scaled.layout.count;++i) {
        if (!memcmp(&id,&tb_scaled.layout.routes[i].color,sizeof id)) image=&tb_scaled.color[i];
        if (!memcmp(&id,&tb_scaled.layout.routes[i].display,sizeof id)) image=&tb_scaled.display[i];
    }
    if (!image || image->native==native) return;
    /* A different native handle without a generation change is not reusable. */
    if (image->native) { tb_vk_scale_fallback("attachment handle changed"); return; }
    image->native=native;
    image->serial=image->native_serial=1;
}

int tb_vk_scale_native(u32 native)
{
    tb_vk_scale_image* image=tb_scale_native_image(native);
    if (!image) return 1;
    ++tb_scale_internal;
    const int ok=tb_vk_scale_materialize(image);
    --tb_scale_internal;
    if (!ok) {
        VK_LOG("authoritative image reconstruction failed; refusing stale guest contents\n");
        abort();
    }
    return ok;
}

u32 tb_vk_scale_sample(u32 native)
{
    tb_vk_scale_image* image=tb_scale_native_image(native);
    return image && image->authoritative && !tb_scale_rejected ? image->handle : native;
}

void tb_vk_scale_wrote(u32 scaled)
{
    tb_vk_scale_image* image=tb_scale_host_image(scaled);
    if (!image) return;
    if (image->native) image->authoritative=1;
    /* A completed image may receive a real guest patch before scanout. */
    for (u32 i=0;i<tb_scaled.layout.count;++i)
        if (image==&tb_scaled.display[i] && tb_scaled.display_ready[i] && tb_scaled.display_ready[i]==image->serial)
            ++tb_scaled.display_ready[i];
    ++image->serial;
    tb_scale_retire_native(image);
}

void tb_vk_scale_fallback(const char* reason)
{
    if (tb_scale_internal || !tb_scaled.percent || tb_scale_rejected) return;
    if (!tb_vk_scale_restore()) {
        VK_LOG("render scale recovery failed (%s); refusing stale Native contents\n",reason);
        abort();
    }
    tb_scale_rejected=1; /* Retain Native until the next launch. */
    tb_scaled.depth.authoritative=0;
    for (u32 i=0;i<tb_scaled.layout.count;++i) {
        tb_scaled.color[i].authoritative=tb_scaled.display[i].authoritative=0;
        tb_scaled.display_ready[i]=0;
        tb_scaled.resolve_source[i]=0;
    }
    tb_vk_scale_skip_frame();
    tb_frame_budget_count(TB_FC_SCALE_RECOVERY,1);
    VK_LOG("render scale %u%% reconstructed for Native continuation (%s)\n",tb_scaled.percent,reason);
    tb_render_scale_fallback();
}

void tb_vk_scale_releasing(u32 native)
{
    tb_vk_scale_image* image=tb_scale_native_image(native);
    if (!image) return;
    tb_vk_scale_fallback("surface generation retired");
    image->native=0;
    image->authoritative=0;
}

void tb_vk_scale_native_mutation(u32 native, const char* reason)
{
    tb_vk_scale_image* image=tb_scale_native_image(native);
    if (image && image->authoritative) tb_vk_scale_fallback(reason);
}

int tb_vk_scale_tracked(u32 native) { return tb_scale_native_image(native)!=NULL; }

/* A CPU zeta write is a real guest observer/mutation. Reconstruct the current
 * frame first, then continue Native with only the written depth/stencil region
 * replaced. Packed guest rows are Z24S8, big endian. Untouched pixels survive. */
int tb_vk_scale_depth_upload(u32 native, tb_scale_rect rect, const u8* packed, u32 pitch)
{
    if (!tb_scale_native_image(native)) return 0;
    tb_vk_scale_fallback("guest depth/stencil write");
    const vk_eng_obj* image=vk_eng_get(native,VK_ENG_DEPTH);
    if (!image || !packed || !rect.width || !rect.height || rect.x>image->w || rect.y>image->h ||
        rect.width>image->w-rect.x || rect.height>image->h-rect.y || pitch<(u64)rect.width*4) return 0;
    for (unsigned stencil=0;stencil<2;++stencil) {
        if (stencil && !(s_vk.depth_aspect & VK_IMAGE_ASPECT_STENCIL_BIT)) break;
        const u32 bpp=stencil?1:4;
        vk_buffer* stage=vk_stage_get(&s_up_stage,(VkDeviceSize)rect.width*rect.height*bpp,VK_BUFFER_USAGE_TRANSFER_SRC_BIT);
        if (!stage || !stage->coherent) return 0;
        for (u32 y=0;y<rect.height;++y) for (u32 x=0;x<rect.width;++x) {
            const u8* pixel=packed+(size_t)y*pitch+4*x;
            const size_t index=(size_t)y*rect.width+x;
            if (stencil) ((u8*)stage->ptr)[index]=pixel[3];
            else ((float*)stage->ptr)[index]=((pixel[0]<<16)|(pixel[1]<<8)|pixel[2])/16777215.0f;
        }
        if (vk_begin()) return 0;
        vk_barrier_image(image->im.img,s_vk.depth_aspect,VK_IMAGE_LAYOUT_GENERAL);
        const VkBufferImageCopy region={
            .imageSubresource={stencil?VK_IMAGE_ASPECT_STENCIL_BIT:VK_IMAGE_ASPECT_DEPTH_BIT,0,0,1},
            .imageOffset={(int32_t)rect.x,(int32_t)rect.y,0},.imageExtent={rect.width,rect.height,1}};
        pvkCmdCopyBufferToImage(s_vk.cmd,stage->buf,image->im.img,VK_IMAGE_LAYOUT_GENERAL,1,&region);
        if (vk_submit_and_wait()) return 0;
    }
    return 1;
}

int tb_vk_scale_uploaded(u32 native, tb_scale_rect rect)
{
    tb_vk_scale_image* image=tb_scale_native_image(native);
    if (!image || !image->authoritative || tb_scale_rejected) return 1;
    /* The Native staging image was just written. Finish that upload before
     * sampling it for a partial scaled patch (also required on the Iris path). */
    tb_eng_complete();
    if (!tb_vk_color_blit(native,rect,image->handle,rect,VK_FILTER_NEAREST)) {
        tb_vk_scale_fallback("partial guest upload could not map");
        return 0; /* Caller reapplies the new rows after recovery. */
    }
    tb_vk_scale_wrote(image->handle);
    return 1;
}

int tb_vk_scale_use(tb_scale_surface color, tb_scale_surface depth, tb_scale_binding* binding)
{
    if (!tb_scaled.percent || tb_scale_rejected || memcmp(&depth,&tb_scaled.layout.depth,sizeof depth)) return 0;
    const u32 handle=tb_vk_scale_find_color(color);
    tb_vk_scale_image* images[]={tb_scale_host_image(handle),&tb_scaled.depth};
    if (!images[0] || !images[0]->native || !images[1]->native) return 0;
    ++tb_scale_internal;
    int ok=1;
    for (unsigned i=0;i<2;++i) if (!images[i]->authoritative) {
        if (!tb_scale_copy(images[i]->native,images[i]->handle)) { ok=0; break; }
        images[i]->authoritative=1;
    }
    --tb_scale_internal;
    if (!ok) { tb_vk_scale_fallback("initial content import"); return 0; }
    tb_scaled.active_color=handle;
    tb_scaled.clear_mask=TB_SCALE_CLEAR_COLOR|TB_SCALE_CLEAR_DEPTH|TB_SCALE_CLEAR_STENCIL;
    tb_scaled.stencil_known=1;
    return tb_vk_scale_bindings(color,depth,0,binding);
}

int tb_vk_scale_draw_ready(u32 color)
{
    const vk_eng_obj* image=vk_eng_get(color,VK_ENG_COLOR);
    if (!image || !image->scale_percent) return 0;
    const tb_scale_extent extent=tb_vk_scale_extent(image);
    const VkRect2D sc=s_e2.have_sc?tb_vk_scale_scissor(image,s_e2.sc):(VkRect2D){{0,0},{image->w,image->h}};
    if (!sc.extent.width || !sc.extent.height) return 1;
    const float whole[]={0,0,(float)extent.guest.width,(float)extent.guest.height};
    const float* vp=s_e2.have_vp && s_e2.vp[2]>0 && s_e2.vp[3]>0?s_e2.vp:whole;
    float mapped[4],slope;
    if (tb_scale_viewport_map(extent,vp,mapped) &&
        tb_scale_viewport_fits(mapped,tb_scaled.viewport_limit,tb_scaled.viewport_min,tb_scaled.viewport_max) &&
        tb_scale_depth_slope(extent,s_e2.depth_slope,&slope)) return 1;
    tb_frame_budget_count(TB_FC_SCALE_SKIP_GEOMETRY,1);
    tb_vk_scale_fallback("unsupported viewport or anisotropic depth slope");
    return 0;
}
