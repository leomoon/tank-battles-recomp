/* Materialize the newest depth producer before a contained transfer observes
 * guest bytes. The original exact CPU conversion remains the readback path. */
static int eng_zdepth_to_guest(u32 slot);
static int tb_depth_guest_read(u32 location,u32 offset,u32 pitch,u32 w,u32 h)
{
 if(!g.ready || !pitch || !w || !h)return 0;
 u32 best=ENG_INVALID;
 const u64 end=(u64)offset+(u64)(h-1)*pitch+(u64)w*4;
 for(u32 i=0;i<g.n_zdepths;i++) {
  eng_zdepth* z=&g.zdepths[i];
  if(z->location!=location || !z->pitch || (h>1 && z->pitch!=pitch) || offset<z->offset)continue;
  u32 xb=0,y=0;u32 color=eng_surface_at(location,offset,z->pitch,&xb,&y);
  u64 color_serial=color==ENG_INVALID?0:g.surfaces[color].color_serial;
  if(z->depth_serial<=color_serial)continue;
  const u32 height=z->used_h<z->h?z->used_h:z->h;
  const u32 width=z->used_w<z->w?z->used_w:z->w;
  const u32 bpp=z->zfmt==1?2:4;
  const u64 relative=(u64)offset-z->offset;
  if(end>(u64)z->offset+(u64)height*z->pitch || relative%z->pitch+(u64)w*4>(u64)width*bpp)continue;
  if(best==ENG_INVALID || z->depth_serial>g.zdepths[best].depth_serial)best=i;
 }
 return best!=ENG_INVALID && eng_zdepth_to_guest(best);
}
