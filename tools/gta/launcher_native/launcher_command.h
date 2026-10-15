#pragma once
#include <string>

// Windows argv quoting, including trailing backslashes before the closing quote.
inline std::wstring QuoteLauncherArgument(const std::wstring& value) {
    std::wstring result = L"\"";
    size_t slashes = 0;
    for (wchar_t character : value) {
        if (character == L'\\') { ++slashes; continue; }
        result.append(slashes * (character == L'"' ? 2 : 1), L'\\');
        slashes = 0;
        if (character == L'"') result += L'\\';
        result += character;
    }
    result.append(slashes * 2, L'\\');
    return result + L'"';
}

inline std::wstring LauncherCommand(const std::wstring& root) {
    return QuoteLauncherArgument(root + L"\\runtime\\python\\pythonw.exe") +
        L" -E -s -B " + QuoteLauncherArgument(root + L"\\tools\\gta\\launcher.py");
}
