#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <cstdint>

// This DLL runs in our 64-bit Python process, never inside GTA or the stock host.
extern "C" __declspec(dllexport) int WINAPI thaw_atomic_store32(
    volatile LONG* target, std::uint32_t value) noexcept {
    if (!target || (reinterpret_cast<std::uintptr_t>(target) & 3u)) return 0;
    InterlockedExchange(target, static_cast<LONG>(value));
    return 1;
}
