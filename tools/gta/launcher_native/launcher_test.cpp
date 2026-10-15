#include <cassert>
#include <windows.h>
#include <shellapi.h>
#include "launcher_command.h"

int main() {
    for (const auto& root : {std::wstring(L"D:\\Games\\GTA AW"), std::wstring(L"D:\\Gry\\Polskie \u0142\u0105ki"),
                             std::wstring(L"D:\\tail\\")}) {
        int count = 0;
        LPWSTR* argv = CommandLineToArgvW(LauncherCommand(root).c_str(), &count);
        assert(argv && count == 5);
        assert(argv[0] == root + L"\\runtime\\python\\pythonw.exe");
        assert(std::wstring(argv[1]) == L"-E" && std::wstring(argv[2]) == L"-s" && std::wstring(argv[3]) == L"-B");
        assert(argv[4] == root + L"\\tools\\gta\\launcher.py");
        LocalFree(argv);
    }
    assert(QuoteLauncherArgument(L"") == L"\"\"");
    return 0;
}
