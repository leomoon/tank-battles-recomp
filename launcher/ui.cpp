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
#include <string_view>
#include <thread>
#include <vector>

namespace tb::launcher {

namespace {

// Match the standard Friends panel at the same integer display scale.
constexpr int logical_width = 320, logical_height = 180, margin = 12;
constexpr int content_width = logical_width - 2 * margin;
struct Color { Uint8 r, g, b; };
constexpr Color background{18, 29, 43}, panel{28, 42, 58}, border{78, 101, 121}, accent{255, 199, 102},
    white{245, 249, 255}, muted{178, 198, 216}, good{140, 220, 150}, bad{255, 140, 130}, focus_ring{255, 199, 102};

struct Input {
    int mouse_x = -1, mouse_y = -1;
    bool clicked = false, enter = false, escape = false, tab = false, backtab = false, backspace = false, space = false;
    bool up = false, down = false, left = false, right = false;
    bool pad_confirm = false, done = false;
    std::string typed;
};

struct Presentation {
    SDL_Rect area{};
    int width = 0, height = 0;

    Presentation(SDL_Renderer* renderer, int max_scale) {
        if (SDL_GetRendererOutputSize(renderer, &width, &height) != 0 || width <= 0 || height <= 0) return;
        const int scale = std::min({max_scale, width / logical_width, height / logical_height});
        const int card_width = scale ? logical_width * scale : std::max(1, std::min(width, height * logical_width / logical_height));
        const int card_height = scale ? logical_height * scale : std::max(1, card_width * logical_height / logical_width);
        area = {(width - card_width) / 2, (height - card_height) / 2, card_width, card_height};
    }

    void map_mouse(SDL_Window* window, Input& input) const {
        int window_width = 0, window_height = 0;
        SDL_GetWindowSize(window, &window_width, &window_height);
        if (!input.clicked) SDL_GetMouseState(&input.mouse_x, &input.mouse_y);
        if (window_width <= 0 || window_height <= 0 || !area.w || !area.h) {
            input.mouse_x = input.mouse_y = -1;
            return;
        }
        const SDL_Point point{
            input.mouse_x * width / window_width - area.x,
            input.mouse_y * height / window_height - area.y,
        };
        if (point.x < 0 || point.x >= area.w || point.y < 0 || point.y >= area.h) {
            input.mouse_x = input.mouse_y = -1;
            return;
        }
        input.mouse_x = point.x * logical_width / area.w;
        input.mouse_y = point.y * logical_height / area.h;
    }

    void compose(SDL_Renderer* renderer, SDL_Texture* canvas) const {
        SDL_SetRenderTarget(renderer, nullptr);
        SDL_SetRenderDrawColor(renderer, 0, 0, 0, 255);
        SDL_RenderClear(renderer);
        if (area.w && area.h) SDL_RenderCopy(renderer, canvas, nullptr, &area);
    }
};

void append_text(std::string& value, std::string_view text, size_t limit) {
    for (const char ch : text)
        if (ch >= 32 && ch < 127 && value.size() < limit) value += ch;
}

std::string display_text(const std::string& value, bool masked, size_t columns) {
    const auto count = std::min(value.size(), columns);
    return masked ? std::string(count, '*') : value.substr(value.size() - count);
}

struct TextEntry {
    std::string* target = nullptr;
    std::string label, draft;
    size_t limit = 0;
    int focus = 10;
    bool masked = false, uppercase = false, symbols = false, accepted = false;

    bool editing() const { return target && !accepted; }
    void begin(std::string& value, const std::string& name, bool hide, size_t max_length) {
        *this = {};
        target = &value;
        label = name;
        draft = value;
        limit = max_length;
        masked = hide;
    }
    void apply(std::string& value) {
        if (accepted && target == &value) {
            value = std::move(draft);
            *this = {};
        }
    }
};

class Gamepad {
public:
    Gamepad() {
        // Match cellPad's positional Cross/Circle mapping, including Nintendo-mode pads.
        SDL_SetHint(SDL_HINT_GAMECONTROLLER_USE_BUTTON_LABELS, "0");
        if (SDL_InitSubSystem(SDL_INIT_GAMECONTROLLER) == 0) connect();
    }
    ~Gamepad() { if (pad_) SDL_GameControllerClose(pad_); }

