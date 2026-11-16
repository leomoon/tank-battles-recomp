/* Exact index-result reuse only. Inputs are freshly read every draw; vertex
 * values, constants, descriptors and GPU resource lifetimes are not cached. */
#define TB_REMAP_LIMIT 65536u
static struct {
 u8* data;
 u32 count,unique;
} tb_remap_entries[2];
static unsigned tb_remap_victim;
static void tb_remap_reset(void) {
 for(unsigned i=0;i<2;i++)free(tb_remap_entries[i].data);
 memset(tb_remap_entries,0,sizeof tb_remap_entries);tb_remap_victim=0;
}
static int tb_remap_build(rsx_vertex_remap* remap,rsx_vertex_ref* refs,u32 count,u32* unique)
{
 TB_DEPTH_COST("index-remap",0,g.frames);
 const char* enabled=getenv("TB_DRAW_REMAP_CACHE");
 if(!enabled || strcmp(enabled,"1") || !remap || !refs || !unique || !count || count>TB_REMAP_LIMIT)
  return rsx_vertex_remap_build(remap,refs,count,unique);
 const size_t source_bytes=(size_t)count*sizeof *refs;
 for(unsigned i=0;i<2;i++) {
  if(!tb_remap_entries[i].data || tb_remap_entries[i].count!=count || memcmp(tb_remap_entries[i].data,refs,source_bytes))continue;
  TB_DEPTH_COST("index-remap-hit",source_bytes,g.frames);
  u8* data=tb_remap_entries[i].data;
  rsx_vertex_ref* output=(rsx_vertex_ref*)(data+source_bytes);
  u32* occurrences=(u32*)(data+source_bytes*2);
  const char* verify=getenv("TB_DRAW_REMAP_VERIFY");
  if(verify && !strcmp(verify,"1")) {
   rsx_vertex_ref* fresh=malloc(source_bytes);rsx_vertex_remap check={0};u32 n=count;
   if(!fresh)return rsx_vertex_remap_build(remap,refs,count,unique);
   memcpy(fresh,refs,source_bytes);
   int ok=rsx_vertex_remap_build(&check,fresh,count,&n) && n==tb_remap_entries[i].unique &&
    !memcmp(fresh,output,(size_t)n*sizeof *fresh) && !memcmp(check.occurrence_to_unique,occurrences,(size_t)count*4);
   free(fresh);rsx_vertex_remap_destroy(&check);
   if(!ok){fprintf(stderr,"[rsx engine] index-remap verification failed\n");exit(87);}
  }
  if(remap->occurrence_capacity<count) {
   u32* next=realloc(remap->occurrence_to_unique,(size_t)count*4);
   if(!next)return rsx_vertex_remap_build(remap,refs,count,unique);
   remap->occurrence_to_unique=next;remap->occurrence_capacity=count;
  }
  *unique=tb_remap_entries[i].unique;
  memcpy(refs,output,(size_t)*unique*sizeof *refs);
  memcpy(remap->occurrence_to_unique,occurrences,(size_t)count*4);
  return 1;
 }
 unsigned slot=tb_remap_victim++%2;
 free(tb_remap_entries[slot].data);tb_remap_entries[slot].data=NULL;
 u8* data=malloc(source_bytes*2+(size_t)count*4);
 if(!data)return rsx_vertex_remap_build(remap,refs,count,unique);
 memcpy(data,refs,source_bytes);
 if(!rsx_vertex_remap_build(remap,refs,count,unique)){free(data);return 0;}
 memcpy(data+source_bytes,refs,(size_t)*unique*sizeof *refs);
 memcpy(data+source_bytes*2,remap->occurrence_to_unique,(size_t)count*4);
 tb_remap_entries[slot].data=data;tb_remap_entries[slot].count=count;tb_remap_entries[slot].unique=*unique;
 return 1;
}
