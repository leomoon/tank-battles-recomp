#pragma once

// Deterministic keyboard navigation for the installed builder's game-free
// consumer check. All mutations go through the same controls as user input.
class DisplaySmoke {
public:
    DisplaySmoke() {
        const char* name = std::getenv("TB_LAUNCHER_DISPLAY_SMOKE");
        if (!name) return;
        if (!std::strcmp(name, "save")) steps_ = save_;
        else if (!std::strcmp(name, "cancel")) steps_ = cancel_;
        else if (!std::strcmp(name, "defaults")) steps_ = defaults_;
        else if (!std::strcmp(name, "preset")) steps_ = preset_;
        else throw std::runtime_error("Unknown launcher display smoke scenario");
    }

    void next() {
        if (steps_.empty()) return;
        SDL_Event event{};
        if (position_ >= steps_.size()) {
            std::fprintf(stderr, "[launcher] Display smoke did not complete its navigation\n");
            event.type = SDL_QUIT;
        } else {
            const auto key = steps_[position_++];
            if (!key) return; // First draw establishes home focus order.
            event.type = SDL_KEYDOWN;
            event.key.keysym.sym = key == backtab ? SDLK_TAB : key;
            if (key == backtab) event.key.keysym.mod = KMOD_SHIFT;
        }
        SDL_PushEvent(&event);
    }

private:
    static constexpr SDL_Keycode backtab = -1;
    static constexpr SDL_Keycode save_[] = {
        0, backtab, SDLK_RETURN, SDLK_RIGHT, SDLK_DOWN, SDLK_RIGHT, SDLK_DOWN,
        SDLK_RETURN, SDLK_DOWN, SDLK_RETURN
    };
    static constexpr SDL_Keycode cancel_[] = {
        0, backtab, SDLK_RETURN, SDLK_LEFT, SDLK_DOWN, SDLK_RIGHT, SDLK_DOWN,
        SDLK_RIGHT, SDLK_ESCAPE, SDLK_ESCAPE
    };
    static constexpr SDL_Keycode defaults_[] = {
        0, backtab, SDLK_RETURN, SDLK_UP, SDLK_RETURN, SDLK_UP, SDLK_UP,
        SDLK_RETURN, SDLK_ESCAPE
    };
    static constexpr SDL_Keycode preset_[] = {
        0, backtab, SDLK_RETURN, SDLK_DOWN, SDLK_RIGHT, SDLK_DOWN, SDLK_RIGHT,
        SDLK_RIGHT, SDLK_DOWN, SDLK_RETURN, SDLK_DOWN, SDLK_RETURN
    };
    std::span<const SDL_Keycode> steps_;
    size_t position_ = 0;
};
