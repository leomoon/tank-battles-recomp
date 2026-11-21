#pragma once
#include <cstdint>
#include <span>
#include <string>

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
enum class InvitePicker { friends, invitations };
void tb_panel_show(InvitePicker kind, std::span<const std::string> choices, size_t selected);
void tb_panel_close();
enum class PanelPadInput { passthrough, captured, back };
PanelPadInput tb_panel_input(uint32_t buttons);
uint32_t tb_panel_take_input();
void tb_show_invite_toast(const char* sender);
void tb_clear_invite_toast();
