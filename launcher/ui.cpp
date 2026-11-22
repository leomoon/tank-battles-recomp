// Launcher window for starting the game and managing online accounts.
// A small immediate-mode interface drawn with SDL's renderer and the built-in
// 5x7 font, so the folder needs no toolkit or font files.
#include "ui.h"

#include "font5x7.h"

#define SDL_MAIN_HANDLED
#include <SDL.h>

#include <algorithm>
#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <functional>
#include <iterator>
#include <mutex>
#include <thread>
#include <vector>

namespace tb::launcher {

namespace {

constexpr int logical_width = 480, logical_height = 320, margin = 24;
struct Color { Uint8 r, g, b; };
constexpr Color background{18, 29, 43}, panel{28, 42, 58}, border{78, 101, 121}, accent{255, 199, 102},
    white{245, 249, 255}, muted{178, 198, 216}, good{140, 220, 150}, bad{255, 140, 130}, focus_ring{255, 199, 102};

struct Input {
    int mouse_x = -1, mouse_y = -1;
    bool clicked = false, enter = false, escape = false, tab = false, backtab = false, backspace = false, space = false;
    std::string typed;
};

class Painter {
public:
    Painter(SDL_Renderer* renderer) : renderer_(renderer) {
        SDL_Surface* surface = SDL_CreateRGBSurfaceWithFormat(0, 95 * 6, 7, 32, SDL_PIXELFORMAT_RGBA32);
        auto* pixels = static_cast<Uint32*>(surface->pixels);
        const int pitch = surface->pitch / 4;
        for (int glyph = 0; glyph < 95; ++glyph)
            for (int row = 0; row < 7; ++row)
                for (int column = 0; column < 5; ++column)
                    if (font5x7[glyph][row] & (16 >> column))
                        pixels[row * pitch + glyph * 6 + column] = SDL_MapRGBA(surface->format, 255, 255, 255, 255);
        font_ = SDL_CreateTextureFromSurface(renderer, surface);
        SDL_SetTextureBlendMode(font_, SDL_BLENDMODE_BLEND);
        SDL_FreeSurface(surface);
    }
    ~Painter() { SDL_DestroyTexture(font_); }

    void fill(int x, int y, int w, int h, Color c) {
        SDL_SetRenderDrawColor(renderer_, c.r, c.g, c.b, 255);
        SDL_Rect rect{x, y, w, h};
        SDL_RenderFillRect(renderer_, &rect);
    }
    void frame(int x, int y, int w, int h, Color c) {
        SDL_SetRenderDrawColor(renderer_, c.r, c.g, c.b, 255);
        SDL_Rect rect{x, y, w, h};
        SDL_RenderDrawRect(renderer_, &rect);
    }
    static int width(const std::string& text, int scale = 1) { return static_cast<int>(text.size()) * 6 * scale - scale; }
    void text(int x, int y, const std::string& text, Color c, int scale = 1) {
        SDL_SetTextureColorMod(font_, c.r, c.g, c.b);
        for (const char ch : text) {
            const int index = (ch >= 32 && ch < 127) ? ch - 32 : '?' - 32;
            SDL_Rect source{index * 6, 0, 5, 7}, target{x, y, 5 * scale, 7 * scale};
            SDL_RenderCopy(renderer_, font_, &source, &target);
            x += 6 * scale;
        }
    }
    // Word-wrapped; returns the height used.
    int paragraph(int x, int y, int w, const std::string& text, Color c) {
        const size_t columns = static_cast<size_t>(std::max(1, (w + 1) / 6));
        std::string line, word;
        int lines = 0;
        auto flush = [&] { this->text(x, y + lines * 10, line, c); line.clear(); ++lines; };
        for (size_t i = 0; i <= text.size(); ++i) {
            if (i == text.size() || text[i] == ' ') {
                if (line.size() + (line.empty() ? 0 : 1) + word.size() > columns && !line.empty()) flush();
                line += (line.empty() ? "" : " ") + word;
                word.clear();
            } else {
                word += text[i];
            }
        }
        if (!line.empty()) flush();
        return lines * 10;
    }

private:
    SDL_Renderer* renderer_;
    SDL_Texture* font_;
};

class Form {
public:
    Form(Painter& painter, Input& input, int& focus) : p_(painter), in_(input), focus_(focus) {}

