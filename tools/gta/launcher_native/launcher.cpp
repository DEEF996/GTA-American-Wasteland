#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <cwctype>
#include <string>
#include "launcher_command.h"

namespace {
int Failure(const wchar_t* message) {
    MessageBoxW(nullptr, message, L"GTA American Wasteland", MB_OK | MB_ICONERROR);
    return 1;
}
bool RegularFile(const std::wstring& path) {
    const DWORD attributes = GetFileAttributesW(path.c_str());
    return attributes != INVALID_FILE_ATTRIBUTES &&
        !(attributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT));
}
}

int WINAPI wWinMain(HINSTANCE, HINSTANCE, PWSTR, int) {
    std::wstring path(32768, L'\0');
    const DWORD count = GetModuleFileNameW(nullptr, path.data(), static_cast<DWORD>(path.size()));
    if (!count || count >= path.size()) return Failure(L"Cannot locate the launcher folder.");
    path.resize(count);
    const std::wstring root = path.substr(0, path.find_last_of(L"\\/"));
    const std::wstring runtime = root + L"\\runtime\\python\\pythonw.exe";
    if (!RegularFile(runtime) || !RegularFile(root + L"\\tools\\gta\\launcher.py"))
        return Failure(L"Extract the COMPLETE release ZIP before opening the launcher.\n"
            L"Keep runtime and tools beside this EXE. No Python installation is needed.\n\n"
            L"Rozpakuj CALA paczke ZIP przed uruchomieniem launchera.\n"
            L"Foldery runtime i tools musza byc obok EXE. Nie instaluj Pythona.");
    unsigned long long hash = 14695981039346656037ULL;
    for (wchar_t character : root) {
        hash ^= static_cast<unsigned short>(std::towlower(character));
        hash *= 1099511628211ULL;
    }
    const std::wstring name = L"Local\\GTA_AW_Desktop_" + std::to_wstring(hash);
    HANDLE mutex = CreateMutexW(nullptr, TRUE, name.c_str());
    if (!mutex) return Failure(L"Cannot create the local launcher lock.");
    if (GetLastError() == ERROR_ALREADY_EXISTS) {
        CloseHandle(mutex);
        MessageBoxW(nullptr, L"The launcher is already open. / Launcher jest juz otwarty.",
                    L"GTA American Wasteland", MB_OK | MB_ICONINFORMATION);
        return 0;
    }
    std::wstring command = LauncherCommand(root);
    STARTUPINFOW startup{};
    startup.cb = sizeof(startup);
    PROCESS_INFORMATION process{};
    const BOOL started = CreateProcessW(runtime.c_str(), command.data(), nullptr, nullptr, FALSE,
        CREATE_NO_WINDOW, nullptr, root.c_str(), &startup, &process);
    DWORD code = 1;
    if (started) {
        CloseHandle(process.hThread);
        if (WaitForSingleObject(process.hProcess, INFINITE) == WAIT_OBJECT_0)
            GetExitCodeProcess(process.hProcess, &code);
        CloseHandle(process.hProcess);
    }
    ReleaseMutex(mutex);
    CloseHandle(mutex);
    if (!started) return Failure(L"The bundled launcher could not start. Extract the complete ZIP again.\n"
        L"Nie mozna uruchomic launchera. Rozpakuj ponownie cala paczke ZIP.");
    if (code != 0) return Failure(L"The launcher stopped. Check launcher-error.log in this folder.\n"
        L"Launcher zakonczyl dzialanie. Sprawdz launcher-error.log w tym folderze.");
    return 0;
}
