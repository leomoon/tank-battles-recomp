// Builder.exe: a self-extracting front for the Tank Battles builder.
//
// The executable ends with a zip of the builder resources (scripts, SDK,
// runtime bundle and toolchain), then a trailer:
//   u64 payload offset | u64 payload size | 32-byte SHA-256 of the payload | "TBBUILD1"
// On first start the payload is verified and extracted with Windows' own
// tar.exe into %LOCALAPPDATA%\TankBattlesRecomp\builder\<digest prefix>, then
// the builder window starts from there. Later starts reuse the extraction.
//   Builder.exe --extract DIR   extracts only, for machines that block the default location.
#include <windows.h>
#include <bcrypt.h>
#include <shellapi.h>

#include <cstdint>
#include <cstdio>
#include <string>
#include <vector>

namespace {

constexpr char magic[8] = {'T', 'B', 'B', 'U', 'I', 'L', 'D', '1'};
constexpr size_t trailer_size = 8 + 8 + 32 + 8;
bool extract_only = false;

void fail(const std::wstring& message) {
    if (extract_only) {
        fwprintf(stderr, L"Tank Battles Builder: %ls\n", message.c_str());
    } else {
        MessageBoxW(nullptr, message.c_str(), L"Tank Battles Builder", MB_OK | MB_ICONERROR);
    }
}

std::wstring own_path() {
    std::wstring buffer(32768, L'\0');
    const DWORD length = GetModuleFileNameW(nullptr, buffer.data(), static_cast<DWORD>(buffer.size()));
    buffer.resize(length);
    return buffer;
}

struct Payload {
    uint64_t offset = 0, size = 0;
    unsigned char digest[32]{};
};

bool read_trailer(HANDLE file, Payload& payload) {
    LARGE_INTEGER size{};
    if (!GetFileSizeEx(file, &size) || size.QuadPart < static_cast<LONGLONG>(trailer_size)) return false;
    LARGE_INTEGER at{};
    at.QuadPart = size.QuadPart - trailer_size;
    unsigned char trailer[trailer_size];
    DWORD count = 0;
    if (!SetFilePointerEx(file, at, nullptr, FILE_BEGIN) || !ReadFile(file, trailer, trailer_size, &count, nullptr) ||
        count != trailer_size || memcmp(trailer + 48, magic, 8) != 0)
        return false;
    memcpy(&payload.offset, trailer, 8);
    memcpy(&payload.size, trailer + 8, 8);
    memcpy(payload.digest, trailer + 16, 32);
    return payload.offset + payload.size + trailer_size == static_cast<uint64_t>(size.QuadPart);
}

std::wstring hex(const unsigned char* bytes, size_t count) {
    static const wchar_t digits[] = L"0123456789abcdef";
    std::wstring text;
    for (size_t i = 0; i < count; ++i) { text += digits[bytes[i] >> 4]; text += digits[bytes[i] & 15]; }
    return text;
}

// Copies the payload to `target` while hashing it; false if it does not match.
bool copy_verified(HANDLE source, const Payload& payload, const std::wstring& target) {
    BCRYPT_ALG_HANDLE algorithm = nullptr;
    BCRYPT_HASH_HANDLE hash = nullptr;
    if (BCryptOpenAlgorithmProvider(&algorithm, BCRYPT_SHA256_ALGORITHM, nullptr, 0) != 0) return false;
    BCryptCreateHash(algorithm, &hash, nullptr, 0, nullptr, 0, 0);
    HANDLE output = CreateFileW(target.c_str(), GENERIC_WRITE, 0, nullptr, CREATE_ALWAYS, FILE_ATTRIBUTE_TEMPORARY, nullptr);
    bool ok = output != INVALID_HANDLE_VALUE;
    LARGE_INTEGER at{};
    at.QuadPart = static_cast<LONGLONG>(payload.offset);
    ok = ok && SetFilePointerEx(source, at, nullptr, FILE_BEGIN);
    std::vector<unsigned char> buffer(1 << 20);
    for (uint64_t left = payload.size; ok && left;) {
        DWORD wanted = static_cast<DWORD>(left < buffer.size() ? left : buffer.size()), got = 0, written = 0;
        ok = ReadFile(source, buffer.data(), wanted, &got, nullptr) && got == wanted &&
             BCryptHashData(hash, buffer.data(), got, 0) == 0 &&
             WriteFile(output, buffer.data(), got, &written, nullptr) && written == got;
        left -= got;
    }
    unsigned char digest[32]{};
    ok = ok && BCryptFinishHash(hash, digest, sizeof digest, 0) == 0 && memcmp(digest, payload.digest, 32) == 0;
    if (output != INVALID_HANDLE_VALUE) CloseHandle(output);
    BCryptDestroyHash(hash);
    BCryptCloseAlgorithmProvider(algorithm, 0);
    return ok;
}

// A small window saying what is happening while tar runs.
HWND show_progress() {
    WNDCLASSW type{};
    type.lpfnWndProc = DefWindowProcW;
    type.hInstance = GetModuleHandleW(nullptr);
    type.lpszClassName = L"TankBattlesBuilderSetup";
    type.hbrBackground = reinterpret_cast<HBRUSH>(COLOR_WINDOW + 1);
    RegisterClassW(&type);
    const int width = 460, height = 120;
    HWND window = CreateWindowW(type.lpszClassName, L"Tank Battles Builder", WS_CAPTION | WS_VISIBLE,
                                (GetSystemMetrics(SM_CXSCREEN) - width) / 2, (GetSystemMetrics(SM_CYSCREEN) - height) / 2,
                                width, height, nullptr, nullptr, type.hInstance, nullptr);
    CreateWindowW(L"STATIC", L"Preparing the builder for its first start. This takes a minute.",
                  WS_CHILD | WS_VISIBLE | SS_CENTER, 10, 30, width - 20, 40, window, nullptr, type.hInstance, nullptr);
    return window;
}

bool run_and_wait(std::wstring command, bool pump) {
    STARTUPINFOW startup{sizeof startup};
    PROCESS_INFORMATION process{};
    if (!CreateProcessW(nullptr, command.data(), nullptr, nullptr, FALSE, CREATE_NO_WINDOW, nullptr, nullptr, &startup, &process))
        return false;
    while (pump && MsgWaitForMultipleObjects(1, &process.hProcess, FALSE, INFINITE, QS_ALLINPUT) == WAIT_OBJECT_0 + 1) {
        MSG message;
        while (PeekMessageW(&message, nullptr, 0, 0, PM_REMOVE)) DispatchMessageW(&message);
    }
    WaitForSingleObject(process.hProcess, INFINITE);
    DWORD status = 1;
    GetExitCodeProcess(process.hProcess, &status);
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    return status == 0;
}

bool exists(const std::wstring& path) { return GetFileAttributesW(path.c_str()) != INVALID_FILE_ATTRIBUTES; }

bool extract(HANDLE self, const Payload& payload, const std::wstring& destination) {
    const std::wstring partial = destination + L".partial", archive = destination + L".zip";
    SHFILEOPSTRUCTW remove{};
    std::wstring doomed = partial + L'\0';
    remove.wFunc = FO_DELETE;
    remove.pFrom = doomed.c_str();
    remove.fFlags = FOF_NO_UI;
    if (exists(partial)) SHFileOperationW(&remove);
    HWND window = show_progress();
    bool ok = copy_verified(self, payload, archive);
    if (!ok) {
        DestroyWindow(window);
        DeleteFileW(archive.c_str());
        fail(L"This copy of Builder.exe is damaged. Download it again.");
        return false;
    }
    wchar_t system[MAX_PATH];
    GetSystemDirectoryW(system, MAX_PATH);
    CreateDirectoryW(partial.c_str(), nullptr);
    ok = run_and_wait(L"\"" + std::wstring(system) + L"\\tar.exe\" -xf \"" + archive + L"\" -C \"" + partial + L"\"", true);
    DeleteFileW(archive.c_str());
    DestroyWindow(window);
    if (!ok || !MoveFileExW(partial.c_str(), destination.c_str(), 0)) {
        fail(L"Could not unpack the builder into " + destination +
             L". Free some disk space, or run \"Builder.exe --extract FOLDER\" to choose another place.");
        return false;
    }
    return true;
}

}  // namespace

