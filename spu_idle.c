/* Bounded parking for Tank Battles' identified two-slot raw SPU worker. */
#include "spu_context.h"
#include "spu_lockstep.h"
#include "win32_compat.h"
#include <stdlib.h>
#include <string.h>

extern void (*g_spu_idle_poll_hook)(spu_context*);

static int tank_battles_spu_idle(const spu_context* ctx)
{
    if ((uint32_t)ctx->image_id != 0x12D69DD7u || ctx->pc != 0x248 ||
        ctx->ls == ctx->ls_store || !ctx->ls ||
        (ctx->gpr[27]._u32[0] != 0 && ctx->gpr[27]._u32[0] != 4))
        return 0;
    const uint32_t sp = ctx->gpr[1]._u32[0];
    if ((sp & 15u) || sp > SPU_LS_SIZE - 48u || ctx->stop_request ||
        ctx->rcv_evt_n || ctx->ch_in_mbox.count ||
        (ctx->event_status & ctx->event_mask))
        return 0;
    /* Only zero/nonzero matters. Both words are aligned and may be changed
     * directly through the PPU's raw local-store mapping without a wake. */
    return __atomic_load_n((const uint32_t*)(ctx->ls + sp + 32), __ATOMIC_ACQUIRE) == 0 &&
           __atomic_load_n((const uint32_t*)(ctx->ls + sp + 36), __ATOMIC_ACQUIRE) == 0;
}

static void tank_battles_spu_poll(spu_context* ctx)
{
    static _Thread_local unsigned idle_polls;
    if (!tank_battles_spu_idle(ctx) || yz_lockstep_enabled()) {
        idle_polls = 0;
        return;
    }
    /* Allow short gaps between commands to complete without scheduling. */
    if (idle_polls < 64) {
        ++idle_polls;
        return;
    }
    AcquireSRWLockExclusive((SRWLOCK*)&ctx->ch_wait_lock);
    if (tank_battles_spu_idle(ctx))
        /* Mailbox, signal and stop producers use spu_ch_wake and this lock.
         * Direct local-store writes and DMA are covered by the 1 ms recheck. */
        SleepConditionVariableSRW((CONDITION_VARIABLE*)&ctx->ch_wait_cv,
                                  (SRWLOCK*)&ctx->ch_wait_lock, 1, 0);
    ReleaseSRWLockExclusive((SRWLOCK*)&ctx->ch_wait_lock);
}

void tank_battles_spu_idle_init(void)
{
    const char* setting = getenv("TB_RAW_SPU_IDLE");
#ifdef __linux__
    const int enabled = !setting || strcmp(setting, "0") != 0;
#else
    const int enabled = setting && strcmp(setting, "1") == 0;
#endif
    g_spu_idle_poll_hook = enabled ? tank_battles_spu_poll : NULL;
}
