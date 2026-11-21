#include "execution_policy_api.h"
#include "fps_overlay.h"
#include "player_settings.h"
#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>

namespace {
using Clock=std::chrono::steady_clock;
std::atomic<uint64_t> flips{0},presents{0};
std::mutex overlay_mutex;
constexpr unsigned visible_choices=5;
struct Picker {
    InvitePicker kind=InvitePicker::friends;
    std::array<std::array<char,40>,visible_choices> choices{};
    unsigned count=0;
    unsigned selected=0;
    bool multiple=false;
};
Picker picker;
bool panel_active=false;
uint32_t panel_previous=0,panel_pressed=0;
bool panel_waits_for_release=false;
struct Toast {
    std::array<char,40> heading{};
    std::array<char,40> detail{};
    bool invitation=true;
};
Toast toast;
Clock::time_point toast_until{};
constexpr unsigned toast_height=43;
constexpr auto toast_duration=std::chrono::seconds(4);
std::atomic<bool>& enabled() {
    static std::atomic<bool> value{[] {const char* e=std::getenv("TB_FPS_OVERLAY");return e && std::atoi(e)!=0;}()};
    return value;
}
// Original five-bit rows, seven rows per glyph. No font asset dependency.
const unsigned char* glyph(char ch) {
    static const unsigned char digits[10][7]={
        {14,17,19,21,25,17,14},{4,12,4,4,4,4,14},{14,17,1,2,4,8,31},
        {30,1,1,14,1,1,30},{2,6,10,18,31,2,2},{31,16,16,30,1,1,30},
        {14,16,16,30,17,17,14},{31,1,2,4,8,8,8},{14,17,17,14,17,17,14},{14,17,17,15,1,1,14}};
    static const char letters[]="AEFGMNPRST.-";
    static const unsigned char rows[][7]={
        {14,17,17,31,17,17,17},{31,16,16,30,16,16,31},{31,16,16,30,16,16,16},
        {14,17,16,23,17,17,14},{17,27,21,21,17,17,17},{17,25,21,19,17,17,17},
        {30,17,17,30,16,16,16},{30,17,17,30,20,18,17},{15,16,16,14,1,1,30},
        {31,4,4,4,4,4,4},{0,0,0,0,0,6,6},{0,0,0,31,0,0,0}};
    static const unsigned char blank[7]={0};
    if (ch>='0'&&ch<='9') return digits[ch-'0'];
    const char* found=std::strchr(letters,ch);
    if (found && ch) return rows[found-letters];
    static const char extra[]="BCDHIJKLOQUVWXYZ_>/";
    static const unsigned char extra_rows[][7]={
        {30,17,17,30,17,17,30},{14,17,16,16,16,17,14},{30,17,17,17,17,17,30},
        {17,17,17,31,17,17,17},{14,4,4,4,4,4,14},{7,2,2,2,2,18,12},
        {17,18,20,24,20,18,17},{16,16,16,16,16,16,31},{14,17,17,17,17,17,14},
        {14,17,17,17,21,18,13},{17,17,17,17,17,17,14},{17,17,17,17,17,10,4},
        {17,17,17,21,21,21,10},{17,17,10,4,10,17,17},{17,17,10,4,4,4,4},
        {31,1,2,4,8,16,31},{0,0,0,0,0,0,31},{16,8,4,2,4,8,16},
        {1,2,2,4,8,8,16}};
    if (ch>='a' && ch<='z') return glyph(ch-'a'+'A');
    found=std::strchr(extra,ch);
    return found && ch ? extra_rows[found-extra] : blank;
}

struct Color { unsigned char red,green,blue; };
constexpr Color card{18,29,43}, border{78,101,121}, selected_row{39,59,78};
constexpr Color accent{255,199,102}, white{245,249,255}, muted{178,198,216};

struct Canvas {
    uint32_t* pixels;
    unsigned width;
    bool bgra;

    void fill(unsigned x,unsigned y,unsigned w,unsigned h,Color color) const {
        const uint32_t first=bgra?color.blue:color.red;
        const uint32_t third=bgra?color.red:color.blue;
        const uint32_t pixel=0xff000000u|(third<<16)|(uint32_t(color.green)<<8)|first;
        for (unsigned row=y;row<y+h;++row)
            std::fill_n(pixels+size_t(row)*width+x,w,pixel);
    }

