"""Generate reviewable instrumented SDK source copies without editing vendored sources."""
import argparse,hashlib,json,re
from pathlib import Path

EDITS={
 'runtime/syscalls/sys_ppu_thread.c':[(
  '    if (priority < 256)\n        SetThreadPriority(t->host_thread, THREAD_PRIORITY_ABOVE_NORMAL);',
  '    /* Tank Battles SurMixer producers use guest priorities 400..404.\n     * Preserve their precedence over the main thread (1001) on Windows. */\n    if (priority < 512) {\n        SetThreadPriority(t->host_thread, THREAD_PRIORITY_ABOVE_NORMAL);\n        if (getenv("AUDIO_RATE")) fprintf(stderr,"[audio-priority] guest=%d host=%d name=%s\\n",\n            priority,GetThreadPriority(t->host_thread),t->name);\n    }')],
 'libs/video/rsx_texture_layout.c': [('void rsx_texture_decode(void* dst, u32 dst_pitch,', 'void tb_texture_decode_reference(void* dst, u32 dst_pitch,')],
 'runtime/syscalls/sys_fs.c':[(
  '    FILE* fp = fopen(host_path, mode);\n    const int open_errno = errno;',
  '    struct stat tb_directory;\n    if (stat(host_path, &tb_directory) == 0 && (tb_directory.st_mode & S_IFMT) == S_IFDIR)\n        return (int64_t)(int32_t)CELL_EISDIR;\n    FILE* fp = fopen(host_path, mode);\n    const int open_errno = errno;')],
 'libs/filesystem/cellFs.c':[(
  '    const char* mode = flags_to_mode(flags);\n    FILE* fp = fopen(host_path, mode);',
  '    HOST_STAT_T tb_directory;\n    if (HOST_STAT(host_path, &tb_directory) == 0 && (tb_directory.st_mode & S_IFMT) == S_IFDIR)\n        return (s32)CELL_EISDIR;\n    const char* mode = flags_to_mode(flags);\n    FILE* fp = fopen(host_path, mode);')],
 'libs/system/cellGame.c':[('static char s_content_path[CELL_GAME_PATH_MAX] = "./gamedata/dev_hdd0/game";','/* Host paths (the repository can live anywhere) need more than the 128-byte\n * guest limit CELL_GAME_PATH_MAX: a truncated host USRDIR path created stray\n * prefix directories such as USR or USRD. Guest-visible paths keep the limit. */\n#define TB_HOST_PATH_MAX 4096\nstatic char s_content_path[TB_HOST_PATH_MAX] = "./gamedata/dev_hdd0/game";'),
 ('static char s_content_info_path[CELL_GAME_PATH_MAX] = "";\nstatic char s_usrdir_path[CELL_GAME_PATH_MAX] = "";\nstatic char s_tmp_path[CELL_GAME_PATH_MAX] = "";','static char s_content_info_path[TB_HOST_PATH_MAX] = "";\nstatic char s_usrdir_path[TB_HOST_PATH_MAX] = "";\nstatic char s_tmp_path[TB_HOST_PATH_MAX] = "";'),
 ('static void ensure_dirs(const char* path)\n{\n    char tmp[CELL_GAME_PATH_MAX];','static void ensure_dirs(const char* path)\n{\n    char tmp[TB_HOST_PATH_MAX];'),
 ('static s32 content_free_kb(void)\n{\n    char path[CELL_GAME_PATH_MAX];','static s32 content_free_kb(void)\n{\n    char path[TB_HOST_PATH_MAX];'),
 ('    char path[CELL_GAME_PATH_MAX];\n    snprintf(path, sizeof(path), "%s/%s", content_root(), check_dir);','    char path[TB_HOST_PATH_MAX];\n    snprintf(path, sizeof(path), "%s/%s", content_root(), check_dir);'),
 ('    char path[CELL_GAME_PATH_MAX];\n    snprintf(path, sizeof(path), "%s/%s", content_root(), create_dir);','    char path[TB_HOST_PATH_MAX];\n    snprintf(path, sizeof(path), "%s/%s", content_root(), create_dir);'),
 ('    char usrdir[CELL_GAME_PATH_MAX];\n    snprintf(usrdir, sizeof(usrdir), "%s/USRDIR", path);','    char usrdir[TB_HOST_PATH_MAX];\n    snprintf(usrdir, sizeof(usrdir), "%s/USRDIR", path);'),
 (
  '            extern const char* ppu_vfs_root;\n            char vpath[CELL_GAME_PATH_MAX];\n            snprintf(vpath, sizeof vpath, "%s/game/%s/USRDIR", ppu_vfs_root, s_check_dir);\n            ensure_dirs(vpath);',
  '            /* Only for a disc-style VFS root with no separate HDD0 mount. Here the\n             * VFS root is the installed, read-only title folder and /dev_hdd0 is\n             * PS3_HDD0_ROOT (already created above), so this made\n             * NPUB30154/game/NPUB30154/USRDIR inside the game. */\n            extern const char* ppu_vfs_root;\n            const char* tb_hdd0 = getenv("PS3_HDD0_ROOT");\n            if (!tb_hdd0 || !*tb_hdd0) {\n            char vpath[TB_HOST_PATH_MAX];\n            snprintf(vpath, sizeof vpath, "%s/game/%s/USRDIR", ppu_vfs_root, s_check_dir);\n            ensure_dirs(vpath);\n            }')],
 'libs/input/cellPad.c':[
 ('    if (!pad_host_window_focused()) {',
  '    if (!pad_host_window_focused()) {\n        (void)tb_keyboard_take();'),
 ('    u16 btns = 0;\n    for (unsigned i = 0; i < sizeof map / sizeof map[0]; i++)',
  '    u16 btns = (u16)tb_keyboard_peek();\n    for (unsigned i = 0; i < sizeof map / sizeof map[0]; i++)'),
 ('    if (!s_host_state[0].connected) pad_poll_keyboard();',
  '    if (!s_host_state[0].connected) pad_poll_keyboard();\n    else (void)tb_keyboard_take(); /* Never replay keys typed while a pad was connected. */'),
 ('    if (off) return;\n\n    PadHostState* hs = &s_host_state[0];',
  '    { static int last=-1; if(getenv("TB_INPUT_TRACE") && last!=off) { fprintf(stderr,"[cellPad] keyboard disabled=%d\\n",off); last=off; } }\n    if (off) { (void)tb_keyboard_take(); return; }\n    if (getenv("TB_INPUT_TRACE")) { static int last=-1; static unsigned n; int state=pad_host_window_focused() | (pad_key_down(PADKEY_Z)<<1) | (pad_key_down(PADKEY_X)<<2) | (pad_key_down(PADKEY_DOWN)<<3); if(state!=last && n++<64) { fprintf(stderr,"[cellPad] keyboard focus/Z/X/Down bits=0x%x\\n",state); last=state; } }\n\n    PadHostState* hs = &s_host_state[0];'),
 ('    if (!s_sdl_inited) {',
  '    /* PS button prompts refer to physical positions, including Nintendo-mode pads. */\n    SDL_SetHint("SDL_GAMECONTROLLER_USE_BUTTON_LABELS", "0");\n    if (!s_sdl_inited) {'),
 ('        const char* name = SDL_GameControllerName(gc);',
  '        const char* name = SDL_GameControllerName(gc);\n        char* tb_mapping = SDL_GameControllerMapping(gc);\n        fprintf(stderr, "[cellPad] SDL mapping: %s; use_button_labels=%s\\n", tb_mapping ? tb_mapping : "?", SDL_GetHint("SDL_GAMECONTROLLER_USE_BUTTON_LABELS"));\n        SDL_free(tb_mapping);'),
 ('        s_host_state[i].buttons = btns;\n\n        /* Analog sticks */',
  '        s_host_state[i].buttons = btns;\n        tb_input_sample(0,(uint32_t)i,btns);\n\n        /* Analog sticks */'),
 ('emit:\n    /* A non-zero len',
  'emit:\n    if (data->len > 0 && port_no == 0) tb_keyboard_ack((uint32_t)data->button[CELL_PAD_BTN_OFFSET_DIGITAL1] | ((uint32_t)data->button[CELL_PAD_BTN_OFFSET_DIGITAL2] << 8));\n    if (data->len > 0) tb_input_sample(1,port_no,(uint32_t)data->button[CELL_PAD_BTN_OFFSET_DIGITAL1] | ((uint32_t)data->button[CELL_PAD_BTN_OFFSET_DIGITAL2] << 8));\n    /* A non-zero len')],
 'libs/audio/cellAudio.c':[(
 ('    SDL_PauseAudioDevice(s_sdl_audio_dev, 0); /* Start playback */'),
 ('    SDL_PauseAudioDevice(s_sdl_audio_dev, 0); /* Start playback */\n    if(getenv("TB_GAMEPLAY_TIMING")) fprintf(stderr,"[gameplay-audio] driver=%s rate=%d channels=%u samples=%u format=0x%X\\n",SDL_GetCurrentAudioDriver(),have.freq,have.channels,have.samples,have.format);')
 )],
 'runtime/ppu/ppu_fs.cpp':[(
  '    size_t n = fread(vm_base + buf, 1, (size_t)nbytes, f);',
  '    size_t n = fread(vm_base + buf, 1, (size_t)nbytes, f);\n    tb_resource_read(fileno(f),(uint64_t)fpos_before,vm_base+buf,n,buf);')],
 'libs/filesystem/edat.c':[
 ('    fclose(in);\n    fclose(out);\n\n    if (rc == 0)',
  '    fclose(in);\n    if (fclose(out) != 0 && rc == 0) rc = -12;\n\n    if (rc == 0)'),
 ('const char* edat_resolve(const char* host_path, char* buf, size_t cap)',
  '#include "'+str(Path(__file__).resolve().parent.parent/'sdk_overrides/edat_cache.inc')+'"\nstatic const char* tb_original_edat_resolve(const char* host_path, char* buf, size_t cap)')],
 'libs/video/rsx_draw_engine.c':[
 ('static void dc_fetch(const rsx_vertex_layout_plan* layout, int allow_remap)',
  '#include "'+str(Path(__file__).resolve().parent.parent/'sdk_overrides/index_remap_cache.inc')+'"\nstatic void dc_fetch(const rsx_vertex_layout_plan* layout, int allow_remap)'),
 ('rsx_vertex_remap_build(&dc.ref_remap, dc.refs, dc.n_refs, &unique)', 'tb_remap_build(&dc.ref_remap, dc.refs, dc.n_refs, &unique)'),
 ('        u8* src = rows + (size_t)r * n * bpp;\n        if (bpp == 8u) {',
  '        u8* src = tb_cached ? (u8*)tb_cached+(size_t)r*s->w*bpp : rows+(size_t)r*n*bpp;\n        if (!tb_cached && bpp == 8u) {'),
 ('        } else if (!rgba) {\n            eng_row_convert(src, src, n, 0, 1);',
  '        } else if (!tb_cached && !rgba) {\n            eng_row_convert(src, src, n, 0, 1);'),

 ('static void eng_row_convert(u8* dst, const u8* src, u32 w, int rgba, int to_guest)',
  '#include "'+str(Path(__file__).resolve().parent.parent/'sdk_overrides/color_readback_cache.inc')+'"\nstatic void eng_row_convert(u8* dst, const u8* src, u32 w, int rgba, int to_guest)'),
 ('    g.be->submit_and_wait(g.be->user, RSX_BE_FLUSH_GUEST_REFERENCE);\n    g.be->readback(g.be->user, s->handle, x, y, n, h, rows, n * bpp);',
  '    const u8* tb_cached=tb_color_readback_rows(s,x,y,n,h,bpp);\n    if(!tb_cached) {\n    g.be->submit_and_wait(g.be->user, RSX_BE_FLUSH_GUEST_REFERENCE);\n    g.be->readback(g.be->user, s->handle, x, y, n, h, rows, n * bpp);\n    }'),
 ('void rsx_draw_engine_shutdown(void)\n{', 'void rsx_draw_engine_shutdown(void)\n{\n    tb_remap_reset();\n    tb_color_readback_reset();'),
 ('static int eng_zdepth_to_guest(u32 slot)\n{', 'static int eng_zdepth_to_guest(u32 slot)\n{\n    TB_DEPTH_COST("depth-to-guest",(uint64_t)g.zdepths[slot].used_w*g.zdepths[slot].used_h*(g.zdepths[slot].zfmt==1?2:4),g.frames);'),
 ('void rsx_draw_engine_guest_read(u32 location, u32 offset, u32 pitch, u32 w, u32 h)\n{', '#include "'+str(Path(__file__).resolve().parent.parent/'sdk_overrides/depth_guest_read.inc')+'"\nvoid rsx_draw_engine_guest_read(u32 location, u32 offset, u32 pitch, u32 w, u32 h)\n{\n    if(tb_depth_guest_read(location,offset,pitch,w,h))return;'),
 ('static u32 eng_zdepth_as_color(u32 slot, const rsx_dsp_texture* t)\n{',
  '#include "'+str(Path(__file__).resolve().parent.parent/'sdk_overrides/depth_path_trace.inc')+'"\nstatic u32 eng_zdepth_as_color(u32 slot, const rsx_dsp_texture* t)\n{\n    TB_DEPTH_COST("depth-as-color",(uint64_t)t->width*t->height*(g.zdepths[slot].zfmt==1?2:4),g.frames);\n    tb_depth_trace(slot,t);'),
 ('    s->w = want_w; s->h = want_h; s->fmt = want_fmt; s->aa_grid = aa_grid;',
  '    s->w = want_w; s->h = want_h; s->fmt = want_fmt; s->aa_grid = aa_grid;\n    if(getenv("TB_GAMEPLAY_TIMING")) fprintf(stderr,"[gameplay-surface] %ux%u fmt=%u aa_grid=0x%X\\n",want_w,want_h,(unsigned)want_fmt,aa_grid);'),
 ('static void eng_textures_touch(u32 location, u32 offset, u32 bytes)\n{\n    for (u32 i = 0; i < g.n_textures; i++) {\n        eng_texture* e = &g.textures[i];\n        if (e->location == location && e->offset >= offset && e->offset - offset < bytes) {\n            e->last_hash_frame = g.frames - 1u;\n            e->last_full_frame = g.frames - ENG_TEXHASH_FULL_EVERY;   /* hash it all */\n            e->rewritten = 1;\n        }\n    }\n}',
  '#include "'+str(Path(__file__).resolve().parent.parent/'sdk_overrides/rsx_texture_coherence.inc')+'"'),
 ('    if (!g.ready || !g.be->color_target_write || !pitch || !w || !h) return;',
  '    if (!g.ready || !pitch || !w || !h) return;\n    const u64 tb_bytes=(u64)(h-1)*pitch+(u64)w*4;\n    if (tb_bytes<=0xFFFFFFFFu) tb_rsx_memory_written(location,offset,(u32)tb_bytes);\n    if (!g.be->color_target_write) return;'),
 ('    g.be->color_target_write(g.be->user, s->handle, x, y, w, h, rows, w * 4u);',
  '    g.be->color_target_write(g.be->user, s->handle, x, y, w, h, rows, w * 4u);\n    g.surfaces[slot].color_serial=++g.write_serial;\n    g.surfaces[slot].guest_dirty=0;'),
 ('static u32 eng_present_surface(u32 buffer_id)',
  '#include "'+str(Path(__file__).resolve().parent.parent/'sdk_overrides/rsx_scanout.inc')+'"\n#include "'+str(Path(__file__).resolve().parent.parent/'sdk_overrides/rsx_trace.inc')+'"\n\nstatic u32 eng_present_surface(u32 buffer_id)'),
 ('                return i;\n    }\n    return eng_current_surface();',
  '                return i;\n        const char* enabled=getenv("GCM_SCANOUT_SURFACES");\n        if (!enabled || atoi(enabled)) return tb_scanout_surface(d);\n    }\n    return eng_current_surface();'),
 ('    pd_present(target);', '    tb_scanout_sample(buffer_id,target,g.surfaces[target].offset);\n    tb_frame_order(buffer_id,g.surfaces[target].offset,g.surfaces[target].color_serial,g.frames);\n    pd_present(target);'),
 ('void rsx_draw_engine_note_flip(u32 buffer_id)\n{\n    g.last_flip_buffer = buffer_id & 7u;\n}',
  '#include "'+str(Path(__file__).resolve().parent.parent/'sdk_overrides/page_census_arm.inc')+'"\nvoid rsx_draw_engine_note_flip(u32 buffer_id)\n{\n    g.last_flip_buffer = buffer_id & 7u;\n    tb_fps_guest_flip();\n    tb_trace_boundary();\n    tb_page_census_arm();\n}'),
 ('    rsx_dispatch_method(&g.rsx, m, arg);', '    tb_trace_method(0,m,arg);\n    rsx_dispatch_method(&g.rsx, m, arg);'),
 ('            g.be->texture_upload(g.be->user, handle, f, m, lv[m].w, lv[m].h,',
  '            if (!f && !m) tb_rsx_texture_capture(location,offset,fmt,w,h,pitch,face,tl->face_bytes,g.tex_staging,tl->dst_row_bytes,tl->rows);\n            g.be->texture_upload(g.be->user, handle, f, m, lv[m].w, lv[m].h,')],
 'libs/video/cellGcmSys.c':[
 ('    extern u32 g_rsx_last_reference;\n    /* #466:',
  '    extern u32 g_rsx_last_reference;\n    if ((m==0x0050u || m==0x0104u || m==0x1D70u || m==0x1D74u || m==0x1800u) && rsx_draw_engine_enabled()) rsx_draw_engine_flush();\n    /* #466:'),
 ('                        atomic_thread_fence(memory_order_release);\n                        vm_write32(la, v);',
  '                        tb_publication_flush();\n                        atomic_thread_fence(memory_order_release);\n                        vm_write32(la, v);'),
 ('    if (!gcm_ctx_room(ctx, 8)) return 0;',
  '    if (s_seg_size && ctx==s_gcm_context_ea) {\n        if (!gcm_ctx_room(ctx,8)) return 0;\n    } else if ((u64)vm_read32(ctx+8)+8u>vm_read32(ctx+4)) return 0;\n    /* A flip needs eight actual bytes, not the recycle heuristic\'s 4 KiB reserve. */'),
 ('static void gcm_set_current_display_buffer(u32 bufferId)\n{',
  'static void gcm_set_current_display_buffer(u32 bufferId)\n{\n    tb_frame_event(1,bufferId,0,0,0);'),
 ('static s32 gcm_flip_request(u32 bufferId, int in_fifo)\n{',
  'static s32 gcm_flip_request(u32 bufferId, int in_fifo)\n{\n    tb_frame_event(0,bufferId,s_fifo_getoff,vm_read32(GCM_CONTROL_GUEST_ADDR),(u32)in_fifo);'),
 ('    rsx_draw_engine_guest_wrote(loc, s_gcm2d.dst_offset + y0 * dst_pitch + x0 * 4u, dst_pitch,',
  '    const u32 tb_offset=s_gcm2d.dst_offset+y0*dst_pitch+x0*4u;\n    for(u32 tb_i=0;tb_i<CELL_GCM_MAX_DISPLAY_BUFFER_NUM;++tb_i) {\n        const CellGcmDisplayInfo* tb_d=&s_display_buffers[tb_i];\n        if(s_display_buffer_set[tb_i] && loc==RSX_LOCATION_LOCAL && tb_offset>=tb_d->offset &&\n           (u64)tb_offset<(u64)tb_d->offset+(u64)tb_d->pitch*tb_d->height)\n            tb_frame_event(2,tb_i,tb_offset,x1-x0,y1-y0);\n    }\n    rsx_draw_engine_guest_wrote(loc, s_gcm2d.dst_offset + y0 * dst_pitch + x0 * 4u, dst_pitch,'),
 ('    const int wdiag = gcm_word_diag();',
  '    const int wdiag = gcm_word_diag();\n    int tb_packet_wait=0;'),
 ('            u32 subch  = (w >> 13) & 7;',
  '            u32 subch  = (w >> 13) & 7;\n            /* PUT can publish a method in pieces. Never consume its unpublished tail. */\n            if (s_fifo_getoff<put && (u64)s_fifo_getoff+4u+(u64)count*4u>put) {\n                static unsigned tb_partial_logs;\n                if (PS3_LOG_FIRST(tb_partial_logs,4)) fprintf(stderr,"[cellGcmSys] waiting for method tail: GET=%08X PUT=%08X words=%u\\n",s_fifo_getoff,put,count);\n                tb_packet_wait=1;\n                break;\n            }'),
 ('      if (s_sema_blocked_pass || s_park_pass || s_report_hold_pass) {',
  '      if (s_sema_blocked_pass || s_park_pass || s_report_hold_pass || tb_packet_wait) {'),
 ('        if (!strcmp(why, "flip") && s_fifo_getoff != put) head_consumed = 0;',
  '        if ((!strcmp(why, "flip") && s_fifo_getoff != put) || tb_packet_wait) head_consumed = 0;'),
 ('                  if (eng) rsx_draw_engine_method((subch << 13) | m, vm_read32(dea)); }',
  '                  const u32 tb_full=(subch<<13)|m;\n                  if (eng && (subch==0 || (subch==1 && !s1_2d) ||\n                      tb_full==0xE920u || tb_full==0xE924u || tb_full==0xE944u ||\n                      tb_full==0xEB00u || tb_full==0xEB04u))\n                      rsx_draw_engine_method(tb_full,vm_read32(dea)); } /* Keep 2D state out of NV4097. */'),
 ('          } }\n        { static int _n = 0; static int cap = -1;',
  '          } }\n        tb_rsx_memory_written(RSX_LOCATION_LOCAL,s_nv309e.offset,out_w*out_h*4u);\n        { static int _n = 0; static int cap = -1;'),
 ('    if (v < 0) v = getenv("GCM_FLIPCB_ONTICK") ? 1 : 0;',
  '    if (v < 0) { const char* e=getenv("GCM_FLIPCB_ONTICK"); v=e ? atoi(e)!=0 : 1; } /* One callback at completion. */'),
 ('    if (v < 0) { const char* e = getenv("GCM_HANDLER_THREAD"); v = (e && *e && *e != \'0\') ? 1 : 0; }',
  '    if (v < 0) { const char* e = getenv("GCM_HANDLER_THREAD"); v = e ? (*e && *e != \'0\') : 1; } /* Deliver while the PPU waits. */'),
 ('static void gcm_2d_method(u32 subch, u32 method, u32 data)\n{',
  '#include "'+str(Path(__file__).resolve().parent.parent/'sdk_overrides/rsx_memory_copy.inc')+'"\n\nstatic void gcm_2d_method(u32 subch, u32 method, u32 data)\n{\n    tb_rsx_transfer_trace(subch,method,data);\n    if (subch == 1) { tb_m2mf_method(method, data); return; }'),
 ('s1_2d = e ? atoi(e) : 0;', 's1_2d = e ? atoi(e) : 1; /* Tank Battles: default NV0039 binding. */'),
 ('                u32 m = (type == 0) ? method + i * 4 : method;\n                /* GCM_SET_USER_COMMAND',
  '                u32 m = (type == 0) ? method + i * 4 : method;\n                if (subch == 1 && s1_2d) { gcm_2d_method(subch, m, vm_read32(dea)); continue; }\n                /* GCM_SET_USER_COMMAND')],
 'libs/video/rsx_vulkan_backend.c':[
 ('X(vkCreateGraphicsPipelines) X(vkDestroyPipeline)', 'X(vkCreateGraphicsPipelines) X(vkCreateComputePipelines) X(vkCmdDispatch) X(vkDestroyPipeline)'),
 ('static void vk_eng_shutdown(void* user)\n{', 'static void tb_depth16_shutdown(void);\nstatic void vk_eng_shutdown(void* user)\n{\n    tb_depth16_shutdown();'),
 ('/* ---- E2: pipelines and draws', '#include "'+str(Path(__file__).resolve().parent.parent/'sdk_overrides/vulkan_depth16.inc')+'"\n/* ---- E2: pipelines and draws'),
 ('    .depth_readback       = vk_eng_depth_readback,', '    .depth_readback       = vk_eng_depth_readback,\n    .depth_as_color       = tb_vk_depth16,'),
 ('    X(vkDestroyDevice) X(vkGetDeviceQueue) X(vkDeviceWaitIdle)',
  '    X(vkCreateQueryPool) X(vkDestroyQueryPool) X(vkGetQueryPoolResults) X(vkCmdResetQueryPool) X(vkCmdWriteTimestamp) X(vkDestroyDevice) X(vkGetDeviceQueue) X(vkDeviceWaitIdle)'),
 ('static int vk_begin(void)',
  '#include "'+str(Path(__file__).resolve().parent.parent/'sdk_overrides/gpu_timing.inc')+'"\n\nstatic int vk_begin(void)'),
 ('    /* A present\'s copy may still be reading the surface this frame is about',
  '    tb_gpu_begin();\n    /* A present\'s copy may still be reading the surface this frame is about'),
 ('    VK_CHECK(pvkEndCommandBuffer(s_vk.cmd), "vkEndCommandBuffer");',
  '    tb_gpu_end();\n    VK_CHECK(pvkEndCommandBuffer(s_vk.cmd), "vkEndCommandBuffer");'),
 ('    VK_CHECK(pvkWaitForFences(s_vk.device, 1, &s_vk.fence, VK_TRUE, UINT64_MAX), "vkWaitForFences");',
  '    VK_CHECK(pvkWaitForFences(s_vk.device, 1, &s_vk.fence, VK_TRUE, UINT64_MAX), "vkWaitForFences");\n    tb_gpu_result();'),
 ('        pvkDestroyDevice(s_vk.device, NULL);',
  '        tb_gpu_shutdown();\n        pvkDestroyDevice(s_vk.device, NULL);'),
 ('    (void)user;\n    if (!s_vk.device) return;\n    if (s_eb_pending) vk_eng_flush();',
  '    const uint64_t tb_guest_serial=tb_fps_guest_serial();\n    (void)user;\n    if (!s_vk.device) return;\n    if (s_eb_pending) vk_eng_flush();'),
 ('        vk_present_record_end(cb);',
  '        if(o) tb_completed_guest_serial=tb_guest_serial;\n        vk_present_record_end(cb);'),
 ('static void vk_window_drop(const char* why)',
  '#include "'+str(Path(__file__).resolve().parent.parent/'sdk_overrides/fps_vulkan.inc')+'"\n\nstatic void vk_window_drop(const char* why)'),
 ('    pvkGetPhysicalDeviceFormatProperties(s_vk.phys, fmt.format, &fp);',
  '    tb_fps_swap_format=fmt.format;\n    pvkGetPhysicalDeviceFormatProperties(s_vk.phys, fmt.format, &fp);'),
 ('    b.srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;\n    b.dstAccessMask = 0;',
  '    tb_fps_window_overlay(cb,s_vk.sc_images[idx]);\n    b.srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;\n    b.dstAccessMask = 0;'),
 ('        vk_destroy_buffer(&s_vk.readback);',
  '        vk_destroy_buffer(&tb_fps_buffer);\n        vk_destroy_buffer(&s_vk.readback);'),
 ('    s_vk.presented++;', '    s_vk.presented++;\n    tb_frame_history(px,s_vk.width,s_vk.height,s_vk.presented);'),
 ('    vk_eng_pipe* P = &s_epipe[s_e2.pipeline - 1];\n    if (!P->used || stride != P->stride) return;',
  '    vk_eng_pipe* P = &s_epipe[s_e2.pipeline - 1];\n    if (!P->used || stride != P->stride) return;\n    { static unsigned n; static const char* dir=(const char*)-1; if (dir == (const char*)-1) dir=getenv("TB_RSX_TEXTURE_CAPTURE"); static int ready; if (dir && s_e2.tex[0] && s_eobj[s_e2.tex[0]].w == 1920 && s_eobj[s_e2.tex[0]].h == 1080) ready=1; if (dir && ready && n < 96) { char path[4096]; snprintf(path,sizeof(path),"%s/draw-%03u.bin",dir,n); FILE* f=fopen(path,"wb"); if(f){fwrite(vertices,stride,vertex_count,f);fclose(f);} snprintf(path,sizeof(path),"%s/draws.txt",dir); f=fopen(path,"a"); if(f){ fprintf(f,"%u pipe=%u vertices=%u stride=%u index=%u texture=%u mask=%x viewport=%f,%f,%f,%f scissor=%u,%u,%u,%u\\n",n,s_e2.pipeline,vertex_count,stride,index_count,s_e2.tex[0],s_e2.tex_mask,s_e2.vp[0],s_e2.vp[1],s_e2.vp[2],s_e2.vp[3],s_e2.sc[0],s_e2.sc[1],s_e2.sc[2],s_e2.sc[3]);fclose(f);} ++n; } }'),
 ('int rsx_vulkan_backend_pump_messages(void)\n{', 'int rsx_vulkan_backend_pump_messages(void)\n{\n    tb_vulkan_event(7,0,0,0);'),
 ('    while (SDL_PollEvent(&e)) {', '    while (SDL_PollEvent(&e)) {\n        if (e.type == SDL_KEYDOWN && !e.key.repeat && e.key.keysym.sym==SDLK_F3) tb_fps_toggle();\n        if (e.type == SDL_KEYDOWN && !e.key.repeat) tb_keyboard_down((uint32_t)e.key.keysym.scancode);\n        if (e.type == SDL_WINDOWEVENT && e.window.event == SDL_WINDOWEVENT_FOCUS_LOST) (void)tb_keyboard_take();\n        if (e.type == SDL_WINDOWEVENT) tb_vulkan_event(8,e.window.event,e.window.windowID,0);'),
 ('    return pvkCreateShaderModule(s_vk.device, &ci, NULL, out) == VK_SUCCESS ? 0 : -1;',
  '    int result = pvkCreateShaderModule(s_vk.device, &ci, NULL, out) == VK_SUCCESS ? 0 : -1;\n    if (!result) tb_vulkan_event(1,stage,words,0);\n    return result;'),
 ('    return P->variant[topo_idx];', '    if (P->variant[topo_idx]) tb_vulkan_event(4,topo_idx,P->stride,P->rt_count);\n    return P->variant[topo_idx];'),
 ('    if (!batched) { vk_submit_and_wait(); return; }', '    tb_vulkan_event(2,vertex_count,index_count,topology);\n    if (!batched) { vk_submit_and_wait(); return; }'),
 ('    r = vk_queue_present(&pi);', '    r = vk_queue_present(&pi);\n    if (r == VK_SUCCESS || r == VK_SUBOPTIMAL_KHR) { tb_fps_present(); tb_gameplay_event(1,tb_completed_guest_serial); tb_vulkan_event(3,s_vk.presented,s_vk.sc_extent.width,s_vk.sc_extent.height); }\n    if (r == VK_SUCCESS || r == VK_SUBOPTIMAL_KHR) tb_render_frame();'),
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
  if relative=='libs/video/rsx_vulkan_backend.c':
   from submission_overlay import instrument
   text=instrument(text)
   settings_edits=[
    ('static void vk_window_open(const char* title)', '#include "'+str(Path(__file__).resolve().parent.parent/'sdk_overrides/player_settings_hooks.inc')+'"\nstatic void vk_window_open(const char* title)'),
    ('    s_vk.window = SDL_CreateWindow(title && title[0] ? title : "ps3recomp",', '    int tb_width=(int)s_vk.width, tb_height=(int)s_vk.height;\n    tb_settings_window_size(&tb_width,&tb_height);\n    s_vk.window = SDL_CreateWindow(title && title[0] ? title : "ps3recomp",'),
    ('                                   (int)s_vk.width, (int)s_vk.height, flags);', '                                   tb_width, tb_height, flags);'),
    ('            if (SDL_SetWindowFullscreen(s_vk.window, full ? 0 : SDL_WINDOW_FULLSCREEN_DESKTOP) == 0)\n                SDL_ShowCursor(full ? SDL_ENABLE : SDL_DISABLE);', '            if (SDL_SetWindowFullscreen(s_vk.window, full ? 0 : SDL_WINDOW_FULLSCREEN_DESKTOP) == 0) {\n                SDL_ShowCursor(full ? SDL_ENABLE : SDL_DISABLE);\n                tb_settings_note_fullscreen(!full);\n            }'),
    ('                    e.window.event == SDL_WINDOWEVENT_MAXIMIZED)) {\n            s_vk_resized = 1;', '                    e.window.event == SDL_WINDOWEVENT_MAXIMIZED)) {\n            if (!(SDL_GetWindowFlags(s_vk.window) & (SDL_WINDOW_FULLSCREEN_DESKTOP | SDL_WINDOW_MAXIMIZED))) {\n                int w,h; SDL_GetWindowSize(s_vk.window,&w,&h); tb_settings_note_window(w,h);\n            }\n            s_vk_resized = 1;')]
   for before,after in settings_edits:
    if text.count(before)!=1:raise SystemExit('Player settings anchor mismatch')
    text=text.replace(before,after,1)
  if relative=='runtime/ppu/ppu_hle.cpp':
   for name in ('ps3_hle_call','ps3_hle_register','ps3_hle_register_ctx','ps3_hle_register_fast'):
    pattern=r'(extern "C" void )'+name+r'(\()'
    text,count=re.subn(pattern,lambda m:m[1]+'tb_sdk_'+name+m[2],text)
    if count!=1:raise SystemExit('Portable HLE boundary anchor mismatch: '+name)
  if relative=='libs/video/rsx_vulkan_backend.c':
   text=text.replace('#ifndef _WIN32\n','/* Vulkan backend is compiled on Linux and Windows. */\n',1)
   text=text.replace('#endif /* !_WIN32 */','')
   text=text.replace('#include <dlfcn.h>\n#include <pthread.h>','#include "'+str(Path(__file__).resolve().parent.parent/'sdk_overrides/vulkan_host.h')+'"',1)
  if relative=='libs/audio/cellAudio.c':
   before='''    return SDL_GetQueuedAudioSize(s_sdl_audio_dev) / (2 * sizeof(float)) <
           CELL_AUDIO_BLOCK_SAMPLES * 4 ? CELL_AUDIO_BLOCK_SAMPLES : 0;'''
   after='''    /* A device may consume partial guest blocks. Reserve room for the
     * entire next block, so submission cannot exceed the four-block bound. */
    return SDL_GetQueuedAudioSize(s_sdl_audio_dev) / (2 * sizeof(float)) <=
           CELL_AUDIO_BLOCK_SAMPLES * 3 ? CELL_AUDIO_BLOCK_SAMPLES : 0;'''
   if text.count(before)!=1:raise SystemExit('Audio queue capacity anchor mismatch')
   text=text.replace(before,after,1)
   begin=text.index('#ifdef _WIN32\nstatic unsigned long long g_audio_underruns;')
   end=text.index('\nstatic int audio_start_mix_thread',begin)
   text=text[:begin]+'#include "'+str(Path(__file__).resolve().parent.parent/'sdk_overrides/audio_clock.inc')+'"\n'+text[end:]
   text='#define PS3RECOMP_AUDIO_USE_SDL2 1\n'+text
  if relative=='libs/input/cellPad.c':
   before='    return (unsigned long long)GetTickCount64();'
   if text.count(before)!=1:raise SystemExit('Pad monotonic-clock anchor mismatch')
   text=text.replace(before,'    return tb_host_now()/1000000ull;',1)
   text='#define PS3RECOMP_PAD_USE_SDL2 1\n#include "'+str(Path(__file__).resolve().parent.parent/'host_services.h')+'"\n'+text
  if relative=='libs/filesystem/edat.c':
   text='#ifdef _WIN32\n#include "'+str(Path(__file__).resolve().parent.parent/'sdk_overrides/cache_windows.inc')+'"\n#endif\n'+text
  def include(match):
   path=original.parent/match[1]
   return '#include "'+str(path.resolve())+'"' if path.is_file() else match[0]
  text=re.sub(r'#include "([^"\n]+)"',include,text)
  text='#include "'+str(Path(__file__).resolve().parent.parent/'execution_policy_api.h')+'"\n'+text
  if relative in ('libs/video/rsx_vulkan_backend.c','libs/input/cellPad.c'):
   functions=['rsx_vulkan_backend_pump_messages'] if relative.endswith('rsx_vulkan_backend.c') else ['pad_poll_sdl2','pad_sdl_hotplug','pad_sdl_close']
   include='#include \"'+str(Path(__file__).resolve().parent.parent/'sdk_overrides/sdl_event_gate.h')+'\"\n'
   for function in functions:
    pattern=r'(^'+('int ' if function.startswith('rsx_') else 'static void ')+function+r'\([^;{]*\)\n\{)'
    text,count=re.subn(pattern,lambda m:include+m[0]+'\n    TB_SDL_EVENT_GATE();',text,flags=re.M)
    if count!=1:raise SystemExit('SDL event gate anchor mismatch: '+function)
  if relative=='libs/video/rsx_vulkan_backend.c':
   anchor='int rsx_vulkan_backend_pump_messages(void)\n{'
   text=text.replace(anchor,'#include \"'+str(Path(__file__).resolve().parent.parent/'sdk_overrides/lifecycle_stats.inc')+'\"\n'+anchor+'\n    tb_lifecycle_stats();')
   for anchor in ('X(vkCreateFence) X(vkDestroyFence)','static int vk_submit_and_wait(void)\n{','pvkWaitForFences(s_vk.device, 1, &s_vk.fence, VK_TRUE, UINT64_MAX)'):
    if text.count(anchor)!=1:raise SystemExit('Fence poll anchor mismatch: '+anchor)
   text=text.replace('X(vkCreateFence) X(vkDestroyFence)', 'X(vkGetFenceStatus) X(vkCreateFence) X(vkDestroyFence)')
   text=text.replace('static int vk_submit_and_wait(void)\n{','#include \"'+str(Path(__file__).resolve().parent.parent/'sdk_overrides/vulkan_fence_poll.inc')+'\"\nstatic int vk_submit_and_wait(void)\n{')
   text=text.replace('pvkWaitForFences(s_vk.device, 1, &s_vk.fence, VK_TRUE, UINT64_MAX)', 'tb_fence_wait(s_vk.fence)')
   # Window present mode: MAILBOX when available (vulkan_present_mode.inc).
   for anchor in ('X(vkGetPhysicalDeviceSurfaceCapabilitiesKHR) X(vkGetPhysicalDeviceSurfaceFormatsKHR)','        .presentMode = VK_PRESENT_MODE_FIFO_KHR,   /* the one mode every driver has */','static int vk_create_swapchain(void)\n{'):
    if text.count(anchor)!=1:raise SystemExit('Present mode anchor mismatch: '+anchor)
   text=text.replace('X(vkGetPhysicalDeviceSurfaceCapabilitiesKHR) X(vkGetPhysicalDeviceSurfaceFormatsKHR)','X(vkGetPhysicalDeviceSurfaceCapabilitiesKHR) X(vkGetPhysicalDeviceSurfaceFormatsKHR) X(vkGetPhysicalDeviceSurfacePresentModesKHR)')
   text=text.replace('        .presentMode = VK_PRESENT_MODE_FIFO_KHR,   /* the one mode every driver has */','        .presentMode = tb_present_mode(),')
   text=text.replace('static int vk_create_swapchain(void)\n{','#include \"'+str(Path(__file__).resolve().parent.parent/'sdk_overrides/vulkan_present_mode.inc')+'\"\nstatic int vk_create_swapchain(void)\n{')
   # Asynchronous draw-batch slots (vulkan_async_batches.inc).
   async_inc=str(Path(__file__).resolve().parent.parent/'sdk_overrides/vulkan_async_batches.inc')
   for before,after in [
    ('static void vk_destroy_image(vk_image* im)\n{\n    if (s_eb_pending) vk_eng_flush();',
     'static int tb_async_inflight(void);\nstatic int tb_async_active(void);\nstatic int tb_async_record(void);\nstatic int tb_async_flush(void);\nstatic void tb_async_init(void);\nstatic void tb_eng_complete(void);\nstatic void tb_ring_window(VkDeviceSize cap, VkDeviceSize* lo, VkDeviceSize* hi);\nstatic void vk_destroy_image(vk_image* im)\n{\n    if (im->img || im->view) { if (s_eb_pending || tb_async_inflight()) { tb_wait_label="complete:destroy-image"; tb_eng_complete(); } }'),
    ('static void vk_destroy_buffer(vk_buffer* b)\n{\n    if (s_eb_pending) vk_eng_flush();',
     'static void vk_destroy_buffer(vk_buffer* b)\n{\n    if (b->buf || b->mem) { if (s_eb_pending || tb_async_inflight()) { tb_wait_label="complete:destroy-buffer"; tb_eng_complete(); } }'),
    ('static void vk_eng_drop_framebuffers(void)\n{\n    if (s_eb_pending) vk_eng_flush();',
     'static void vk_eng_drop_framebuffers(void)\n{\n    if (s_eb_pending || tb_async_inflight()) { tb_wait_label="complete:drop-framebuffers"; tb_eng_complete(); }'),
    ('    (void)user;\n    if (s_eb_pending) vk_eng_flush();\n    if (!pipeline || pipeline > VK_ENG_MAX_PIPE',
     '    (void)user;\n    if (s_eb_pending || tb_async_inflight()) { tb_wait_label="complete:pipeline-release"; tb_eng_complete(); }\n    if (!pipeline || pipeline > VK_ENG_MAX_PIPE'),
    ('static void vk_eb_release(void)\n{\n    if (s_eb_pending) vk_eng_flush();',
     'static void vk_eb_release(void)\n{\n    tb_wait_label="complete:release"; tb_eng_complete();'),
    ('    (void)user; (void)reason;      /* every other submission has already completed */\n    if (s_eb_pending) vk_eng_flush();',
     '    (void)user; (void)reason;      /* synchronous: every submission has completed on return */\n    tb_wait_label="complete:engine-submit"; tb_eng_complete();'),
    ('    s_eb_pending = 0;               /* first: the submit below must not re-enter */\n    vk_submit_and_wait();',
     '    s_eb_pending = 0;               /* first: the submit below must not re-enter */\n    if (tb_async_flush()) return;\n    vk_submit_and_wait();'),
    ('static VkDeviceSize vk_eb_take(VkDeviceSize* head, VkDeviceSize cap, VkDeviceSize bytes, VkDeviceSize align)\n{\n    const VkDeviceSize at = (*head + align - 1u) / align * align;\n    if (at + bytes > cap) return (VkDeviceSize)-1;',
     '#include "'+async_inc+'"\n#include "'+str(Path(__file__).resolve().parent.parent/'sdk_overrides/vulkan_surface_blit.inc')+'"\nstatic VkDeviceSize vk_eb_take(VkDeviceSize* head, VkDeviceSize cap, VkDeviceSize bytes, VkDeviceSize align)\n{\n    VkDeviceSize lo, hi;\n    tb_ring_window(cap, &lo, &hi);\n    const VkDeviceSize from = *head < lo ? lo : *head;\n    const VkDeviceSize at = (from + align - 1u) / align * align;\n    if (at + bytes > hi) return (VkDeviceSize)-1;'),
    ('    s_eb.on = 1;\n    return 1;','    s_eb.on = 1;\n    tb_async_init();\n    return 1;'),
    ('    if (!vk_eb_active()) return -2;','    if (!vk_eb_active() && !tb_async_record()) return -2;'),
    ('    VK_CHECK(pvkBeginCommandBuffer(s_vk.cmd, &bi), "vkBeginCommandBuffer");\n',
     '    VK_CHECK(pvkBeginCommandBuffer(s_vk.cmd, &bi), "vkBeginCommandBuffer");\n    /* Asynchronous batches: begin after everything submitted, so this\n     * command buffer\'s fence also means every earlier batch finished. */\n    if (tb_async_active()) vk_cmd_after_everything(s_vk.cmd);\n'),
   ]:
    if text.count(before)!=1:raise SystemExit('Async batch anchor mismatch: '+before[:80])
    text=text.replace(before,after)
   # Per-frame work counters and GPU timestamps for the frame-budget timeline
   # (vulkan_frame_counters.inc, vulkan_gpu_frame_timing.inc).
   overrides_dir=Path(__file__).resolve().parent.parent/'sdk_overrides'
   for before,after in [
    ('VK_WSI_DEVICE_FNS(VK_DECLARE)\n','VK_WSI_DEVICE_FNS(VK_DECLARE)\n#include "'+str(overrides_dir/'vulkan_frame_counters.inc')+'"\n'),
    ('static int vk_begin(void)\n{','#include "'+str(overrides_dir/'vulkan_gpu_frame_timing.inc')+'"\nstatic int vk_begin(void)\n{'),
    ('        tb_gpu_shutdown();\n','        tb_gpu_shutdown();\n        tb_ts_shutdown();\n'),
    ('static int vk_submit_and_wait(void)\n{','static int vk_submit_and_wait(void)\n{\n    tb_frame_budget_count(TB_FC_SYNC_SUBMIT_WAIT, 1);'),
   ]:
    if text.count(before)!=1:raise SystemExit('Frame counter anchor mismatch: '+before[:60])
    text=text.replace(before,after)
   # Clears join the open batch too when submission is asynchronous.
   if text.count('    const int batched = vk_eb_active();\n')!=2:raise SystemExit('Async clear anchor mismatch')
   text=text.replace('    const int batched = vk_eb_active();\n','    const int batched = vk_eb_active() || tb_async_record();\n')
   header=Path(__file__).resolve().parent.parent/'sdk_overrides/depth_cost_trace.h'
   # Unique generated call-site labels preserve separate monotonic counters.
   definitions=list(re.finditer(r'^static [^;{]*?\b(\w+)\([^;{]*?\)\n\{',text,re.M))
   def wait_site(match):
    owner=next((d[1] for d in reversed(definitions) if d.start()<match.start()),'unknown')
    line=text[:match.start()].count('\n')+1
    fn=match[1];label=('wait:' if fn=='vk_submit_and_wait' else 'flush:')+owner+':'+str(line)
    return 'TB_WAIT_SITE("'+label+'",'+fn+')'
   text=re.sub(r'\b(vk_submit_and_wait|vk_eng_flush)\(\)',wait_site,text)
   anchor='static void vk_eng_submit_and_wait(void* user, u32 reason)\n{'
   consumer='\n#if defined(__linux__)\n    static tb_depth_cost_totals totals[4];\n    static const char* names[]={"consumer:vertex-ring","consumer:guest-reference","consumer:shutdown","consumer:unknown"};\n    unsigned slot=reason<3?reason:3;\n    tb_depth_cost_scope consumer_scope __attribute__((cleanup(tb_depth_cost_end))) =\n        {&totals[slot],names[slot],tb_depth_cost_enabled()?tb_depth_cost_now():0,0,0};\n#endif\n'
   if text.count(anchor)!=1:raise SystemExit('Consumer scope anchor mismatch')
   text=text.replace(anchor,anchor+consumer)
   macro='#if defined(__linux__)\n#define TB_WAIT_SITE(label,fn) ({ TB_DEPTH_COST(label,0,0); tb_wait_label=label; fn(); })\n#else\n#define TB_WAIT_SITE(label,fn) (tb_wait_label=label, fn())\n#endif\n'
   text='#include "'+str(header)+'"\n#include "'+str(Path(__file__).resolve().parent.parent/'frame_budget.h')+'"\n'+macro+text
  if relative=='libs/video/cellGcmSys.c':
   gpu_inc=str(Path(__file__).resolve().parent.parent/'sdk_overrides/nv3089_gpu.inc')
   for before,after in [('static void nv3089_blit(void)\n{','#include "'+gpu_inc+'"\nstatic void nv3089_blit(void)\n{'),
                        ('    /* Only the 32-bit colour formats are handled; anything else would need a\n','    if (tb_nv3089_gpu(in_pitch, dst_pitch, out_x, out_y, out_w, out_h)) return;\n    /* Only the 32-bit colour formats are handled; anything else would need a\n')]:
    if text.count(before)!=1:raise SystemExit('NV3089 GPU anchor mismatch: '+before[:60])
    text=text.replace(before,after)
   anchor='if ((m==0x0050u || m==0x0104u || m==0x1D70u || m==0x1D74u || m==0x1800u) && rsx_draw_engine_enabled()) rsx_draw_engine_flush();'
   cases=''.join('case '+method+'u: { TB_DEPTH_COST("fifo:'+method+'",0,0); tb_publication_flush_at('+method+'u); break; }' for method in ['0x0050','0x0104','0x1D70','0x1D74','0x1800'])
   if text.count(anchor)!=1:raise SystemExit('FIFO wait anchor mismatch')
   text=text.replace(anchor,'if(rsx_draw_engine_enabled())switch(m){'+cases+'default:break;}')
   text='#include "'+str(Path(__file__).resolve().parent.parent/'sdk_overrides/gcm_label_publication.inc')+'"\n'+text
  if relative=='libs/video/rsx_draw_engine.c':
   text='#include "'+str(Path(__file__).resolve().parent.parent/'sdk_overrides/depth_cost_trace.h')+'"\n'+text
   for function,label in [('eng_vertex_layout','vertex-layout'),('dc_fetch','vertex-fetch'),('eng_pipeline_get','engine-pipeline'),('sink_bind_textures','texture-binding')]:
    pattern=r'(^static [^;{]*?\b'+function+r'\([^;{]*?\)\n\{)'
    text,n=re.subn(pattern,lambda m:m[0]+'\n    TB_DEPTH_COST("'+label+'",0,g.frames);',text,flags=re.M)
    if n!=1:raise SystemExit('Draw cost anchor mismatch: '+function)
   anchor0='#include "'+str(Path(__file__).resolve().parent.parent/'sdk_overrides/page_census_arm.inc')+'"\n'
   if text.count(anchor0)!=1:raise SystemExit('GPU blit engine anchor mismatch')
   text=text.replace(anchor0,anchor0+'#include "'+str(Path(__file__).resolve().parent.parent/'sdk_overrides/gpu_blit_engine.inc')+'"\n')
   anchor='    if(tb_depth_guest_read(location,offset,pitch,w,h))return;'
   text=text.replace(anchor,'    TB_DEPTH_COST("surface-guest-read",(uint64_t)w*h*4,g.frames);\n'+anchor)
   # Frame-budget work counters at the texture and guest-readback entry points.
   text='#include "'+str(Path(__file__).resolve().parent.parent/'frame_budget.h')+'"\n'+text
   for signature,count in [
    (r'static u64 eng_texture_content_hash\(u32 location, u32 offset, u32 span,','TB_FC_TEXTURE_HASH_BYTES, span'),
    (r'static u64 eng_texture_sparse_hash\(u32 location, u32 offset, u32 span,','TB_FC_SPARSE_HASH_BYTES, span'),
    (r'static u32 eng_texture_upload\(u32 location, u32 offset, u32 fmt, u32 w, u32 h,','TB_FC_TEXTURE_UPLOAD_TEXELS, w*h'),
    (r'void rsx_draw_engine_guest_read\(u32 location, u32 offset, u32 pitch, u32 w, u32 h\)','TB_FC_GUEST_COLOR_READ_BYTES, w*h*4'),
    (r'static int eng_zdepth_to_guest\(u32 slot\)','TB_FC_GUEST_DEPTH_READ, 1'),
   ]:
    text,n=re.subn('(^'+signature+r'[^{]*\{)',lambda m:m[0]+'\n    tb_frame_budget_count('+count+');',text,count=1,flags=re.M)
    if n!=1:raise SystemExit('Frame counter draw-engine anchor mismatch: '+signature)
  target=out/relative;target.parent.mkdir(parents=True,exist_ok=True)
  if not target.exists() or target.read_text()!=text:target.write_text(text)
  record=dict(source=relative,sha256=digest,output_sha256=hashlib.sha256(text.encode()).hexdigest())
  overrides={'libs/video/rsx_vulkan_backend.c':['fps_vulkan.inc','lifecycle_stats.inc','gpu_timing.inc','vulkan_draw_group.inc','vulkan_depth16.inc','vulkan_fence_poll.inc','vulkan_present_mode.inc','vulkan_async_batches.inc','vulkan_surface_blit.inc','vulkan_frame_counters.inc','vulkan_gpu_frame_timing.inc'],'libs/audio/cellAudio.c':'audio_clock.inc','libs/video/cellGcmSys.c':['rsx_memory_copy.inc','gcm_label_publication.inc','nv3089_gpu.inc'],'libs/filesystem/edat.c':'edat_cache.inc','libs/video/rsx_draw_engine.c':['rsx_scanout.inc','rsx_texture_coherence.inc','rsx_trace.inc','depth_path_trace.inc','depth_guest_read.inc','page_census_arm.inc','gpu_blit_engine.inc']}
  if relative in overrides:
   names=overrides[relative] if isinstance(overrides[relative],list) else [overrides[relative]]
   record['port_inputs']={name:hashlib.sha256((Path(__file__).resolve().parent.parent/'sdk_overrides'/name).read_bytes()).hexdigest() for name in names}
  if relative=='libs/video/rsx_vulkan_backend.c':
   record['port_inputs']['player_settings_hooks.inc']=hashlib.sha256((Path(__file__).resolve().parent.parent/'sdk_overrides/player_settings_hooks.inc').read_bytes()).hexdigest()
   for name in ('depth16.comp','depth16_spirv.inc'):
    record['port_inputs']['shaders/'+name]=hashlib.sha256((Path(__file__).resolve().parent.parent/'shaders'/name).read_bytes()).hexdigest()
  if relative in ('libs/video/rsx_vulkan_backend.c','libs/video/rsx_draw_engine.c','libs/video/cellGcmSys.c'):
   record.setdefault('port_inputs',{})['depth_cost_trace.h']=hashlib.sha256((Path(__file__).resolve().parent.parent/'sdk_overrides/depth_cost_trace.h').read_bytes()).hexdigest()
  if relative=='libs/video/rsx_draw_engine.c':
   record['port_inputs']['color_readback_cache.inc']=hashlib.sha256((Path(__file__).resolve().parent.parent/'sdk_overrides/color_readback_cache.inc').read_bytes()).hexdigest()
   record['port_inputs']['index_remap_cache.inc']=hashlib.sha256((Path(__file__).resolve().parent.parent/'sdk_overrides/index_remap_cache.inc').read_bytes()).hexdigest()
  if relative in ('libs/video/rsx_vulkan_backend.c','libs/input/cellPad.c'):
   record.setdefault('port_inputs',{})['sdl_event_gate.h']=hashlib.sha256((Path(__file__).resolve().parent.parent/'sdk_overrides/sdl_event_gate.h').read_bytes()).hexdigest()
  records.append(record)
 (out/'manifest.json').write_text(json.dumps(records,indent=2)+'\n')
if __name__=='__main__':main()
