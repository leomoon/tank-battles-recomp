#!/usr/bin/env python3
"""Check distinct guest/present rates, stalled sampling, raster bounds and toggle."""
import os
from pathlib import Path
import subprocess
from port import ROOT
out=ROOT/'build-tests/fps-overlay';out.mkdir(parents=True,exist_ok=True)
(out/'test.cpp').write_text(r'''
#include "fps_overlay.h"
#include "execution_policy_api.h"
#include <cassert>
#include <cmath>
#include <vector>
int main(){
 FpsSample s;s.update(0,100,200);assert(!s.ready);
 s.update(1000000000,130,260);assert(s.ready&&s.game_fps==30&&s.present_fps==60);
 assert(std::abs(s.frame_ms-1000.0/30)<1e-8);
 s.update(1250000000,131,261);assert(s.game_fps==30); // No noisy partial update.
 s.update(2000000000,160,260);assert(s.game_fps==30&&s.present_fps==0);
 s.update(3000000000,160,320);assert(s.game_fps==0&&s.frame_ms==0&&s.present_fps==60);
 s.update(1,0,0);assert(!s.ready); // Reset a clock/counter discontinuity.
 constexpr unsigned w=144,h=32,pitch=w*4+16;
 std::vector<unsigned char> pixels(pitch*h+32,0xa5);
 assert(tb_fps_overlay(pixels.data(),w,h,pitch,1)==1);
 unsigned ink=0;
 for(unsigned y=0;y<h;y++){
  for(unsigned x=0;x<w;x++){assert(pixels[y*pitch+x*4+3]==255);ink+=pixels[y*pitch+x*4]==240;}
  for(unsigned x=w*4;x<pitch;x++)assert(pixels[y*pitch+x]==0xa5);
 }
 assert(ink>100);
 for(unsigned i=pitch*h;i<pixels.size();i++)assert(pixels[i]==0xa5);
 const auto original=pixels;
 assert(!tb_fps_overlay(pixels.data(),1,h,pitch,1));assert(pixels==original);
 tb_fps_toggle();assert(!tb_fps_overlay(pixels.data(),w,h,pitch,1));assert(pixels==original);
 tb_fps_toggle();assert(tb_fps_overlay(pixels.data(),w,h,pitch,1));
 tb_fps_toggle();
 tb_panel_show("INVITE FRIEND","PlayerB","ARROWS Z OK X BACK");
 assert(tb_fps_overlay(pixels.data(),w,h,pitch,1));
 assert(tb_panel_input(0x4000));assert(!tb_panel_take_input()); // Opening button is still held.
 assert(tb_panel_input(0));assert(tb_panel_input(0x4000));assert(tb_panel_take_input()==0x4000);
 assert(tb_panel_input(0x4000));assert(!tb_panel_take_input());
 tb_panel_close();assert(!tb_panel_input(0x4000));
 assert(!tb_fps_overlay(pixels.data(),w,h,pitch,1));
}
''')
subprocess.run(['c++','-std=c++20','-O2','-I'+str(ROOT),str(out/'test.cpp'),str(ROOT/'fps_overlay.cpp'),str(ROOT/'player_settings.cpp'),str(ROOT/'rendering_profile.cpp'),'-pthread','-o',str(out/'test')],check=True)
subprocess.run([str(out/'test')],env=dict(os.environ,TB_FPS_OVERLAY='1'),check=True)
print('PASS: game and presentation cadence, frame time, sampling, clipping, padding and toggle')