    void text(unsigned x,unsigned y,const char* line,unsigned scale,Color color) const {
        for (;*line && x+5*scale<=width;++line,x+=6*scale) {
            const auto* rows=glyph(*line);
            for (unsigned row=0;row<7;++row)
                for (unsigned column=0;column<5;++column)
                    if (rows[row]&(16u>>column))
                        fill(x+column*scale,y+row*scale,scale,scale,color);
        }
    }
};

void draw_picker(const Canvas& canvas,unsigned height,unsigned scale,const Picker& view) {
    const unsigned inset=10*scale;
    const unsigned heading_scale=scale+scale/2;
    const bool inviting=view.kind==InvitePicker::friends;
    const bool rooms=view.kind==InvitePicker::rooms || view.kind==InvitePicker::searching_rooms;
    canvas.fill(0,0,canvas.width,height,border);
    canvas.fill(1,1,canvas.width-2,height-2,card);
    canvas.fill(0,0,canvas.width,2*scale,accent);
    const auto* heading=rooms?"PUBLIC ROOMS":inviting?"INVITE FRIENDS":"ACCEPT INVITATION";
    canvas.text(inset,9*scale,heading,heading_scale,white);
    canvas.fill(inset,23*scale,canvas.width-2*inset,scale,border);

    const unsigned rows=std::max(1u,view.count);
    for (unsigned row=0;row<rows;++row) {
        const unsigned y=(28+row*14)*scale;
        const bool highlighted=view.count && row==view.selected;
        if (highlighted) {
            canvas.fill(inset,y,canvas.width-2*inset,13*scale,selected_row);
            canvas.fill(inset,y,2*scale,13*scale,accent);
        }
        const auto* label=inviting?"NO ONLINE FRIENDS":"NO INVITATIONS";
        if (rooms) label=view.kind==InvitePicker::searching_rooms?"SEARCHING...":"NO PUBLIC ROOMS";
        if (view.count) label=view.choices[row].data();
        canvas.text(17*scale,y+3*scale,label,scale,highlighted?white:muted);
    }

    const unsigned footer=(30+rows*14)*scale;
    if (view.multiple)
        canvas.text(inset,footer,"D-PAD OR ARROWS TO CHOOSE",scale,muted);
    const auto* pad_help=view.count?"PLAYSTATION  X ACCEPT  O BACK":"PLAYSTATION  O BACK";
    const auto* keyboard_help=view.count?"KEYBOARD     Z ACCEPT  X BACK":"KEYBOARD     X BACK";
    if (rooms && view.count) {
        pad_help="PLAYSTATION  X JOIN  O BACK";
        keyboard_help="KEYBOARD     Z JOIN  X BACK";
    }
    canvas.text(inset,footer+11*scale,pad_help,scale,white);
    canvas.text(inset,footer+22*scale,keyboard_help,scale,muted);
    if (rooms) canvas.text(inset,footer+33*scale,"SQUARE / KEYBOARD A REFRESH",scale,muted);
}

void draw_toast(const Canvas& canvas,unsigned height,unsigned scale,const Toast& view,double remaining) {
    canvas.fill(0,0,canvas.width,height,border);
    canvas.fill(1,1,canvas.width-2,height-2,card);
    canvas.fill(0,0,3*scale,height,accent);
    canvas.text(12*scale,9*scale,view.heading.data(),view.invitation?scale+scale/2:scale,white);
    canvas.text(12*scale,28*scale,view.detail.data(),scale,muted);
    const auto remaining_width=static_cast<unsigned>((canvas.width-4*scale)*remaining);
    canvas.fill(3*scale,height-2*scale,remaining_width,scale,accent);
}
}

void tb_panel_show(InvitePicker kind,std::span<const std::string> choices,size_t selected) {
    std::lock_guard guard(overlay_mutex);
    picker.kind=kind;
    selected=choices.empty()?0:std::min(selected,choices.size()-1);
    const auto first=selected/visible_choices*visible_choices;
    picker.count=static_cast<unsigned>(std::min<size_t>(choices.size()-first,visible_choices));
    picker.selected=static_cast<unsigned>(selected-first);
    picker.multiple=choices.size()>1;
    for (unsigned row=0;row<picker.count;++row)
        std::snprintf(picker.choices[row].data(),picker.choices[row].size(),"%s",choices[first+row].c_str());
    if (!panel_active) { panel_pressed=0;panel_previous=UINT32_MAX; }
    panel_active=true;
}
void tb_panel_close() {
    std::lock_guard guard(overlay_mutex);
    if (panel_active) panel_waits_for_release=panel_previous!=0;
    panel_active=false; panel_pressed=panel_previous=0;
}
PanelPadInput tb_panel_input(uint32_t buttons) {
    std::lock_guard guard(overlay_mutex);
    if (!panel_active) {
        if (!buttons) panel_waits_for_release=false;
        return panel_waits_for_release?PanelPadInput::captured:PanelPadInput::passthrough;
    }
    const uint32_t pressed=buttons&~panel_previous;
    panel_pressed|=pressed;
    panel_previous=buttons;
    // Let the title unwind its search screen or empty inbox on the same Back
    // press that dismisses the panel, then suppress held input until release.
    const bool title_back=picker.kind==InvitePicker::rooms || picker.kind==InvitePicker::searching_rooms ||
        (picker.kind==InvitePicker::invitations && !picker.count);
    if (title_back && (pressed&0x2000))
        return PanelPadInput::back;
    return PanelPadInput::captured;
}
uint32_t tb_panel_take_input() {
    std::lock_guard guard(overlay_mutex);
    const auto buttons=panel_pressed; panel_pressed=0; return buttons;
}
void tb_show_invite_toast(const char* sender) {
    std::lock_guard guard(overlay_mutex);
    std::snprintf(toast.heading.data(),toast.heading.size(),"%s",sender);
    std::snprintf(toast.detail.data(),toast.detail.size(),"INVITED YOU");
    toast.invitation=true;
    toast_until=Clock::now()+toast_duration;
}
void tb_clear_invite_toast() {
    std::lock_guard guard(overlay_mutex);
    if (toast.invitation) toast={};
}
void tb_show_network_toast(const char* heading,const char* detail) {
    std::lock_guard guard(overlay_mutex);
    std::snprintf(toast.heading.data(),toast.heading.size(),"%s",heading);
    std::snprintf(toast.detail.data(),toast.detail.size(),"%s",detail);
    toast.invitation=false;
    toast_until=Clock::now()+toast_duration;
}

