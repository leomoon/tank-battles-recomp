#!/usr/bin/env python3
"""Build and run exact Z16 GPU conversion against the actual Vulkan backend."""
import os
import resource
import subprocess
import sys
from pathlib import Path
from port import ROOT

subprocess.run([sys.executable,str(ROOT/'scripts/compile_depth_shader.py'),'--check'],check=True)
out=ROOT/'build-tests/vulkan-depth16';out.mkdir(parents=True,exist_ok=True)
sdk=ROOT/'external/ps3recomp'
subprocess.run([sys.executable,str(ROOT/'scripts/instrument_sdk.py'),'--sdk',str(sdk),'--output',str(out/'overlay')],check=True)
(out/'fixture.c').write_text('#include "'+str(out/'overlay/libs/video/rsx_vulkan_backend.c')+'"\n#include "'+str(ROOT/'tests/vulkan_depth16.inc')+'"\n')
(out/'CMakeLists.txt').write_text('''cmake_minimum_required(VERSION 3.20)
project(DrawGroups LANGUAGES C CXX)
set(CMAKE_CXX_STANDARD 20)
find_package(glslang CONFIG REQUIRED)
find_package(SDL2 REQUIRED)
add_executable(groups fixture.c "@SDK@/libs/video/rsx_shader_spirv.cpp" "@SDK@/libs/video/rsx_texture_layout.c" hooks.c)
target_include_directories(groups PRIVATE "@ROOT@" "@SDK@/include" "@SDK@/libs/video")
target_compile_definitions(groups PRIVATE PS3RECOMP_HAVE_SPIRV_TRANSLATION=1)
target_compile_options(groups PRIVATE -ffunction-sections -fdata-sections)
target_link_options(groups PRIVATE -Wl,--gc-sections)
target_link_libraries(groups PRIVATE glslang::glslang glslang::SPIRV SDL2::SDL2 m dl pthread)
if(TARGET glslang::glslang-default-resource-limits)
 target_link_libraries(groups PRIVATE glslang::glslang-default-resource-limits)
endif()
'''.replace('@ROOT@',str(ROOT)).replace('@SDK@',str(sdk)))
(out/'hooks.c').write_text('''#include "execution_policy_api.h"
uint64_t tb_render_clock(unsigned k){return 0;} void tb_render_finish(unsigned k,uint64_t s){} void tb_render_frame(void){}
void tb_vulkan_event(unsigned k,uint32_t a,uint32_t b,uint32_t c){} void tb_frame_history(const void* p,uint32_t w,uint32_t h,uint32_t n){}
void tb_fps_present(void){} uint64_t tb_fps_guest_serial(void){return 0;} void tb_gameplay_event(unsigned k,uint64_t n){}
int tb_fps_overlay(void* p,uint32_t w,uint32_t h,uint32_t r,uint32_t s){return 0;} void tb_fps_toggle(void){}
void tb_keyboard_down(uint32_t s){} uint32_t tb_keyboard_take(void){return 0;}
''')
subprocess.run(['cmake','-S',str(out),'-B',str(out/'build'),'-G','Ninja'],check=True)
subprocess.run(['cmake','--build',str(out/'build'),'--parallel','2'],check=True)
if '--build-only' not in sys.argv:
 resource.setrlimit(resource.RLIMIT_CORE,(0,0))
 env={k:v for k,v in os.environ.items() if not k.startswith(('TB_','PS3RECOMP_VK_','VK_DRAW_'))}
 subprocess.run([str(out/'build/groups')],env=env,check=True,timeout=40)
 print('PASS: actual GPU depth16 fixture')
