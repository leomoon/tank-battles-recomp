#!/usr/bin/env python3
"""Test generated window preference hooks with real SDL's dummy video driver.

Only Vulkan window creation/extensions are substituted; no compositor or GPU
acceptance is claimed. Event handling, fullscreen state and settings IO are real.
"""
from pathlib import Path
import os
import subprocess
import sys
from port import ROOT

subprocess.run([sys.executable,str(ROOT/'scripts/test_vulkan_draw_groups.py'),'--build-only'],check=True)
out=ROOT/'build-tests/player-window';out.mkdir(parents=True,exist_ok=True)
backend=ROOT/'build-tests/vulkan-groups/overlay/libs/video/rsx_vulkan_backend.c'
(out/'fixture.c').write_text(r'''
#include <SDL.h>
#include <SDL_vulkan.h>
static SDL_Window* fixture_window(const char* t,int x,int y,int w,int h,Uint32 flags) {
 return SDL_CreateWindow(t,x,y,w,h,flags & ~SDL_WINDOW_VULKAN);
}
static SDL_bool fixture_extensions(SDL_Window* window,unsigned* count,const char** names) {
 (void)window;*count=1;if(names)names[0]="VK_KHR_surface";return SDL_TRUE;
}
#define SDL_CreateWindow fixture_window
#define SDL_Vulkan_GetInstanceExtensions fixture_extensions
#include "@BACKEND@"
#undef SDL_CreateWindow
#undef SDL_Vulkan_GetInstanceExtensions
#include "player_settings.h"
#include <assert.h>
static void key(int sym,int mods) {
 SDL_Event event;memset(&event,0,sizeof event);event.type=SDL_KEYDOWN;event.key.keysym.sym=sym;event.key.keysym.mod=mods;
 assert(SDL_PushEvent(&event)==1);assert(rsx_vulkan_backend_pump_messages()==0);
}
int main(void) {
 tb_settings_init();s_vk.width=1280;s_vk.height=720;
 s_vk.sc_extent.width=1920;s_vk.sc_extent.height=1200;
 const vk_rect fit=vk_window_fit();
 assert(fit.w==1920 && fit.h==1080 && fit.x==0 && fit.y==60);
 vk_window_open("preferences fixture");assert(s_vk.window);
 int expected_w=1280,expected_h=720;
 tb_window_initial_size(&expected_w,&expected_h);
 int w,h;SDL_GetWindowSize(s_vk.window,&w,&h);
 const int initially_full=(SDL_GetWindowFlags(s_vk.window)&SDL_WINDOW_FULLSCREEN_DESKTOP)!=0;
 assert(initially_full==atoi(getenv("TEST_EXPECT_FULLSCREEN")));
 if(initially_full) key(SDLK_F11,0);
 SDL_GetWindowSize(s_vk.window,&w,&h);assert(w==expected_w && h==expected_h);
 int max_w=0,max_h=0;SDL_GetWindowMaximumSize(s_vk.window,&max_w,&max_h);assert(!max_w && !max_h);
 assert(SDL_GetWindowFlags(s_vk.window)&SDL_WINDOW_RESIZABLE);
 const int scale=tb_settings_render_scale();
 int huge_w=16000,huge_h=9000;tb_window_fit_size(0,&huge_w,&huge_h);
 SDL_Rect bounds;assert(!SDL_GetDisplayUsableBounds(0,&bounds));assert(huge_w<=bounds.w && huge_h<=bounds.h);
 SDL_SetWindowSize(s_vk.window,900,600);assert(rsx_vulkan_backend_pump_messages()==0);
 SDL_Delay(250);assert(rsx_vulkan_backend_pump_messages()==0);
 int saved_w=0,saved_h=0;assert(tb_settings_window_size(&saved_w,&saved_h));assert(saved_w==900 && saved_h==600);
 key(SDLK_F4,0);assert(tb_settings_antialiasing()==1);
 key(SDLK_F4,0);assert(tb_settings_antialiasing()==0);
 key(SDLK_F11,0);assert(SDL_GetWindowFlags(s_vk.window)&SDL_WINDOW_FULLSCREEN_DESKTOP);
 assert(tb_settings_window_size(&saved_w,&saved_h));assert(saved_w==900 && saved_h==600);
 key(SDLK_RETURN,KMOD_ALT);assert(!(SDL_GetWindowFlags(s_vk.window)&SDL_WINDOW_FULLSCREEN_DESKTOP));
 SDL_Delay(250);assert(rsx_vulkan_backend_pump_messages()==0);
 SDL_GetWindowSize(s_vk.window,&w,&h);assert(w==900 && h==600);
 assert(tb_settings_window_size(&saved_w,&saved_h));assert(saved_w==900 && saved_h==600);
 SDL_MinimizeWindow(s_vk.window);assert(rsx_vulkan_backend_pump_messages()==0);
 SDL_RestoreWindow(s_vk.window);assert(rsx_vulkan_backend_pump_messages()==0);
 SDL_Delay(250);assert(rsx_vulkan_backend_pump_messages()==0);
 assert(tb_settings_render_scale()==scale);
 assert(s_vk.width==1280 && s_vk.height==720); // Guest render size is unchanged.
 vk_window_drop("fixture complete");return 0;
}
'''.replace('@BACKEND@',str(backend)))
(out/'hooks.c').write_text((ROOT/'build-tests/vulkan-groups/hooks.c').read_text())
(out/'CMakeLists.txt').write_text('''cmake_minimum_required(VERSION 3.20)
project(PlayerWindow LANGUAGES C CXX)
set(CMAKE_CXX_STANDARD 20)
find_package(SDL2 REQUIRED)
add_executable(window_settings fixture.c hooks.c "@ROOT@/player_settings.cpp")
target_include_directories(window_settings PRIVATE "@ROOT@" "@ROOT@/external/ps3recomp/include" "@ROOT@/external/ps3recomp/libs/video")
target_compile_options(window_settings PRIVATE -ffunction-sections -fdata-sections)
target_link_options(window_settings PRIVATE -Wl,--gc-sections)
target_link_libraries(window_settings PRIVATE SDL2::SDL2 m dl pthread)
add_executable(display_window "@ROOT@/tests/player_window.c" "@ROOT@/player_settings.cpp")
target_include_directories(display_window PRIVATE "@ROOT@")
target_link_libraries(display_window PRIVATE SDL2::SDL2 pthread)
'''.replace('@ROOT@',str(ROOT)))
subprocess.run(['cmake','-S',str(out),'-B',str(out/'build'),'-G','Ninja'],check=True)
subprocess.run(['cmake','--build',str(out/'build'),'--parallel','2'],check=True)
settings=out/'settings.ini';settings.write_text('window_width = 1366\nwindow_height = 768\nfullscreen = 0\n')
env={k:v for k,v in os.environ.items() if not k.startswith(('TB_','PS3RECOMP_'))}
env.update(SDL_VIDEODRIVER='dummy',TB_SETTINGS_FILE=str(settings),PS3RECOMP_VK_WINDOW='1',TEST_EXPECT_FULLSCREEN='0')
subprocess.run([str(out/'build/window_settings')],env=env,check=True,timeout=15)
text=settings.read_text();assert 'fullscreen = 0' in text and 'window_width = 900' in text
print('PASS: generated backend restores size, saves resize/fullscreen toggles and preserves guest render size (SDL dummy window).')

