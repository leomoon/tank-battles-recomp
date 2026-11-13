"""Generate reviewable instrumented SDK source copies without editing the submodule."""
import argparse,hashlib,json,re
from pathlib import Path

EDITS={
 'libs/video/rsx_vulkan_backend.c':[
 ('int rsx_vulkan_backend_pump_messages(void)\n{', 'int rsx_vulkan_backend_pump_messages(void)\n{\n    tb_vulkan_event(7,0,0,0);'),
 ('    while (SDL_PollEvent(&e)) {', '    while (SDL_PollEvent(&e)) {\n        if (e.type == SDL_WINDOWEVENT) tb_vulkan_event(8,e.window.event,e.window.windowID,0);'),
 ('    return pvkCreateShaderModule(s_vk.device, &ci, NULL, out) == VK_SUCCESS ? 0 : -1;',
  '    int result = pvkCreateShaderModule(s_vk.device, &ci, NULL, out) == VK_SUCCESS ? 0 : -1;\n    if (!result) tb_vulkan_event(1,stage,words,0);\n    return result;'),
 ('    return P->variant[topo_idx];', '    if (P->variant[topo_idx]) tb_vulkan_event(4,topo_idx,P->stride,P->rt_count);\n    return P->variant[topo_idx];'),
 ('    if (!batched) { vk_submit_and_wait(); return; }', '    tb_vulkan_event(2,vertex_count,index_count,topology);\n    if (!batched) { vk_submit_and_wait(); return; }'),
 ('    r = vk_queue_present(&pi);', '    r = vk_queue_present(&pi);\n    if (r == VK_SUCCESS || r == VK_SUBOPTIMAL_KHR) tb_vulkan_event(3,s_vk.presented,s_vk.sc_extent.width,s_vk.sc_extent.height);'),
 ('    vk_eng_upload_rows(o->im.img, face, mip, w, h, tight, rows, src, row_bytes);', '    vk_eng_upload_rows(o->im.img, face, mip, w, h, tight, rows, src, row_bytes);\n    tb_vulkan_event(5,texture,w,h);'),
 ('        if (o) {\n            vk_barrier_image_on(cb, o->im.img,', '        if (o) {\n            tb_vulkan_event(6,surface,o->w,o->h);\n            vk_barrier_image_on(cb, o->im.img,'),
 ('    VK_LOG("offscreen target %ux%u ready, depth format %d (%s)\\n",',
  '    tb_vulkan_event(0,s_g.on,s_vk.eng_active,s_vk.windowed && s_vk.swapchain);\n    if (vk_env_on("PS3RECOMP_VK_WINDOW") && (!s_vk.window || !s_vk.swapchain)) return -1;\n    if (vk_env_on("PS3RECOMP_VK_GUEST_PROGRAMS") && (!s_g.on || !s_vk.eng_active)) return -1;\n    VK_LOG("offscreen target %ux%u ready, depth format %d (%s)\\n",')],
 'runtime/ppu/ppu_hle.cpp':[( '        ps3_msf("hle:unresolved:0x%08X", nid);','        tb_execution_event(2,nid,0,(uint32_t)ctx->lr);\n        ps3_msf("hle:unresolved:0x%08X", nid);')],
 'runtime/ppu/ppu_loader.cpp':[
 ('    g_active_ctx = ctx;\n    /* BCTRL_RING','    tb_execution_event(0,(uint32_t)ctx->ctr,0,(uint32_t)ctx->lr);\n    g_active_ctx = ctx;\n    /* BCTRL_RING'),
 ('    /* Garbage virtual-call guard:', '    tb_execution_event(1,(uint32_t)ctx->ctr,0,(uint32_t)ctx->lr);\n    /* Garbage virtual-call guard:'),
 ('    if (addr == 0) {\n        /* Say so.', '    if (addr == 0) {\n        tb_execution_event(1,0,0,(uint32_t)ctx->lr);\n        /* Say so.'),
 ('        if (s_depth > 4000) {','        if (s_depth > 4000) {\n            tb_execution_event(1,addr,0,(uint32_t)ctx->lr);'),
 ('        if (fn2) {','        if (fn2) {\n            tb_execution_event(7,addr,0,toc_reg);')],
 'runtime/spu/spu_channels.c':[
 ('    if (!fn) {\n        uint32_t p0 = ctx->pc & SPU_LS_MASK;', '    if (!fn) {\n        tb_execution_event(4,ctx->pc,ctx->image_id,ctx->resident_ovl);\n        uint32_t p0 = ctx->pc & SPU_LS_MASK;'),
 ('void spu_indirect_branch(spu_context* ctx)\n{','void spu_indirect_branch(spu_context* ctx)\n{\n    tb_execution_event(5,ctx->pc,ctx->image_id,ctx->resident_ovl);')],
 'runtime/spu/spu_interp.c':[( 'uint32_t spu_interp_run_until(spu_context* ctx, uint32_t start_lsa, uint32_t stop_lsa) {','uint32_t spu_interp_run_until(spu_context* ctx, uint32_t start_lsa, uint32_t stop_lsa) {\n    tb_execution_event(3,start_lsa,ctx->image_id,ctx->resident_ovl);')],
 'runtime/spu/spu_dispatch_mt.c':[( 'void spu_indirect_branch_mt(spu_context* ctx)\n{','void spu_indirect_branch_mt(spu_context* ctx)\n{\n    tb_execution_event(5,ctx->pc,ctx->image_id,ctx->resident_ovl);')],
 'runtime/spu/spu_raw.c':[( '    int halted = spu_run_with_halt(s->entry, c);','    tb_execution_event(6,c->pc,c->image_id,c->resident_ovl);\n    int halted = spu_run_with_halt(s->entry, c);')],
}
def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--sdk',required=True);parser.add_argument('--output',required=True);args=parser.parse_args()
 root=Path(args.sdk).resolve();out=Path(args.output);out.mkdir(parents=True,exist_ok=True);records=[]
 for relative,edits in EDITS.items():
  original=root/relative;text=original.read_text();digest=hashlib.sha256(text.encode()).hexdigest()
  for before,after in edits:
   if text.count(before)!=1:raise SystemExit('SDK anchor mismatch: '+relative)
   text=text.replace(before,after,1)
  def include(match):
   path=original.parent/match[1]
   return '#include "'+str(path.resolve())+'"' if path.is_file() else match[0]
  text=re.sub(r'#include "([^"\n]+)"',include,text)
  text='#include "'+str(Path(__file__).resolve().parent.parent/'execution_policy_api.h')+'"\n'+text
  target=out/relative;target.parent.mkdir(parents=True,exist_ok=True)
  if not target.exists() or target.read_text()!=text:target.write_text(text)
  records.append(dict(source=relative,sha256=digest,output_sha256=hashlib.sha256(text.encode()).hexdigest()))
 (out/'manifest.json').write_text(json.dumps(records,indent=2)+'\n')
if __name__=='__main__':main()
