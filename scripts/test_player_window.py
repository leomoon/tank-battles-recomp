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
 vk_window_open("preferences fixture");assert(s_vk.window);
 int w,h;SDL_GetWindowSize(s_vk.window,&w,&h);assert(w==1366 && h==768);
 SDL_SetWindowSize(s_vk.window,900,600);assert(rsx_vulkan_backend_pump_messages()==0);
 int saved_w=0,saved_h=0;assert(tb_settings_window_size(&saved_w,&saved_h));assert(saved_w==900 && saved_h==600);
 key(SDLK_F11,0);assert(SDL_GetWindowFlags(s_vk.window)&SDL_WINDOW_FULLSCREEN_DESKTOP);
 assert(tb_settings_window_size(&saved_w,&saved_h));assert(saved_w==900 && saved_h==600);
 key(SDLK_RETURN,KMOD_ALT);assert(!(SDL_GetWindowFlags(s_vk.window)&SDL_WINDOW_FULLSCREEN_DESKTOP));
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
'''.replace('@ROOT@',str(ROOT)))
subprocess.run(['cmake','-S',str(out),'-B',str(out/'build'),'-G','Ninja'],check=True)
subprocess.run(['cmake','--build',str(out/'build'),'--parallel','2'],check=True)
settings=out/'settings.ini';settings.write_text('window_width = 1366\nwindow_height = 768\nfullscreen = 0\n')
env={k:v for k,v in os.environ.items() if not k.startswith(('TB_','PS3RECOMP_'))}
env.update(SDL_VIDEODRIVER='dummy',TB_SETTINGS_FILE=str(settings),PS3RECOMP_VK_WINDOW='1')
subprocess.run([str(out/'build/window_settings')],env=env,check=True,timeout=15)
text=settings.read_text();assert 'fullscreen = 0' in text and 'window_width = 900' in text
print('PASS: generated backend restores size, saves resize/fullscreen toggles and preserves guest render size (SDL dummy window).')
