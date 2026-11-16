#pragma once
/* sdl2-compat event conversion and hotplug must not run concurrently on the
 * guest polling thread and the window owner. SDL's recursive joystick lock
 * also keeps instance-index lists stable through hotplug transactions. */
static inline void tb_sdl_gate_end(int* held) { if(*held)SDL_UnlockJoysticks(); }
#define TB_SDL_EVENT_GATE() \
    SDL_LockJoysticks(); \
    int tb_sdl_gate __attribute__((cleanup(tb_sdl_gate_end))) = 1
