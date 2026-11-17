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
#ifdef __cplusplus
}
#endif
