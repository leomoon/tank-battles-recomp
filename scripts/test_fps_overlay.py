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
#include <algorithm>
#include <array>
#include <cassert>
#include <cmath>
#include <thread>
#include <vector>
int main(){
 FpsSample s;s.update(0,100,200);assert(!s.ready);
 s.update(1000000000,130,260);assert(s.ready&&s.game_fps==30&&s.present_fps==60);
 assert(std::abs(s.frame_ms-1000.0/30)<1e-8);
 s.update(1250000000,131,261);assert(s.game_fps==30); // No noisy partial update.
 s.update(2000000000,160,260);assert(s.game_fps==30&&s.present_fps==0);
 s.update(3000000000,160,320);assert(s.game_fps==0&&s.frame_ms==0&&s.present_fps==60);
 s.update(1,0,0);assert(!s.ready); // Reset a clock/counter discontinuity.
 std::vector<uint32_t> pixels(TB_OVERLAY_BUFFER_SIZE/4+1,0xa5a5a5a5);
 tb_overlay_region region{};
 assert(tb_draw_overlay(pixels.data(),1280,720,0,&region));
 assert(region.x==8 && region.y==8 && region.width==288 && region.height==64);
 assert(std::count(pixels.begin(),pixels.begin()+region.width*region.height,0xfff0f0f0u)>100);
 assert(pixels[region.width*region.height]==0xa5a5a5a5);
 const auto original=pixels;
 assert(!tb_draw_overlay(pixels.data(),1,720,0,&region));assert(pixels==original);
 tb_fps_toggle();assert(!tb_draw_overlay(pixels.data(),1280,720,0,&region));assert(pixels==original);
 tb_fps_toggle();assert(tb_draw_overlay(pixels.data(),1280,720,0,&region));
 tb_fps_toggle();
 const std::array<std::string,6> friends{"PlayerB","FriendC","FriendD","FriendE","FriendF","FriendG"};
 tb_panel_show(InvitePicker::friends,friends,1);
 assert(tb_draw_overlay(pixels.data(),1280,720,0,&region));
 assert(region.width==448 && region.height==268 && region.x==416 && region.y==226);
 assert(pixels[57*region.width+20]==0xff2b1d12u);
 assert(pixels[85*region.width+20]==0xff66c7ffu); // Selected row accent.
 assert(pixels[region.width*region.height]==0xa5a5a5a5);
 assert(tb_draw_overlay(pixels.data(),320,180,1,&region));
 assert(region.x==48 && region.y==23 && region.width==224 && region.height==134);
 assert(pixels[0]==0xffffc766u); // Swapchain channel order.
 assert(tb_draw_overlay(pixels.data(),3840,2160,0,&region));
 assert(region.x==1248 && region.y==678 && region.width==1344 && region.height==804);
 assert(pixels.back()==0xa5a5a5a5);
 tb_panel_show(InvitePicker::friends,friends,5);
 assert(tb_draw_overlay(pixels.data(),1280,720,0,&region));
 assert(region.height==156);
 assert(tb_panel_input(0x4000)==PanelPadInput::captured);assert(!tb_panel_take_input()); // Opening button is still held.
 assert(tb_panel_input(0)==PanelPadInput::captured);assert(tb_panel_input(0x4000)==PanelPadInput::captured);assert(tb_panel_take_input()==0x4000);
 assert(tb_panel_input(0x4000)==PanelPadInput::captured);assert(!tb_panel_take_input());
 tb_panel_close();assert(tb_panel_input(0x4000)==PanelPadInput::captured);
 assert(tb_panel_input(0)==PanelPadInput::passthrough);assert(tb_panel_input(0x4000)==PanelPadInput::passthrough);
 assert(!tb_draw_overlay(pixels.data(),1280,720,0,&region));
 tb_panel_show(InvitePicker::invitations,{},0);
 assert(tb_panel_input(0)==PanelPadInput::captured);
 assert(tb_panel_input(0x2000)==PanelPadInput::back);
 assert(tb_panel_take_input()==0x2000);
 assert(tb_panel_input(0x2000)==PanelPadInput::captured);
 tb_panel_close();
 assert(tb_panel_input(0x2000)==PanelPadInput::captured);
 assert(tb_panel_input(0)==PanelPadInput::passthrough);
 tb_show_invite_toast("PlayerB");
 assert(!tb_draw_overlay(pixels.data(),1280,720,0,&region));
 std::vector<uint32_t> toast(TB_NOTIFICATION_BUFFER_SIZE/4+1,0xa5a5a5a5);
 tb_overlay_region toast_region{};
 assert(tb_draw_notification_toast(toast.data(),1280,720,0,&toast_region));
 assert(toast_region.width==368 && toast_region.height==86 && toast_region.x==20 && toast_region.y==20);
 assert(toast[toast_region.width*toast_region.height]==0xa5a5a5a5);
 assert(tb_panel_input(0x4000)==PanelPadInput::passthrough);
 const std::array<std::string,1> invites{"PlayerB"};
 tb_panel_show(InvitePicker::invitations,invites,0);
 assert(tb_draw_overlay(pixels.data(),1280,720,0,&region));
 assert(region.width==448 && region.height==156 && region.x==416 && region.y==282);
 assert(pixels[57*region.width+20]==0xff66c7ffu); // Invite is still selectable.
 assert(tb_draw_notification_toast(toast.data(),1280,720,0,&toast_region));
 assert(toast_region.x==20 && toast_region.y==20); // Inbox and toast keep independent positions.
 tb_panel_show(InvitePicker::invitations,friends,0);
 assert(tb_draw_overlay(pixels.data(),3840,2160,0,&region));
 assert(region.width==1344 && region.height==804);
 assert(pixels.back()==0xa5a5a5a5);
 assert(tb_draw_notification_toast(toast.data(),3840,2160,0,&toast_region));
 assert(toast_region.x==60 && toast_region.y==60 && toast_region.width==1104 && toast_region.height==258);
 assert(toast.back()==0xa5a5a5a5);
 assert(tb_draw_overlay(pixels.data(),320,134,1,&region));
 assert(region.width==224 && region.height==134); // Keep the picker usable in a short window.
 std::this_thread::sleep_for(std::chrono::seconds(4));
 assert(!tb_draw_notification_toast(toast.data(),1280,720,0,&toast_region));
 assert(tb_draw_overlay(pixels.data(),1280,720,0,&region));
 assert(region.width==448 && region.height==268 && region.x==416 && region.y==226);
 tb_panel_close();
 assert(!tb_draw_overlay(pixels.data(),1280,720,0,&region));
 tb_show_network_toast("ACCOUNT ALREADY SIGNED IN","CLOSE OTHER SESSION AND RETRY");
 tb_clear_invite_toast(); // The offline inbox reset must preserve the connection error.
 std::fill(toast.begin(),toast.end(),0xa5a5a5a5);
 assert(tb_draw_notification_toast(toast.data(),1280,720,0,&toast_region));
 assert(toast_region.x==20 && toast_region.y==20 && toast_region.width==448 && toast_region.height==86);
 assert(toast[toast_region.width*toast_region.height]==0xa5a5a5a5);
 tb_show_invite_toast("PlayerB");
 tb_clear_invite_toast();
 assert(!tb_draw_notification_toast(toast.data(),1280,720,0,&toast_region));
 tb_panel_show(InvitePicker::rooms,friends,0);
 assert(tb_draw_overlay(pixels.data(),1280,720,0,&region));
 assert(region.width==448 && region.height==290 && region.x==416 && region.y==215);
 assert(tb_panel_input(0)==PanelPadInput::captured);
 assert(tb_panel_input(0x8000)==PanelPadInput::captured);
 assert(tb_panel_take_input()==0x8000);
 assert(tb_panel_input(0)==PanelPadInput::captured);
 assert(tb_panel_input(0x2000)==PanelPadInput::back);
 assert(tb_panel_take_input()==0x2000);
 assert(tb_panel_input(0x2000)==PanelPadInput::captured);
 tb_panel_show(InvitePicker::searching_rooms,{},0);
 assert(tb_draw_overlay(pixels.data(),1280,720,0,&region));
 assert(region.height==178);
 tb_panel_close();
}
''')
subprocess.run(['c++','-std=c++20','-O2','-I'+str(ROOT),str(out/'test.cpp'),str(ROOT/'fps_overlay.cpp'),str(ROOT/'player_settings.cpp'),str(ROOT/'rendering_profile.cpp'),'-pthread','-o',str(out/'test')],check=True)
subprocess.run([str(out/'test')],env=dict(os.environ,TB_FPS_OVERLAY='1'),check=True)
print('PASS: cadence, overlay toggle, picker input, simultaneous toast and expiry')