    void added(int index) {
        if (!pad_ && SDL_IsGameController(index)) pad_ = SDL_GameControllerOpen(index);
    }
    void removed(SDL_JoystickID instance) {
        if (!matches(instance)) return;
        SDL_GameControllerClose(pad_);
        pad_ = nullptr;
        connect();
    }
    bool matches(SDL_JoystickID instance) const {
        return pad_ && SDL_JoystickInstanceID(SDL_GameControllerGetJoystick(pad_)) == instance;
    }

private:
    void connect() {
        for (int index = 0; !pad_ && index < SDL_NumJoysticks(); ++index) added(index);
    }
    SDL_GameController* pad_ = nullptr;
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
    int paragraph(int x, int y, int w, const std::string& text, Color c, int max_lines = 3) {
        const size_t columns = static_cast<size_t>(std::max(1, (w + 1) / 6));
        std::string line, word;
        int lines = 0;
        auto flush = [&] { this->text(x, y + lines * 8, line.substr(0, columns), c); line.clear(); ++lines; };
        for (size_t i = 0; i <= text.size() && lines < max_lines; ++i) {
            if (i == text.size() || text[i] == ' ') {
                if (line.size() + (line.empty() ? 0 : 1) + word.size() > columns && !line.empty()) flush();
                line += (line.empty() ? "" : " ") + word;
                word.clear();
            } else {
                word += text[i];
            }
        }
        if (!line.empty() && lines < max_lines) flush();
        return lines * 8;
    }

private:
    SDL_Renderer* renderer_;
    SDL_Texture* font_;
};

class Form {
public:
    Form(Painter& painter, Input& input, int& focus, TextEntry* editor = nullptr)
        : p_(painter), in_(input), focus_(focus), editor_(editor) {}

    bool button(int x, int y, int w, const std::string& label, bool primary = false, bool enabled = true) {
        const int id = next();
        const int h = 16;
        const bool hover = enabled && inside(x, y, w, h);
        const bool focused = enabled && focus_ == id;
        p_.fill(x, y, w, h, primary && enabled ? Color{70, 96, 122} : panel);
        p_.frame(x, y, w, h, focused ? focus_ring : hover ? white : border);
        p_.text(x + (w - Painter::width(label)) / 2, y + 5, label, enabled ? (primary ? white : muted) : border);
        if (hover && in_.clicked) focus_ = id;
        return enabled && ((hover && in_.clicked) || (focused && (in_.enter || in_.space || in_.pad_confirm)));
    }

    // Returns true when Enter is pressed in this field.
    bool field(int x, int y, int w, const std::string& label, std::string& value, bool masked = false,
               size_t limit = 64, bool enabled = true) {
        const int id = next();
        if (editor_) editor_->apply(value);
        const int box = y + 8, h = 14, key_width = 20;
        const bool clicked = enabled && in_.clicked && inside(x, box, w, h);
        if (clicked) focus_ = id;
        const bool focused = enabled && focus_ == id;
        const bool open_editor = editor_ && focused &&
            (in_.pad_confirm || (clicked && in_.mouse_x >= x + w - key_width));
        p_.text(x, y, label, muted);
        p_.fill(x, box, w, h, Color{12, 20, 30});
        p_.frame(x, box, w, h, focused ? focus_ring : border);
        p_.text(x + w - key_width + 4, box + 4, "KB", enabled ? muted : border);
        if (open_editor) {
            editor_->begin(value, label, masked, limit);
        } else if (focused) {
            append_text(value, in_.typed, limit);
            if (in_.backspace && !value.empty()) value.pop_back();
        }
        const auto shown = display_text(value, masked, (w - key_width - 10) / 6);
        p_.text(x + 4, box + 4, shown + (focused && (SDL_GetTicks() / 500) % 2 ? "_" : ""), enabled ? white : border);
        return focused && in_.enter && !open_editor;
    }

