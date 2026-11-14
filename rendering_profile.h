#pragma once
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
uint64_t tb_render_clock(unsigned kind);
void tb_render_finish(unsigned kind, uint64_t start);
void tb_render_frame(void);
void tb_gameplay_event(unsigned kind, uint64_t serial);
typedef struct { unsigned kind; uint64_t start; } tb_render_scope;
static inline void tb_render_scope_end(tb_render_scope* scope) {
    if (scope->start) tb_render_finish(scope->kind, scope->start);
}
#ifdef __cplusplus
}
#endif
// GCC/Clang cleanup covers early returns in instrumented C and C++ functions.
#define TB_RENDER_SCOPE(kind) tb_render_scope tb_render_timer __attribute__((cleanup(tb_render_scope_end))) = {kind, tb_render_clock(kind)}
