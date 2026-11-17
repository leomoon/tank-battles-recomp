#pragma once
// Narrow host primitives shared by diagnostics and the audio-clock adapter.
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#ifdef _WIN32
#include <windows.h>
#include <process.h>
static inline void tb_host_write(const char* data, size_t size) {
    HANDLE output = GetStdHandle(STD_ERROR_HANDLE);
    while (size) {
        DWORD done = 0;
        if (!WriteFile(output, data, (DWORD)size, &done, NULL) || !done) break;
        data += done; size -= done;
    }
}
static inline uint64_t tb_host_now(void) {
    LARGE_INTEGER now, frequency;
    QueryPerformanceCounter(&now); QueryPerformanceFrequency(&frequency);
    return (uint64_t)(now.QuadPart / frequency.QuadPart) * 1000000000ull +
           (uint64_t)(now.QuadPart % frequency.QuadPart) * 1000000000ull / frequency.QuadPart;
}
static inline void tb_host_sleep_us(unsigned us) { Sleep((us + 999) / 1000); }
static inline void tb_host_env(const char* name, const char* value, int replace) {
    if (replace || !getenv(name)) _putenv_s(name, value);
}
#else
#include <errno.h>
#include <stdlib.h>
#include <time.h>
#include <unistd.h>
static inline void tb_host_write(const char* data, size_t size) {
    while (size) {
        ssize_t done = write(2, data, size);
        if (done < 0) { if (errno == EINTR) continue; break; }
        data += done; size -= (size_t)done;
    }
}
static inline uint64_t tb_host_now(void) {
    struct timespec value; clock_gettime(CLOCK_MONOTONIC, &value);
    return (uint64_t)value.tv_sec * 1000000000ull + value.tv_nsec;
}
static inline void tb_host_sleep_us(unsigned us) { usleep(us); }
static inline void tb_host_env(const char* name, const char* value, int replace) { setenv(name, value, replace); }
#endif
