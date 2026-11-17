/* Exercise the translator actually shipped in the production runtime archive. */
#include "rsx_shader_spirv.h"
#include <stdio.h>

int main(void)
{
    if (!rsx_hlsl_to_spirv_available()) {
        fprintf(stderr, "FAIL: production runtime contains the disabled shader compiler stub\n");
        return 1;
    }
    const char* shaders[] = {
        "float4 main(float4 p : POSITION) : SV_Position { return p; }",
        "float4 main() : SV_Target0 { return float4(1,0,0,1); }"
    };
    const int stages[] = {RSX_SHADER_STAGE_VERTEX, RSX_SHADER_STAGE_FRAGMENT};
    u32 spirv[16384];
    char log[4096];
    for (unsigned i = 0; i < 2; ++i) {
        u32 count = 0;
        if (rsx_hlsl_to_spirv(shaders[i], stages[i], spirv, 16384, &count, log, sizeof log)
                || count < 5 || spirv[0] != 0x07230203u) {
            fprintf(stderr, "FAIL: production stage %u translation: %s\n", i, log);
            return 1;
        }
    }
    puts("PASS: production runtime translates vertex and fragment HLSL to SPIR-V");
    return 0;
}
