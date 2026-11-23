#define SDL_MAIN_HANDLED
#include <SDL.h>
#include "execution_policy_api.h"
#include "rpcn/friend_name.h"
#include "sdk_overrides/friends_input.inc"
#include <array>
#include <cassert>
#include <cstring>

int main() {
    using tb::rpcn::FriendNameEntry;
    FriendNameEntry name;
    name.open();assert(!name.done() && !name.error.empty());
    name.append("zx_User-42");assert(name.value=="zx_User-42");
    name.append("bad@name");assert(name.value=="zx_User-42" && !name.error.empty());
    name.append("\xc3\xa9");assert(name.value=="zx_User-42");
    name.append("\n");assert(name.value=="zx_User-42");
    name.append("123456");assert(name.value.size()==16);
    name.append("x");assert(name.value.size()==16 && !name.error.empty());
    name.erase();assert(name.value.size()==15 && name.error.empty());
    assert(name.done());
    const auto saved=name.value;
    name.open();name.erase();name.cancel();assert(name.value==saved);
    name.value.clear();name.open();
    for (unsigned key=0;key<38;++key) {
        name.key=key;const auto c=name.character();
        assert(c && std::strchr("abcdefghijklmnopqrstuvwxyz0123456789_-",c));
    }
    name.key=0;name.upper=true;name.choose();assert(name.value=="A");
    name.key=38;name.choose();assert(name.value.empty());
    name.key=39;name.choose();assert(name.editing && !name.error.empty());
    name.key=0;name.move(-1,-1);assert(name.key==39);
    name.move(1,1);assert(name.key==0);
    SDL_SetMainReady();assert(!SDL_Init(SDL_INIT_VIDEO | SDL_INIT_GAMECONTROLLER));
    SDL_Window* window=SDL_CreateWindow("Friends input fixture",0,0,640,480,SDL_WINDOW_HIDDEN);
    assert(window);
    const int controller=SDL_JoystickAttachVirtual(SDL_JOYSTICK_TYPE_GAMECONTROLLER,6,16,1);
    assert(controller>=0 && SDL_NumJoysticks()>0);
    tb_friends_input_mode(1,1);tb_friends_sdl_sync_text();assert(SDL_IsTextInputActive());
    SDL_Event event{};event.type=SDL_KEYDOWN;event.key.keysym.sym=SDLK_z;event.key.keysym.scancode=SDL_SCANCODE_Z;
    assert(tb_friends_sdl_event(&event));assert(!tb_keyboard_peek());
    event.type=SDL_TEXTINPUT;std::strcpy(event.text.text,"zx");assert(tb_friends_sdl_event(&event));
    tb_friends_input input{};
    assert(tb_friends_take_input(&input) && input.kind==TB_FRIENDS_KEY && input.value==TB_FRIENDS_CONFIRM);
    assert(tb_friends_take_input(&input) && input.kind==TB_FRIENDS_TEXT && !std::strcmp(input.text,"zx"));
    assert(!tb_friends_take_input(&input));
    assert(!SDL_JoystickDetachVirtual(controller)); // Typing remains available through hotplug.
    assert(tb_friends_text_requested());
    const int reconnected=SDL_JoystickAttachVirtual(SDL_JOYSTICK_TYPE_GAMECONTROLLER,6,16,1);
    assert(reconnected>=0 && tb_friends_text_requested());
    event={};event.type=SDL_KEYDOWN;event.key.keysym.scancode=SDL_SCANCODE_ESCAPE;
    assert(tb_friends_sdl_event(&event));
    assert(tb_friends_take_input(&input) && input.value==TB_FRIENDS_ESCAPE);
    assert(tb_friends_key(SDL_SCANCODE_BACKSPACE,1,1,0));
    assert(tb_friends_take_input(&input) && input.value==TB_FRIENDS_BACKSPACE);
    assert(tb_friends_key(SDL_SCANCODE_Z,1,1,0));assert(!tb_friends_take_input(&input));
    assert(!tb_friends_key(SDL_SCANCODE_RETURN,1,0,KMOD_ALT)); // Fullscreen remains a window command.
    std::array<uint8_t,SDL_NUM_SCANCODES> keys{};
    keys[SDL_SCANCODE_Z]=1;
    tb_friends_update_keyboard(keys.data(),keys.size());
    tb_friends_input_mode(0,0);tb_friends_sdl_sync_text();assert(!SDL_IsTextInputActive());
    assert(tb_friends_keyboard_captured()); // Closing with a held key cannot press Cross in the game.
    assert(tb_friends_key(SDL_SCANCODE_Z,1,1,0));
    keys.fill(0);tb_friends_update_keyboard(keys.data(),keys.size());assert(!tb_friends_keyboard_captured());
    tb_keyboard_down(SDL_SCANCODE_Z);assert(tb_keyboard_take()==0x4000);
    tb_friends_input_mode(1,1);tb_friends_sdl_sync_text();
    tb_friends_text("pending");
    event={};event.type=SDL_WINDOWEVENT;event.window.event=SDL_WINDOWEVENT_FOCUS_LOST;
    tb_friends_sdl_event(&event);assert(!SDL_IsTextInputActive() && !tb_friends_window_focused());
    assert(tb_friends_take_input(&input) && input.kind==TB_FRIENDS_FOCUS_LOST);
    assert(!tb_friends_take_input(&input)); // Queued typing was discarded on focus loss.
    tb_friends_input_mode(1,0);
    event.window.event=SDL_WINDOWEVENT_FOCUS_GAINED;tb_friends_sdl_event(&event);
    assert(tb_friends_window_focused() && !SDL_IsTextInputActive());
    tb_friends_input_mode(0,0);tb_friends_sdl_finish();
    SDL_JoystickDetachVirtual(reconnected);SDL_DestroyWindow(window);SDL_Quit();
}
