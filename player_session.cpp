#include "player_session.h"
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <ctime>
#include <filesystem>
#include <string>
#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#include <process.h>
#define TB_DUP2 _dup2
#define TB_FILENO _fileno
#define TB_PID _getpid
#else
#include <sys/stat.h>
#include <unistd.h>
#define TB_DUP2 dup2
#define TB_FILENO fileno
#define TB_PID getpid
#endif

void tb_player_session_begin()
{
    if (!getenv("PS3_VERBOSE")) {
#ifdef _WIN32
        _putenv_s("PS3_VERBOSE", "0");
#else
        setenv("PS3_VERBOSE", "0", 0);
#endif
    }
    // Harnesses own their stdout/stderr capture. Probe results stay on stdio.
    if (getenv("TB_SESSION_CAPTURED") || (getenv("TB_LOG_CONSOLE") && std::string(getenv("TB_LOG_CONSOLE")) != "0")) return;
    for (const char* probe : {"TB_EXECUTION_PROBE", "TB_OFFLINE_PROBE", "TB_ENTITLEMENT_PROBE", "TB_ASSET_PROBE", "TB_HLE_AUDIT_ONLY", "TB_DIAGNOSTIC_STALL"})
        if (getenv(probe)) return;
    namespace fs = std::filesystem;
    fs::path directory;
    if (const char* custom = getenv("TB_LOG_DIR"); custom && *custom) directory = custom;
    else if (const char* state = getenv("XDG_STATE_HOME"); state && fs::path(state).is_absolute()) directory = fs::path(state)/"tank-battles-recomp/logs";
#ifdef _WIN32
    else if (const char* local = getenv("LOCALAPPDATA"); local && *local) directory = fs::path(local)/"tank-battles-recomp/logs";
#endif
    else if (const char* home = getenv("HOME"); home && *home) directory = fs::path(home)/".local/state/tank-battles-recomp/logs";
    else directory = "game/logs";
    std::error_code error;
    fs::create_directories(directory, error);
    if (error) { fprintf(stderr, "[session] Could not create log directory: %s\n", error.message().c_str()); return; }
    const auto now = std::chrono::system_clock::now();
    const auto stamp = std::chrono::system_clock::to_time_t(now);
    std::tm date{};
#ifdef _WIN32
    localtime_s(&date, &stamp);
#else
    localtime_r(&stamp, &date);
#endif
    char name[80];
    std::strftime(name, sizeof name, "session-%Y%m%d-%H%M%S", &date);
    const auto path = directory/(std::string(name)+"-"+std::to_string(TB_PID())+".log");
    FILE* stream = fopen(path.string().c_str(), "ab");
    if (!stream) { fprintf(stderr, "[session] Could not open log: %s\n", path.string().c_str()); return; }
#ifndef _WIN32
    fchmod(TB_FILENO(stream), 0644);
#endif
    fprintf(stderr, "[session] log: %s\n", path.string().c_str());
    fflush(stdout); fflush(stderr);
    const int fd = TB_FILENO(stream);
    TB_DUP2(fd, TB_FILENO(stdout)); TB_DUP2(fd, TB_FILENO(stderr));
    fclose(stream);
}
