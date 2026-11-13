#!/usr/bin/env python3
"""Run the SDK SPIR-V translation/cache tests without a GPU or game dump."""
import subprocess
from port import ROOT, tool


def main():
    sdk = ROOT / 'external/ps3recomp'
    directory = ROOT / 'build-tests/vulkan-shaders'
    directory.mkdir(parents=True, exist_ok=True)
    fixture = (sdk / 'libs/video/tests/test_shader_spirv.c').read_text()
    # Adapt only the include location and scratch directory. Assertions stay intact.
    adaptations = {
        '#include "../rsx_shader_spirv.h"': '#include "' + str(sdk / 'libs/video/rsx_shader_spirv.h') + '"',
        '/tmp/ps3recomp_spirv_cache_XXXXXX': str(directory / 'spirv-cache-XXXXXX'),
    }
    for original, replacement in adaptations.items():
        if fixture.count(original) != 1:
            raise SystemExit('SDK shader-test fixture anchor mismatch: ' + original)
        fixture = fixture.replace(original, replacement, 1)
    (directory / 'fixture.c').write_text(fixture)
    (directory / 'CMakeLists.txt').write_text('''cmake_minimum_required(VERSION 3.20)
project(VulkanShaderChecks LANGUAGES C CXX)
set(CMAKE_CXX_STANDARD 20)
find_package(glslang CONFIG REQUIRED)
add_executable(shader_checks fixture.c "@SDK@/libs/video/rsx_shader_spirv.cpp")
target_include_directories(shader_checks PRIVATE "@SDK@/include" "@SDK@/libs/video")
target_compile_definitions(shader_checks PRIVATE PS3RECOMP_HAVE_SPIRV_TRANSLATION=1)
target_link_libraries(shader_checks PRIVATE glslang::glslang glslang::SPIRV)
if(TARGET glslang::glslang-default-resource-limits)
    target_link_libraries(shader_checks PRIVATE glslang::glslang-default-resource-limits)
endif()
'''.replace('@SDK@', str(sdk)))
    build = directory / 'build'
    subprocess.run([tool('cmake'), '-S', str(directory), '-B', str(build), '-G', 'Ninja'], check=True)
    subprocess.run([tool('cmake'), '--build', str(build), '--parallel', '2'], check=True)
    subprocess.run([str(build / 'shader_checks')], cwd=directory, check=True)


if __name__ == '__main__':
    main()
