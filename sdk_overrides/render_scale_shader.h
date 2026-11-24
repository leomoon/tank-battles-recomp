#pragma once

#include <stddef.h>
#include <string.h>

/* Apply after the SDK's sample-grid conversion. fp_alpha.w is zero on
 * Native draws and the exact host-to-guest sample ratio on scaled draws. */
static inline int tb_scale_wpos(char* hlsl, size_t capacity)
{
    static const char native[] = "floor(input.position.xy / float2(";
    static const char corrected[] =
        "floor(input.position.xy * (fp_alpha.w > 0.0 ? fp_alpha.w : 1.0) / float2(";
    char* at = strstr(hlsl, native);
    if (!at || !strstr(hlsl, "_rsx_wpos(input.position)")) return 0;
    const size_t growth = sizeof corrected - sizeof native;
    if (strlen(hlsl) + growth >= capacity) return -1;
    char* rest = at + sizeof native - 1;
    memmove(rest + growth, rest, strlen(rest) + 1);
    memcpy(at, corrected, sizeof corrected - 1);
    return 1;
}
