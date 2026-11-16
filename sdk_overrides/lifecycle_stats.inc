/* Opt-in bounded renderer resource census, sampled on its owning walker. */
static void tb_lifecycle_stats(void)
{
    static int enabled=-1;static uint64_t last;static unsigned samples;
    if(enabled<0)enabled=getenv("TB_LIFECYCLE_STATS")!=NULL;
    if(!enabled || samples>=400)return;
    uint64_t now=SDL_GetTicks64();if(samples && now-last<10000)return;
    unsigned objects=0,pipelines=0,variants=0;
    for(unsigned i=0;i<VK_ENG_MAX_OBJ;i++)objects+=s_eobj[i].kind!=0;
    for(unsigned i=0;i<VK_ENG_MAX_PIPE;i++)if(s_epipe[i].used){pipelines++;for(unsigned j=0;j<VK_ENG_TOPOLOGIES;j++)variants+=s_epipe[i].variant[j]!=VK_NULL_HANDLE;}
    fprintf(stderr,"[lifecycle] ms=%llu objects=%u pipelines=%u variants=%u framebuffers=%u\n",(unsigned long long)now,objects,pipelines,variants,s_e2.fb_count);
    last=now;samples++;
}
