/* The host device, rather than a rounded usleep, paces ring consumption.
 * Never advance the guest cursor or notify its producer while the device
 * already has four blocks queued. A missing device uses monotonic time. */
#include "../host_services.h"
#ifdef _WIN32
static unsigned __stdcall audio_mix_thread_func(void* arg)
#else
static void* audio_mix_thread_func(void* arg)
#endif
{
    (void)arg;
    printf("[cellAudio] Mixing thread started\n");
    u64 start=tb_host_now(),blocks=0;
    u64 stats_start=start,stats_blocks=0,stats_silent=0;
    const int rate=getenv("AUDIO_RATE")!=NULL,peak_on=getenv("AUDIO_PEAK")!=NULL;
    while (s_mix_thread_running) {
        const int room=audio_backend_room();
        if (room>=0 && room<CELL_AUDIO_BLOCK_SAMPLES) {audio_backend_wait();continue;}
        const u64 now=tb_host_now();
        if (room<0) {
            const u64 due=start+blocks*CELL_AUDIO_BLOCK_SAMPLES*1000000000ull/CELL_AUDIO_SAMPLE_RATE;
            if (now<due) {tb_host_sleep_us(1000);continue;}
            if (now-due>50000000ull)
                start=now-blocks*CELL_AUDIO_BLOCK_SAMPLES*1000000000ull/CELL_AUDIO_SAMPLE_RATE;
        }
        audio_mix_one_block();
        audio_dump_mix();
        audio_backend_submit(s_mix_buffer,CELL_AUDIO_BLOCK_SAMPLES);
        audio_notify_event_queues();
        ++blocks;++stats_blocks;
        float peak=0;
        if (rate || peak_on) {peak=audio_mix_peak();if (peak==0) ++stats_silent;}
        if ((rate || peak_on) && now-stats_start>=1000000000ull) {
            fprintf(stderr,"[audio-clock] %.2f blocks/s queue=%.2f ms silent=%.1f%% peak=%.4f\n",
                stats_blocks*1e9/(now-stats_start),audio_backend_queued_samples()*1000.0/CELL_AUDIO_SAMPLE_RATE,
                stats_blocks?100.0*stats_silent/stats_blocks:0,peak);
            stats_start=now;stats_blocks=stats_silent=0;
        }
    }
    printf("[cellAudio] Mixing thread stopped\n");
    return 0;
}