    bool button(int x, int y, int w, const std::string& label, bool primary = false, bool enabled = true) {
        const int id = next();
        const int h = 18;
        const bool hover = enabled && inside(x, y, w, h);
        const bool focused = enabled && focus_ == id;
        p_.fill(x, y, w, h, primary && enabled ? Color{70, 96, 122} : panel);
        p_.frame(x, y, w, h, focused ? focus_ring : hover ? white : border);
        p_.text(x + (w - Painter::width(label)) / 2, y + 6, label, enabled ? (primary ? white : muted) : border);
        if (hover && in_.clicked) focus_ = id;
        return enabled && ((hover && in_.clicked) || (focused && (in_.enter || in_.space)));
    }

    // Returns true when Enter is pressed in this field.
    bool field(int x, int y, int w, const std::string& label, std::string& value, bool masked = false,
               size_t limit = 64, bool enabled = true) {
        const int id = next();
        const bool focused = enabled && focus_ == id;
        p_.text(x, y, label, muted);
        const int box = y + 10, h = 16;
        p_.fill(x, box, w, h, Color{12, 20, 30});
        p_.frame(x, box, w, h, focused ? focus_ring : border);
        if (enabled && in_.clicked && inside(x, box, w, h)) focus_ = id;
        bool submit = false;
        if (focused) {
            for (const char ch : in_.typed)
                if (ch >= 32 && ch < 127 && value.size() < limit) value += ch;
            if (in_.backspace && !value.empty()) value.pop_back();
            submit = in_.enter;
        }
        std::string shown = masked ? std::string(value.size(), '*') : value;
        const size_t visible = static_cast<size_t>((w - 8) / 6);
        if (shown.size() >= visible) shown = shown.substr(shown.size() - visible + 1);
        p_.text(x + 4, box + 5, shown + (focused && (SDL_GetTicks() / 500) % 2 ? "_" : ""), enabled ? white : border);
        return submit;
    }

    void checkbox(int x, int y, const std::string& label, bool& value) {
        const int id = next();
        const bool focused = focus_ == id;
        const int w = 12 + Painter::width(label);
        p_.frame(x, y, 9, 9, focused ? focus_ring : border);
        if (value) p_.fill(x + 2, y + 2, 5, 5, accent);
        p_.text(x + 14, y + 1, label, white);
        if (inside(x, y - 2, w, 13) && in_.clicked) { focus_ = id; value = !value; }
        else if (focused && (in_.space || in_.enter)) value = !value;
    }

    int count() const { return count_; }

private:
    int next() { return count_++; }
    bool inside(int x, int y, int w, int h) const {
        return in_.mouse_x >= x && in_.mouse_x < x + w && in_.mouse_y >= y && in_.mouse_y < y + h;
    }
    Painter& p_;
    Input& in_;
    int& focus_;
    int count_ = 0;
};

enum class Screen { home, sign_in, create, request_reset, reset_password };

struct State {
    Screen screen = Screen::home;
    std::string server, npid, password, confirm, email, token, ca_path, reset_code;
    bool remember = true, advanced = false, show_token = false;
    std::string message;
    bool message_good = false;
    std::optional<std::pair<Account, std::string>> session;   // Password known for this run.
};

struct Worker {
    std::thread thread;
    std::mutex lock;
    std::optional<Result> result;
    std::atomic<bool> running{false};

