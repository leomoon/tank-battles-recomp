// Persisted player settings: player_settings.h.
#include "player_settings.h"

#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <mutex>
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
    std::atomic<int> antialiasing{0};
    std::string path;
    std::mutex mutex;
};
Settings& settings() { static Settings* s = new Settings; return *s; }   // outlives exit()

void load(Settings& s) {
    FILE* f = fopen(s.path.c_str(), "r");
    if (!f) return;
    char line[256];
    while (fgets(line, sizeof line, f)) {
        char key[64];
        int value;
        if (sscanf(line, " %63[a-z_] = %d", key, &value) != 2) continue;
        if (!strcmp(key, "overlay")) s.overlay = value != 0;
        else if (!strcmp(key, "fullscreen")) s.fullscreen = value != 0;
        else if (!strcmp(key, "antialiasing")) s.antialiasing.store(value != 0);
        else if (!strcmp(key, "window_width") && value >= 320 && value <= 16384) s.width = value;
        else if (!strcmp(key, "window_height") && value >= 180 && value <= 16384) s.height = value;
    }
    fclose(f);
}

// Write a temporary file and rename it over the old one, so a crash mid-write
// never leaves a truncated settings file.
void save(Settings& s) {
    if (s.path.empty()) return;
    static unsigned serial;
#ifdef _WIN32
    const int pid = _getpid();
#else
    const int pid = getpid();
#endif
    std::error_code error;
    const auto parent = std::filesystem::path(s.path).parent_path();
    if (!parent.empty()) std::filesystem::create_directories(parent, error);
    if (error) return;
    const std::string tmp = s.path + ".tmp." + std::to_string(pid) + "." + std::to_string(++serial);
    FILE* f = fopen(tmp.c_str(), "w");
    if (!f) return;
    fprintf(f, "# Tank Battles Recomp player settings, written by the game.\n");
    if (s.overlay >= 0) fprintf(f, "overlay = %d\n", s.overlay);
    if (s.fullscreen >= 0) fprintf(f, "fullscreen = %d\n", s.fullscreen);
    fprintf(f, "antialiasing = %d\n", s.antialiasing.load());
    if (s.width && s.height) fprintf(f, "window_width = %d\nwindow_height = %d\n", s.width, s.height);
    const bool ok = fclose(f) == 0;
#ifdef _WIN32
    if (ok) MoveFileExA(tmp.c_str(), s.path.c_str(), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH);
#else
    if (ok) std::rename(tmp.c_str(), s.path.c_str());
#endif
    std::remove(tmp.c_str());
}
}

extern "C" void tb_settings_init(void) {
    Settings& s = settings();
    const char* aa = getenv("TB_FXAA");
    s.antialiasing.store(aa && atoi(aa) != 0);
    const char* path = getenv("TB_SETTINGS_FILE");
    std::string fallback;
    if (!path) {
        const char* root = getenv("PS3_GAMEDATA_ROOT");
        if (!root || !*root) return;
        fallback = (std::filesystem::path(root) / "settings.ini").string();
        path = fallback.c_str();
    }
    if (!*path) return;
    std::lock_guard<std::mutex> lock(s.mutex);
    s.path = path;
    load(s);
    if (aa) s.antialiasing.store(atoi(aa) != 0);
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
