/* Exercise the translator actually shipped in the production runtime archive. */
#include "rsx_shader_spirv.h"
#include "rsx_fp_decompiler.h"
#include "../../sdk_overrides/render_scale_shader.h"
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
    for (unsigned bounds = 0; bounds < 2; ++bounds) {
        char hlsl[4096] =
            "Texture2D    rsx_tex[16] : register(t0);\n"
            "cbuffer F : register(b1) {\n"
            "    float4 fp_alpha;\n};\n"
            "struct PSInput { float4 position : SV_Position; };\n"
            "#define _rsx_wpos(p) float4((p).x + fp_alpha.z, "
            "(fp_alpha.y != 0 ? fp_alpha.y - (p).y : (p).y) + fp_alpha.z, (p).z, (p).w)\n"
            "float4 main(PSInput input) : SV_TARGET {\n"
            "    return _rsx_wpos(input.position);\n}\n";
        u32 count = 0;
        if (rsx_fp_apply_wpos_grid(hlsl, sizeof hlsl, 2, 2) != 1 ||
            tb_scale_wpos(hlsl, sizeof hlsl) != 1 ||
            (bounds && rsx_fp_apply_depth_bounds(hlsl, sizeof hlsl, 0) != 1) ||
            (bounds && strstr(hlsl, "float4 fp_dbounds;") > strstr(hlsl, "float4 fp_scale;"))) {
            fprintf(stderr, "FAIL: scale XY/depth-bounds uniform layout\n");
            return 1;
        }
        if (rsx_hlsl_to_spirv(hlsl, RSX_SHADER_STAGE_FRAGMENT, spirv, 16384, &count, log, sizeof log)
                || count < 5 || spirv[0] != 0x07230203u) {
            fprintf(stderr, "FAIL: scaled WPOS/depth-bounds translation (%u): %s\n", bounds, log);
            return 1;
        }
    }
    puts("PASS: production HLSL translation, scale XY WPOS and optional depth-bounds layout");
    return 0;
}
