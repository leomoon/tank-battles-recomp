#pragma once
// The movable player folder layout, resolved from the launcher's own location.
#include <filesystem>
#include <fstream>
#include <optional>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#ifdef _WIN32
#include <windows.h>
#else
#include <climits>
#include <unistd.h>
#endif

namespace tb::launcher {

namespace fs = std::filesystem;

inline constexpr const char* title_id = "NPUB30154";

// UTF-8 at every text boundary, whatever the C++ standard's char8_t says.
inline std::string utf8(const fs::path& path) {
    const auto text = path.u8string();
    return std::string(text.begin(), text.end());
}

inline fs::path from_utf8(const std::string& text) {
    return fs::path(std::u8string(text.begin(), text.end()));
}

inline fs::path own_directory() {
#ifdef _WIN32
    std::wstring buffer(32768, L'\0');
    const DWORD length = GetModuleFileNameW(nullptr, buffer.data(), static_cast<DWORD>(buffer.size()));
    if (!length || length >= buffer.size()) throw std::runtime_error("Cannot determine the launcher's folder");
    buffer.resize(length);
    return fs::path(buffer).parent_path();
#else
    std::error_code error;
    const auto self = fs::read_symlink("/proc/self/exe", error);
    if (error) throw std::runtime_error("Cannot determine the launcher's folder");
    return self.parent_path();
#endif
}

struct Folder {
    fs::path root;

    fs::path game() const { return root / "game"; }
    fs::path hdd0() const { return game() / "dev_hdd0"; }
    fs::path title() const { return hdd0() / "game" / title_id; }
    fs::path executable() const { return title() / "USRDIR" / "EBOOT.elf"; }
    fs::path online() const { return root / "online"; }
    fs::path logs() const { return root / "logs"; }
    fs::path settings() const { return game() / "settings.ini"; }
    fs::path player() const {
#ifdef _WIN32
        return root / "player" / "TankBattlesRecomp.exe";
#else
        return root / "player" / "TankBattlesRecomp";
#endif
    }

    // Matches scripts/port.py launch_environment for the same layout.
    std::vector<std::pair<std::string, fs::path>> environment() const {
        return {{"PS3_VFS_ROOT", title()}, {"PS3_HDD0_ROOT", hdd0()}, {"PS3_HDD1_ROOT", game() / "dev_hdd1"},
                {"PS3_GAMEDATA_ROOT", game()}, {"PS3_DEV_FLASH", game() / "dev_flash"},
                {"PS3_PARAM_SFO", title() / "PARAM.SFO"}, {"PS3_TITLE", "Tank Battles Recomp"},
                {"TB_EXPECTED_TITLE_ID", title_id}, {"PS3_STDIO_UNBUFFERED", "1"}, {"PS3_VERBOSE", "0"},
                {"TB_LOG_DIR", logs()}};
    }
};

inline Folder locate(fs::path root = {}) {
    Folder folder{root.empty() ? own_directory() : std::move(root)};
    std::error_code error;
    if (!fs::is_regular_file(folder.player(), error))
        throw std::runtime_error("The player is missing from " + utf8(folder.root) +
                                 ". Build it again with the Tank Battles builder.");
    if (!fs::is_regular_file(folder.executable(), error) || !fs::is_regular_file(folder.title() / "PARAM.SFO", error))
        throw std::runtime_error("The game files are missing from " + utf8(folder.game()) +
                                 ". Build again with the Tank Battles builder.");
    for (const auto& directory : {folder.game() / "dev_hdd1", folder.logs()})
        fs::create_directories(directory, error);
    return folder;
}

}  // namespace tb::launcher
