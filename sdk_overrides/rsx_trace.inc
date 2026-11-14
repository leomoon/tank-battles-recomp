/* Opt-in frame-scoped RSX ingress diagnostics. Render-thread access only.
 * A request file contains a unique alphanumeric scene label. Capture starts
 * at the next guest flip notification and stops after three guest flip scopes.
 * Host refreshes do not terminate a command frame.
 * Guest memory is observed without forcing GPU readbacks or modifying it. */
static struct {
    const char* dir;
    int initialized;
    FILE* methods;
    char label[64], last[64];
    u32 frame, draw, remaining;
} tb_trace;

static int tb_trace_write(const char* path, const void* data, size_t size)
{
    FILE* f = fopen(path, "wb");
    if (!f) return 0;
    const int ok = fwrite(data, 1, size, f) == size;
    return fclose(f) == 0 && ok;
}

static void tb_trace_boundary(void)
{
    if (!tb_trace.initialized) {
        tb_trace.dir = getenv("TB_RSX_TRACE");
        tb_trace.initialized = 1;
    }
    if (!tb_trace.dir || !*tb_trace.dir) return;
    char path[4096];
    if (tb_trace.methods) {
        const int ok = !ferror(tb_trace.methods);
        const int closed = fclose(tb_trace.methods) == 0;
        tb_trace.methods = NULL;
        if (ok && closed) {
            snprintf(path,sizeof path,"%s/%s-%03u.done",tb_trace.dir,tb_trace.label,tb_trace.frame);
            tb_trace_write(path,"complete\n",9);
        }
        if (tb_trace.remaining) --tb_trace.remaining;
    }
    if (!tb_trace.remaining) {
        snprintf(path,sizeof path,"%s/request",tb_trace.dir);
        FILE* f = fopen(path,"r");
        char label[64] = {0};
        if (f) { const int n=fscanf(f,"%63s",label); fclose(f); if (n!=1) return; }
        if (!label[0] || !strcmp(label,tb_trace.last)) return;
        for (u32 i=0;label[i];++i)
            if (!((label[i]>='a'&&label[i]<='z')||(label[i]>='A'&&label[i]<='Z')||
                  (label[i]>='0'&&label[i]<='9')||label[i]=='-')) return;
        snprintf(path,sizeof path,"%s/%s-000.initial",tb_trace.dir,label);
        FILE* existing=fopen(path,"rb");
        if (existing) {fclose(existing);return;}
        strcpy(tb_trace.label,label);strcpy(tb_trace.last,label);
        tb_trace.remaining=3;tb_trace.frame=0;
    } else ++tb_trace.frame;
    tb_trace.draw=0;
    snprintf(path,sizeof path,"%s/%s-%03u.scope",tb_trace.dir,tb_trace.label,tb_trace.frame);
    FILE* meta=fopen(path,"w");
    if (meta) {fprintf(meta,"guest-flip buffer=%u host-presents=%u\n",g.last_flip_buffer,g.frames);fclose(meta);}
    snprintf(path,sizeof path,"%s/%s-%03u.initial",tb_trace.dir,tb_trace.label,tb_trace.frame);
    FILE* f=fopen(path,"wb");
    if (!f) { tb_trace.remaining=0;return; }
    fwrite(g.rsx.regs,1,sizeof g.rsx.regs,f);
    fwrite(g.rsx.vp,1,sizeof g.rsx.vp,f);
    fwrite(g.rsx.constants,1,sizeof g.rsx.constants,f);
    fclose(f);
    snprintf(path,sizeof path,"%s/%s-%03u.methods",tb_trace.dir,tb_trace.label,tb_trace.frame);
    tb_trace.methods=fopen(path,"wb");
}

static void tb_trace_method(u32 kind,u32 method,u32 arg)
{
    if (!tb_trace.methods) return;
    const u32 record[3]={kind,method,arg};
    fwrite(record,sizeof record,1,tb_trace.methods);
    if (kind || method!=0x1808 || arg) return;
    char path[4096];
    snprintf(path,sizeof path,"%s/%s-%03u-draw-%04u.state",tb_trace.dir,tb_trace.label,tb_trace.frame,tb_trace.draw);
    FILE* f=fopen(path,"wb");
    if (!f) return;
    fwrite(g.rsx.regs,1,sizeof g.rsx.regs,f);
    fwrite(g.rsx.vp,1,sizeof g.rsx.vp,f);
    fwrite(g.rsx.constants,1,sizeof g.rsx.constants,f);
    const u8* fp=NULL;u32 fp_bytes=0;
    eng_guest_programs(NULL,NULL,&fp,&fp_bytes);
    fwrite(&fp_bytes,4,1,f);
    if (fp && fp_bytes) fwrite(fp,1,fp_bytes,f);
    fclose(f);
    for (u32 unit=0;unit<16;++unit) {
        rsx_dsp_texture t;rsx_dsp_get_texture(&g.rsx,unit,&t);
        if (!t.enabled || !t.width || !t.height) continue;
        const u32 bytes=t.depth>1 ? eng_texture_span_3d(t.format,t.width,t.height,t.depth,t.mipmaps,t.pitch)
            : eng_texture_span(t.format,t.width,t.height,t.mipmaps,t.pitch,t.cubemap);
        const u8* raw=bytes && bytes<=(32u<<20) ? eng_guest_ptr(NULL,t.location,t.offset,bytes) : NULL;
        if (!raw) continue;
        u64 hash=14695981039346656037ull;
        for (u32 i=0;i<bytes;++i) {hash^=raw[i];hash*=1099511628211ull;}
        snprintf(path,sizeof path,"%s/memory-%016llx.raw",tb_trace.dir,(unsigned long long)hash);
        FILE* existing=fopen(path,"rb");
        if (existing) fclose(existing); else tb_trace_write(path,raw,bytes);
        snprintf(path,sizeof path,"%s/%s-%03u.resources",tb_trace.dir,tb_trace.label,tb_trace.frame);
        f=fopen(path,"a");
        if (f) {fprintf(f,"%u %u %u %u %u %016llx\n",tb_trace.draw,unit,t.location,t.offset,bytes,(unsigned long long)hash);fclose(f);}
    }
    ++tb_trace.draw;
}

void tb_rsx_transfer_trace(u32 subchannel,u32 method,u32 arg)
{
    tb_trace_method(subchannel+1,method,arg);
}