extern "C" void tb_fps_guest_flip() {flips.fetch_add(1,std::memory_order_relaxed);tb_gameplay_event(0,0);}
extern "C" uint64_t tb_fps_guest_serial() {return flips.load(std::memory_order_relaxed);}
extern "C" void tb_fps_present() {presents.fetch_add(1,std::memory_order_relaxed);}
extern "C" void tb_fps_toggle() {auto& e=enabled();const bool on=!e.load(std::memory_order_relaxed);e.store(on,std::memory_order_relaxed);tb_settings_note_overlay(on);}
static int draw_overlay(void* pixels,uint32_t width,uint32_t height,int bgra,tb_overlay_region* region,bool notification)
{
    if (!pixels || !region) return 0;

    enum class Mode { fps, picker, invitation };
    Mode mode=Mode::fps;
    Picker picker_view;
    Toast toast_view;
    double remaining=0;
    {
        std::lock_guard guard(overlay_mutex);
        if (!notification && panel_active) {
            mode=Mode::picker;
            picker_view=picker;
        }
        if (notification && toast.heading[0]) {
            const auto now=Clock::now();
            if (now<toast_until) {
                toast_view=toast;
                remaining=std::chrono::duration<double>(toast_until-now)/toast_duration;
                mode=Mode::invitation;
            } else toast={};
        }
    }
    if (notification && !toast_view.heading[0]) return 0;
    if (mode==Mode::fps && !enabled().load(std::memory_order_relaxed)) return 0;

    unsigned card_width, card_height, margin;
    switch (mode) {
    case Mode::picker:
        card_width=224;
        card_height=(picker_view.kind==InvitePicker::rooms || picker_view.kind==InvitePicker::searching_rooms?75:64)
            +std::max(1u,picker_view.count)*14;
        margin=0;
        break;
    case Mode::invitation:
        card_width=toast_view.invitation?184:224; card_height=toast_height; margin=10;
        break;
    case Mode::fps:
        card_width=144; card_height=32; margin=8;
        break;
    }
    const bool centered=mode==Mode::picker;
    const unsigned max_scale=mode==Mode::fps
        ? (height>=1400?3u:2u)
        : std::clamp((height+180)/360,2u,TB_OVERLAY_MAX_SCALE);
    const unsigned scale=std::min({max_scale,width/(card_width+2*margin),height/(card_height+2*margin)});
    if (!scale) return 0;

    const unsigned draw_width=card_width*scale, draw_height=card_height*scale;
    const unsigned offset=mode==Mode::fps?margin:margin*scale;
    *region={centered?(width-draw_width)/2:offset,centered?(height-draw_height)/2:offset,draw_width,draw_height};
    const Canvas canvas{static_cast<uint32_t*>(pixels),draw_width,bgra!=0};
    if (mode==Mode::picker) {
        draw_picker(canvas,draw_height,scale,picker_view);
    } else if (mode==Mode::invitation) {
        draw_toast(canvas,draw_height,scale,toast_view,remaining);
    } else {
        static FpsSample sample;
        const auto ns=std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now().time_since_epoch()).count();
        sample.update(ns,flips.load(std::memory_order_relaxed),presents.load(std::memory_order_relaxed));
        char lines[3][32]={"GAME -- FPS","PRESENT -- FPS","FRAME -- MS"};
        if (sample.ready) {
            std::snprintf(lines[0],32,"GAME %.1f FPS",sample.game_fps);
            std::snprintf(lines[1],32,"PRESENT %.1f FPS",sample.present_fps);
            if (sample.game_fps>0) std::snprintf(lines[2],32,"FRAME %.1f MS",sample.frame_ms);
        }
        canvas.fill(0,0,draw_width,draw_height,{16,16,16});
        for (unsigned line=0;line<3;++line)
            canvas.text(3*scale,(3+line*10)*scale,lines[line],scale,{240,240,240});
    }
    return 1;
}

extern "C" int tb_draw_overlay(void* pixels,uint32_t width,uint32_t height,int bgra,tb_overlay_region* region) {
    return draw_overlay(pixels,width,height,bgra,region,false);
}

extern "C" int tb_draw_notification_toast(void* pixels,uint32_t width,uint32_t height,int bgra,tb_overlay_region* region) {
    return draw_overlay(pixels,width,height,bgra,region,true);
}
