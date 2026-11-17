// Minimal stand-in for `glslangValidator -V <in.comp> -o <out.spv>` built from
// the glslang libraries the runtime already links, for hosts without the
// glslang command-line tools (scripts/compile_depth_shader.py --glslang).
// Compute shaders only, default options: the SPIR-V must match what
// glslangValidator -V emits for the same glslang version (check with --check).
#include <glslang/Public/ResourceLimits.h>
#include <glslang/Public/ShaderLang.h>
#include <glslang/SPIRV/GlslangToSpv.h>

#include <cstdio>
#include <fstream>
#include <iterator>
#include <string>
#include <vector>

int main(int argc, char** argv) {
    if (argc != 3) {
        std::fprintf(stderr, "usage: glsl_spirv_tool <shader.comp> <out.spv>\n");
        return 2;
    }
    std::ifstream in(argv[1], std::ios::binary);
    const std::string source((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
    if (source.empty()) {
        std::fprintf(stderr, "cannot read %s\n", argv[1]);
        return 2;
    }
    glslang::InitializeProcess();
    int status = 1;
    {
        glslang::TShader shader(EShLangCompute);
        const char* text = source.c_str();
        const char* name = argv[1];
        shader.setStringsWithLengthsAndNames(&text, nullptr, &name, 1);
        shader.setEnvInput(glslang::EShSourceGlsl, EShLangCompute, glslang::EShClientVulkan, 100);
        shader.setEnvClient(glslang::EShClientVulkan, glslang::EShTargetVulkan_1_0);
        shader.setEnvTarget(glslang::EShTargetSpv, glslang::EShTargetSpv_1_0);
        const EShMessages messages = static_cast<EShMessages>(EShMsgSpvRules | EShMsgVulkanRules);
        glslang::TProgram program;
        if (!shader.parse(GetDefaultResources(), 100, false, messages)) {
            std::fprintf(stderr, "%s\n%s\n", shader.getInfoLog(), shader.getInfoDebugLog());
        } else {
            program.addShader(&shader);
            if (!program.link(messages)) {
                std::fprintf(stderr, "%s\n%s\n", program.getInfoLog(), program.getInfoDebugLog());
            } else {
                std::vector<unsigned int> spirv;
                glslang::GlslangToSpv(*program.getIntermediate(EShLangCompute), spirv);
                std::ofstream out(argv[2], std::ios::binary);
                out.write(reinterpret_cast<const char*>(spirv.data()), static_cast<std::streamsize>(spirv.size() * 4));
                status = out ? 0 : 1;
            }
        }
    }
    glslang::FinalizeProcess();
    return status;
}
