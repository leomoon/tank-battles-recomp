"""Generate reviewable instrumented SDK source copies without editing the submodule."""
import argparse,hashlib,json,re
from pathlib import Path

EDITS={
 'runtime/syscalls/sys_fs.c':[(
  '    FILE* fp = fopen(host_path, mode);\n    const int open_errno = errno;',
  '    struct stat tb_directory;\n    if (stat(host_path, &tb_directory) == 0 && (tb_directory.st_mode & S_IFMT) == S_IFDIR)\n        return (int64_t)(int32_t)CELL_EISDIR;\n    FILE* fp = fopen(host_path, mode);\n    const int open_errno = errno;')],
 'libs/filesystem/cellFs.c':[(
  '    const char* mode = flags_to_mode(flags);\n    FILE* fp = fopen(host_path, mode);',
  '    HOST_STAT_T tb_directory;\n    if (HOST_STAT(host_path, &tb_directory) == 0 && (tb_directory.st_mode & S_IFMT) == S_IFDIR)\n        return (s32)CELL_EISDIR;\n    const char* mode = flags_to_mode(flags);\n    FILE* fp = fopen(host_path, mode);')],
 'libs/input/cellPad.c':[
 ('        s_host_state[i].buttons = btns;\n\n        /* Analog sticks */',
  '        s_host_state[i].buttons = btns;\n        tb_input_sample(0,(uint32_t)i,btns);\n\n        /* Analog sticks */'),
 ('emit:\n    /* A non-zero len',
  'emit:\n    if (data->len > 0) tb_input_sample(1,port_no,(uint32_t)data->button[CELL_PAD_BTN_OFFSET_DIGITAL1] | ((uint32_t)data->button[CELL_PAD_BTN_OFFSET_DIGITAL2] << 8));\n    /* A non-zero len')],
 'runtime/ppu/ppu_fs.cpp':[(
  '    size_t n = fread(vm_base + buf, 1, (size_t)nbytes, f);',
  '    size_t n = fread(vm_base + buf, 1, (size_t)nbytes, f);\n    tb_resource_read(fileno(f),(uint64_t)fpos_before,vm_base+buf,n,buf);')],
 'libs/filesystem/edat.c':[
 ('    fclose(in);\n    fclose(out);\n\n    if (rc == 0)',
  '    fclose(in);\n    if (fclose(out) != 0 && rc == 0) rc = -12;\n\n    if (rc == 0)'),
 ('const char* edat_resolve(const char* host_path, char* buf, size_t cap)',
  '#if defined(__linux__)\n#include "'+str(Path(__file__).resolve().parent.parent/'sdk_overrides/edat_cache.inc')+'"\n#else\nconst char* edat_resolve(const char* host_path, char* buf, size_t cap)')],
 'libs/video/rsx_draw_engine.c':[
 ('            g.be->texture_upload(g.be->user, handle, f, m, lv[m].w, lv[m].h,',
  '            if (!f && !m) tb_rsx_texture_capture(location,offset,fmt,w,h,pitch,face,tl->face_bytes,g.tex_staging,tl->dst_row_bytes,tl->rows);\n            g.be->texture_upload(g.be->user, handle, f, m, lv[m].w, lv[m].h,')],
 'libs/video/cellGcmSys.c':[
 ('static void gcm_2d_method(u32 subch, u32 method, u32 data)\n{',
  '#include "'+str(Path(__file__).resolve().parent.parent/'sdk_overrides/rsx_memory_copy.inc')+'"\n\nstatic void gcm_2d_method(u32 subch, u32 method, u32 data)\n{\n    if (subch == 1) { tb_m2mf_method(method, data); return; }'),
 ('s1_2d = e ? atoi(e) : 0;', 's1_2d = e ? atoi(e) : 1; /* Tank Battles: default NV0039 binding. */'),
 ('                u32 m = (type == 0) ? method + i * 4 : method;\n                /* GCM_SET_USER_COMMAND',
  '                u32 m = (type == 0) ? method + i * 4 : method;\n                if (subch == 1 && s1_2d) { gcm_2d_method(subch, m, vm_read32(dea)); continue; }\n                /* GCM_SET_USER_COMMAND')],
 'libs/video/rsx_vulkan_backend.c':[
 ('    vk_eng_pipe* P = &s_epipe[s_e2.pipeline - 1];\n    if (!P->used || stride != P->stride) return;',
  '    vk_eng_pipe* P = &s_epipe[s_e2.pipeline - 1];\n    if (!P->used || stride != P->stride) return;\n    { static unsigned n; static const char* dir=(const char*)-1; if (dir == (const char*)-1) dir=getenv("TB_RSX_TEXTURE_CAPTURE"); static int ready; if (dir && s_e2.tex[0] && s_eobj[s_e2.tex[0]].w == 1920 && s_eobj[s_e2.tex[0]].h == 1080) ready=1; if (dir && ready && n < 96) { char path[4096]; snprintf(path,sizeof(path),"%s/draw-%03u.bin",dir,n); FILE* f=fopen(path,"wb"); if(f){fwrite(vertices,stride,vertex_count,f);fclose(f);} snprintf(path,sizeof(path),"%s/draws.txt",dir); f=fopen(path,"a"); if(f){ fprintf(f,"%u pipe=%u vertices=%u stride=%u index=%u texture=%u mask=%x viewport=%f,%f,%f,%f scissor=%u,%u,%u,%u\\n",n,s_e2.pipeline,vertex_count,stride,index_count,s_e2.tex[0],s_e2.tex_mask,s_e2.vp[0],s_e2.vp[1],s_e2.vp[2],s_e2.vp[3],s_e2.sc[0],s_e2.sc[1],s_e2.sc[2],s_e2.sc[3]);fclose(f);} ++n; } }'),
 ('int rsx_vulkan_backend_pump_messages(void)\n{', 'int rsx_vulkan_backend_pump_messages(void)\n{\n    tb_vulkan_event(7,0,0,0);'),
 ('    while (SDL_PollEvent(&e)) {', '    while (SDL_PollEvent(&e)) {\n        if (e.type == SDL_WINDOWEVENT) tb_vulkan_event(8,e.window.event,e.window.windowID,0);'),
 ('    return pvkCreateShaderModule(s_vk.device, &ci, NULL, out) == VK_SUCCESS ? 0 : -1;',
  '    int result = pvkCreateShaderModule(s_vk.device, &ci, NULL, out) == VK_SUCCESS ? 0 : -1;\n    if (!result) tb_vulkan_event(1,stage,words,0);\n    return result;'),
 ('    return P->variant[topo_idx];', '    if (P->variant[topo_idx]) tb_vulkan_event(4,topo_idx,P->stride,P->rt_count);\n    return P->variant[topo_idx];'),
 ('    if (!batched) { vk_submit_and_wait(); return; }', '    tb_vulkan_event(2,vertex_count,index_count,topology);\n    if (!batched) { vk_submit_and_wait(); return; }'),
 ('    r = vk_queue_present(&pi);', '    r = vk_queue_present(&pi);\n    if (r == VK_SUCCESS || r == VK_SUBOPTIMAL_KHR) tb_vulkan_event(3,s_vk.presented,s_vk.sc_extent.width,s_vk.sc_extent.height);\n    if (r == VK_SUCCESS || r == VK_SUBOPTIMAL_KHR) tb_render_frame();'),
 ('    vk_eng_upload_rows(o->im.img, face, mip, w, h, tight, rows, src, row_bytes);', '    vk_eng_upload_rows(o->im.img, face, mip, w, h, tight, rows, src, row_bytes);\n    tb_vulkan_event(5,texture,w,h);'),
 ('        if (o) {\n            vk_barrier_image_on(cb, o->im.img,', '        if (o) {\n            tb_vulkan_event(6,surface,o->w,o->h);\n            vk_barrier_image_on(cb, o->im.img,'),
 ('    VK_LOG("offscreen target %ux%u ready, depth format %d (%s)\\n",',
  '    tb_vulkan_event(0,s_g.on,s_vk.eng_active,s_vk.windowed && s_vk.swapchain);\n    if (vk_env_on("PS3RECOMP_VK_WINDOW") && (!s_vk.window || !s_vk.swapchain)) return -1;\n    if (vk_env_on("PS3RECOMP_VK_GUEST_PROGRAMS") && (!s_g.on || !s_vk.eng_active)) return -1;\n    VK_LOG("offscreen target %ux%u ready, depth format %d (%s)\\n",')],
 'runtime/ppu/ppu_hle.cpp':[( '    g_last_hle_nid = nid;', '    tb_resource_hle(nid);\n    g_last_hle_nid = nid;'),( '        ps3_msf("hle:unresolved:0x%08X", nid);','        tb_execution_event(2,nid,0,(uint32_t)ctx->lr);\n        ps3_msf("hle:unresolved:0x%08X", nid);')],
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
# Profile scopes are opt-in, inclusive CPU wall time, and preserve every early return.
# Function signatures are matched in the original pinned source, not generated code.
PROFILE_FUNCTIONS = {
 'libs/video/rsx_vulkan_backend.c': {
  'vk_eng_draw': 0, 'vk_submit_and_wait': 1, 'vk_eng_translate': 2,
  'vk_eng_pipeline_create': 3, 'vk_eng_variant': 4, 'vk_eng_texture_upload': 5,
  'vk_eng_present': 6, 'vk_present_window': 7, 'vk_present_complete_locked': 8,
  'vk_dump_ppm': 9,
 },
}
def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--sdk',required=True);parser.add_argument('--output',required=True);args=parser.parse_args()
 root=Path(args.sdk).resolve();out=Path(args.output);out.mkdir(parents=True,exist_ok=True);records=[]
 for relative,edits in EDITS.items():
  original=root/relative;text=original.read_text();digest=hashlib.sha256(text.encode()).hexdigest()
  for function,kind in PROFILE_FUNCTIONS.get(relative,{}).items():
   pattern = r'(^static [^;{]*?\b'+function+r'\([^;{]*?\)\n\{)'
   text,n = re.subn(pattern, lambda m: m[0]+'\n    TB_RENDER_SCOPE('+str(kind)+');', text, flags=re.M)
   if n != 1: raise SystemExit('SDK profile anchor mismatch: '+function)
  for before,after in edits:
   if text.count(before)!=1:raise SystemExit('SDK anchor mismatch: '+relative)
   text=text.replace(before,after,1)
  def include(match):
   path=original.parent/match[1]
   return '#include "'+str(path.resolve())+'"' if path.is_file() else match[0]
  if relative == 'libs/filesystem/edat.c': text += '\n#endif /* Linux cache override */\n'
  text=re.sub(r'#include "([^"\n]+)"',include,text)
  text='#include "'+str(Path(__file__).resolve().parent.parent/'execution_policy_api.h')+'"\n'+text
  target=out/relative;target.parent.mkdir(parents=True,exist_ok=True)
  if not target.exists() or target.read_text()!=text:target.write_text(text)
  record=dict(source=relative,sha256=digest,output_sha256=hashlib.sha256(text.encode()).hexdigest())
  overrides={'libs/video/cellGcmSys.c':'rsx_memory_copy.inc','libs/filesystem/edat.c':'edat_cache.inc'}
  if relative in overrides:
   override=Path(__file__).resolve().parent.parent/'sdk_overrides'/overrides[relative]
   record['port_inputs']={override.name:hashlib.sha256(override.read_bytes()).hexdigest()}
  records.append(record)
 (out/'manifest.json').write_text(json.dumps(records,indent=2)+'\n')
if __name__=='__main__':main()
