// Play Tank Battles: starts the private player from its own folder.
//
// The folder is self-contained and movable (see scripts/player_folder.py):
//   Play Tank Battles[.exe]      this launcher
//   player/TankBattlesRecomp     the user's locally built executable
//   game/                        installed content plus saves and settings.ini
//   online/                      optional RPCN profiles; online/active selects one
//   logs/                        session logs
// No Python, source checkout or builder workspace is needed. Without a selected
// profile, or with --offline, the game starts offline.
#include "launcher_paths.h"

#include <cstdio>
#include <string>
#include <vector>

#ifdef _WIN32
#include <windows.h>
#else
#include <cerrno>
#include <cstring>
#include <unistd.h>
#endif

namespace fs = std::filesystem;

namespace {

#ifdef _WIN32
void report(const std::wstring& message) {
    MessageBoxW(nullptr, message.c_str(), L"Tank Battles", MB_OK | MB_ICONERROR);
}

std::wstring quote(const std::wstring& argument) {
    // CommandLineToArgvW rules: backslashes double only before a quote.
    std::wstring result = L"\"";
    size_t slashes = 0;
    for (const wchar_t c : argument) {
        if (c == L'\\') { ++slashes; continue; }
        if (c == L'"') result.append(slashes * 2 + 1, L'\\');
        else result.append(slashes, L'\\');
        slashes = 0;
        result.push_back(c);
    }
    result.append(slashes * 2, L'\\');
    return result + L"\"";
}
#else
void report(const std::string& message) {
    fprintf(stderr, "Tank Battles: %s\n", message.c_str());
}
#endif

}  // namespace

#ifdef _WIN32
int WINAPI WinMain(HINSTANCE, HINSTANCE, LPSTR, int) {
    int count = 0;
    wchar_t** argv = CommandLineToArgvW(GetCommandLineW(), &count);
    std::vector<std::wstring> arguments(argv + (count ? 1 : 0), argv + count);
    LocalFree(argv);
    bool offline = false;
    for (const auto& argument : arguments) offline |= argument == L"--offline";
    tb::launcher::Folder folder;
    try {
        folder = tb::launcher::locate();
    } catch (const std::exception& error) {
        report(tb::launcher::from_utf8(error.what()).wstring());
        return 2;
    }
    for (const auto& [name, value] : folder.environment())
        SetEnvironmentVariableW(tb::launcher::from_utf8(name).wstring().c_str(), value.wstring().c_str());
    std::wstring command = quote(folder.player().wstring()) + L" " + quote(folder.executable().wstring());
    if (!offline) {
        if (const auto profile = folder.selected_profile())
            command += L" --rpcn-profile " + quote(profile->wstring());
    }
    STARTUPINFOW startup{sizeof startup};
    PROCESS_INFORMATION process{};
    if (!CreateProcessW(folder.player().wstring().c_str(), command.data(), nullptr, nullptr, FALSE,
                        CREATE_NO_WINDOW, nullptr, folder.root.wstring().c_str(), &startup, &process)) {
        report(L"Could not start " + folder.player().wstring() + L" (error " + std::to_wstring(GetLastError()) + L")");
        return 2;
    }
    WaitForSingleObject(process.hProcess, INFINITE);
    DWORD status = 1;
    GetExitCodeProcess(process.hProcess, &status);
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    return static_cast<int>(status);
}
#else
int main(int argc, char** argv) {
    bool offline = false;
    for (int i = 1; i < argc; ++i) offline |= std::strcmp(argv[i], "--offline") == 0;
    tb::launcher::Folder folder;
    try {
        folder = tb::launcher::locate();
    } catch (const std::exception& error) {
        report(error.what());
        return 2;
    }
    for (const auto& [name, value] : folder.environment())
        setenv(name.c_str(), value.c_str(), 1);
    const std::string player = folder.player().string(), executable = folder.executable().string();
    std::string profile;
    if (!offline) {
        if (const auto selected = folder.selected_profile()) profile = selected->string();
    }
    std::vector<char*> command{const_cast<char*>(player.c_str()), const_cast<char*>(executable.c_str())};
    if (!profile.empty()) {
        command.push_back(const_cast<char*>("--rpcn-profile"));
        command.push_back(profile.data());
    }
    command.push_back(nullptr);
    if (chdir(folder.root.c_str()) != 0) {
        report("Cannot enter the player folder: " + folder.root.string());
        return 2;
    }
    execv(player.c_str(), command.data());
    report("Could not start " + player + ": " + std::strerror(errno));
    return 2;
}
#endif
