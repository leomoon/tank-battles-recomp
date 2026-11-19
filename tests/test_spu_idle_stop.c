/* Reuse the real raw-SPU fixture's VM, MMIO helpers and runtime link stubs. */
#define main raw_fixture_main
#include "../external/ps3recomp/runtime/spu/tests/test_spu_raw_runcntl_stop.c"
#undef main
#include "../spu_idle.c"
uint32_t spu_rchcnt(spu_context*, uint32_t);

static void idle_worker(spu_context* ctx)
{
    ctx->pc = 0x248;
    ctx->gpr[1]._u32[0] = 0x100;
    ctx->gpr[27]._u32[0] = 0;
    spu_ls_write32(ctx, 0x120, 0);
    spu_ls_write32(ctx, 0x124, 0);
    __atomic_store_n(&s_parked, 1, __ATOMIC_RELEASE);
    while (spu_rchcnt(ctx, SPU_RdInMbox) == 0) {}
    abort(); /* Stop must unwind this worker, preserving the stopped status. */
}

int main(void)
{
    if (raw_fixture_main()) return 1;
    setenv("TB_RAW_SPU_IDLE", "1", 1);
    tank_battles_spu_idle_init();
    memset(s_prog[0], 0x75, sizeof s_prog[0]);
    memcpy(vm_base + G_PROG, s_prog[0], sizeof s_prog[0]);
    spu_workload_register_img(spu_workload_fingerprint(s_prog[0], sizeof s_prog[0]),
                             idle_worker, 0x12D69DD7, "idle fixture");
    const uint32_t base = SPU_RAW_BASE;
    for (int repeat = 0; repeat < 8; ++repeat) {
        s_parked = 0;
        proxy_load(0, 0);
        w32(base + SPU_RAW_NPC, 0);
        w32(base + SPU_RAW_RUNCNTL, RUNCNTL_RUN);
        for (int i = 0; i < 2000 && !__atomic_load_n(&s_parked, __ATOMIC_ACQUIRE); ++i)
            nap();
        for (int i = 0; i < 5; ++i) nap();
        CHECK(s_parked && started(0), "idle worker did not start");
        w32(base + SPU_RAW_RUNCNTL, RUNCNTL_STOP);
        CHECK(!started(0), "idle worker did not leave on stop");
        CHECK(be32_load(base + SPU_RAW_STATUS) == SPU_STATUS_STOPPED,
              "idle worker reported a stop code");
        proxy_load(0, 1);
        w32(base + SPU_RAW_NPC, 0);
        w32(base + SPU_RAW_RUNCNTL, RUNCNTL_RUN);
        for (int i = 0; i < 2000 && (started(0) || s_ran_b != repeat + 2); ++i)
            nap();
        CHECK(s_ran_b == repeat + 2 && !started(0), "replacement program failed");
    }
    return g_fails ? 1 : 0;
}
