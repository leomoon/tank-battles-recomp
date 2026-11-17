/* WASAPI-style 10 ms device consumption, using the production mixer loop.
 * SDL still opens its dummy device; these private hooks model only its queue.
 */
static atomic_uint_fast64_t tb_burst_start, tb_burst_submitted;
static atomic_uint tb_burst_notifications, tb_burst_fast;
static uint64_t tb_burst_last;

static Uint32 tb_fixture_audio_size(SDL_AudioDeviceID device)
{
    (void)device;
    uint64_t start = atomic_load(&tb_burst_start);
    if (!start) return 0;
    uint64_t consumed = ((tb_host_now() - start) / 10000000ull) * 480;
    uint64_t submitted = atomic_load(&tb_burst_submitted);
    return submitted > consumed ? (Uint32)((submitted - consumed) * 2 * sizeof(float)) : 0;
}

static int tb_fixture_audio_submit(SDL_AudioDeviceID device, const void* data, Uint32 bytes)
{
    (void)device; (void)data;
    if (!atomic_load(&tb_burst_start)) atomic_store(&tb_burst_start, tb_host_now());
    atomic_fetch_add(&tb_burst_submitted, bytes / (2 * sizeof(float)));
    return 0;
}

static void tb_fixture_audio_observe(void)
{
    uint64_t now = tb_host_now();
    /* Ignore startup priming; measure steady producer notification cadence. */
    unsigned count = atomic_fetch_add(&tb_burst_notifications, 1);
    if (count > 8 && now - tb_burst_last < 1000000ull)
        atomic_fetch_add(&tb_burst_fast, 1);
    tb_burst_last = now;
}

static int tb_fixture_audio_check(void)
{
    unsigned count = atomic_load(&tb_burst_notifications);
    unsigned fast = atomic_load(&tb_burst_fast);
    printf("burst-device: %u notifications, %u spaced under 1 ms\n", count, fast);
    /* A 256-frame/48 kHz guest needs 5.333 ms periods, even if the host
     * consumes 480 frames together. A refill burst skips producer work. */
    return count < 100 || fast * 20 > count;
}
