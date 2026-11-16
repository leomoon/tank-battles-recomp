#pragma once
/* Linux-only, bounded, opt-in inclusive wall costs. Requested bytes include
 * cache hits and failed requests; they are not claimed as actual GPU traffic. */
#if defined(__linux__)
#include <time.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
typedef struct { uint64_t calls, bytes, ns, last_report, reports; } tb_depth_cost_totals;
typedef struct {
    tb_depth_cost_totals* totals;
    const char* path;
    uint64_t start, bytes, frame;
} tb_depth_cost_scope;
static inline uint64_t tb_depth_cost_now(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC,&t);
    return (uint64_t)t.tv_sec*1000000000ull+(uint64_t)t.tv_nsec;
}
static inline int tb_depth_cost_enabled(void) {
    static int enabled=-1;
    if(enabled<0)enabled=getenv("TB_DEPTH_PATH_TRACE")!=NULL;
    return enabled;
}
static inline void tb_depth_cost_end(tb_depth_cost_scope* s) {
    if(!s->start)return;
    tb_depth_cost_totals* t=s->totals;
    const uint64_t now=tb_depth_cost_now();
    t->calls++;t->bytes+=s->bytes;t->ns+=now-s->start;
    /* Time-aligned cumulative snapshots, bounded to ten minutes per path. */
    if(t->reports<600 && (t->calls<=4 || now-t->last_report>=1000000000ull)) {
        t->reports++;t->last_report=now;
        fprintf(stderr,"[depth-cost] path=%s frame=%llu calls=%llu requested_bytes=%llu inclusive_ns=%llu monotonic_ns=%llu\n",
            s->path,(unsigned long long)s->frame,(unsigned long long)t->calls,
            (unsigned long long)t->bytes,(unsigned long long)t->ns,(unsigned long long)now);
    }
}
#define TB_DEPTH_COST(path,bytes,frame) \
    static tb_depth_cost_totals tb_depth_cost_sum; \
    tb_depth_cost_scope tb_depth_cost_timer __attribute__((cleanup(tb_depth_cost_end))) = \
        {&tb_depth_cost_sum,path,tb_depth_cost_enabled()?tb_depth_cost_now():0,bytes,frame}
#else
#define TB_DEPTH_COST(path,bytes,frame) ((void)0)
#endif
