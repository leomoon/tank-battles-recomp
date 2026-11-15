"""Apply the bounded same-target draw-group correction to pinned SDK copies."""
from pathlib import Path


def instrument(text):
    def replace(before, after):
        nonlocal text
        if text.count(before) != 1:
            raise ValueError('Submission overlay anchor mismatch: '+before[:100])
        text = text.replace(before, after, 1)
    root = Path(__file__).resolve().parent.parent
    replace('static int vk_begin(void)', '#include "'+str(root/'sdk_overrides/vulkan_draw_group.inc')+'"\n\nstatic int vk_begin(void)')
    replace('    tb_gpu_end();', '    tb_group_end();\n    tb_gpu_end();')
    replace('    if (!e || *e != \'1\' || !s_g.on) return 0;', '    if ((!tb_group_on() && (!e || *e != \'1\')) || !s_g.on) return 0;')
    replace('    if (s_eb.max_draws > VK_EB_MAX_SETS) s_eb.max_draws = VK_EB_MAX_SETS;', '    if (s_eb.max_draws > VK_EB_MAX_SETS) s_eb.max_draws = VK_EB_MAX_SETS;\n    if (tb_group_on()) s_eb.max_draws=64; /* fixed bound independent of legacy batch knobs */')
    replace('static int vk_eb_active(void) { return s_eb.on == 1; }', 'static int vk_eb_active(void) { return s_eb.on == 1 && !tb_group_on(); }')
    # The serial upload/clear/snapshot branches call vk_begin, which flushes
    # pending draws before touching staging or recording outside the subpass.
    replace('    int batched = vk_eb_on() && vbytes <= VK_EB_VB_BYTES && ibytes <= VK_EB_IB_BYTES;', '''    int group_safe=1;
    if(tb_group_on()) {
        for(u32 unit=0;unit<RSX_MAX_TEXTURES;unit++) {
            if(!(s_e2.tex_mask & (1u<<unit)))continue;
            u32 handle=s_e2.tex[unit];
            if(handle>=VK_ENG_MAX_OBJ)continue;
            if(s_eobj[handle].kind==VK_ENG_VIEW)handle=s_eobj[handle].surface;
            if(handle>=VK_ENG_MAX_OBJ)continue;
            for(u32 target=0;target<n;target++)
                if(handle==s_eng_bound.rt[target])group_safe=0;
            if(zo && handle==s_eng_bound.depth)group_safe=0;
        }
        if(s_eb_pending && (!group_safe || tb_group_framebuffer!=fb || tb_group_renderpass!=rp))vk_eng_flush();
    }
    int batched = (!tb_group_on() || group_safe) && vk_eb_on() && vbytes <= VK_EB_VB_BYTES && ibytes <= VK_EB_IB_BYTES;''')
    replace('        vk_cmd_after_everything(s_vk.cmd);\n    } else {', '        if(!tb_group_on()) vk_cmd_after_everything(s_vk.cmd);\n    } else {')
    replace('    for (u32 i = 0; i < n; i++)\n        vk_barrier_image(s_eobj[s_eng_bound.rt[i]].im.img, VK_IMAGE_ASPECT_COLOR_BIT, VK_IMAGE_LAYOUT_GENERAL);', '''    const int continue_group=batched && tb_group_on() && tb_group_open;
    if(!continue_group) {
        for (u32 i = 0; i < n; i++)
            vk_barrier_image(s_eobj[s_eng_bound.rt[i]].im.img, VK_IMAGE_ASPECT_COLOR_BIT, VK_IMAGE_LAYOUT_GENERAL);
        if(batched && tb_group_on())vk_cmd_after_everything(s_vk.cmd);
    }''')
    prefix, text = text.split('static void vk_eng_draw(void* user, rsx_topology topology, const void* vertices, u32 vertex_count,', 1)
    replace('    pvkCmdBeginRenderPass(s_vk.cmd, &rbi, VK_SUBPASS_CONTENTS_INLINE);\n    pvkCmdBindPipeline(s_vk.cmd, VK_PIPELINE_BIND_POINT_GRAPHICS, pipe);', '''    if(!continue_group)pvkCmdBeginRenderPass(s_vk.cmd, &rbi, VK_SUBPASS_CONTENTS_INLINE);
    if(batched && tb_group_on()) {
        tb_group_open=1;tb_group_framebuffer=fb;tb_group_renderpass=rp;
    }
    pvkCmdBindPipeline(s_vk.cmd, VK_PIPELINE_BIND_POINT_GRAPHICS, pipe);''')
    replace('    pvkCmdEndRenderPass(s_vk.cmd);\n    tb_vulkan_event(2,vertex_count,index_count,topology);', '    if(!batched || !tb_group_on())pvkCmdEndRenderPass(s_vk.cmd);\n    tb_vulkan_event(2,vertex_count,index_count,topology);')
    return prefix+'static void vk_eng_draw(void* user, rsx_topology topology, const void* vertices, u32 vertex_count,'+text
