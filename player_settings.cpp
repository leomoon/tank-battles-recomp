// Persisted player settings: player_settings.h.
#include "player_settings.h"

#include <atomic>
#include <algorithm>
#include <charconv>
#include <cerrno>
#include <string_view>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <mutex>
#include <optional>
#include <string>
#ifdef _WIN32
#include <process.h>
#include <windows.h>
#else
#include <unistd.h>
#endif

namespace {
struct Settings {
    int overlay = -1, fullscreen = -1, width = 0, height = 0;
    int render_scale = 100, window_preset = 0;
    std::atomic<int> effective_scale{100};
    std::atomic<int> antialiasing{0};
    std::string path;
    std::mutex mutex;
};
Settings& settings() { static Settings* s = new Settings; return *s; }   // outlives exit()

bool valid_scale(int value) {
    return std::find(tb::display::render_presets.begin(), tb::display::render_presets.end(), value) != tb::display::render_presets.end();
}
bool valid_preset(int value) {
    for (const auto& preset : tb::display::window_presets)
        if (static_cast<int>(preset.choice) == value) return true;
    return false;
}
bool number(std::string_view text, int& value) {
    const auto first = text.find_first_not_of(" \t\r\n");
    if (first == text.npos) return false;
    text.remove_prefix(first);
    text = text.substr(0, text.find_last_not_of(" \t\r\n") + 1);
    const auto result = std::from_chars(text.data(), text.data() + text.size(), value);
    return result.ec == std::errc{} && result.ptr == text.data() + text.size();
}
std::filesystem::path native_path(const std::string& value) { return std::filesystem::path(std::u8string(value.begin(), value.end())); }
std::optional<std::filesystem::path> path_from_env(const char* name) {
#ifdef _WIN32
    const std::wstring wide_name(name, name + std::strlen(name));
    const wchar_t* value = _wgetenv(wide_name.c_str());
#else
    const char* value = getenv(name);
#endif
    if (!value) return std::nullopt;
    return std::filesystem::path(value);
}
FILE* open_file(const std::string& path, bool writing) {
#ifdef _WIN32
    return _wfopen(native_path(path).c_str(), writing ? L"w" : L"r");
#else
    return fopen(path.c_str(), writing ? "w" : "r");
#endif
}
bool load(Settings& s) {
    FILE* f = open_file(s.path, false);
    if (!f) return errno == ENOENT;
    char line[256];
    while (fgets(line, sizeof line, f)) {
        std::string_view text(line);
        const auto equal = text.find('=');
        if (equal == text.npos) continue;
        auto key = text.substr(0, equal);
        const auto first = key.find_first_not_of(" \t");
        if (first == key.npos) continue;
        key = key.substr(first, key.find_last_not_of(" \t") - first + 1);
        int value;
        if (!number(text.substr(equal + 1), value)) continue;
        if (key == "overlay" && (value == 0 || value == 1)) s.overlay = value;
        else if (key == "fullscreen" && (value == 0 || value == 1)) s.fullscreen = value;
        else if (key == "antialiasing" && (value == 0 || value == 1)) s.antialiasing.store(value);
        else if (key == "window_width" && value >= 320 && value <= 16384) s.width = value;
        else if (key == "window_height" && value >= 180 && value <= 16384) s.height = value;
        else if (key == "render_scale" && valid_scale(value)) s.render_scale = value;
        else if (key == "window_preset" && valid_preset(value)) s.window_preset = value;
    }
    if (!s.width || !s.height) s.width = s.height = 0;
    const bool ok = !ferror(f);
    return fclose(f) == 0 && ok;
}

// Write a temporary file and rename it over the old one, so a crash mid-write
// never leaves a truncated settings file.
bool save(Settings& s) {
    if (s.path.empty()) return false;
    static std::atomic<unsigned> serial{0};
#ifdef _WIN32
    const int pid = _getpid();
#else
    const int pid = getpid();
#endif
    std::error_code error;
    const auto parent = native_path(s.path).parent_path();
    if (!parent.empty()) std::filesystem::create_directories(parent, error);
    if (error) return false;
    const std::string tmp = s.path + ".tmp." + std::to_string(pid) + "." + std::to_string(++serial);
    FILE* f = open_file(tmp, true);
    if (!f) return false;
    fprintf(f, "# Tank Battles Recomp player settings, shared by the launcher and game.\n");
    if (s.overlay >= 0) fprintf(f, "overlay = %d\n", s.overlay);
    if (s.fullscreen >= 0) fprintf(f, "fullscreen = %d\n", s.fullscreen);
    fprintf(f, "antialiasing = %d\n", s.antialiasing.load());
    if (s.width && s.height) fprintf(f, "window_width = %d\nwindow_height = %d\n", s.width, s.height);
    fprintf(f, "render_scale = %d\nwindow_preset = %d\n", s.render_scale, s.window_preset);
    const bool written = !ferror(f);
    const bool ok = fclose(f) == 0 && written;
    bool replaced = false;
#ifdef _WIN32
    if (ok) replaced = MoveFileExW(native_path(tmp).c_str(), native_path(s.path).c_str(), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH) != 0;
#else
    if (ok) replaced = std::rename(tmp.c_str(), s.path.c_str()) == 0;
#endif
    std::filesystem::remove(native_path(tmp), error);
    return replaced;
}
}

