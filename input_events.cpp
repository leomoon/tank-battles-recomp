#include "execution_policy_api.h"
#include <atomic>
#include <SDL2/SDL_scancode.h>

// The renderer owns SDL event pumping. Keep a down edge until cellPad samples
// it, even if SDL has already processed the matching release in the same pump.
static std::atomic<uint32_t> pending{0};
static std::atomic<bool> friends_toggle{false};
extern "C" void tb_friends_request_toggle(void) {friends_toggle.store(true,std::memory_order_relaxed);}
extern "C" int tb_friends_take_toggle(void) {return friends_toggle.exchange(false,std::memory_order_relaxed);}
extern "C" void tb_keyboard_down(uint32_t scan)
{
    if (scan==SDL_SCANCODE_F2) {tb_friends_request_toggle();return;}
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
