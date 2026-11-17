/* The host device, rather than a rounded usleep, paces ring consumption.
 * Never advance the guest cursor or notify its producer while the device
 * already has four blocks queued. A missing device uses monotonic time.
 * Windows devices commonly consume 480 frames every 10 ms. Refilling all
 * that space at once skips guest producer work between 256-frame periods;
 * keep the guest cursor paced at 48 kHz independently of those device bursts.
 */
#include "../host_services.h"
#ifdef _WIN32
static unsigned __stdcall audio_mix_thread_func(void* arg)
#else
static void* audio_mix_thread_func(void* arg)
#endif
{
    (void)arg;
    printf("[cellAudio] Mixing thread started\n");
#ifdef _WIN32
    /* Restore Windows audio scheduling lost when the SDK loop was replaced.
     * The render and guest workers must not delay this 5.333 ms deadline. */
    SetThreadPriority(GetCurrentThread(), THREAD_PRIORITY_HIGHEST);
    HMODULE audio_avrt=LoadLibraryW(L"avrt.dll");
    typedef HANDLE (WINAPI *audio_mmcss_begin_fn)(LPCWSTR, LPDWORD);
    typedef BOOL (WINAPI *audio_mmcss_end_fn)(HANDLE);
    audio_mmcss_begin_fn audio_mmcss_begin=audio_avrt ?
        (audio_mmcss_begin_fn)(void*)GetProcAddress(audio_avrt,"AvSetMmThreadCharacteristicsW") : NULL;
    audio_mmcss_end_fn audio_mmcss_end=audio_avrt ?
        (audio_mmcss_end_fn)(void*)GetProcAddress(audio_avrt,"AvRevertMmThreadCharacteristics") : NULL;
    DWORD audio_task=0;
    HANDLE audio_mmcss=audio_mmcss_begin && audio_mmcss_end ?
        audio_mmcss_begin(L"Pro Audio",&audio_task) : NULL;
    fprintf(stderr,"[audio-clock] Windows paced guest blocks; MMCSS=%s\n",audio_mmcss ? "on" : "unavailable");
    if (audio_backend_room() >= 0) {
        /* Prime the host, not the guest ring: notifications/cursor advances
         * still happen once per real 256-frame period. */
        const float silence[CELL_AUDIO_BLOCK_SAMPLES * 2] = {0};
        /* One padding block plus the first real block cover a 10 ms host
         * quantum, while leaving headroom for a paced producer. Filling the
         * entire queue here would immediately put the guest clock behind it. */
        audio_backend_submit(silence, CELL_AUDIO_BLOCK_SAMPLES);
    }
#endif
    u64 start=tb_host_now(),blocks=0;
    u64 stats_start=start,stats_blocks=0,stats_silent=0,stats_bursts=0,last_mix=0;
    const int rate=getenv("AUDIO_RATE")!=NULL,peak_on=getenv("AUDIO_PEAK")!=NULL;
    while (s_mix_thread_running) {
        const int room=audio_backend_room();
        if (room>=0 && room<CELL_AUDIO_BLOCK_SAMPLES) {audio_backend_wait();continue;}
        const u64 now=tb_host_now();
#ifdef _WIN32
        /* Even a late wake just before the next deadline must give the guest
         * producer a chance to run before consuming its following block. */
        if (last_mix && now-last_mix<CELL_AUDIO_BLOCK_SAMPLES*1000000000ull/CELL_AUDIO_SAMPLE_RATE/2) {
            tb_host_sleep_us(1000);continue;
        }
#endif
#ifdef _WIN32
        {
#else
        if (room<0) {
#endif
            const u64 due=start+blocks*CELL_AUDIO_BLOCK_SAMPLES*1000000000ull/CELL_AUDIO_SAMPLE_RATE;
            if (now<due) {tb_host_sleep_us(1000);continue;}
#ifdef _WIN32
            /* A late wake must not be followed by back-to-back guest reads.
             * Resume from now; never invent consumption for missed periods. */
            if (now-due>CELL_AUDIO_BLOCK_SAMPLES*1000000000ull/CELL_AUDIO_SAMPLE_RATE)
#else
            if (now-due>50000000ull)
#endif
                start=now-blocks*CELL_AUDIO_BLOCK_SAMPLES*1000000000ull/CELL_AUDIO_SAMPLE_RATE;
        }
        audio_mix_one_block();
        audio_dump_mix();
        audio_backend_submit(s_mix_buffer,CELL_AUDIO_BLOCK_SAMPLES);
        audio_notify_event_queues();
        ++blocks;++stats_blocks;
        if (last_mix && now-last_mix<1000000ull) ++stats_bursts;
        last_mix=now;
        float peak=0;
        if (rate || peak_on) {peak=audio_mix_peak();if (peak==0) ++stats_silent;}
        if ((rate || peak_on) && now-stats_start>=1000000000ull) {
            fprintf(stderr,"[audio-clock] %.2f blocks/s queue=%.2f ms silent=%.1f%% peak=%.4f bursts=%llu\n",
                stats_blocks*1e9/(now-stats_start),audio_backend_queued_samples()*1000.0/CELL_AUDIO_SAMPLE_RATE,
                stats_blocks?100.0*stats_silent/stats_blocks:0,peak,(unsigned long long)stats_bursts);
            stats_start=now;stats_blocks=stats_silent=stats_bursts=0;
        }
    }
#ifdef _WIN32
    if (audio_mmcss) audio_mmcss_end(audio_mmcss);
    if (audio_avrt) FreeLibrary(audio_avrt);
#endif
    printf("[cellAudio] Mixing thread stopped\n");
    return 0;
}
