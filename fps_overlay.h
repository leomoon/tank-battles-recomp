#pragma once
#include <cstdint>
#include <span>
#include <string>
#include <vector>

// Guest flips and host refreshes are deliberately independent counters.
struct FpsSample {
    uint64_t start=0,guest=0,presents=0;
    bool initialized=false,ready=false;
    double game_fps=0,present_fps=0,frame_ms=0;
    void update(uint64_t ns,uint64_t flips,uint64_t refreshes) {
        if (!initialized || ns<start || flips<guest || refreshes<presents) {
            initialized=true;ready=false;start=ns;guest=flips;presents=refreshes;return;
        }
        if (ns-start<500000000ull) return;
        const double seconds=(ns-start)/1e9;
        game_fps=(flips-guest)/seconds;
        present_fps=(refreshes-presents)/seconds;
        frame_ms=game_fps>0?1000.0/game_fps:0;
        ready=true;start=ns;guest=flips;presents=refreshes;
    }
};

// A small native selection panel shares the window-only overlay surface.
enum class InvitePicker { friends, invitations, rooms, searching_rooms };
void tb_panel_show(InvitePicker kind, std::span<const std::string> choices, size_t selected);
void tb_panel_update(InvitePicker kind, std::span<const std::string> choices, size_t selected);
void tb_panel_close();
bool tb_panel_visible(InvitePicker kind);
void tb_panel_close_if(InvitePicker kind);
enum class PanelPadInput { passthrough, captured, back };
PanelPadInput tb_panel_input(uint32_t buttons);
uint32_t tb_panel_take_input();
enum class FriendsSection { friends, received, sent, add };
struct FriendsPanelView {
    std::string account, endpoint;
    FriendsSection section=FriendsSection::friends;
    std::vector<std::string> rows;
    size_t selected=0;
    bool connected=false, busy=false, details=false;
    bool connecting=false;
    bool editing=false, upper=false;
    unsigned keyboard_key=0;
    std::string username;
    std::vector<std::string> actions;
    size_t action_selected=0;
    bool confirm_remove=false;
    std::string status;
    std::string message;
};
// Only an invitation sender can suspend its picker beneath Friends.
bool tb_friends_panel_show(const FriendsPanelView&,bool from_invite=false);
bool tb_friends_panel_visible();
void tb_friends_panel_close();
bool tb_show_friends_hint();
void tb_show_friend_request_toast(const char* sender);
void tb_clear_friend_request_toasts();
bool tb_friend_request_toast_visible();
void tb_show_invite_toast(const char* sender);
void tb_clear_invite_toast();
void tb_show_network_toast(const char* heading,const char* detail);
