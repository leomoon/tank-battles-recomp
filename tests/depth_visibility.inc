/* Use the actual NV0039 override and actual tiled-memory implementation with
 * the generated draw engine, rather than mocking its observation callback. */
static struct { u32 localSize; } s_config={GUEST_LOCAL_EA};
static u8* tb_depth_test_ptr(void* user,u32 location,u32 offset,u32 bytes)
{
 (void)user;
 if(location>1 || (u64)offset+bytes>GUEST_LOCAL_EA)return NULL;
 return vm_base+(location==RSX_LOCATION_LOCAL?GUEST_LOCAL_EA:0)+offset;
}
static rsx_tiled_mem s_gcm_mem={.ptr=tb_depth_test_ptr};
#include "sdk_overrides/rsx_memory_copy.inc"

static void test_depth_guest_visibility(void)
{
 rsx_draw_engine_set_default(1);
 engine_up();rsx_draw_backend be=g_stub_backend;be.depth_as_color=stub_depth_as_color;
 rsx_draw_engine_set_backend(&be);g_argb_calls=0;g_argb_fail=0;g_readback_depth=.5f;
 u8* bytes=vm_base+GUEST_LOCAL_EA+ZETA_OFFSET;memset(bytes,0x5a,2048);
 depth_pass_then_read_as(1u,0x92u,512u,0);draw_triangle();
 CHECK(g_argb_calls==1 && stub.n_depth_readback==0 && bytes[0]==0x5a,"GPU sampling leaves guest depth dirty before observation");
 /* NV0039 passes the byte span as pitch for each individual row. */
 memset(vm_base+0x2000,0xa5,32);
 tb_m2mf.src_dma=0xfeed0000;tb_m2mf.dst_dma=0xfeed0001;
 tb_m2mf.src=ZETA_OFFSET+512;tb_m2mf.dst=0x2000;
 tb_m2mf.src_pitch=512;tb_m2mf.dst_pitch=8;
 tb_m2mf.columns=4;tb_m2mf.rows=2;tb_m2mf.format=0x101;
 tb_m2mf_copy();
 CHECK(stub.n_depth_readback==1 && bytes[0]==0x80 && bytes[1]==0 && bytes[512]==0x80 && bytes[513]==0,"contained partial transfer materializes newest Z16 producer exactly");
 CHECK(vm_base[0x2000]==0x80 && vm_base[0x2001]==0 && vm_base[0x2008]==0x80 && vm_base[0x2009]==0 && vm_base[0x2004]==0xa5,"actual NV0039 pitched transfer observes current GPU depth and preserves destination gaps");
 rsx_draw_engine_guest_read(0,ZETA_OFFSET,512,2,4);
 CHECK(stub.n_depth_readback==1,"unchanged producer reuses materialized guest depth");
 draw_triangle();CHECK(g_argb_calls==1,"guest observation retains unchanged GPU view");
 depth_pass_then_read_as(1u,0x92u,512u,0);draw_triangle();g_readback_depth=.25f;
 rsx_draw_engine_guest_read(0,ZETA_OFFSET,512,2,4);
 CHECK(stub.n_depth_readback==2 && bytes[0]==0x40 && bytes[1]==0,"new producer forces another exact guest materialization");
 memset(vm_base+0x3000,0xa5,32);
 tb_m2mf.src=ZETA_OFFSET+1024;tb_m2mf.dst=0x3000;
 tb_m2mf.src_pitch=(u32)-512;tb_m2mf.dst_pitch=16;tb_m2mf.format=0x302;
 tb_m2mf_copy();
 CHECK(vm_base[0x3000]==0x40 && vm_base[0x3003]==0x40 && vm_base[0x3010]==0x40 && vm_base[0x3001]==0xa5 && stub.n_depth_readback==2,"actual NV0039 negative source pitch and byte strides reuse current materialized depth");
 m(M_TEX_UNIT+0x0c,0);m(M_COLOR_A_OFFSET,ZETA_OFFSET);
 m(M_COLOR_A_PITCH,512);m(M_SURFACE_CLIP_H,128u<<16);draw_triangle();
 g_stub_ramp=1;rsx_draw_engine_guest_read(0,ZETA_OFFSET,512,1,1);g_stub_ramp=0;
 CHECK(stub.n_depth_readback==2 && (bytes[0]!=0x40 || bytes[1]!=0),"newer color alias wins over older materialized depth");
 m(M_COLOR_A_OFFSET,0x40000);g_readback_depth=.75f;
 depth_pass_then_read_as(1u,0x92u,512u,0);draw_triangle();
 rsx_draw_engine_guest_read(0,ZETA_OFFSET,512,1,1);
 CHECK(stub.n_depth_readback==3 && bytes[0]==0xbf && bytes[1]==0xff,"newer depth producer wins over the older color alias");
 engine_down();g_readback_depth=.5f;
}
