// Play Tank Battles: the movable player folder's launcher.
//
//   Play Tank Battles[.exe]      this launcher
//   player/TankBattlesRecomp     the user's locally built executable
//   game/                        installed content plus saves and settings.ini
//   online/                      saved RPCN accounts; online/active selects one
//   logs/                        session logs
//
// With no arguments it opens a small window: Play (online when signed in),
// Play Offline, Sign In and Create Account. No Python, source checkout or
// builder workspace is needed.
//
//   --offline                 start the game offline now
//   --play                    start now, online if the selected account's password is remembered
//   --account ACTION ...      scriptable account management, password on stdin:
//       sign-in --server HOST:PORT --user NAME [--token T] [--ca FILE] [--remember]
//       create  --server HOST:PORT --user NAME --email ADDRESS [--ca FILE] [--remember]
//       status | sign-out | forget --server HOST:PORT --user NAME
#include "accounts.h"
#include "launcher_paths.h"
#include "platform.h"
#include "ui.h"

#include <cstdio>
#include <cstring>
#include <iostream>
#include <string>
#include <vector>

#ifdef _WIN32
#include <windows.h>
#endif

namespace fs = std::filesystem;
using namespace tb::launcher;

namespace {

void report(const std::string& message) {
#ifdef _WIN32
    MessageBoxW(nullptr, from_utf8(message).wstring().c_str(), L"Tank Battles", MB_OK | MB_ICONERROR);
#endif
    fprintf(stderr, "Tank Battles: %s\n", message.c_str());
}

std::string option(const std::vector<std::string>& arguments, const char* name) {
    for (size_t i = 0; i + 1 < arguments.size(); ++i)
        if (arguments[i] == name) return arguments[i + 1];
    return {};
}

bool flag(const std::vector<std::string>& arguments, const char* name) {
    for (const auto& argument : arguments)
        if (argument == name) return true;
    return false;
}

int account_cli(Accounts& accounts, const std::vector<std::string>& arguments) {
    const std::string action = option(arguments, "--account");
    auto print = [](const Result& result) {
        printf("result=%s\nmessage=%s\n", result.code.c_str(), result.message.c_str());
        return result.ok() ? 0 : 1;
    };
    if (action == "status") {
        const auto active = accounts.active();
        printf("active=%s\n", active ? active->id().c_str() : "");
        for (const auto& account : accounts.saved())
            printf("saved=%s remembered=%d\n", account.id().c_str(), account.remembered ? 1 : 0);
        return 0;
    }
    if (action == "sign-out") {
        accounts.sign_out();
        return print({"signed_out", "Signed out."});
    }
    Account account{option(arguments, "--server"), option(arguments, "--user"), option(arguments, "--token")};
    if (account.endpoint.empty()) account.endpoint = default_server;
    if (action == "forget") {
        accounts.forget(account);
        return print({"forgotten", "Removed the saved account."});
    }
    for (const auto& saved : accounts.saved())   // A saved account keeps its server certificate.
        if (saved.id() == account.id()) account.ca = saved.ca;
    if (const auto ca = option(arguments, "--ca"); !ca.empty()) {
        try {
            account.ca = accounts.import_ca(from_utf8(ca), account.endpoint);
        } catch (const std::exception& error) {
            return print({"invalid_input", error.what()});
        }
    }
    std::string password;
    std::getline(std::cin, password);   // Never on the command line.
    if (!password.empty() && password.back() == '\r') password.pop_back();
    const bool remember = flag(arguments, "--remember");
    if (action == "sign-in") return print(accounts.sign_in(account, password, remember));
    if (action == "create") return print(accounts.create(account, option(arguments, "--email"), password, remember));
    fprintf(stderr, "Unknown --account action %s\n", action.c_str());
    return 2;
}

void remove_stale_sessions(const Folder& folder) {
    // Profiles from a run that crashed; each is erased after a normal run.
    std::error_code error;
    for (const auto& directory : {private_temporary_directory(folder.online() / ".session"), folder.online() / ".session"})
        for (const auto& entry : fs::directory_iterator(directory, error))
            if (entry.path().filename().string().rfind("session-", 0) == 0) erase_private(entry.path());
}

int launch(const std::vector<std::string>& arguments) {
    Folder folder;
    try {
        folder = locate();
    } catch (const std::exception& error) {
        report(error.what());
        return 2;
    }
    Accounts accounts(folder);
    if (flag(arguments, "--account")) return account_cli(accounts, arguments);
    remove_stale_sessions(folder);

    std::optional<std::pair<Account, std::string>> online;
    if (flag(arguments, "--offline")) {
    } else if (flag(arguments, "--play")) {
        if (const auto active = accounts.active())
            if (auto password = accounts.remembered_password(*active)) online = std::make_pair(*active, *password);
    } else {
        const auto choice = run_ui(accounts);
        if (!choice) {
            report("Could not open the launcher window; starting offline.");
        } else if (choice->kind == Choice::quit) {
            return 0;
        } else if (choice->kind == Choice::online) {
            online = choice->session;
        }
    }
    const int status = accounts.play(online);
    if (status < 0) {
        report("Could not start " + utf8(folder.player()));
        return 2;
    }
    return status;
}

}  // namespace

#ifdef _WIN32
int WINAPI WinMain(HINSTANCE, HINSTANCE, LPSTR, int) {
    // A GUI program; scripted use from a console still gets its output.
    if (AttachConsole(ATTACH_PARENT_PROCESS)) {
        freopen("CONOUT$", "w", stdout);
        freopen("CONOUT$", "w", stderr);
    }
    int count = 0;
    wchar_t** argv = CommandLineToArgvW(GetCommandLineW(), &count);
    std::vector<std::string> arguments;
    for (int i = 1; i < count; ++i) arguments.push_back(utf8(fs::path(argv[i])));
    LocalFree(argv);
    return launch(arguments);
}
#else
int main(int argc, char** argv) {
    return launch(std::vector<std::string>(argv + 1, argv + argc));
}
#endif
