#pragma once
/* Opt-in per-frame timeline for the frame-budget tool (scripts/frame_budget.py).
 *
 * TB_FRAME_BUDGET=<path> records the toolkit's PS3_FTRACE timeline into <path>,
 * but only for TB_FRAME_BUDGET_SECONDS (default 20) starting
 * TB_FRAME_BUDGET_DELAY seconds (default 5) after the process receives SIGUSR2
 * on POSIX or consumes the file named by TB_FRAME_BUDGET_TRIGGER on either OS,
 * so a measurement helper can place the window inside verified live gameplay.
 * The port adds its own span kinds beside the toolkit's (ps3_ftrace.h 1..15). */
#include <stdint.h>

#define TB_FT_CLOCK_SLEEP 16  /* frame clock sleeping between ticks              */
#define TB_FT_PUT_ADVANCE 17  /* instant: the guest's FIFO put moved, a = put    */
#define TB_FT_FENCE_WAIT  18  /* walker blocked on a GPU fence                   */
#define TB_FT_DRAIN_CALL  19  /* one cellGcm_rsx_process_fifo call, a = put seen */
#define TB_FT_VBLANK      20  /* instant: one vblank beat was ticked             */
#define TB_FT_PRESENT_FINISH 21 /* present thread: wait, swapchain blit, present  */
#define TB_FT_BATCH_SUBMIT 22 /* instant: a draw batch was submitted, a = 1 idle */
#define TB_FT_PUBLISH      23 /* instant: a label/reference/report was published, a = method */
#define TB_FT_COUNT        24 /* instant: work counter, a = TB_FC_* id, b = amount           */
#define TB_FT_GPU_CMD      25 /* instant: one command buffer's GPU execution, b = ns         */
#define TB_FT_GPU_PASS     26 /* instant: one render pass's GPU time, a = w<<16|h, b = ns     */
#define TB_FT_SCALE_STATE  27 /* instant: presented source; a=requested %, b=effective % */
#define TB_FT_SCALE_SCENE  28 /* instant: scene candidate host extent; a=width, b=height */
#define TB_FT_SCALE_OUTPUT 29 /* instant: actual present-source extent; a=width, b=height */
#define TB_FT_COLOR_STORE 30 /* completed guest color write; a=location<<31|surface offset, b=bytes */
#define TB_FT_DEPTH_STORE 31 /* completed guest depth write; same payload */
#define TB_FT_DEPTH_ALIAS 32 /* newly converted GPU depth alias; same payload, not a guest write */

/* Per-frame work counters (TB_FT_COUNT). Ids are part of the trace format:
 * append only, and keep scripts/frame_budget.py COUNTER_NAMES in step. */
enum {
    TB_FC_QUEUE_SUBMIT = 1, TB_FC_ALLOC_MEMORY, TB_FC_CREATE_IMAGE, TB_FC_CREATE_BUFFER,
    TB_FC_RENDER_PASS, TB_FC_BARRIER, TB_FC_DESC_ALLOC, TB_FC_DESC_WRITE,
    TB_FC_CLEAR_IMAGE, TB_FC_COPY_IMAGE_TO_BUFFER, TB_FC_COPY_BUFFER_TO_IMAGE, TB_FC_COPY_IMAGE,
    TB_FC_BLIT_IMAGE, TB_FC_CREATE_PIPELINE, TB_FC_DRAW, TB_FC_DISPATCH,
    TB_FC_CREATE_FRAMEBUFFER, TB_FC_CREATE_SHADER, TB_FC_SYNC_SUBMIT_WAIT,
    TB_FC_TEXTURE_HASH_BYTES, TB_FC_TEXTURE_UPLOAD_TEXELS, TB_FC_GUEST_COLOR_READ_BYTES,
    TB_FC_GUEST_DEPTH_READ, TB_FC_SPARSE_HASH_BYTES,
    TB_FC_LOGICAL_DRAW, TB_FC_LOGICAL_SCENE_CANDIDATE, TB_FC_LOGICAL_DEPTH_ONLY,
    TB_FC_ENGINE_NATIVE_DRAW, TB_FC_ENGINE_SCALED_DRAW,
    TB_FC_COLOR_READBACK, TB_FC_COLOR_READBACK_BYTES,
    TB_FC_SCALE_SKIP_ALIAS, TB_FC_SCALE_SKIP_STENCIL, TB_FC_SCALE_RESOURCE_REJECT,
    TB_FC_SCALE_PRESENT_NATIVE, TB_FC_SCALE_PRESENT_SCALED, TB_FC_SCALE_FALLBACK_PRESENT,
    TB_FC_DEPTH_MATERIALIZE, TB_FC_DEPTH_MATERIALIZE_BYTES,
    TB_FC_GPU_DEPTH_CONVERT, TB_FC_GPU_DEPTH_CONVERT_BYTES,
    TB_FC_COLOR_MATERIALIZE, TB_FC_COLOR_MATERIALIZE_BYTES,
};

#ifdef __cplusplus
extern "C" {
#endif
/* Before any toolkit hook runs: translate TB_FRAME_BUDGET into PS3_FTRACE. */
void tb_frame_budget_init(void);
int tb_frame_budget_on(void);
uint64_t tb_frame_budget_now(void);
/* The wait site a fence-wait span is attributed to: a label the instrumented
 * call site sets just before it may wait, reported by number with the
 * number-to-label table printed once per label as "[frame-budget] site". */
#ifdef __cplusplus
extern thread_local const char* tb_wait_label;
#else
extern __thread const char* tb_wait_label;
#endif
uint32_t tb_frame_budget_site(const char* label);
void tb_frame_budget_span(unsigned kind, uint32_t a, uint64_t t0, uint64_t t1);
/* An instant record carrying two payload words; a no-op unless recording. */
void tb_frame_budget_mark(unsigned kind, uint32_t a, uint32_t b);
/* Adds amount to counter id for the current frame; a cheap branch when off. */
void tb_frame_budget_count(uint32_t id, uint32_t amount);
#ifdef __cplusplus
}
#endif