int WINAPI WinMain(HINSTANCE, HINSTANCE, LPSTR, int) {
    int count = 0;
    wchar_t** argv = CommandLineToArgvW(GetCommandLineW(), &count);
    std::wstring chosen;
    if (count == 3 && std::wstring(argv[1]) == L"--extract") {
        chosen = argv[2];
        extract_only = true;
    }
    LocalFree(argv);

    HANDLE self = CreateFileW(own_path().c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING, 0, nullptr);
    Payload payload;
    if (self == INVALID_HANDLE_VALUE || !read_trailer(self, payload)) {
        fail(L"This copy of Builder.exe is incomplete. Download it again.");
        return 2;
    }
    std::wstring destination = chosen;
    if (destination.empty()) {
        wchar_t local[MAX_PATH];
        if (!GetEnvironmentVariableW(L"LOCALAPPDATA", local, MAX_PATH)) { fail(L"LOCALAPPDATA is not set."); return 2; }
        std::wstring base = std::wstring(local) + L"\\TankBattlesRecomp";
        CreateDirectoryW(base.c_str(), nullptr);
        base += L"\\builder";
        CreateDirectoryW(base.c_str(), nullptr);
        destination = base + L"\\" + hex(payload.digest, 6);
    }
    if (!exists(destination + L"\\package.json") && !extract(self, payload, destination)) return 2;
    CloseHandle(self);
    if (!chosen.empty()) return 0;
    const std::wstring python = destination + L"\\toolchain\\python\\pythonw.exe";
    std::wstring command = L"\"" + python + L"\" -E -s -B \"" + destination + L"\\scripts\\builder_app.py\"";
    STARTUPINFOW startup{sizeof startup};
    PROCESS_INFORMATION process{};
    if (!CreateProcessW(python.c_str(), command.data(), nullptr, nullptr, FALSE, 0, nullptr, destination.c_str(), &startup, &process)) {
        fail(L"Could not start the builder from " + destination + L". Delete that folder and start Builder.exe again.");
        return 2;
    }
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    return 0;
}
