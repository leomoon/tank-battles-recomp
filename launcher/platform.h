#pragma once
// Operating-system services the launcher needs: running the player, private
// files, and remembered passwords.
#include <filesystem>
#include <optional>
#include <string>
#include <utility>
#include <vector>

namespace tb::launcher {

namespace fs = std::filesystem;

struct Output {
    int status = -1;          // Exit status, or -1 if the program could not start.
    std::string text;         // stdout and stderr together.
};

// Runs `program arguments...` with extra environment variables and waits.
Output run_captured(const fs::path& program, const std::vector<fs::path>& arguments,
                    const std::vector<std::pair<std::string, fs::path>>& environment);

// Starts the game and waits for it; returns its exit status (-1 if it did not start).
int run_game(const fs::path& program, const std::vector<fs::path>& arguments,
             const std::vector<std::pair<std::string, fs::path>>& environment, const fs::path& directory);

// Writes a file only the current user can read (POSIX 0600 / owner-only DACL),
// creating its directory the same way.
void write_private(const fs::path& path, const std::string& content);

// Overwrites and removes a file that held a secret.
void erase_private(const fs::path& path);

// A directory for short-lived secret files, readable only by this user.
fs::path private_temporary_directory(const fs::path& fallback);

// Remembered passwords. Windows uses Credential Manager. Elsewhere there is no
// universally available keyring, so the caller keeps them in a 0600 file.
bool credential_store_available();
bool store_password(const std::string& id, const std::string& password);
std::optional<std::string> load_password(const std::string& id);
void forget_password(const std::string& id);

}  // namespace tb::launcher
