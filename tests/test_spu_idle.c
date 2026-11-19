#include "../spu_idle.c"
#include <assert.h>
#include <pthread.h>
#include <stdio.h>
#include <time.h>

void (*g_spu_idle_poll_hook)(spu_context*);
int yz_lockstep_enabled(void) { return 0; }

static spu_context context;
static unsigned char local_store[SPU_LS_SIZE];
static int action, notify, writer_ready;
static uint64_t published_ns, maximum_latency_ns[4];

static uint64_t monotonic_ns(void)
{
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    return (uint64_t)now.tv_sec * 1000000000u + now.tv_nsec;
}

static void* produce(void* unused)
{
    (void)unused;
    __atomic_store_n(&writer_ready, 1, __ATOMIC_RELEASE);
    struct timespec delay = {0, 100000};
    nanosleep(&delay, NULL);
    published_ns = monotonic_ns();
    if (action == 0) spu_channel_write(&context.ch_in_mbox, 0x12345678);
    if (action == 1)
        __atomic_store_n((uint32_t*)(local_store + 0x124), 1, __ATOMIC_RELEASE);
    if (action == 2) context.stop_request = 1;
    if (action == 3) context.event_status = context.event_mask = 1;
    if (notify) {
        /* Same publication order and lock as spu_ch_wake. */
        AcquireSRWLockExclusive((SRWLOCK*)&context.ch_wait_lock);
        ReleaseSRWLockExclusive((SRWLOCK*)&context.ch_wait_lock);
        WakeAllConditionVariable((CONDITION_VARIABLE*)&context.ch_wait_cv);
    }
    return NULL;
}

int main(void)
{
    spu_context_init(&context, 0);
    context.ls = local_store;
    context.pc = 0x248;
    context.image_id = 0x12D69DD7;
    context.gpr[1]._u32[0] = 0x100;
    setenv("TB_RAW_SPU_IDLE", "0", 1);
    tank_battles_spu_idle_init();
    assert(!g_spu_idle_poll_hook);
    setenv("TB_RAW_SPU_IDLE", "1", 1);
    tank_battles_spu_idle_init();
    assert(g_spu_idle_poll_hook);
    assert(tank_battles_spu_idle(&context));
    context.gpr[27]._u32[0] = 4;
    assert(tank_battles_spu_idle(&context));
    context.gpr[27]._u32[0] = 8;
    assert(!tank_battles_spu_idle(&context));
    context.gpr[27]._u32[0] = 0;
    context.ls = context.ls_store;
    assert(!tank_battles_spu_idle(&context));
    context.ls = local_store;
    context.image_id++;
    assert(!tank_battles_spu_idle(&context));
    context.image_id--;
    context.pc = 0x240;
    assert(!tank_battles_spu_idle(&context));
    context.pc = 0x248;
    context.gpr[1]._u32[0] = SPU_LS_SIZE;
    assert(!tank_battles_spu_idle(&context));
    context.gpr[1]._u32[0] = 0x100;
    context.rcv_evt_n = 1;
    assert(!tank_battles_spu_idle(&context));
    context.rcv_evt_n = 0;
    for (unsigned slot = 0; slot < 2; ++slot) {
        local_store[0x120 + slot * 4] = 1;
        assert(!tank_battles_spu_idle(&context));
        local_store[0x120 + slot * 4] = 0;
    }
    spu_channel_write(&context.ch_in_mbox, 42);
    g_spu_idle_poll_hook(&context);
    assert(spu_channel_read(&context.ch_in_mbox) == 42);
    for (int i = 0; i < 65; ++i) g_spu_idle_poll_hook(&context);
    /* Work is published around the predicate-to-wait boundary. Local-store
     * writes deliberately omit a notification and depend on the timed recheck. */
    for (int round = 0; round < 32; ++round) {
        for (action = 0; action < 4; ++action) {
            notify = action != 1;
            writer_ready = 0;
            context.stop_request = context.event_status = context.event_mask = 0;
            memset(local_store + 0x120, 0, 8);
            pthread_t writer;
            assert(pthread_create(&writer, NULL, produce, NULL) == 0);
            while (!__atomic_load_n(&writer_ready, __ATOMIC_ACQUIRE)) {}
            for (int i = 0; i < 1000 && tank_battles_spu_idle(&context); ++i)
                g_spu_idle_poll_hook(&context);
            assert(!tank_battles_spu_idle(&context));
            const uint64_t observed_ns = monotonic_ns();
            pthread_join(writer, NULL);
            const uint64_t latency = observed_ns - published_ns;
            if (latency > maximum_latency_ns[action]) maximum_latency_ns[action] = latency;
            if (action == 0)
                assert(spu_channel_read(&context.ch_in_mbox) == 0x12345678);
        }
    }
    printf("Maximum fixture observation latency (us), mailbox=%llu local-store=%llu stop=%llu event=%llu\n",
           (unsigned long long)(maximum_latency_ns[0] / 1000),
           (unsigned long long)(maximum_latency_ns[1] / 1000),
           (unsigned long long)(maximum_latency_ns[2] / 1000),
           (unsigned long long)(maximum_latency_ns[3] / 1000));
    return 0;
}
