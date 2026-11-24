/* Drive the production engine through public FIFO methods. Reuse the pinned
 * SDK recording backend; simulate a proven/reserved Vulkan group here. GPU
 * allocation/content/generation tests live in vulkan_scale_ownership.inc. */
#if defined(_WIN32)
#define setenv(name,value,overwrite) _putenv_s(name,value)
#define unsetenv(name) _putenv_s(name,"")
#endif
#define main sdk_unused_engine_main
#include "test_rsx_draw_engine.c"
#undef main
#include "sdk_overrides/render_scale.h"
#include "rsx_texture_layout.h"
#include <assert.h>

static unsigned selected_percent;
static int reserved, fallback, scaled_clears;
static u32 scaled_color, scaled_depth, observed_draw_color;
static u32 native_depth,native_color;
static int depth_cpu_writes,color_cpu_reads;
void tb_rsx_guest_read_bytes(u32 location,u32 offset,u32 bytes);
void tb_rsx_guest_wrote_bytes(u32 location,u32 offset,u32 bytes);
int tb_vk_scale_tracked(u32 native) { return selected_percent!=100 && reserved && (native==native_depth || native==native_color); }
int tb_vk_scale_depth_upload(u32 native,tb_scale_rect rect,const u8* rows,u32 pitch)
{
    assert(native==native_depth && rect.x==1 && rect.y==1 && rect.width==1 && rect.height==1 && pitch==4);
    assert(rows[0]==0x80 && rows[3]==20);
    ++depth_cpu_writes; fallback=1;
    return 1;
}
void tb_scanout_sample(uint32_t buffer,uint32_t surface,uint32_t offset) { (void)buffer;(void)surface;(void)offset; }
void tb_frame_order(uint32_t buffer,uint32_t offset,uint64_t serial,uint32_t present)
{ (void)buffer;(void)offset;(void)serial;(void)present; }
void tb_fps_guest_flip(void) {}
void tb_rsx_texture_capture(uint32_t location,uint32_t offset,uint32_t format,uint32_t width,uint32_t height,
    uint32_t pitch,const void* raw,uint32_t raw_bytes,const void* decoded,uint32_t row_bytes,uint32_t rows)
{ (void)location;(void)offset;(void)format;(void)width;(void)height;(void)pitch;(void)raw;(void)raw_bytes;(void)decoded;(void)row_bytes;(void)rows; }
int tb_vk_scale_requested(void) { return (int)selected_percent; }
void tb_vk_scale_fallback(const char* reason) { (void)reason; fallback=1; }
int tb_vk_scale_draw_ready(u32 color) { return !fallback && color==scaled_color; }
int tb_vk_scale_use(tb_scale_surface color, tb_scale_surface depth, tb_scale_binding* binding)
{
    if (!reserved || fallback || selected_percent==100) return 0;
    assert(color.size.width==2560 && color.size.height==1440 && color.pitch==10240);
    assert(depth.size.width==2560 && depth.size.height==1440 && depth.pitch==10240);
    *binding=(tb_scale_binding){scaled_color,scaled_depth,selected_percent,
        100.0f/selected_percent,100.0f/selected_percent};
    return 1;
}
void tb_vk_scale_clear(tb_scale_surface color, tb_scale_surface depth, tb_scale_rect rect,
        unsigned mask, const float rgba[4], float z, unsigned stencil)
{
    (void)color; (void)depth; (void)rgba; (void)z; (void)stencil;
    assert(rect.width==2560 && rect.height==1440 && (mask & TB_SCALE_CLEAR_COLOR));
    ++scaled_clears;
}
static void recorded_draw(void* user, rsx_topology topology, const void* vertices,
        u32 vertex_count, u32 stride, const u32* indices, u32 index_count)
{
    observed_draw_color=stub.bound_rt[0];
    stub_draw(user,topology,vertices,vertex_count,stride,indices,index_count);
}
static void recorded_readback(void* user,u32 surface,u32 x,u32 y,u32 w,u32 h,void* out,u32 pitch)
{
    ++color_cpu_reads;
    stub_readback(user,surface,x,y,w,h,out,pitch);
}
int main(void)
{
    vm_base=calloc(1,64u<<20);
    assert(vm_base);
    ppu_vm_size=64u<<20;
    unsetenv("TB_COLOR_READBACK_CACHE");
    for (u32 i=0;i<12;++i) {
        u8* p=vm_base+VTX_OFFSET+i*4;
        p[0]=0x3f; p[1]=0x80;
    }
    const unsigned presets[]={50,75,100,150,200,300};
    for (unsigned i=0;i<6;++i) {
        selected_percent=presets[i]; reserved=fallback=scaled_clears=depth_cpu_writes=0;
        native_depth=native_color=0;
        rsx_draw_backend backend=g_stub_backend;
        backend.draw=recorded_draw;
        backend.readback=recorded_readback;
        engine_up_with(&backend);
        scaled_color=stub_handle(); scaled_depth=stub_handle();
        m(M_SURFACE_FORMAT,8u|(2u<<5)|(4u<<12));
        m(M_SURFACE_CLIP_H,1280u<<16); m(M_SURFACE_CLIP_V,720u<<16);
        m(M_COLOR_A_PITCH,10240); m(M_CONTEXT_DMA_ZETA,DMA_LOCAL);
        m(M_COLOR_TARGET,CELL_GCM_SURFACE_TARGET_1);
        m(M_CONTEXT_DMA_COLOR_B,DMA_LOCAL); m(M_COLOR_B_OFFSET,0x40000); m(0x021c,10240);
        m(M_ZETA_OFFSET,0x2000000); m(M_ZETA_PITCH,10240);
        m(M_SCISSOR_H,1280u<<16); m(M_SCISSOR_V,720u<<16);
        m(M_CLEAR_BUFFERS,0xf3);
        draw_triangle();
        assert(stub.n_draw==1 && observed_draw_color!=scaled_color);
        native_depth=stub.bound_depth;
        native_color=observed_draw_color;
        reserved=1;
        const int native_clears=stub.n_clear_color;
        const int native_depth_clears=stub.n_clear_ds;
        m(M_CLEAR_BUFFERS,0xf3);
        assert(stub.n_clear_color==native_clears+(selected_percent==100));
        assert(stub.n_clear_ds==native_depth_clears+(selected_percent==100));
        assert(scaled_clears==(selected_percent!=100));
        for (unsigned draw=0;draw<145;++draw) {
            const int before=stub.n_draw;
            draw_triangle();
            assert(stub.n_draw==before+1);
            assert((observed_draw_color==scaled_color)==(selected_percent!=100));
        }
        const u32 byte_offset=0x40000+10240;
        const int reads_before=color_cpu_reads;
        tb_rsx_guest_read_bytes(RSX_LOCATION_LOCAL,byte_offset,8);
        tb_rsx_guest_read_bytes(RSX_LOCATION_LOCAL,byte_offset,8);
        assert(color_cpu_reads==reads_before+(selected_percent==100?2:1));
        const unsigned green_byte=rsx_texture_argb_is_rgba()?1:2;
        vm_base[GUEST_LOCAL_EA+byte_offset+green_byte]=0x99;
        const int writes_before=g_written.n;
        tb_rsx_guest_wrote_bytes(RSX_LOCATION_LOCAL,byte_offset+green_byte,1);
        const u8 expected_pixel[]={0x11,0x99,0x33,0x44};
        assert(g_written.n==writes_before+1 && g_written.x==0 && g_written.y==1 && g_written.w==1);
        assert(!memcmp(g_written.first,expected_pixel,4));
        m(M_SURFACE_FORMAT,8u|(2u<<5));
        m(M_SURFACE_CLIP_H,256u<<16); m(M_SURFACE_CLIP_V,256u<<16);
        m(M_COLOR_B_OFFSET,0x700000); m(0x021c,1024);
        m(M_ZETA_OFFSET,0x710000); m(M_ZETA_PITCH,1024);
        draw_triangle();
        assert(stub.n_draw==147 && observed_draw_color!=scaled_color); /* independent shadow/utility */
        m(M_SURFACE_FORMAT,8u|(2u<<5)|(4u<<12));
        m(M_SURFACE_CLIP_H,1280u<<16); m(M_SURFACE_CLIP_V,720u<<16);
        m(M_COLOR_B_OFFSET,0x40000); m(0x021c,10240);
        m(M_ZETA_OFFSET,0x2000000); m(M_ZETA_PITCH,10240);
        if (selected_percent!=100) {
            const u32 offset=0x2000000+10240+4;
            u8* packed=vm_base+GUEST_LOCAL_EA+offset;
            packed[0]=0x80; packed[1]=packed[2]=0; packed[3]=20;
            rsx_draw_engine_guest_wrote(RSX_LOCATION_LOCAL,offset,10240,1,1);
            assert(depth_cpu_writes==1 && fallback);
        }
        fallback=1;
        draw_triangle();
        assert(stub.n_draw==148 && observed_draw_color!=scaled_color);
        engine_down();
    }
    free(vm_base);
    puts("PASS: production FIFO engine emits one draw/clear per operation at all six presets; unknown and fallback routes remain Native");
    return 0;
}
