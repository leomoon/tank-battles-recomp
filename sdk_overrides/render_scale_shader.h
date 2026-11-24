#pragma once

#include <stddef.h>
#include <string.h>

/* Convert host raster positions before the SDK's sample-grid and window
 * origin/pixel-center conversion. fp_scale.xy holds independent guest/host
 * ratios; zero selects identity for Native draws of the shared shader. */
static inline int tb_scale_wpos(char* hlsl, size_t capacity)
{
    static const char native[] = "floor(input.position.xy / float2(";
    static const char corrected[] =
        "floor(input.position.xy * float2(fp_scale.x > 0.0 ? fp_scale.x : 1.0, "
        "fp_scale.y > 0.0 ? fp_scale.y : 1.0) / float2(";
    static const char alpha[] = "float4 fp_alpha;";
    static const char uniform[] = "\n    float4 fp_scale;\n";
    if (!hlsl || !capacity) return -1;
    size_t length = 0;
    while (length < capacity && hlsl[length]) ++length;
    if (length == capacity) return -1;
    char* at = strstr(hlsl, native);
    if (!at || !strstr(hlsl, "_rsx_wpos(input.position)")) return 0;
    char* cb = strstr(hlsl, alpha);
    if (!cb || cb >= at || strstr(hlsl, "fp_scale")) return -1;
    const size_t growth = sizeof corrected - sizeof native;
    if (growth + sizeof uniform - 1 >= capacity - length) return -1;
    char* rest = at + sizeof native - 1;
    memmove(rest + growth, rest, strlen(rest) + 1);
    memcpy(at, corrected, sizeof corrected - 1);
    /* A later SDK depth-bounds transform inserts fp_dbounds immediately
     * after fp_alpha, keeping fp_scale after that optional vector. */
    char* bounds = strstr(hlsl, "float4 fp_dbounds;");
    cb = bounds && bounds < at ? bounds + sizeof "float4 fp_dbounds;" - 1 : cb + sizeof alpha - 1;
    memmove(cb + sizeof uniform - 1, cb, strlen(cb) + 1);
    memcpy(cb, uniform, sizeof uniform - 1);
    return 1;
}