    template <class Job> void start(Job job, Uint32 wake) {
        running = true;
        thread = std::thread([this, job, wake] {
            Result outcome;
            try {
                outcome = job();
            } catch (const std::exception& error) {
                outcome = {"failed", error.what()};
            }
            { std::lock_guard guard(lock); result = std::move(outcome); }
            running = false;
            SDL_Event event{};
            event.type = wake;
            SDL_PushEvent(&event);
        });
    }
    std::optional<Result> take() {
        std::lock_guard guard(lock);
        if (!result) return std::nullopt;
        if (thread.joinable()) thread.join();
        auto outcome = result;
        result.reset();
        return outcome;
    }
    ~Worker() { if (thread.joinable()) thread.join(); }
};

Account account_from(const State& state, Accounts& accounts) {
    Account account{state.server, state.npid, state.token};
    if (state.advanced && !state.ca_path.empty()) {
        fs::path chosen = from_utf8(state.ca_path);
        account.ca = chosen.parent_path() == accounts.folder().online() ? chosen
                                                                         : accounts.import_ca(chosen, state.server);
    }
    return account;
}

void fill_from(State& state, const Account& account) {
    state.server = account.endpoint;
    state.npid = account.npid;
    state.token = account.token;
    state.show_token = !account.token.empty();
    state.ca_path = account.ca.empty() ? std::string() : utf8(account.ca);
    state.advanced = !account.ca.empty();
    state.password.clear();
    state.confirm.clear();
}

std::optional<fs::path> smoke_output_directory() {
#ifdef _WIN32
    // SDL_getenv uses the Windows narrow code page, but SDL_SaveBMP expects UTF-8.
    constexpr wchar_t variable[] = L"TB_LAUNCHER_UI_SMOKE";
    SetLastError(ERROR_SUCCESS);
    const DWORD size = GetEnvironmentVariableW(variable, nullptr, 0);
    if (!size) {
        if (GetLastError() == ERROR_SUCCESS) return fs::path{};
        return std::nullopt;
    }
    std::wstring value(size, L'\0');
    const DWORD length = GetEnvironmentVariableW(variable, value.data(), size);
    if (length >= size || !length) return std::nullopt;
    value.resize(length);
    return fs::path(value);
#else
    if (const char* value = std::getenv("TB_LAUNCHER_UI_SMOKE")) return fs::path(value);
    return std::nullopt;
#endif
}

}  // namespace

std::optional<Choice> run_ui(Accounts& accounts) {
    SDL_SetMainReady();
    SDL_SetHint(SDL_HINT_VIDEO_X11_NET_WM_BYPASS_COMPOSITOR, "0");
    if (SDL_Init(SDL_INIT_VIDEO) != 0) return std::nullopt;
    SDL_DisplayMode mode{};
    int scale = 2;
    if (SDL_GetDesktopDisplayMode(0, &mode) == 0) scale = std::clamp(mode.h * 6 / 10 / logical_height, 1, 4);
    SDL_Window* window = SDL_CreateWindow("Tank Battles", SDL_WINDOWPOS_CENTERED, SDL_WINDOWPOS_CENTERED,
                                          logical_width * scale, logical_height * scale, SDL_WINDOW_RESIZABLE);
    if (!window) { SDL_Quit(); return std::nullopt; }
    SDL_Renderer* renderer = SDL_CreateRenderer(window, -1, SDL_RENDERER_ACCELERATED | SDL_RENDERER_PRESENTVSYNC);
    if (!renderer) renderer = SDL_CreateRenderer(window, -1, SDL_RENDERER_SOFTWARE);
    if (!renderer) { SDL_DestroyWindow(window); SDL_Quit(); return std::nullopt; }
    SDL_RenderSetLogicalSize(renderer, logical_width, logical_height);
    SDL_RenderSetIntegerScale(renderer, SDL_TRUE);
    SDL_StartTextInput();
    const Uint32 wake = SDL_RegisterEvents(1);

    std::optional<Choice> choice;
    {
        Painter painter(renderer);
        State state;
        Worker worker;
        int focus = 0, focus_count = 1;
        if (auto active = accounts.active()) {
            if (auto password = accounts.remembered_password(*active)) state.session = std::make_pair(*active, *password);
            fill_from(state, *active);
        }
        auto go = [&](Screen screen) {
            state.screen = screen;
            state.message.clear();
            focus = 0;
        };
        // Smoke mode for tests: draw each screen once and leave.
        const auto smoke = smoke_output_directory();
        int smoke_frames = 0;

        while (!choice) {
            Input input;
            SDL_Event event;
            bool have = SDL_WaitEventTimeout(&event, 250);
            while (have) {
                switch (event.type) {
                case SDL_QUIT: choice = Choice{}; break;
                case SDL_MOUSEMOTION: input.mouse_x = event.motion.x; input.mouse_y = event.motion.y; break;
                case SDL_MOUSEBUTTONDOWN:
                    if (event.button.button == SDL_BUTTON_LEFT) {
                        input.clicked = true;
                        input.mouse_x = event.button.x;
                        input.mouse_y = event.button.y;
                    }
                    break;
                case SDL_TEXTINPUT: input.typed += event.text.text; break;
                case SDL_DROPFILE:
                    if (state.screen != Screen::home && !worker.running) {
                        state.advanced = true;
                        state.ca_path = event.drop.file;
                    }
                    SDL_free(event.drop.file);
                    break;
                case SDL_KEYDOWN: {
                    const auto key = event.key.keysym.sym;
                    const bool shift = event.key.keysym.mod & KMOD_SHIFT;
                    if (key == SDLK_RETURN || key == SDLK_KP_ENTER) input.enter = true;
                    else if (key == SDLK_ESCAPE) input.escape = true;
                    else if (key == SDLK_TAB) (shift ? input.backtab : input.tab) = true;
                    else if (key == SDLK_BACKSPACE) input.backspace = true;
                    else if (key == SDLK_SPACE) input.space = true;
                    else if (key == SDLK_DOWN) input.tab = true;
                    else if (key == SDLK_UP) input.backtab = true;
                    else if (key == SDLK_v && (event.key.keysym.mod & KMOD_CTRL)) {
                        if (char* clip = SDL_GetClipboardText()) { input.typed += clip; SDL_free(clip); }
                    }
                    break;
                }
                default: break;
                }
                have = SDL_PollEvent(&event);
            }
            if (choice) break;
            // Mouse events already arrive in logical coordinates; the polled state does not.
            if (int mouse_x, mouse_y; input.mouse_x < 0) {
                SDL_GetMouseState(&mouse_x, &mouse_y);
                float lx = 0, ly = 0;
                SDL_RenderWindowToLogical(renderer, mouse_x, mouse_y, &lx, &ly);
                input.mouse_x = static_cast<int>(lx);
                input.mouse_y = static_cast<int>(ly);
            }
            if (input.tab) focus = (focus + 1) % std::max(1, focus_count);
            if (input.backtab) focus = (focus + focus_count - 1) % std::max(1, focus_count);
            // Space types in text fields; it only presses buttons and boxes.
            const bool busy = worker.running;
            if (busy) input = Input{input.mouse_x, input.mouse_y};
            if (auto result = worker.take()) {
                state.message = result->message;
                state.message_good = result->ok();
                if (result->code == "reset_sent") {
                    state.screen = Screen::reset_password;
                    focus = 2;
                } else if (result->code == "password_reset") {
                    if (state.session && state.session->first.id() == Account{state.server, state.npid, {}}.id())
                        state.session.reset();
                    state.reset_code.clear();
                    state.password.clear();
                    state.confirm.clear();
                    state.email.clear();
                    state.screen = Screen::sign_in;
                    focus = 2;
                } else if (result->ok()) {
                    state.session = std::make_pair(*accounts.active(), state.password);
                    state.password.clear();
                    state.confirm.clear();
                    state.screen = Screen::home;
                    focus = 0;
                } else if (result->code == "token_required") {
                    state.screen = Screen::sign_in;
                    state.show_token = true;
                }
            }

            painter.fill(0, 0, logical_width, logical_height, background);
            painter.fill(0, 0, logical_width, 3, accent);
            painter.text(margin, 14, "TANK BATTLES", white, 3);
            Form form(painter, input, focus);
            const int column = logical_width - 2 * margin;
            int y = 48;
            const auto active = accounts.active();

            if (state.screen == Screen::home) {
                if (input.escape) choice = Choice{};
                std::string status = "Offline.";
                if (active && state.session && state.session->first.id() == active->id())
                    status = "Signed in as " + active->npid + " on " + active->endpoint + ".";
                else if (active)
                    status = active->npid + " on " + active->endpoint + ": sign in to play online.";
                painter.text(margin, y, status, muted);
                y += 18;
                const bool online_ready = active && state.session && state.session->first.id() == active->id();
                if (online_ready) {
                    if (form.button(margin, y, column, "Play online as " + active->npid, true))
                        choice = Choice{Choice::online, state.session};
                    y += 24;
                    if (form.button(margin, y, column, "Play offline")) choice = Choice{Choice::offline};
                } else {
                    if (form.button(margin, y, column, "Play offline", true)) choice = Choice{Choice::offline};
                    if (active) {
                        y += 24;
                        if (form.button(margin, y, column, "Sign in as " + active->npid)) { fill_from(state, *active); go(Screen::sign_in); }
                    }
                }
                y += 30;
                const int half = (column - 8) / 2;
                if (form.button(margin, y, half, "Sign in")) {
                    if (active) fill_from(state, *active);
                    go(Screen::sign_in);
                }
                if (form.button(margin + half + 8, y, half, "Create account")) {
                    state.npid.clear(); state.password.clear(); state.confirm.clear(); state.token.clear();
                    state.show_token = false;
                    go(Screen::create);
                }
                if (active) {
                    y += 24;
                    if (form.button(margin, y, half, "Sign out")) {
                        accounts.sign_out();
                        state.session.reset();
                        state.message = "Signed out. The game will start offline.";
                        state.message_good = true;
                    }
                    if (form.button(margin + half + 8, y, half, "Forget " + active->npid)) {
                        accounts.forget(*active);
                        state.session.reset();
                        state.message = "Removed the saved account and its password from this computer.";
                        state.message_good = true;
                    }
                }
                y += 30;
                if (!state.message.empty())
                    y += painter.paragraph(margin, y, column, state.message, state.message_good ? good : bad) + 4;
                painter.paragraph(margin, logical_height - 34, column,
                                  "Online accounts belong to the RPCN server you choose. They are not PlayStation Network accounts.",
                                  border);
            } else {
                const bool signing_in = state.screen == Screen::sign_in;
                const bool creating = state.screen == Screen::create;
                const bool requesting_reset = state.screen == Screen::request_reset;
                const bool setting_password = state.screen == Screen::reset_password;
                const char* heading = signing_in ? "Sign in" : creating ? "Create an account" : "Reset password";
                painter.text(margin, y - 4, heading, accent, 2);
                y += 16;
                const int half = (column - 8) / 2;
                const auto previous_server = state.server;
                bool submit = form.field(margin, y, half, "Server", state.server, false, 80, !busy);
                if (state.server != previous_server) {
                    // A private CA and sign-in token belong to the previous server.
                    state.ca_path.clear();
                    state.token.clear();
                    state.show_token = false;
                    state.reset_code.clear();
                }
                const auto previous_user = state.npid;
                submit |= form.field(margin + half + 8, y, half, "Username", state.npid, false, 16, !busy);
                if (state.npid != previous_user) state.reset_code.clear();
                y += 30;
                if (creating || requesting_reset) {
                    submit |= form.field(margin, y, column, requesting_reset ? "Email used for this account" : "Email (for this server only)",
                                         state.email, false, 120, !busy);
                    y += 30;
                } else if (setting_password) {
                    submit |= form.field(margin, y, column, "Reset code from email", state.reset_code, true, 64, !busy);
                    y += 30;
                }
                if (creating || setting_password) {
                    submit |= form.field(margin, y, half, setting_password ? "New password" : "Password",
                                         state.password, true, 64, !busy);
                    submit |= form.field(margin + half + 8, y, half, "Repeat password", state.confirm, true, 64, !busy);
                } else if (signing_in) {
                    submit |= form.field(margin, y, half, "Password", state.password, true, 64, !busy);
                    if (state.show_token)
                        submit |= form.field(margin + half + 8, y, half, "Token from email", state.token, false, 64, !busy);
                }
                if (!requesting_reset) y += 32;
                if (signing_in || creating) form.checkbox(margin, y, "Remember me on this computer", state.remember);
                form.checkbox(margin + half + 8, y, "Advanced", state.advanced);
                y += 14;
                if (state.advanced) {
                    submit |= form.field(margin, y, column, "Private server certificate (PEM path, or drop the file here)",
                                         state.ca_path, false, 400, !busy);
                    y += 30;
                }
                const int buttons = logical_height - 58;
                const bool extra_action = signing_in || requesting_reset;
                const int button_width = extra_action ? (column - 16) / 3 : half;
                const char* action = signing_in ? "Sign in" : creating ? "Create account" :
                                     requesting_reset ? "Send reset code" : "Change password";
                const bool pressed = form.button(margin, buttons, button_width, busy ? "Contacting server..." : action, true, !busy);
                const bool extra = extra_action && form.button(margin + button_width + 8, buttons, button_width,
                                                                signing_in ? "Forgot password?" : "I have a code", false, !busy);
                const bool back = form.button(margin + column - button_width, buttons, button_width, "Back", false, !busy);
                if ((back || input.escape) && !busy) {
                    if (requesting_reset || setting_password) {
                        state.password.clear();
                        state.confirm.clear();
                        state.reset_code.clear();
                        go(setting_password ? Screen::request_reset : Screen::sign_in);
                    } else {
                        go(Screen::home);
                    }
                } else if (extra) {
                    state.password.clear();
                    state.confirm.clear();
                    state.reset_code.clear();
                    if (signing_in) state.email.clear();
                    go(signing_in ? Screen::request_reset : Screen::reset_password);
                    focus = 2;
                } else if ((pressed || submit) && !busy) {
                    if ((creating || setting_password) && state.password != state.confirm) {
                        state.message = "The two passwords are different.";
                        state.message_good = false;
                    } else {
                        try {
                            Account account = account_from(state, accounts);
                            const std::string password = state.password, email = state.email;
                            const bool remember = state.remember;
                            state.message = "Contacting " + account.endpoint + "...";
                            state.message_good = true;
                            if (requesting_reset) {
                                account.token.clear();
                                worker.start([&accounts, account, email] {
                                    return accounts.request_reset(account, email); }, wake);
                            } else if (setting_password) {
                                account.token.clear();
                                const std::string code = state.reset_code;
                                worker.start([&accounts, account, code, password] {
                                    return accounts.reset_password(account, code, password); }, wake);
                            } else if (creating)
                                worker.start([&accounts, account, email, password, remember] {
                                    return accounts.create(account, email, password, remember); }, wake);
                            else
                                worker.start([&accounts, account, password, remember] {
                                    return accounts.sign_in(account, password, remember); }, wake);
                        } catch (const std::exception& error) {
                            state.message = error.what();
                            state.message_good = false;
                        }
                    }
                }
                if (!state.message.empty())
                    painter.paragraph(margin, std::min(y + 2, buttons - 24), column, state.message,
                                      state.message_good ? good : bad);
            }
            focus_count = form.count();
            if (smoke && !smoke->empty()) {
                // TB_LAUNCHER_UI_SMOKE=DIR also saves each screen for review.
                int w = 0, h = 0;
                SDL_GetRendererOutputSize(renderer, &w, &h);
                if (SDL_Surface* shot = SDL_CreateRGBSurfaceWithFormat(0, w, h, 32, SDL_PIXELFORMAT_ARGB8888)) {
                    const auto path = *smoke / ("screen-" + std::to_string(smoke_frames) + ".bmp");
                    if (SDL_RenderReadPixels(renderer, nullptr, SDL_PIXELFORMAT_ARGB8888, shot->pixels, shot->pitch) != 0 ||
                        SDL_SaveBMP(shot, utf8(path).c_str()) != 0)
                        std::fprintf(stderr, "Could not capture launcher screen %d: %s\n", smoke_frames, SDL_GetError());
                    SDL_FreeSurface(shot);
                } else {
                    std::fprintf(stderr, "Could not allocate launcher screenshot: %s\n", SDL_GetError());
                }
            }
            SDL_RenderPresent(renderer);
            if (smoke) {
                static const Screen order[] = {Screen::home, Screen::sign_in, Screen::create, Screen::request_reset, Screen::reset_password};
                if (++smoke_frames >= static_cast<int>(std::size(order))) choice = Choice{};
                else state.screen = order[smoke_frames];
            }
        }
    }
    SDL_StopTextInput();
    SDL_DestroyRenderer(renderer);
    SDL_DestroyWindow(window);
    SDL_Quit();
    return choice;
}

}  // namespace tb::launcher
