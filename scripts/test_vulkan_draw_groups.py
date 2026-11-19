#!/usr/bin/env python3
"""Build and run an original actual-backend Vulkan lifetime/order regression."""
import os
import resource
import subprocess
import sys
from pathlib import Path
from port import ROOT

out=ROOT/'build-tests/vulkan-groups';out.mkdir(parents=True,exist_ok=True)
sdk=ROOT/'external/ps3recomp'
subprocess.run([sys.executable,str(ROOT/'scripts/instrument_sdk.py'),'--sdk',str(sdk),'--output',str(out/'overlay')],check=True)
(out/'fixture.c').write_text('#include "'+str(out/'overlay/libs/video/rsx_vulkan_backend.c')+'"\n#include "'+str(ROOT/'tests/vulkan_draw_groups.inc')+'"\n')
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
__thread const char* tb_wait_label; uint32_t tb_frame_budget_site(const char* l){return 0;} int tb_frame_budget_on(void){return 0;} uint64_t tb_frame_budget_now(void){return 0;} void tb_frame_budget_span(unsigned k,uint32_t a,uint64_t t0,uint64_t t1){}
void tb_frame_budget_count(uint32_t id,uint32_t amount){} void tb_frame_budget_mark(unsigned kind,uint32_t a,uint32_t b){}
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
 subprocess.run([str(out/'build/groups')],env=dict(env,TB_VK_FENCE_POLL='1'),check=True,timeout=40)
 subprocess.run([str(out/'build/groups')],env=dict(env,TB_VK_ASYNC='0'),check=True,timeout=40)
 print('PASS: the same fixture with serial (TB_VK_ASYNC=0) batch submission')
 legacy=subprocess.run([str(out/'build/groups')],env=dict(env,TB_VK_SUBMIT_MODE='serial'),capture_output=True,text=True,timeout=40)
 if legacy.returncode==0 or 'tb_group_open' not in legacy.stderr:raise AssertionError('Serial negative control did not reject queued-draw assertion')
 print('PASS: serial negative control rejects the queued-draw assertion')
