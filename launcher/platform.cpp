#include "platform.h"

#include "launcher_paths.h"

#include <cstdio>
#include <cstring>
#include <fstream>
#include <random>
#include <stdexcept>

#ifdef _WIN32
#include <windows.h>
#include <sddl.h>
#include <wincred.h>
#else
#include <cerrno>
#include <dlfcn.h>
#include <fcntl.h>
#include <spawn.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>
extern char** environ;
#endif

namespace tb::launcher {

namespace {

std::string random_suffix() {
    std::random_device device;
    char text[17];
    snprintf(text, sizeof text, "%08x%08x", device(), device());
    return text;
}

#ifdef _WIN32
std::wstring quote(const std::wstring& argument) {
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

std::wstring command_line(const fs::path& program, const std::vector<fs::path>& arguments) {
    std::wstring line = quote(program.wstring());
    for (const auto& argument : arguments) line += L" " + quote(argument.wstring());
    return line;
}

// The environment block with additions, so the launcher's own stays unchanged.
std::wstring environment_block(const std::vector<std::pair<std::string, fs::path>>& additions) {
    std::vector<std::wstring> entries;
    if (wchar_t* current = GetEnvironmentStringsW()) {
        for (const wchar_t* entry = current; *entry; entry += wcslen(entry) + 1) {
            const std::wstring text(entry);
            bool replaced = false;
            for (const auto& [name, value] : additions) {
                const auto wide = from_utf8(name).wstring();
                replaced |= _wcsnicmp(text.c_str(), (wide + L"=").c_str(), wide.size() + 1) == 0;
            }
            if (!replaced) entries.push_back(text);
        }
        FreeEnvironmentStringsW(current);
    }
    for (const auto& [name, value] : additions)
        entries.push_back(from_utf8(name).wstring() + L"=" + value.wstring());
    std::wstring block;
    for (const auto& entry : entries) block += entry + L'\0';
    return block + L'\0';
}

SECURITY_ATTRIBUTES* owner_only(SECURITY_ATTRIBUTES& attributes) {
    // Protected DACL: full control for the owner (this user) and nobody else.
    PSECURITY_DESCRIPTOR descriptor = nullptr;
    if (!ConvertStringSecurityDescriptorToSecurityDescriptorW(L"D:P(A;OICI;FA;;;OW)", SDDL_REVISION_1, &descriptor, nullptr))
        return nullptr;
    attributes = {sizeof attributes, descriptor, FALSE};
    return &attributes;
}

std::wstring credential_target(const std::string& id) {
    return L"Tank Battles Recomp/" + from_utf8(id).wstring();
}
#endif

}  // namespace

#ifdef _WIN32
Output run_captured(const fs::path& program, const std::vector<fs::path>& arguments,
                    const std::vector<std::pair<std::string, fs::path>>& environment) {
    Output output;
    SECURITY_ATTRIBUTES inherit{sizeof inherit, nullptr, TRUE};
    HANDLE read = nullptr, write = nullptr;
    if (!CreatePipe(&read, &write, &inherit, 0)) return output;
    SetHandleInformation(read, HANDLE_FLAG_INHERIT, 0);
    STARTUPINFOW startup{sizeof startup};
    startup.dwFlags = STARTF_USESTDHANDLES;
    startup.hStdOutput = startup.hStdError = write;
    startup.hStdInput = GetStdHandle(STD_INPUT_HANDLE);
    PROCESS_INFORMATION process{};
    auto line = command_line(program, arguments);
    auto block = environment_block(environment);
    const bool started = CreateProcessW(program.wstring().c_str(), line.data(), nullptr, nullptr, TRUE,
                                        CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT, block.data(), nullptr,
                                        &startup, &process);
    CloseHandle(write);
    if (started) {
        char buffer[4096];
        DWORD count = 0;
        while (ReadFile(read, buffer, sizeof buffer, &count, nullptr) && count) output.text.append(buffer, count);
        WaitForSingleObject(process.hProcess, INFINITE);
        DWORD status = 1;
        GetExitCodeProcess(process.hProcess, &status);
        output.status = static_cast<int>(status);
        CloseHandle(process.hThread);
        CloseHandle(process.hProcess);
    }
    CloseHandle(read);
    return output;
}

int run_game(const fs::path& program, const std::vector<fs::path>& arguments,
             const std::vector<std::pair<std::string, fs::path>>& environment, const fs::path& directory) {
    STARTUPINFOW startup{sizeof startup};
    PROCESS_INFORMATION process{};
    auto line = command_line(program, arguments);
    auto block = environment_block(environment);
    if (!CreateProcessW(program.wstring().c_str(), line.data(), nullptr, nullptr, FALSE,
                        CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT, block.data(), directory.wstring().c_str(),
                        &startup, &process))
        return -1;
    WaitForSingleObject(process.hProcess, INFINITE);
    DWORD status = 1;
    GetExitCodeProcess(process.hProcess, &status);
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    return static_cast<int>(status);
}

void write_private(const fs::path& path, const std::string& content) {
    SECURITY_ATTRIBUTES attributes{};
    auto* security = owner_only(attributes);
    CreateDirectoryW(path.parent_path().wstring().c_str(), security);
    const auto temporary = path.wstring() + L"." + from_utf8(random_suffix()).wstring();
    HANDLE file = CreateFileW(temporary.c_str(), GENERIC_WRITE, 0, security, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (security) LocalFree(security->lpSecurityDescriptor);
    if (file == INVALID_HANDLE_VALUE) throw std::runtime_error("Cannot write " + utf8(path));
    DWORD written = 0;
    const bool ok = WriteFile(file, content.data(), static_cast<DWORD>(content.size()), &written, nullptr) &&
                    written == content.size();
    CloseHandle(file);
    if (!ok || !MoveFileExW(temporary.c_str(), path.wstring().c_str(), MOVEFILE_REPLACE_EXISTING)) {
        DeleteFileW(temporary.c_str());
        throw std::runtime_error("Cannot write " + utf8(path));
    }
}

fs::path private_temporary_directory(const fs::path&) {
    // The runtime profile is short-lived and must not sit beside saved accounts.
    // write_private creates this child directory with an owner-only DACL.
    return fs::temp_directory_path() / L"TankBattlesRecomp";
}

bool credential_store_available() { return true; }

bool store_password(const std::string& id, const std::string& password) {
    auto target = credential_target(id);
    CREDENTIALW credential{};
    credential.Type = CRED_TYPE_GENERIC;
    credential.TargetName = target.data();
    credential.CredentialBlobSize = static_cast<DWORD>(password.size());
    credential.CredentialBlob = reinterpret_cast<LPBYTE>(const_cast<char*>(password.data()));
    credential.Persist = CRED_PERSIST_LOCAL_MACHINE;
    std::wstring user = from_utf8(id).wstring();
    credential.UserName = user.data();
    return CredWriteW(&credential, 0);
}

std::optional<std::string> load_password(const std::string& id) {
    PCREDENTIALW credential = nullptr;
    if (!CredReadW(credential_target(id).c_str(), CRED_TYPE_GENERIC, 0, &credential)) return std::nullopt;
    std::string password(reinterpret_cast<const char*>(credential->CredentialBlob), credential->CredentialBlobSize);
    CredFree(credential);
    return password;
}

void forget_password(const std::string& id) {
    CredDeleteW(credential_target(id).c_str(), CRED_TYPE_GENERIC, 0);
}
#else
namespace {

int spawn(const fs::path& program, const std::vector<fs::path>& arguments,
          const std::vector<std::pair<std::string, fs::path>>& environment, posix_spawn_file_actions_t* actions,
          pid_t& pid) {
    std::vector<std::string> storage{program.string()};
    for (const auto& argument : arguments) storage.push_back(argument.string());
    std::vector<char*> argv;
    for (auto& value : storage) argv.push_back(value.data());
    argv.push_back(nullptr);
    std::vector<std::string> variables;
    for (char** entry = environ; *entry; ++entry) {
        bool replaced = false;
        for (const auto& [name, value] : environment)
            replaced |= std::strncmp(*entry, (name + "=").c_str(), name.size() + 1) == 0;
        if (!replaced) variables.emplace_back(*entry);
    }
    for (const auto& [name, value] : environment) variables.push_back(name + "=" + value.string());
    std::vector<char*> envp;
    for (auto& value : variables) envp.push_back(value.data());
    envp.push_back(nullptr);
    return posix_spawn(&pid, storage[0].c_str(), actions, nullptr, argv.data(), envp.data());
}

int wait_for(pid_t pid) {
    int status = 0;
    while (waitpid(pid, &status, 0) < 0)
        if (errno != EINTR) return -1;
    return WIFEXITED(status) ? WEXITSTATUS(status) : 128 + WTERMSIG(status);
}

}  // namespace

Output run_captured(const fs::path& program, const std::vector<fs::path>& arguments,
                    const std::vector<std::pair<std::string, fs::path>>& environment) {
    Output output;
    int pipe_ends[2];
    if (pipe(pipe_ends) != 0) return output;
    posix_spawn_file_actions_t actions;
    posix_spawn_file_actions_init(&actions);
    posix_spawn_file_actions_adddup2(&actions, pipe_ends[1], STDOUT_FILENO);
    posix_spawn_file_actions_adddup2(&actions, pipe_ends[1], STDERR_FILENO);
    posix_spawn_file_actions_addclose(&actions, pipe_ends[0]);
    pid_t pid = 0;
    const int error = spawn(program, arguments, environment, &actions, pid);
    posix_spawn_file_actions_destroy(&actions);
    close(pipe_ends[1]);
    if (error == 0) {
        char buffer[4096];
        for (;;) {
            const ssize_t count = read(pipe_ends[0], buffer, sizeof buffer);
            if (count > 0) output.text.append(buffer, static_cast<size_t>(count));
            else if (count == 0 || errno != EINTR) break;
        }
        output.status = wait_for(pid);
    }
    close(pipe_ends[0]);
    return output;
}

int run_game(const fs::path& program, const std::vector<fs::path>& arguments,
             const std::vector<std::pair<std::string, fs::path>>& environment, const fs::path& directory) {
    const auto previous = fs::current_path();
    std::error_code ignored;
    fs::current_path(directory, ignored);
    pid_t pid = 0;
    const int error = spawn(program, arguments, environment, nullptr, pid);
    fs::current_path(previous, ignored);
    return error ? -1 : wait_for(pid);
}

void write_private(const fs::path& path, const std::string& content) {
    mkdir(path.parent_path().c_str(), 0700);
    const auto temporary = path.string() + "." + random_suffix();
    const int file = open(temporary.c_str(), O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0600);
    if (file < 0) throw std::runtime_error("Cannot write " + path.string());
    const bool ok = write(file, content.data(), content.size()) == static_cast<ssize_t>(content.size());
    close(file);
    if (!ok || rename(temporary.c_str(), path.c_str()) != 0) {
        unlink(temporary.c_str());
        throw std::runtime_error("Cannot write " + path.string());
    }
}

fs::path private_temporary_directory(const fs::path& fallback) {
    // A per-user tmpfs when the session has one; never shared /tmp.
    if (const char* runtime = getenv("XDG_RUNTIME_DIR"); runtime && *runtime && fs::path(runtime).is_absolute()) {
        const auto directory = fs::path(runtime) / "tank-battles-recomp";
        mkdir(directory.c_str(), 0700);
        struct stat info{};
        if (lstat(directory.c_str(), &info) == 0 && S_ISDIR(info.st_mode) &&
            info.st_uid == getuid() && (info.st_mode & 077) == 0) return directory;
    }
    // No session tmpfs: use a private per-user directory in the OS temp area.
    // A pre-existing path must pass the same ownership and mode checks.
    (void)fallback;
    const auto directory = fs::temp_directory_path() / ("tank-battles-recomp-" + std::to_string(getuid()));
    mkdir(directory.c_str(), 0700);
    struct stat info{};
    if (lstat(directory.c_str(), &info) == 0 && S_ISDIR(info.st_mode) &&
        info.st_uid == getuid() && (info.st_mode & 077) == 0) return directory;
    throw std::runtime_error("Cannot create a private temporary directory for online play");
}

namespace {
// libsecret is optional on Linux. Load its small synchronous API at runtime so
// the portable player has no additional link or package dependency.
struct SecretApi {
    void* library = dlopen("libsecret-1.so.0", RTLD_NOW | RTLD_LOCAL);
    using Schema = void;
    using NewSchema = Schema* (*)(const char*, int, ...);
    using FreeSchema = void (*)(Schema*);
    using Store = int (*)(const Schema*, const char*, const char*, const char*, void*, void**, ...);
    using Lookup = char* (*)(const Schema*, void*, void**, ...);
    using Clear = int (*)(const Schema*, void*, void**, ...);
    using FreePassword = void (*)(char*);
    NewSchema new_schema = library ? reinterpret_cast<NewSchema>(dlsym(library, "secret_schema_new")) : nullptr;
    FreeSchema free_schema = library ? reinterpret_cast<FreeSchema>(dlsym(library, "secret_schema_unref")) : nullptr;
    Store store = library ? reinterpret_cast<Store>(dlsym(library, "secret_password_store_sync")) : nullptr;
    Lookup lookup = library ? reinterpret_cast<Lookup>(dlsym(library, "secret_password_lookup_sync")) : nullptr;
    Clear clear = library ? reinterpret_cast<Clear>(dlsym(library, "secret_password_clear_sync")) : nullptr;
    FreePassword free_password = library ? reinterpret_cast<FreePassword>(dlsym(library, "secret_password_free")) : nullptr;
    bool ready() const { return new_schema && free_schema && store && lookup && clear && free_password; }
};

SecretApi& secret_api() {
    static SecretApi api;
    return api;
}

constexpr const char* secret_schema_name = "to.lmx.play.tank-battles.rpcn";
}

bool credential_store_available() { return secret_api().ready(); }

bool store_password(const std::string& id, const std::string& password) {
    auto& api = secret_api();
    if (!api.ready()) return false;
    auto* schema = api.new_schema(secret_schema_name, 0, "account", 0, nullptr);
    if (!schema) return false;
    const bool stored = api.store(schema, "default", ("Tank Battles: " + id).c_str(),
                                  password.c_str(), nullptr, nullptr, "account", id.c_str(), nullptr);
    api.free_schema(schema);
    return stored;
}

std::optional<std::string> load_password(const std::string& id) {
    auto& api = secret_api();
    if (!api.ready()) return std::nullopt;
    auto* schema = api.new_schema(secret_schema_name, 0, "account", 0, nullptr);
    if (!schema) return std::nullopt;
    char* value = api.lookup(schema, nullptr, nullptr, "account", id.c_str(), nullptr);
    std::optional<std::string> result;
    if (value) { result = value; api.free_password(value); }
    api.free_schema(schema);
    return result;
}

void forget_password(const std::string& id) {
    auto& api = secret_api();
    if (!api.ready()) return;
    auto* schema = api.new_schema(secret_schema_name, 0, "account", 0, nullptr);
    if (!schema) return;
    api.clear(schema, nullptr, nullptr, "account", id.c_str(), nullptr);
    api.free_schema(schema);
}
#endif

void erase_private(const fs::path& path) {
    std::error_code error;
    const auto size = fs::file_size(path, error);
    if (!error) {
        std::ofstream stream(path, std::ios::binary | std::ios::in | std::ios::out);
        if (stream) stream << std::string(static_cast<size_t>(size), '\0');
    }
    fs::remove(path, error);
}

}  // namespace tb::launcher
