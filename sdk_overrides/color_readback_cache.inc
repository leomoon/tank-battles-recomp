/* Cache host GPU readback bytes only. Every request still materializes guest
 * bytes and follows the original endian/tiling path. FIFO-thread ownership. */
static void eng_row_convert(u8* dst,const u8* src,u32 w,int rgba,int to_guest);
static struct {
 u8* bytes;
 u32 capacity,handle,location,offset,w,h,pitch,bpp;
 u64 serial;
} tb_color_readback;
static void tb_color_readback_note(int hit,u64 request,u64 fetched)
{
#if defined(__linux__)
 if(!tb_depth_cost_enabled())return;
 static u64 calls,hits,requested,readback,last;static unsigned reports;
 calls++;hits+=hit!=0;requested+=request;readback+=fetched;
 const u64 now=tb_depth_cost_now();
 if(reports<600 && now-last>=1000000000ull) {
  reports++;last=now;
  fprintf(stderr,"[color-readback] frame=%u calls=%llu hits=%llu requested_bytes=%llu fetched_bytes=%llu monotonic_ns=%llu\n",g.frames,
   (unsigned long long)calls,(unsigned long long)hits,(unsigned long long)requested,(unsigned long long)readback,(unsigned long long)now);
 }
#else
 (void)hit;(void)request;(void)fetched;
#endif
}
static void tb_color_readback_reset(void)
{
 free(tb_color_readback.bytes);
 memset(&tb_color_readback,0,sizeof tb_color_readback);
}
static const u8* tb_color_readback_rows(const eng_surface* s,u32 x,u32 y,u32 w,u32 h,u32 bpp)
{
 const char* enabled=getenv("TB_COLOR_READBACK_CACHE");
 const u64 bytes=(u64)s->w*s->h*bpp;
 if(!enabled || strcmp(enabled,"1") || !s->color_serial || !bytes || bytes>(32u<<20) ||
    x>s->w || w>s->w-x || y>s->h || h>s->h-y)return 0;
 const int hit=tb_color_readback.serial==s->color_serial && tb_color_readback.handle==s->handle &&
  tb_color_readback.location==s->location && tb_color_readback.offset==s->offset &&
  tb_color_readback.w==s->w && tb_color_readback.h==s->h && tb_color_readback.pitch==s->pitch && tb_color_readback.bpp==bpp;
 if(!hit) {
  if(tb_color_readback.capacity<bytes) {
   u8* p=realloc(tb_color_readback.bytes,(size_t)bytes);
   if(!p)return 0;
   tb_color_readback.bytes=p;tb_color_readback.capacity=(u32)bytes;
  }
  g.be->submit_and_wait(g.be->user,RSX_BE_FLUSH_GUEST_REFERENCE);
  g.be->readback(g.be->user,s->handle,0,0,s->w,s->h,tb_color_readback.bytes,s->w*bpp);
  tb_frame_budget_count(TB_FC_COLOR_READBACK,1);
  tb_frame_budget_count(TB_FC_COLOR_READBACK_BYTES,(u32)bytes);
  tb_color_readback.handle=s->handle;tb_color_readback.location=s->location;tb_color_readback.offset=s->offset;
  tb_color_readback.w=s->w;tb_color_readback.h=s->h;tb_color_readback.pitch=s->pitch;tb_color_readback.bpp=bpp;
  for(u32 r=0;r<s->h;r++) {
   u8* row=tb_color_readback.bytes+(size_t)r*s->w*bpp;
   if(bpp==8)for(u32 c=0;c<s->w*4;c++){u8 t=row[c*2];row[c*2]=row[c*2+1];row[c*2+1]=t;}
   else eng_row_convert(row,row,s->w,rsx_texture_argb_is_rgba(),1);
  }
  tb_color_readback.serial=s->color_serial;
 }
 tb_color_readback_note(hit,(u64)w*h*bpp,hit?0:bytes);
 return tb_color_readback.bytes+((size_t)y*s->w+x)*bpp;
}