extern "C" void tb_settings_init(void) {
    Settings& s = settings();
    std::lock_guard<std::mutex> lock(s.mutex);
    s.path.clear();
    s.overlay = s.fullscreen = -1;
    s.width = s.height = s.window_preset = 0;
    s.render_scale = 100;
    const char* aa = getenv("TB_FXAA");
    int initial_scale = 100;
    if (const char* value = getenv("TB_RENDER_SCALE"))
        if (!number(value, initial_scale) || !valid_scale(initial_scale)) initial_scale = 100;
    s.effective_scale.store(initial_scale);
    s.antialiasing.store(aa && atoi(aa) != 0);
    auto path = path_from_env("TB_SETTINGS_FILE");
    if (!path) {
        const auto root = path_from_env("PS3_GAMEDATA_ROOT");
        if (!root || root->empty()) return;
        path = *root / "settings.ini";
    }
    if (path->empty()) return;
    const auto utf8 = path->u8string();
    s.path.assign(utf8.begin(), utf8.end());
    load(s);
    if (aa) s.antialiasing.store(atoi(aa) != 0);
    int scale = s.render_scale;
    if (const char* override_scale = getenv("TB_RENDER_SCALE"))
        if (!number(override_scale, scale) || !valid_scale(scale)) scale = 100;
    s.effective_scale.store(scale);
    auto set = [](const char* key, const char* value) {
#ifdef _WIN32
        _putenv_s(key, value);
#else
        setenv(key, value, 1);
#endif
    };
    if (s.overlay >= 0 && !getenv("TB_FPS_OVERLAY")) set("TB_FPS_OVERLAY", s.overlay ? "1" : "0");
    if (s.fullscreen >= 0 && !getenv("PS3RECOMP_FULLSCREEN") && !getenv("PS3RECOMP_VK_FULLSCREEN"))
        set("PS3RECOMP_FULLSCREEN", s.fullscreen ? "1" : "0");
}

extern "C" int tb_settings_window_size(int* width, int* height) {
    Settings& s = settings();
    std::lock_guard<std::mutex> lock(s.mutex);
    if (s.path.empty() || !s.width || !s.height) return 0;
    *width = s.width;
    *height = s.height;
    return 1;
}

extern "C" int tb_settings_window_preset(int* width, int* height) {
    Settings& s = settings();
    std::lock_guard<std::mutex> lock(s.mutex);
    for (const auto& preset : tb::display::window_presets) {
        if (static_cast<int>(preset.choice) == s.window_preset && preset.width) {
            *width = preset.width;
            *height = preset.height;
            return 1;
        }
    }
    return 0;
}

extern "C" void tb_settings_note_overlay(int on) {
    Settings& s = settings();
    std::lock_guard<std::mutex> lock(s.mutex);
    if (s.path.empty() || s.overlay == (on != 0)) return;
    s.overlay = on != 0;
    save(s);
}

extern "C" void tb_settings_note_fullscreen(int on) {
    Settings& s = settings();
    std::lock_guard<std::mutex> lock(s.mutex);
    if (s.path.empty() || s.fullscreen == (on != 0)) return;
    s.fullscreen = on != 0;
    save(s);
}

extern "C" void tb_settings_note_window(int width, int height) {
    Settings& s = settings();
    std::lock_guard<std::mutex> lock(s.mutex);
    if (s.path.empty() || width < 320 || height < 180 || width > 16384 || height > 16384 || (s.width == width && s.height == height)) return;
    s.width = width;
    s.height = height;
    save(s);
}

extern "C" int tb_settings_antialiasing(void) {
    return settings().antialiasing.load(std::memory_order_relaxed);
}

extern "C" void tb_settings_note_antialiasing(int on) {
    Settings& s = settings();
    std::lock_guard<std::mutex> lock(s.mutex);
    if (s.antialiasing.exchange(on != 0) == (on != 0)) return;
    save(s);
}

extern "C" int tb_settings_render_scale(void) {
    return settings().effective_scale.load(std::memory_order_relaxed);
}

namespace tb::display {
bool load(const std::filesystem::path& path, Preferences& preferences, std::string& error) {
    error.clear();
    Settings stored;
    const auto utf8 = path.u8string();
    stored.path.assign(utf8.begin(), utf8.end());
    if (!::load(stored)) { error = "Could not read display settings."; return false; }
    preferences = {stored.fullscreen == 1 ? Mode::borderless : Mode::windowed,
                   static_cast<WindowSize>(stored.window_preset), stored.render_scale};
    return true;
}
bool save(const std::filesystem::path& path, const Preferences& preferences, std::string& error) {
    error.clear();
    if ((preferences.mode != Mode::windowed && preferences.mode != Mode::borderless) ||
        !valid_scale(preferences.render_scale) || !valid_preset(static_cast<int>(preferences.window_size))) {
        error = "Invalid display preferences."; return false;
    }
    Settings stored;
    const auto utf8 = path.u8string();
    stored.path.assign(utf8.begin(), utf8.end());
    if (!::load(stored)) { error = "Could not read display settings."; return false; }
    stored.fullscreen = preferences.mode == Mode::borderless;
    stored.window_preset = static_cast<int>(preferences.window_size);
    stored.render_scale = preferences.render_scale;
    if (!::save(stored)) { error = "Could not save display settings."; return false; }
    return true;
}
}
