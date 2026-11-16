/* When does a guest-visible RSX publication (back-end/texture label 0x1D70/
 * 0x1D74, NV406E semaphore release, SET_REFERENCE 0x0050, NOTIFY 0x0104,
 * GET_REPORT 0x1800) have to wait for the host GPU?
 *
 * The guest uses these values to learn that the RSX has consumed the commands
 * and data before them, so it may overwrite its vertex, index, constant and
 * texture memory or recycle the command buffer. In this port the walker
 * consumes every one of those inputs on the CPU while it processes the FIFO:
 * vertex fetch, index remap, constants and texture decode copy guest bytes
 * into host rings and staging before the draw is even recorded, and the GPU
 * never reads or writes guest memory directly. Every GPU result the guest can
 * observe reaches guest memory through an explicit readback that the walker
 * itself performs synchronously (colour/depth materialization, transfers), so
 * it is already in guest memory when a later publication is processed.
 *
 * Waiting for GPU completion before writing the value is therefore not
 * observable by the guest; it only serialized the walker on the GPU about 85
 * times per frame (8 ms of a 34 ms frame in default Battle). Publishing at
 * processing time is what the RSX itself does relative to its command fetch,
 * and what RPCS3 does with its default (non-strict) rendering settings, with
 * which this title renders correctly.
 *
 * TB_LABEL_SYNC=1 restores the previous flush-and-wait before every
 * publication, for A/B comparison and as a fallback. */
#include <stdint.h>
#include <stdlib.h>
int rsx_draw_engine_enabled(void);
void rsx_draw_engine_flush(void);

static int tb_label_sync(void)
{
    static int sync = -1;
    if (sync < 0) {
        const char* e = getenv("TB_LABEL_SYNC");
        sync = e && *e == '1';
    }
    return sync;
}

/* Called before a guest-visible publication. */
#include "../frame_budget.h"
/* Standalone FIFO fixtures link this file without the port's timeline. */
__attribute__((weak)) int tb_frame_budget_on(void) { return 0; }
__attribute__((weak)) uint64_t tb_frame_budget_now(void) { return 0; }
__attribute__((weak)) void tb_frame_budget_span(unsigned kind, uint32_t a, uint64_t t0, uint64_t t1)
{ (void)kind; (void)a; (void)t0; (void)t1; }
static void tb_publication_flush_at(uint32_t method)
{
    if (tb_frame_budget_on()) {
        const uint64_t t = tb_frame_budget_now();
        tb_frame_budget_span(TB_FT_PUBLISH, method, t, t);
    }
    if (tb_label_sync() && rsx_draw_engine_enabled()) rsx_draw_engine_flush();
}
static void tb_publication_flush(void) { tb_publication_flush_at(0x006C); }   /* NV406E release */
