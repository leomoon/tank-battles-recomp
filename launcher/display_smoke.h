#pragma once

// Deterministic keyboard/controller navigation for the installed builder's game-free
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
        else {
            const std::string_view scenario(name);
            controller_ = scenario.starts_with("pad-scale-");
            const std::string prefix = controller_ ? "pad-scale-" : "scale-";
            const auto selected = std::find_if(display::render_presets.begin(), display::render_presets.end(),
                [&](int percent) { return scenario == prefix + std::to_string(percent); });
            if (selected == display::render_presets.end())
                throw std::runtime_error("Unknown launcher display smoke scenario");
            // Restore the draft defaults through the UI before selecting the
            // requested value, independent of the previously saved preset.
            sequence_ = {0, backtab, SDLK_RETURN, SDLK_UP, SDLK_RETURN,
                         SDLK_DOWN, SDLK_DOWN, SDLK_DOWN};
            const auto native = std::find(display::render_presets.begin(), display::render_presets.end(), 100);
            const auto delta = selected - native;
            sequence_.insert(sequence_.end(), static_cast<size_t>(std::abs(delta)), delta < 0 ? SDLK_LEFT : SDLK_RIGHT);
            sequence_.insert(sequence_.end(), {SDLK_DOWN, SDLK_RETURN, SDLK_DOWN, SDLK_RETURN});
            steps_ = sequence_;
            if (controller_) {
                virtual_pad_ = SDL_JoystickAttachVirtual(SDL_JOYSTICK_TYPE_GAMECONTROLLER,
                    SDL_CONTROLLER_AXIS_MAX, SDL_CONTROLLER_BUTTON_MAX, 0);
                if (virtual_pad_ < 0 || !SDL_IsGameController(virtual_pad_))
                    throw std::runtime_error("Could not create launcher smoke controller");
                instance_ = SDL_JoystickGetDeviceInstanceID(virtual_pad_);
            }
        }
    }

    ~DisplaySmoke() { if (virtual_pad_ >= 0) SDL_JoystickDetachVirtual(virtual_pad_); }

    void next() {
        if (steps_.empty()) return;
        SDL_Event event{};
        if (position_ >= steps_.size()) {
            std::fprintf(stderr, "[launcher] Display smoke did not complete its navigation\n");
            event.type = SDL_QUIT;
        } else {
            const auto key = steps_[position_++];
            if (!key) return; // First draw establishes home focus order.
            if (controller_) {
                event.type = SDL_CONTROLLERBUTTONDOWN;
                event.cbutton.which = instance_;
                switch (key) {
                case SDLK_RETURN: event.cbutton.button = SDL_CONTROLLER_BUTTON_A; break;
                case backtab: case SDLK_UP: event.cbutton.button = SDL_CONTROLLER_BUTTON_DPAD_UP; break;
                case SDLK_DOWN: event.cbutton.button = SDL_CONTROLLER_BUTTON_DPAD_DOWN; break;
                case SDLK_LEFT: event.cbutton.button = SDL_CONTROLLER_BUTTON_DPAD_LEFT; break;
                case SDLK_RIGHT: event.cbutton.button = SDL_CONTROLLER_BUTTON_DPAD_RIGHT; break;
                default: throw std::runtime_error("Unknown launcher smoke controller action");
                }
            } else {
                event.type = SDL_KEYDOWN;
                event.key.keysym.sym = key == backtab ? SDLK_TAB : key;
                if (key == backtab) event.key.keysym.mod = KMOD_SHIFT;
            }
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
    std::vector<SDL_Keycode> sequence_;
    size_t position_ = 0;
    bool controller_ = false;
    int virtual_pad_ = -1;
    SDL_JoystickID instance_ = -1;
};
