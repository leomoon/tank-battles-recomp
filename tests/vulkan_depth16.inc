#include <assert.h>
static void check_depth16(u32 w,u32 h) {
 u32 n=w*h,z=vk_eng_depth_target_create(NULL,w,h);assert(z);
 vk_buffer upload={0};assert(!vk_create_host_buffer(&upload,(VkDeviceSize)n*4,VK_BUFFER_USAGE_TRANSFER_SRC_BIT));assert(upload.coherent);
 float* values=(float*)upload.ptr;u32 seed=0x9d170321;
 for(u32 i=0;i<n;i++) {
  u32 bits;
  if(i<65535u*5u) {u32 q=i/5;float boundary=(float)(((double)q+.5)/65535.0);memcpy(&bits,&boundary,4);bits+=i%5-2;}
  else if(i<65535u*5u+12u) {
   const u32 special[]={0,0x80000000u,1,0x007fffffu,0x00800000u,0x3f800000u,0x3f800001u,0x7f800000u,0xff800000u,0x7fc00001u,0x7f800001u,0xffc00001u};bits=special[i-65535u*5u];
  }
  else {seed^=seed<<13;seed^=seed>>17;seed^=seed<<5;bits=seed;}
  memcpy(values+i,&bits,4);
 }
assert(!vk_begin());
 vk_eng_obj* source=vk_eng_get(z,VK_ENG_DEPTH);vk_barrier_image(source->im.img,s_vk.depth_aspect,VK_IMAGE_LAYOUT_GENERAL);
 VkBufferImageCopy region={.imageSubresource={VK_IMAGE_ASPECT_DEPTH_BIT,0,0,1},.imageExtent={w,h,1}};
 pvkCmdCopyBufferToImage(s_vk.cmd,upload.buf,source->im.img,VK_IMAGE_LAYOUT_GENERAL,1,&region);assert(!vk_submit_and_wait());
 u32 t=tb_vk_depth16(NULL,z,w,h,0x92);assert(t);
 unsigned char* output=malloc((size_t)n*2+16);memset(output,0xa5,(size_t)n*2+16);
 vk_eng_readback(NULL,t,0,0,w,h,output,w*2);
 u32 naive_mismatches=0;
 for(u32 i=0;i<n;i++) {float d=values[i];d=d>0?d:0;d=d<1?d:1;u32 expected=(u32)((double)d*65535+.5);volatile float rounded_product=d*65535.0f;u32 naive=(u32)(rounded_product+.5f);naive_mismatches+=naive!=expected;u32 actual=output[i*2]|((u32)output[i*2+1]<<8);
  if(actual!=expected){fprintf(stderr,"depth16 mismatch i=%u f=%g wanted=%u actual=%u\n",i,d,expected,actual);abort();}}
 if(n>65535u*5u)assert(naive_mismatches>0);
 for(u32 i=0;i<16;i++)assert(output[n*2+i]==0xa5);
 u32 view=vk_eng_surface_view(NULL,t,0xaae4,0xb2);assert(view);
 vk_eng_texture_release(NULL,t);assert(!vk_eng_get(view,VK_ENG_VIEW));assert(!vk_eng_get(t,VK_ENG_COLOR));
 assert(!tb_vk_depth16(NULL,z,w+1,h,0x92));assert(!tb_vk_depth16(NULL,z,w,h,0x90));
 vk_eng_clear_depth_stencil(NULL,z,1,0.25f,0);
 t=tb_vk_depth16(NULL,z,w,h,0x92);assert(t);vk_eng_readback(NULL,t,0,0,1,1,output,2);assert((output[0]|(output[1]<<8))==16384);
 vk_eng_texture_release(NULL,t);
 /* Exercise the actual engine callback beyond the finite object-table size. */
 if(w==1 && h==1)for(u32 i=0;i<VK_ENG_MAX_OBJ+32;i++) {
  t=tb_vk_depth16(NULL,z,w,h,0x92);assert(t);assert(vk_eng_surface_view(NULL,t,0xaae4,0xb2));
  vk_eng_texture_release(NULL,t);assert(!vk_eng_get(t,VK_ENG_COLOR));
 }
 t=tb_vk_depth16(NULL,z,w,h,0x92);assert(t);
 vk_eng_depth_target_release(NULL,z);
 vk_eng_readback(NULL,t,0,0,1,1,output,2);assert((output[0]|(output[1]<<8))==16384);
 vk_eng_texture_release(NULL,t);vk_destroy_buffer(&upload);free(output);
}
int main(void) {
 setenv("VK_DRAW_BATCH","0",1);setenv("TB_DEPTH_GPU","1",1);
 assert(!vk_init_all(16,16,"exact GPU depth16 fixture"));assert(!vk_eng_init(NULL,16,16));
 u32 z=vk_eng_depth_target_create(NULL,1,1);assert(z);
 setenv("TB_DEPTH_GPU","0",1);assert(!tb_vk_depth16(NULL,z,1,1,0x92));
 /* Unset (an empty value on Windows) is the default: on for every OS. */
 unsetenv("TB_DEPTH_GPU");
 u32 t=tb_vk_depth16(NULL,z,1,1,0x92);assert(t);vk_eng_texture_release(NULL,t);
 vk_eng_depth_target_release(NULL,z);setenv("TB_DEPTH_GPU","1",1);
 check_depth16(1024,2048);check_depth16(13,7);check_depth16(1,1);
 vk_eng_shutdown(NULL);pvkDeviceWaitIdle(s_vk.device);
 puts("PASS: GPU Z16 conversion matches exact CPU rounding at every 16-bit boundary and random float32 values, odd shapes, views, producer updates and fallback");return 0;
}