for preset in (720,1080,1440):
    settings.write_text(f'window_preset = {preset}\nwindow_width = 900\nwindow_height = 600\nrender_scale = 150\nfullscreen = 1\n')
    subprocess.run([str(out/'build/window_settings')],env=dict(env,TEST_EXPECT_FULLSCREEN='1'),check=True,timeout=15)
    assert 'render_scale = 150' in settings.read_text()
settings.write_text('window_width = 16000\nwindow_height = 9000\nfullscreen = 1\n')
subprocess.run([str(out/'build/window_settings')],env=dict(env,PS3RECOMP_FULLSCREEN='0',PS3RECOMP_VK_FULLSCREEN='1'),check=True,timeout=15)
# Relaunch uses the stabilized saved windowed dimensions after the override is removed.
subprocess.run([str(out/'build/window_settings')],env=env,check=True,timeout=15)
print('PASS: preset priority, monitor bounds, borderless startup, explicit windowed override, restore and independent render scale.')

if '--desktop-driver' in sys.argv:
    driver = sys.argv[sys.argv.index('--desktop-driver') + 1]
    for full in (0,1):
        settings.write_text(f'window_preset = 720\nfullscreen = {full}\nrender_scale = 200\n')
        desktop = dict(env, SDL_VIDEODRIVER=driver)
        subprocess.run([str(out/'build/display_window')],env=desktop,check=True,timeout=30)
        subprocess.run([str(out/'build/display_window')],env=desktop,check=True,timeout=30)