    void checkbox(int x, int y, const std::string& label, bool& value) {
        const int id = next();
        const bool focused = focus_ == id;
        const int w = 12 + Painter::width(label);
        p_.frame(x, y, 9, 9, focused ? focus_ring : border);
        if (value) p_.fill(x + 2, y + 2, 5, 5, accent);
        p_.text(x + 14, y + 1, label, white);
        if (inside(x, y - 2, w, 13) && in_.clicked) { focus_ = id; value = !value; }
        else if (focused && (in_.space || in_.enter || in_.pad_confirm)) value = !value;
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
    TextEntry* editor_;
    int count_ = 0;
};

void draw_keyboard(Painter& painter, Input& input, TextEntry& editor) {
    if (input.escape) {
        editor = {};
        return;
    }
    append_text(editor.draft, input.typed, editor.limit);
    if (input.backspace && !editor.draft.empty()) editor.draft.pop_back();
    if (input.done || input.enter) {
        editor.accepted = true;
        return;
    }

    constexpr int keys_per_row = 10, action_row = 4, action_count = 6, key_count = keys_per_row * action_row;
    int row = editor.focus / keys_per_row;
    int columns = row == action_row ? action_count : keys_per_row;
    int column = editor.focus % keys_per_row;
    column = (column + columns + int(input.right || input.tab) - int(input.left || input.backtab)) % columns;
    if (input.up || input.down) {
        row = (row + action_row + 1 + int(input.down) - int(input.up)) % (action_row + 1);
        const int next_columns = row == action_row ? action_count : keys_per_row;
        column = (2 * column + 1) * next_columns / (2 * columns);
    }
    editor.focus = row * keys_per_row + column;

    painter.fill(0, 0, logical_width, logical_height, background);
    painter.fill(0, 0, logical_width, 2, accent);
    painter.text(margin, 8, "ON-SCREEN KEYBOARD", white, 2);
    painter.text(margin, 29, editor.label, muted);
    painter.fill(margin, 39, content_width, 17, Color{12, 20, 30});
    painter.frame(margin, 39, content_width, 17, border);
    painter.text(margin + 4, 44, display_text(editor.draft, editor.masked, (content_width - 14) / 6) + "_", white);

    constexpr std::string_view letters = "1234567890" "qwertyuiop" "asdfghjkl-" "zxcvbnm_@.";
    constexpr std::string_view symbols = "!@#$%^&*()" "-_=+[]{}\\|" ";:'\",.<>/?" "`~01234567";
    static_assert(letters.size() == key_count && symbols.size() == key_count);
    const auto keys = editor.symbols ? symbols : letters;
    input.space = false;   // A physical Space is text in this modal.
    Form form(painter, input, editor.focus);
    for (int key = 0; key < key_count; ++key) {
        char ch = keys[key];
        if (editor.uppercase && ch >= 'a' && ch <= 'z') ch = char(ch - 'a' + 'A');
        const int x = margin + (key % keys_per_row) * content_width / keys_per_row;
        const int right = margin + (key % keys_per_row + 1) * content_width / keys_per_row;
        if (form.button(x, 62 + (key / keys_per_row) * 18, right - x - 2, std::string(1, ch)))
            append_text(editor.draft, std::string_view(&ch, 1), editor.limit);
    }

    const char* actions[] = {editor.uppercase ? "ABC" : "abc", editor.symbols ? "Letters" : "Symbols",
                             "Space", "Delete", "Done", "Cancel"};
    int action = -1;
    for (int index = 0; index < action_count; ++index) {
        const int x = margin + index * content_width / action_count;
        const int right = margin + (index + 1) * content_width / action_count;
        if (form.button(x, 136, right - x - 2, actions[index], index == 4)) action = index;
    }
    switch (action) {
    case 0: editor.uppercase = !editor.uppercase; editor.symbols = false; break;
    case 1: editor.symbols = !editor.symbols; break;
    case 2: append_text(editor.draft, " ", editor.limit); break;
    case 3: if (!editor.draft.empty()) editor.draft.pop_back(); break;
    case 4: editor.accepted = true; break;
    case 5: editor = {}; break;
    default: break;
    }
    painter.text(margin, 156, "D-pad move   PS X / Xbox A type", muted);
    painter.text(margin, 167, "Start done   PS O / Xbox B cancel", muted);
}

enum class Screen { home, sign_in, create, request_reset, reset_password };

struct State {
    Screen screen = Screen::home;
    std::string server, npid, password, confirm, email, token, ca_path, reset_code;
    bool remember = true, advanced = false, show_token = false;
    bool verifying_saved = false;
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

void save_smoke_frame(SDL_Renderer* renderer, const fs::path& output, int frame) {
    if (output.empty()) return;
    int width = 0, height = 0;
    SDL_GetRendererOutputSize(renderer, &width, &height);
    SDL_Surface* shot = SDL_CreateRGBSurfaceWithFormat(0, width, height, 32, SDL_PIXELFORMAT_ARGB8888);
    if (!shot) {
        std::fprintf(stderr, "Could not allocate launcher screenshot: %s\n", SDL_GetError());
        return;
    }
    const auto path = output / ("screen-" + std::to_string(frame) + ".bmp");
    if (SDL_RenderReadPixels(renderer, nullptr, SDL_PIXELFORMAT_ARGB8888, shot->pixels, shot->pitch) != 0 ||
        SDL_SaveBMP(shot, utf8(path).c_str()) != 0)
        std::fprintf(stderr, "Could not capture launcher screen %d: %s\n", frame, SDL_GetError());
    SDL_FreeSurface(shot);
}

bool smoke_keyboard(Painter& painter) {
    std::string value = "x";
    int focus = 0;
    TextEntry editor;
    Input confirm;
    confirm.pad_confirm = true;
    if (Form(painter, confirm, focus, &editor).field(margin, 29, content_width, "Password", value, true, 3) ||
        !editor.editing() || !editor.masked) return false;
    draw_keyboard(painter, confirm, editor); // q
    Input right;
    right.right = true;
    draw_keyboard(painter, right, editor);
    draw_keyboard(painter, confirm, editor); // w
    draw_keyboard(painter, confirm, editor); // Limit applies to controller input.
    if (editor.draft != "xqw" || value != "x" || display_text(editor.draft, editor.masked, 10) != "***") return false;
    Input done;
    done.done = true;
    draw_keyboard(painter, done, editor);
    editor.apply(value);
    if (editor.target || value != "xqw") return false;
    editor.begin(value, "Password", true, 3);
    Input back;
    back.backspace = true;
    draw_keyboard(painter, back, editor);
    if (editor.draft != "xq") return false;
    back.escape = true;
    draw_keyboard(painter, back, editor);
    editor.apply(value);
    if (editor.target || value != "xqw") return false;

    editor.begin(value, "Password", true, 3);
    Input idle;
    draw_keyboard(painter, idle, editor);
    return true;
}

}  // namespace

std::optional<Choice> run_ui(Accounts& accounts) {
    SDL_SetMainReady();
    SDL_SetHint(SDL_HINT_VIDEO_X11_NET_WM_BYPASS_COMPOSITOR, "0");
    if (SDL_Init(SDL_INIT_VIDEO) != 0) return std::nullopt;
    SDL_DisplayMode mode{};
    int scale = 2;
    if (SDL_GetDesktopDisplayMode(0, &mode) == 0)
        scale = std::clamp((mode.h + 180) / 360, 1, 6);
    SDL_Window* window = SDL_CreateWindow("Tank Battles", SDL_WINDOWPOS_CENTERED, SDL_WINDOWPOS_CENTERED,
                                          logical_width * scale, logical_height * scale, SDL_WINDOW_RESIZABLE);
    if (!window) { SDL_Quit(); return std::nullopt; }
    SDL_SetWindowMinimumSize(window, logical_width, logical_height);
    SDL_SetWindowMaximumSize(window, logical_width * scale, logical_height * scale);
    SDL_Renderer* renderer = SDL_CreateRenderer(window, -1, SDL_RENDERER_ACCELERATED | SDL_RENDERER_PRESENTVSYNC |
                                                            SDL_RENDERER_TARGETTEXTURE);
    if (!renderer) renderer = SDL_CreateRenderer(window, -1, SDL_RENDERER_SOFTWARE | SDL_RENDERER_TARGETTEXTURE);
    if (!renderer) { SDL_DestroyWindow(window); SDL_Quit(); return std::nullopt; }
    SDL_Texture* canvas = SDL_CreateTexture(renderer, SDL_PIXELFORMAT_RGBA8888, SDL_TEXTUREACCESS_TARGET,
                                            logical_width, logical_height);
    if (!canvas) {
        SDL_DestroyRenderer(renderer); SDL_DestroyWindow(window); SDL_Quit(); return std::nullopt;
    }
    SDL_StartTextInput();
    const Uint32 wake = SDL_RegisterEvents(1);

    std::optional<Choice> choice;
    {
        Painter painter(renderer);
        State state;
        Worker worker;
        Gamepad pad;
        TextEntry editor;
        int focus = 0, focus_count = 1;
        if (auto active = accounts.active()) {
            if (auto password = accounts.remembered_password(*active)) state.session = std::make_pair(*active, *password);
            fill_from(state, *active);
        }
        auto go = [&](Screen screen) {
            state.screen = screen;
            state.message.clear();
            focus = 0;
            editor = {};
        };
        Screen recovery_back = Screen::sign_in;
        auto start_recovery = [&](Screen back) {
            recovery_back = back;
            state.password.clear();
            state.confirm.clear();
            state.reset_code.clear();
            state.email.clear();
            go(Screen::request_reset);
            focus = 2;
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
                case SDL_QUIT:
                    std::fprintf(stderr, "[launcher] Window close requested\n");
                    choice = Choice{};
                    break;
                case SDL_CONTROLLERDEVICEADDED: pad.added(event.cdevice.which); break;
                case SDL_CONTROLLERDEVICEREMOVED: pad.removed(event.cdevice.which); break;
                case SDL_CONTROLLERBUTTONDOWN:
                    if (!pad.matches(event.cbutton.which)) break;
                    switch (event.cbutton.button) {
                    case SDL_CONTROLLER_BUTTON_A: input.pad_confirm = true; break;
                    case SDL_CONTROLLER_BUTTON_B:
                        if (state.screen != Screen::home) input.escape = true;
                        break;
                    case SDL_CONTROLLER_BUTTON_START: input.done = true; break;
                    case SDL_CONTROLLER_BUTTON_DPAD_UP: input.up = true; break;
                    case SDL_CONTROLLER_BUTTON_DPAD_DOWN: input.down = true; break;
                    case SDL_CONTROLLER_BUTTON_DPAD_LEFT: input.left = true; break;
                    case SDL_CONTROLLER_BUTTON_DPAD_RIGHT: input.right = true; break;
                    default: break;
                    }
                    break;
                case SDL_MOUSEBUTTONDOWN:
                    if (event.button.button == SDL_BUTTON_LEFT) {
                        input.clicked = true;
                        input.mouse_x = event.button.x;
                        input.mouse_y = event.button.y;
                    }
                    break;
                case SDL_TEXTINPUT: input.typed += event.text.text; break;
                case SDL_DROPFILE:
                    if (state.screen != Screen::home && !worker.running && !editor.editing()) {
                        state.advanced = true;
                        state.ca_path = event.drop.file;
                    }
                    SDL_free(event.drop.file);
                    break;
                case SDL_KEYDOWN: {
                    const auto key = event.key.keysym.sym;
                    const bool shift = event.key.keysym.mod & KMOD_SHIFT;
                    if (key == SDLK_RETURN || key == SDLK_KP_ENTER) input.enter = true;
                    else if (key == SDLK_ESCAPE && !event.key.repeat) input.escape = true;
                    else if (key == SDLK_TAB) (shift ? input.backtab : input.tab) = true;
                    else if (key == SDLK_BACKSPACE) input.backspace = true;
                    else if (key == SDLK_SPACE) input.space = true;
                    else if (key == SDLK_DOWN) input.down = true;
                    else if (key == SDLK_UP) input.up = true;
                    else if (key == SDLK_LEFT) input.left = true;
                    else if (key == SDLK_RIGHT) input.right = true;
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
            const Presentation presentation(renderer, scale);
            presentation.map_mouse(window, input);
            if (editor.editing()) {
                SDL_SetRenderTarget(renderer, canvas);
                draw_keyboard(painter, input, editor);
                presentation.compose(renderer, canvas);
                SDL_RenderPresent(renderer);
                continue;
            }
            if (input.tab || input.down || input.right) focus = (focus + 1) % std::max(1, focus_count);
            if (input.backtab || input.up || input.left) focus = (focus + focus_count - 1) % std::max(1, focus_count);
            // Space types in text fields; it only presses buttons and boxes.
            const bool busy = worker.running;
            if (busy) input = Input{input.mouse_x, input.mouse_y};
            if (auto result = worker.take()) {
                if (state.verifying_saved) {
                    state.verifying_saved = false;
                    if (result->code == "verified") {
                        choice = Choice{Choice::online, state.session};
                        continue;
                    }
                    if (result->code == "wrong_password" || result->code == "unknown_user") {
                        state.session.reset();
                        if (auto saved = accounts.active()) fill_from(state, *saved);
                        go(Screen::sign_in);
                    }
                    state.message = result->message;
                    state.message_good = false;
                    continue;
                }
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

            SDL_SetRenderTarget(renderer, canvas);
            painter.fill(0, 0, logical_width, logical_height, background);
            painter.fill(0, 0, logical_width, 2, accent);
            painter.text(margin, 8, "TANK BATTLES", white, 2);
            Form form(painter, input, focus, &editor);
            const int column = content_width;
            int y = 29;
            const auto active = accounts.active();

            if (state.screen == Screen::home) {
                if (input.escape) {
                    std::fprintf(stderr, "[launcher] Escape pressed on the home screen\n");
                    choice = Choice{};
                }
                std::string status = "Offline.";
                if (active && state.session && state.session->first.id() == active->id())
                    status = "Saved: " + active->npid + " on " + active->endpoint + ".";
                else if (active)
                    status = active->npid + " on " + active->endpoint + ": sign in to play online.";
                painter.paragraph(margin, y, column, status, muted, 2);
                y += 22;
                const bool online_ready = active && state.session && state.session->first.id() == active->id();
                if (online_ready) {
                    if (form.button(margin, y, column, busy ? "Checking account..." : "Play online as " + active->npid,
                                    true, !busy)) {
                        const auto session = *state.session;
                        state.verifying_saved = true;
                        state.message = "Checking the saved password with " + session.first.endpoint + "...";
                        worker.start([&accounts, session] {
                            return accounts.verify_saved(session.first, session.second); }, wake);
                    }
                    y += 20;
                    if (form.button(margin, y, column, "Play offline")) choice = Choice{Choice::offline};
                } else {
                    if (form.button(margin, y, column, "Play offline", true)) choice = Choice{Choice::offline};
                    if (active) {
                        y += 20;
                        if (form.button(margin, y, column, "Sign in as " + active->npid)) { fill_from(state, *active); go(Screen::sign_in); }
                    }
                }
                y += 22;
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
                    y += 20;
                    if (form.button(margin, y, half, "Sign out")) {
                        accounts.sign_out();
                        state.session.reset();
                        state.message = "Signed out. The game will start offline.";
                        state.message_good = true;
                    }
                    if (!online_ready && form.button(margin + half + 8, y, half, "Forgot password")) {
                        fill_from(state, *active);
                        start_recovery(Screen::home);
                    }
                }
                y += 22;
                if (!state.message.empty())
                    painter.paragraph(margin, y, column, state.message, state.message_good ? good : bad);
                painter.text(margin, 168, "D-pad / Tab move   PS X / Xbox A select", muted);
            } else {
                const bool signing_in = state.screen == Screen::sign_in;
                const bool creating = state.screen == Screen::create;
                const bool requesting_reset = state.screen == Screen::request_reset;
                const bool setting_password = state.screen == Screen::reset_password;
                const char* heading = signing_in ? "Sign in" : creating ? "Create an account" : "Reset password";
                painter.text(logical_width - margin - Painter::width(heading), 12, heading, accent);
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
                y += 24;
                if (creating || requesting_reset) {
                    submit |= form.field(margin, y, column, "Email for this account",
                                         state.email, false, 120, !busy);
                    y += 24;
                } else if (setting_password) {
                    submit |= form.field(margin, y, column, "Reset code from email", state.reset_code, true, 64, !busy);
                    y += 24;
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
                if (!requesting_reset) y += 23;
                if (signing_in || creating) form.checkbox(margin, y, "Remember me", state.remember);
                form.checkbox(margin + half + 8, y, "Advanced", state.advanced);
                y += 12;
                if (state.advanced) {
                    submit |= form.field(margin, y, column, "Private CA (.pem or drop file)",
                                         state.ca_path, false, 400, !busy);
                    y += 24;
                }
                const int buttons = logical_height - 19;
                const bool extra_action = signing_in || requesting_reset;
                const int button_width = extra_action ? (column - 16) / 3 : half;
                const char* action = signing_in ? "Sign in" : creating ? "Create account" :
                                     requesting_reset ? "Send reset code" : "Change password";
                const bool pressed = form.button(margin, buttons, button_width, busy ? "Working..." : action, true, !busy);
                const bool extra = extra_action && form.button(margin + button_width + 8, buttons, button_width,
                                                                signing_in ? "Forgot password" : "I have a code", false, !busy);
                const bool back = form.button(margin + column - button_width, buttons, button_width, "Back", false, !busy);
                if ((back || input.escape) && !busy) {
                    if (requesting_reset || setting_password) {
                        state.password.clear();
                        state.confirm.clear();
                        state.reset_code.clear();
                        go(setting_password ? Screen::request_reset : recovery_back);
                    } else {
                        go(Screen::home);
                    }
                } else if (extra && signing_in) {
                    start_recovery(Screen::sign_in);
                } else if (extra) {
                    state.password.clear();
                    state.confirm.clear();
                    state.reset_code.clear();
                    go(Screen::reset_password);
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
                    painter.paragraph(margin, y + 1, column, state.message, state.message_good ? good : bad);
                else if (!busy)
                    painter.text(margin, y + 1, "Type or press PS X / Xbox A to open KB", muted);
            }
            focus_count = form.count();
            if (focus >= focus_count) focus = 0;
            presentation.compose(renderer, canvas);
            if (smoke) save_smoke_frame(renderer, *smoke, smoke_frames);
            SDL_RenderPresent(renderer);
            if (smoke) {
                static const Screen order[] = {Screen::home, Screen::sign_in, Screen::create, Screen::request_reset, Screen::reset_password};
                if (++smoke_frames >= static_cast<int>(std::size(order))) {
                    SDL_SetRenderTarget(renderer, canvas);
                    const bool keyboard_ok = !SDL_GetHintBoolean(SDL_HINT_GAMECONTROLLER_USE_BUTTON_LABELS, SDL_TRUE) &&
                                             smoke_keyboard(painter);
                    presentation.compose(renderer, canvas);
                    if (keyboard_ok) save_smoke_frame(renderer, *smoke, smoke_frames);
                    else std::fprintf(stderr, "The launcher keyboard smoke check failed\n");
                    choice = Choice{};
                } else state.screen = order[smoke_frames];
            }
        }
    }
    SDL_StopTextInput();
    SDL_DestroyTexture(canvas);
    SDL_DestroyRenderer(renderer);
    SDL_DestroyWindow(window);
    SDL_Quit();
    return choice;
}

}  // namespace tb::launcher
