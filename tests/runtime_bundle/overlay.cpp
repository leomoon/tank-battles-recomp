// Exercise the production raster and input bridge without a window or game.
#include "fps_overlay.h"
#include "execution_policy_api.h"
#include <algorithm>
#include <array>
#include <cassert>
#include <cmath>
#include <fstream>
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
 assert(region.x+region.width==1280-8 && region.y==8 && region.width==34 && region.height==26);
 assert(std::count(pixels.begin(),pixels.begin()+region.width*region.height,0xfff0f0f0u)>0);
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
 assert(tb_draw_notification_toast(toast.data(),1280,720,0,&toast_region)); // Connection error stays visible.
 std::this_thread::sleep_for(std::chrono::seconds(4));
 assert(!tb_draw_notification_toast(toast.data(),1280,720,0,&toast_region));
 tb_show_friend_request_toast("RequestA");
 tb_show_friend_request_toast("RequestA"); // Duplicate notices must not restart or queue the same toast.
 assert(tb_friend_request_toast_visible());
 tb_show_invite_toast("PlayerB");
 tb_show_friend_request_toast("RequestB");
 tb_clear_invite_toast(); // Preserve both request toasts.
 assert(tb_friend_request_toast_visible());
 std::this_thread::sleep_for(std::chrono::seconds(4));
 assert(tb_friend_request_toast_visible()); // RequestB advances after RequestA expires.
 tb_show_invite_toast("PlayerC");
 tb_clear_friend_request_toasts();
 assert(!tb_friend_request_toast_visible());
 assert(tb_draw_notification_toast(toast.data(),1280,720,0,&toast_region)); // Invitation was queued, not overwritten.
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
 FriendsPanelView manager;
 manager.account="Captain_01234567";manager.endpoint="play.lmx.to:31313";manager.connected=true;
 manager.rows={"PlayerB  ONLINE","FriendC  OFFLINE","FriendD  ONLINE","FriendE  OFFLINE","FriendF  ONLINE","FriendG  ONLINE"};
 manager.selected=5;
 assert(tb_friends_panel_show(manager));
 assert(tb_friends_panel_visible());
 assert(tb_draw_overlay(pixels.data(),1280,720,0,&region));
 assert(region.x==416 && region.y==215 && region.width==448 && region.height==290);
 assert(pixels[123*region.width+20]==0xff66c7ffu); // Second page selection.
 assert(tb_panel_input(0)==PanelPadInput::captured);
 assert(tb_panel_input(0x2000)==PanelPadInput::captured); // Never forwards Back into the title.
 assert(tb_panel_take_input()==0x2000);
 assert(tb_draw_overlay(pixels.data(),3840,2160,1,&region));
 assert(region.width==1344 && region.height==870 && pixels.back()==0xa5a5a5a5);
 assert(!tb_draw_overlay(pixels.data(),200,120,0,&region));
 manager.selected=0;
 assert(tb_friends_panel_show(manager));
 assert(tb_draw_overlay(pixels.data(),1280,720,0,&region));
 {
  std::ofstream shot("friends-panel.ppm",std::ios::binary);
  shot<<"P6\n"<<region.width<<" "<<region.height<<"\n255\n";
  for(size_t i=0;i<region.width*region.height;++i) {
   char rgb[3]={char(pixels[i]),char(pixels[i]>>8),char(pixels[i]>>16)};shot.write(rgb,3);
  }
 }
 for(auto section:{FriendsSection::received,FriendsSection::sent,FriendsSection::add}) {
  manager.section=section;manager.rows.clear();
  assert(tb_friends_panel_show(manager));assert(tb_draw_overlay(pixels.data(),1280,720,0,&region));
 }
 manager.editing=true;manager.username="User-0123456789_";manager.keyboard_key=39;
 assert(tb_friends_panel_show(manager));
 assert(tb_draw_overlay(pixels.data(),1280,720,0,&region));
 assert(region.width==448 && region.height==368 && region.x==416 && region.y==176);
 {
  std::ofstream shot("friends-keyboard.ppm",std::ios::binary);
  shot<<"P6\n"<<region.width<<" "<<region.height<<"\n255\n";
  for(size_t i=0;i<region.width*region.height;++i) {
   char rgb[3]={char(pixels[i]),char(pixels[i]>>8),char(pixels[i]>>16)};shot.write(rgb,3);
  }
 }
 assert(tb_draw_overlay(pixels.data(),3840,2160,1,&region));
 assert(region.width==1344 && region.height==1104 && pixels.back()==0xa5a5a5a5);
 manager.editing=false;manager.details=true;manager.section=FriendsSection::friends;
 manager.rows={"FriendC  OFFLINE"};manager.selected=0;manager.confirm_remove=true;
 manager.actions={"KEEP FRIEND","REMOVE FRIEND"};manager.action_selected=1;
 assert(tb_friends_panel_show(manager));assert(tb_draw_overlay(pixels.data(),3840,2160,1,&region));
 assert(pixels.back()==0xa5a5a5a5);
 manager.status="Friend request sent. The other player needs to accept it.";
 assert(tb_friends_panel_show(manager));assert(tb_draw_overlay(pixels.data(),1280,720,0,&region));
 {
  std::ofstream shot("friends-result.ppm",std::ios::binary);
  shot<<"P6\n"<<region.width<<" "<<region.height<<"\n255\n";
  for(size_t i=0;i<region.width*region.height;++i) {
   char rgb[3]={char(pixels[i]),char(pixels[i]>>8),char(pixels[i]>>16)};shot.write(rgb,3);
  }
 }
 manager.status.clear();manager.details=false;
 manager.connected=false;assert(tb_friends_panel_show(manager));
 assert(tb_draw_overlay(pixels.data(),1280,720,0,&region));
 manager.connected=true;manager.busy=true;assert(tb_friends_panel_show(manager));
 assert(tb_draw_overlay(pixels.data(),1280,720,0,&region));
 tb_friends_panel_close();
 assert(!tb_friends_panel_visible());
 assert(tb_panel_input(0x2000)==PanelPadInput::captured);
 assert(tb_panel_input(0)==PanelPadInput::passthrough);
 assert(!tb_draw_overlay(pixels.data(),1280,720,0,&region));
 tb_panel_show(InvitePicker::rooms,friends,0);
 assert(!tb_friends_panel_show(manager)); // Room browser keeps ownership.
 tb_panel_close();
 tb_panel_show(InvitePicker::friends,friends,1);
 assert(!tb_friends_panel_show(manager));
 assert(tb_friends_panel_show(manager,true));
 tb_friends_panel_close();
 assert(tb_draw_overlay(pixels.data(),1280,720,0,&region));
 assert(region.height==268 && pixels[85*region.width+20]==0xff66c7ffu); // Restored picker selection.
 tb_panel_close();

 // The largest legal roster still paints only the selected page.
 manager.busy=false;
 manager.rows.clear();
 for (unsigned i=0;i<1024;++i)
    manager.rows.push_back("TankFriend"+std::to_string(100000+i)+"  OFFLINE");
 manager.selected=1023;
 assert(tb_friends_panel_show(manager));
 std::fill(pixels.begin(),pixels.end(),0xa5a5a5a5);
 assert(tb_draw_overlay(pixels.data(),450,300,0,&region));
 assert(region.width==448 && region.height==290 && region.x==1 && region.y==5);
 assert(pixels[195*region.width+20]==0xff66c7ffu);
 assert(pixels[region.width*region.height]==0xa5a5a5a5);
 assert(tb_draw_overlay(pixels.data(),3840,2160,1,&region));
 assert(region.width==1344 && region.height==870 && pixels.back()==0xa5a5a5a5);
 tb_friends_panel_close();
}
