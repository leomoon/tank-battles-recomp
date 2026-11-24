/* Drive one launcher frame per scripted SDL input under the dummy driver. */
#include <SDL.h>
#include <assert.h>
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static SDL_Window* launcher_window;

SDL_Window* SDLCALL SDL_CreateWindow(const char* title, int x, int y, int w, int h, Uint32 flags)
{
    SDL_Window* (SDLCALL *create_window)(const char*, int, int, int, int, Uint32) =
        dlsym(RTLD_NEXT, "SDL_CreateWindow");
    assert(create_window);
    return launcher_window = create_window(title, x, y, w, h, flags);
}

int SDLCALL SDL_WaitEventTimeout(SDL_Event* event, int timeout)
{
    static int (SDLCALL *wait_event)(SDL_Event*, int);
    static const char* next;
    static SDL_JoystickID pad;
    if (!wait_event) {
        wait_event = dlsym(RTLD_NEXT, "SDL_WaitEventTimeout");
        assert(wait_event);
        next = getenv("TB_TEST_LAUNCHER_INPUT");
    }
    if (timeout == 0 || !next || !*next) return wait_event(event, timeout);
    char token[32];
    const size_t length = strcspn(next, " ");
    assert(length && length < sizeof token);
    memcpy(token, next, length);
    token[length] = 0;
    next += length + (next[length] == ' ');
    memset(event, 0, sizeof *event);
    if (!strcmp(token, "idle")) return 0;
    if (!strcmp(token, "pad")) {
        const int index = SDL_JoystickAttachVirtual(SDL_JOYSTICK_TYPE_GAMECONTROLLER,
                                                   SDL_CONTROLLER_AXIS_MAX, SDL_CONTROLLER_BUTTON_MAX, 0);
        assert(index >= 0 && SDL_IsGameController(index));
        pad = SDL_JoystickGetDeviceInstanceID(index);
        event->type = SDL_CONTROLLERDEVICEADDED;
        event->cdevice.which = index;
    } else if (token[0] == '@') {
        int x, y, width, height;
        assert(sscanf(token + 1, "%d,%d", &x, &y) == 2);
        assert(launcher_window);
        SDL_GetWindowSize(launcher_window, &width, &height);
        event->type = SDL_MOUSEBUTTONDOWN;
        event->button.button = SDL_BUTTON_LEFT;
        event->button.x = width * x / 320;
        event->button.y = height * y / 180;
    } else if (token[0] == 'p' && token[1] != 0) {
        event->type = SDL_CONTROLLERBUTTONDOWN;
        event->cbutton.which = pad;
        switch (token[1]) {
        case 'a': event->cbutton.button = SDL_CONTROLLER_BUTTON_A; break;
        case 'b': event->cbutton.button = SDL_CONTROLLER_BUTTON_B; break;
        case 'u': event->cbutton.button = SDL_CONTROLLER_BUTTON_DPAD_UP; break;
        case 'd': event->cbutton.button = SDL_CONTROLLER_BUTTON_DPAD_DOWN; break;
        default: abort();
        }
    } else {
        event->type = SDL_KEYDOWN;
        if (!strcmp(token, "shift-tab")) {
            event->key.keysym.sym = SDLK_TAB;
            event->key.keysym.mod = KMOD_SHIFT;
        } else {
            event->key.keysym.sym = SDL_GetKeyFromName(token);
            assert(event->key.keysym.sym != SDLK_UNKNOWN);
        }
    }
    return 1;
}
