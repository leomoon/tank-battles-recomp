#include "execution_policy_api.h"
#include <atomic>
#include <algorithm>
#include <cstring>
#include <deque>
#include <mutex>
#include <SDL2/SDL_scancode.h>
#include <SDL2/SDL_keycode.h>

// The renderer owns SDL event pumping. Keep a down edge until cellPad samples
// it, even if SDL has already processed the matching release in the same pump.
static std::atomic<uint32_t> pending{0};
static std::atomic<bool> friends_toggle{false};
static std::atomic<bool> friends_capture{false},friends_editing{false},release_keys{false},friends_focus{true};
static std::mutex friends_input_lock;
static std::deque<tb_friends_input> friends_inputs;
extern "C" void tb_friends_input_mode(int captured,int editing) {
    std::lock_guard guard(friends_input_lock);
    const bool changed=friends_capture.load()!=bool(captured) || friends_editing.load()!=bool(editing);
    if (changed) {
        friends_inputs.clear();pending.store(0);release_keys.store(true);
    }
    friends_capture.store(captured);friends_editing.store(captured && editing);
}
extern "C" int tb_friends_keyboard_captured() {return friends_capture.load() || release_keys.load();}
extern "C" int tb_friends_text_requested() {return friends_editing.load() && friends_focus.load();}
extern "C" int tb_friends_window_focused() {return friends_focus.load();}
extern "C" int tb_friends_key(uint32_t scan,int pressed,int repeat,uint32_t modifiers) {
    if (!friends_focus.load()) return 1;
    if (scan==SDL_SCANCODE_F2) {
        if (pressed && !repeat) tb_friends_request_toggle();
        return 1;
    }
    if (scan==SDL_SCANCODE_F3 || scan==SDL_SCANCODE_F4 || scan==SDL_SCANCODE_F11 ||
        (scan==SDL_SCANCODE_RETURN && (modifiers&KMOD_ALT))) return 0;
    if (!friends_capture.load()) return release_keys.load();
    if (!pressed) return 1;
    if (repeat && scan!=SDL_SCANCODE_BACKSPACE && scan!=SDL_SCANCODE_UP && scan!=SDL_SCANCODE_DOWN &&
        scan!=SDL_SCANCODE_LEFT && scan!=SDL_SCANCODE_RIGHT) return 1;
    uint32_t action=0;
    switch (scan) {
    case SDL_SCANCODE_UP:action=TB_FRIENDS_UP;break;
    case SDL_SCANCODE_RIGHT:action=TB_FRIENDS_RIGHT;break;
    case SDL_SCANCODE_DOWN:action=TB_FRIENDS_DOWN;break;
    case SDL_SCANCODE_LEFT:action=TB_FRIENDS_LEFT;break;
    case SDL_SCANCODE_Z:action=TB_FRIENDS_CONFIRM;break;
    case SDL_SCANCODE_X:action=TB_FRIENDS_BACK;break;
    case SDL_SCANCODE_RETURN:case SDL_SCANCODE_KP_ENTER:action=TB_FRIENDS_ENTER;break;
    case SDL_SCANCODE_ESCAPE:action=TB_FRIENDS_ESCAPE;break;
    case SDL_SCANCODE_BACKSPACE:action=TB_FRIENDS_BACKSPACE;break;
    default:return 1;
    }
    std::lock_guard guard(friends_input_lock);
    if (friends_inputs.size()<64) friends_inputs.push_back({TB_FRIENDS_KEY,action,{}});
    return 1;
}
extern "C" void tb_friends_text(const char* text) {
    if (!text || !tb_friends_text_requested()) return;
    std::lock_guard guard(friends_input_lock);
    if (friends_inputs.size()>=64) return;
    tb_friends_input input{TB_FRIENDS_TEXT,0,{}};
    // Anything longer than a valid username is deliberately preserved as an
    // overlong chunk for the editor to reject, rather than silently truncating.
    const auto size=std::min(std::strlen(text),sizeof input.text-1);
    std::memcpy(input.text,text,size);friends_inputs.push_back(input);
}
extern "C" void tb_friends_focus(int focused) {
    friends_focus.store(focused);
    if (!focused) {
        std::lock_guard guard(friends_input_lock);
        pending.store(0);friends_toggle.store(false);friends_inputs.clear();release_keys.store(true);
        if (friends_capture.load()) friends_inputs.push_back({TB_FRIENDS_FOCUS_LOST,0,{}});
    }
}
extern "C" void tb_friends_update_keyboard(const uint8_t* keys,int count) {
    if (keys && count>0 && std::none_of(keys,keys+count,[](uint8_t key){return key!=0;})) release_keys.store(false);
}
extern "C" int tb_friends_take_input(tb_friends_input* input) {
    std::lock_guard guard(friends_input_lock);
    if (!input || friends_inputs.empty()) return 0;
    *input=friends_inputs.front();friends_inputs.pop_front();return 1;
}
extern "C" void tb_friends_request_toggle(void) {friends_toggle.store(true,std::memory_order_relaxed);}
extern "C" int tb_friends_take_toggle(void) {return friends_toggle.exchange(false,std::memory_order_relaxed);}
extern "C" void tb_keyboard_down(uint32_t scan)
{
    if (scan==SDL_SCANCODE_F2) {tb_friends_request_toggle();return;}
    if (tb_friends_keyboard_captured()) return;
    static const struct { uint32_t scan, mask; } keys[] = {
        {SDL_SCANCODE_UP,0x10}, {SDL_SCANCODE_RIGHT,0x20},
        {SDL_SCANCODE_DOWN,0x40}, {SDL_SCANCODE_LEFT,0x80},
        {SDL_SCANCODE_Z,0x4000}, {SDL_SCANCODE_X,0x2000},
        {SDL_SCANCODE_A,0x8000}, {SDL_SCANCODE_S,0x1000},
        {SDL_SCANCODE_Q,0x400}, {SDL_SCANCODE_W,0x800},
        {SDL_SCANCODE_1,0x100}, {SDL_SCANCODE_2,0x200},
        {SDL_SCANCODE_RETURN,8}, {SDL_SCANCODE_TAB,1}
    };
    for (const auto& key:keys) if (key.scan==scan) {
        pending.fetch_or(key.mask,std::memory_order_relaxed);return;
    }
}
extern "C" uint32_t tb_keyboard_take(void)
{
    return pending.exchange(0,std::memory_order_relaxed);
}

extern "C" uint32_t tb_keyboard_peek(void) { return pending.load(std::memory_order_relaxed); }
extern "C" void tb_keyboard_ack(uint32_t mask) { pending.fetch_and(~mask,std::memory_order_relaxed); }
