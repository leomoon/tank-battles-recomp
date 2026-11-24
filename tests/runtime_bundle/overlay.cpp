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
static void save_overlay(const char* path,const tb_overlay_region& region,const std::vector<uint32_t>& pixels) {
    std::ofstream shot(path,std::ios::binary);
    shot<<"P6\n"<<region.width<<" "<<region.height<<"\n255\n";
    for (size_t i=0;i<region.width*region.height;++i) {
        const char rgb[3]={char(pixels[i]),char(pixels[i]>>8),char(pixels[i]>>16)};
        shot.write(rgb,3);
    }
}

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
 std::vector<std::string> invite_choices(friends.begin(),friends.end());
 invite_choices.emplace_back("MANAGE FRIENDS");
 tb_panel_show(InvitePicker::friends,invite_choices,1);
 assert(tb_draw_overlay(pixels.data(),1280,720,0,&region));
 assert(region.width==640 && region.height==360 && region.x==320 && region.y==180);
 assert(pixels[85*region.width+20]==0xff2b1d12u);
 assert(pixels[125*region.width+20]==0xff66c7ffu); // Selected row accent.
 assert(pixels[region.width*region.height]==0xa5a5a5a5);
 save_overlay("friends-invite.ppm",region,pixels);
 assert(tb_draw_overlay(pixels.data(),320,180,1,&region));
 assert(region.x==4 && region.y==4 && region.width==312 && region.height==172);
 assert(pixels[0]==0xffffc766u); // Swapchain channel order.
 assert(tb_draw_overlay(pixels.data(),3840,2160,0,&region));
 assert(region.x==960 && region.y==540 && region.width==1920 && region.height==1080);
 assert(pixels.back()==0xa5a5a5a5);
 tb_panel_show(InvitePicker::friends,invite_choices,5);
 assert(tb_draw_overlay(pixels.data(),1280,720,0,&region));
 assert(pixels[89*region.width+20]==0xff66c7ffu); // First row on the next page.
 tb_panel_update(InvitePicker::friends,invite_choices,6);
 assert(tb_draw_overlay(pixels.data(),1280,720,0,&region));
 assert(pixels[279*region.width+20]==0xff66c7ffu); // Manage Friends keeps its own row.
 const std::array<std::string,1> no_online_friends{"MANAGE FRIENDS"};
 tb_panel_update(InvitePicker::friends,no_online_friends,0);
 assert(tb_draw_overlay(pixels.data(),1280,720,0,&region));
 assert(region.height==360 && pixels[89*region.width+20]==0xff2b1d12u);
 assert(pixels[279*region.width+20]==0xff66c7ffu);
 save_overlay("friends-invite-empty.ppm",region,pixels);
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
 assert(region.width==640 && region.height==360 && region.x==320 && region.y==180);
 save_overlay("invitations.ppm",region,pixels);
 assert(pixels[89*region.width+20]==0xff66c7ffu); // Invite is still selectable.
 assert(tb_draw_notification_toast(toast.data(),1280,720,0,&toast_region));
 assert(toast_region.x==20 && toast_region.y==20); // Inbox and toast keep independent positions.
 tb_panel_show(InvitePicker::invitations,friends,0);
 assert(tb_draw_overlay(pixels.data(),3840,2160,0,&region));
 assert(region.width==1920 && region.height==1080);
 assert(pixels.back()==0xa5a5a5a5);
 assert(tb_draw_notification_toast(toast.data(),3840,2160,0,&toast_region));
 assert(toast_region.x==60 && toast_region.y==60 && toast_region.width==1104 && toast_region.height==258);
 assert(toast.back()==0xa5a5a5a5);
 assert(!tb_draw_overlay(pixels.data(),320,134,1,&region)); // Same minimum height as Friends.
 std::this_thread::sleep_for(std::chrono::seconds(4));
 assert(!tb_draw_notification_toast(toast.data(),1280,720,0,&toast_region));
 assert(tb_draw_overlay(pixels.data(),1280,720,0,&region));
 assert(region.width==640 && region.height==360 && region.x==320 && region.y==180);
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
 assert(region.width==640 && region.height==360 && region.x==320 && region.y==180);
 save_overlay("public-rooms.ppm",region,pixels);
 assert(tb_panel_input(0)==PanelPadInput::captured);
 assert(tb_panel_input(0x8000)==PanelPadInput::captured);
 assert(tb_panel_take_input()==0x8000);
 assert(tb_panel_input(0)==PanelPadInput::captured);
 assert(tb_panel_input(0x2000)==PanelPadInput::back);
 assert(tb_panel_take_input()==0x2000);
 assert(tb_panel_input(0x2000)==PanelPadInput::captured);
 tb_panel_show(InvitePicker::searching_rooms,{},0);
 assert(tb_draw_overlay(pixels.data(),1280,720,0,&region));
 assert(region.width==640 && region.height==360 && region.x==320 && region.y==180);
 tb_panel_close();
 FriendsPanelView manager;
 manager.account="Captain_01234567";manager.endpoint="play.lmx.to:31313";manager.connected=true;
 manager.rows={"PlayerB  ONLINE","FriendC  OFFLINE","FriendD  ONLINE","FriendE  OFFLINE","FriendF  ONLINE","FriendG  ONLINE"};
 manager.selected=5;
 assert(tb_friends_panel_show(manager));
 assert(tb_friends_panel_visible());
 assert(tb_draw_overlay(pixels.data(),1280,720,0,&region));
 assert(region.x==320 && region.y==180 && region.width==640 && region.height==360);
 assert(pixels[114*region.width+20]==0xff66c7ffu); // Active category.
 assert(pixels[243*region.width+20]==0xff66c7ffu); // Sixth row on the first page.
 assert(tb_panel_input(0)==PanelPadInput::captured);
 assert(tb_panel_input(0x2000)==PanelPadInput::captured); // Never forwards Back into the title.
 assert(tb_panel_take_input()==0x2000);
 assert(tb_draw_overlay(pixels.data(),3840,2160,1,&region));
 assert(region.width==1920 && region.height==1080 && pixels.back()==0xa5a5a5a5);
 assert(!tb_draw_overlay(pixels.data(),200,120,0,&region));
 manager.selected=0;
 assert(tb_friends_panel_show(manager));
 assert(tb_draw_overlay(pixels.data(),1280,720,0,&region));
 save_overlay("friends-panel.ppm",region,pixels);
 for(auto section:{FriendsSection::received,FriendsSection::sent,FriendsSection::add}) {
  manager.section=section;manager.rows.clear();
  assert(tb_friends_panel_show(manager));assert(tb_draw_overlay(pixels.data(),1280,720,0,&region));
 }
 manager.editing=true;manager.username="User-0123456789_";manager.keyboard_key=39;
 assert(tb_friends_panel_show(manager));
 assert(tb_draw_overlay(pixels.data(),1280,720,0,&region));
 assert(region.width==640 && region.height==432 && region.x==320 && region.y==144);
 save_overlay("friends-keyboard.ppm",region,pixels);
 assert(tb_draw_overlay(pixels.data(),3840,2160,1,&region));
 assert(region.width==1920 && region.height==1296 && pixels.back()==0xa5a5a5a5);
 manager.editing=false;manager.details=true;manager.section=FriendsSection::friends;
 manager.rows={"FriendC  OFFLINE"};manager.selected=0;manager.confirm_remove=true;
 manager.actions={"KEEP FRIEND","REMOVE FRIEND"};manager.action_selected=1;
 assert(tb_friends_panel_show(manager));assert(tb_draw_overlay(pixels.data(),3840,2160,1,&region));
 assert(pixels.back()==0xa5a5a5a5);
 manager.status="Friend request sent. The other player needs to accept it.";
 assert(tb_friends_panel_show(manager));assert(tb_draw_overlay(pixels.data(),1280,720,0,&region));
 save_overlay("friends-result.ppm",region,pixels);
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
 tb_panel_show(InvitePicker::friends,invite_choices,1);
 assert(!tb_friends_panel_show(manager));
 assert(tb_friends_panel_show(manager,true));
 tb_friends_panel_close();
 assert(tb_draw_overlay(pixels.data(),1280,720,0,&region));
 assert(region.height==360 && pixels[125*region.width+20]==0xff66c7ffu); // Restored picker selection.
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
 assert(region.width==1920 && region.height==1080 && pixels.back()==0xa5a5a5a5);
 assert(pixels[871*region.width+60]==0xffffc766u); // Eighth row on the last page.
 tb_friends_panel_close();

 // All room and invitation states use the Friends panel's dimensions.
 for (const auto size:std::array<std::array<unsigned,2>,4>{{{320,180},{450,300},{1280,720},{3840,2160}}}) {
  assert(tb_friends_panel_show(manager));
  tb_overlay_region expected{};
  assert(tb_draw_overlay(pixels.data(),size[0],size[1],0,&expected));
  tb_friends_panel_close();
  for (auto kind:{InvitePicker::rooms,InvitePicker::searching_rooms,InvitePicker::invitations}) {
   for (auto count:{0u,1u,5u}) {
    tb_panel_show(kind,std::span<const std::string>(friends).first(count),0);
    std::fill(pixels.begin(),pixels.end(),0xa5a5a5a5);
    assert(tb_draw_overlay(pixels.data(),size[0],size[1],0,&region));
    assert(region.x==expected.x && region.y==expected.y &&
           region.width==expected.width && region.height==expected.height);
    assert(pixels[region.width*region.height]==0xa5a5a5a5);
    tb_panel_close();
   }
  }
 }
}
