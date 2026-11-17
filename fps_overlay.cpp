#include "execution_policy_api.h"
#include "fps_overlay.h"
#include "player_settings.h"
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>

namespace {
std::atomic<uint64_t> flips{0},presents{0};
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
    return found && ch ? rows[found-letters] : blank;
}
}

extern "C" void tb_fps_guest_flip() {flips.fetch_add(1,std::memory_order_relaxed);tb_gameplay_event(0,0);}
extern "C" uint64_t tb_fps_guest_serial() {return flips.load(std::memory_order_relaxed);}
extern "C" void tb_fps_present() {presents.fetch_add(1,std::memory_order_relaxed);}
extern "C" void tb_fps_toggle() {auto& e=enabled();const bool on=!e.load(std::memory_order_relaxed);e.store(on,std::memory_order_relaxed);tb_settings_note_overlay(on);}
extern "C" int tb_fps_overlay(void* pixels,uint32_t width,uint32_t height,uint32_t pitch,uint32_t scale)
{
    if (!enabled().load(std::memory_order_relaxed)) return 0;
    if (!pixels || !scale || scale>3 || width<144*scale || height<32*scale || uint64_t(pitch)<uint64_t(width)*4) return 0;
    static FpsSample sample;
    const auto ns=std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now().time_since_epoch()).count();
    sample.update(ns,flips.load(std::memory_order_relaxed),presents.load(std::memory_order_relaxed));
    char lines[3][32]={"GAME -- FPS","PRESENT -- FPS","FRAME -- MS"};
    if (sample.ready) {
        std::snprintf(lines[0],sizeof lines[0],"GAME %.1f FPS",sample.game_fps);
        std::snprintf(lines[1],sizeof lines[1],"PRESENT %.1f FPS",sample.present_fps);
        if (sample.game_fps>0) std::snprintf(lines[2],sizeof lines[2],"FRAME %.1f MS",sample.frame_ms);
    }
    auto* bytes=static_cast<unsigned char*>(pixels);
    for (uint32_t y=0;y<height;y++) for (uint32_t x=0;x<width;x++) {
        auto* p=bytes+size_t(y)*pitch+x*4;p[0]=p[1]=p[2]=16;p[3]=255;
    }
    for (unsigned line=0;line<3;line++) for (unsigned ch=0;lines[line][ch];ch++) {
        const auto* g=glyph(lines[line][ch]);
        for (unsigned y=0;y<7;y++) for (unsigned x=0;x<5;x++) if (g[y]&(16>>x))
            for (unsigned sy=0;sy<scale;sy++) for (unsigned sx=0;sx<scale;sx++) {
                const unsigned px=(3+ch*6+x)*scale+sx,py=(3+line*10+y)*scale+sy;
                if (px>=width || py>=height) continue;
                auto* p=bytes+size_t(py)*pitch+px*4;p[0]=p[1]=p[2]=240;p[3]=255;
            }
    }
    return 1;
}
