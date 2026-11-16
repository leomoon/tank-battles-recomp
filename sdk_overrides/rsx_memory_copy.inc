#include "depth_cost_trace.h"
/* Tank Battles uses NV0039 on subchannel 1 without SET_OBJECT commands.
 * These registers describe byte columns, not pixels or an endian conversion.
 * Keep this source in the port; instrument_sdk.py includes it in the pinned SDK. */
static struct {
    u32 src_dma, dst_dma, src, dst, src_pitch, dst_pitch, columns, rows, format;
} tb_m2mf;

static int tb_m2mf_range(u32 dma, u32 offset, u32 width, u32 rows, s32 pitch)
{
    if (dma != 0xFEED0000u && dma != 0xFEED0001u) return 0;
    const int64_t last = (int64_t)offset + (int64_t)(rows - 1u) * pitch;
    const int64_t lo = last < offset ? last : offset;
    const uint64_t hi = (uint64_t)(last > offset ? last : offset) + width;
    if (lo < 0 || hi > 0x100000000ull) return 0;
    if (dma == 0xFEED0000u && hi > s_config.localSize) return 0;
    return 1;
}

static void tb_m2mf_copy(void)
{
    TB_DEPTH_COST("nv0039-copy",(uint64_t)tb_m2mf.columns*tb_m2mf.rows,0);
    const u32 n = tb_m2mf.columns, rows = tb_m2mf.rows;
    const u32 si = tb_m2mf.format & 0xffu, di = (tb_m2mf.format >> 8) & 0xffu;
    if (!n || !rows) return;
    const uint64_t bytes = (uint64_t)n * rows;
    const uint64_t sw = (uint64_t)(n - 1u) * si + 1u;
    const uint64_t dw = (uint64_t)(n - 1u) * di + 1u;
    const s32 sp = (s32)tb_m2mf.src_pitch, dp = (s32)tb_m2mf.dst_pitch;
    if (!si || si > 4 || !di || di > 4 || bytes > (256u << 20) ||
        sw > (256u << 20) || dw > (256u << 20) ||
        !tb_m2mf_range(tb_m2mf.src_dma, tb_m2mf.src, (u32)sw, rows, sp) ||
        !tb_m2mf_range(tb_m2mf.dst_dma, tb_m2mf.dst, (u32)dw, rows, dp)) {
        static unsigned logs;
        if (logs++ < 8) fprintf(stderr, "[NV0039] invalid transfer geometry or DMA context\n");
        return;
    }
    const u32 sl = tb_m2mf.src_dma == 0xFEED0000u ? RSX_LOCATION_LOCAL : RSX_LOCATION_MAIN;
    const u32 dl = tb_m2mf.dst_dma == 0xFEED0000u ? RSX_LOCATION_LOCAL : RSX_LOCATION_MAIN;
    /* Snapshot the input before writing: source and destination may overlap,
     * including different rows of one pitched allocation. */
    u8* snapshot = (u8*)malloc((size_t)bytes);
    u8* row = (u8*)malloc((size_t)(sw > dw ? sw : dw));
    if (!snapshot || !row) { free(snapshot); free(row); return; }
    for (u32 y = 0; y < rows; ++y) {
        const u32 off = (u32)((int64_t)tb_m2mf.src + (int64_t)y * sp);
        if (rsx_draw_engine_enabled())
            rsx_draw_engine_guest_read(sl, off, (u32)sw, ((u32)sw + 3u) / 4u, 1);
        if (!rsx_tiled_read(&s_gcm_mem, sl, off, row, (u32)sw)) goto done;
        for (u32 x = 0; x < n; ++x) snapshot[(size_t)y * n + x] = row[(size_t)x * si];
    }
    for (u32 y = 0; y < rows; ++y) {
        const u32 off = (u32)((int64_t)tb_m2mf.dst + (int64_t)y * dp);
        if (di != 1 && !rsx_tiled_read(&s_gcm_mem, dl, off, row, (u32)dw)) goto done;
        for (u32 x = 0; x < n; ++x) row[(size_t)x * di] = snapshot[(size_t)y * n + x];
        if (!rsx_tiled_write(&s_gcm_mem, dl, off, row, (u32)dw)) goto done;
        if (rsx_draw_engine_enabled())
            rsx_draw_engine_guest_wrote(dl, off, (u32)dw, ((u32)dw + 3u) / 4u, 1);
    }
    { static const char* trace; static int initialized; static unsigned count;
      if (!initialized) { trace = getenv("TB_RSX_TEXTURE_CAPTURE"); initialized = 1; }
      if (trace && count++ < 64)
          fprintf(stderr, "[NV0039] copied src=%x:%08x dst=%x:%08x columns=%u rows=%u format=%04x\n",
                  sl, tb_m2mf.src, dl, tb_m2mf.dst, n, rows, tb_m2mf.format); }
done:
    free(row);
    free(snapshot);
}

static void tb_m2mf_method(u32 method, u32 data)
{
    switch (method) {
    case 0x0184: tb_m2mf.src_dma = data; break;
    case 0x0188: tb_m2mf.dst_dma = data; break;
    case 0x030C: tb_m2mf.src = data; break;
    case 0x0310: tb_m2mf.dst = data; break;
    case 0x0314: tb_m2mf.src_pitch = data; break;
    case 0x0318: tb_m2mf.dst_pitch = data; break;
    case 0x031C: tb_m2mf.columns = data; break;
    case 0x0320: tb_m2mf.rows = data; break;
    case 0x0324: tb_m2mf.format = data; break;
    case 0x0328: tb_m2mf_copy(); break;
    }
}
