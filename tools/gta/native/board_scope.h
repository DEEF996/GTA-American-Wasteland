#pragma once
#include <windows.h>
#include <cstdint>
#include <cstring>

namespace gta_thaw::board_preview {
using StateFn = int(__cdecl*)(int, void*);
// Every state this preview changes, including texture/raster and alpha state dependencies.
constexpr int States[] = {1,3,4,6,7,8,9,10,11,12,20,29,30};
struct SavedState { std::uintptr_t value[sizeof(States)/sizeof(States[0])]; };
inline bool SaveState(StateFn get, SavedState& state) noexcept {
    for (unsigned i = 0; i < sizeof(States)/sizeof(States[0]); ++i)
        if (!get(States[i], &state.value[i])) return false;
    return true;
}
inline bool RestoreState(StateFn set, const SavedState& state) noexcept {
    bool ok = true;
    for (unsigned i = 0; i < sizeof(States)/sizeof(States[0]); ++i)
        if (!set(States[i], reinterpret_cast<void*>(state.value[i]))) ok = false;
    return ok;
}
inline bool DrawStateScoped(StateFn set, StateFn get, void(*draw)(void*), void* context,
                            std::uint64_t& restored) {
    SavedState saved{};
    if (!SaveState(get, saved)) return false;
    bool ok = false;
    __try { draw(context); }
    __finally { ok = RestoreState(set, saved); if (ok) ++restored; }
    return ok;
}

// Pinned RwIm3DEnd clears exactly 15 DWORDs; reject an already active immediate batch.
inline bool DrawImmediateScoped(void* pool, bool(*draw)(void*), void* context, bool(*end)(),
                                std::uint64_t& restored) {
    unsigned char before[60];
    std::memcpy(before,pool,sizeof(before));
    std::uint32_t elements = 0;
    std::uint16_t count = 0;
    std::memcpy(&elements,before+4,4);
    std::memcpy(&count,before,2);
    if (elements || count) return false;
    bool ok = false;
    __try { ok = draw(context); }
    __finally {
        __try {
            std::memcpy(&elements,static_cast<unsigned char*>(pool)+4,4);
            if (elements && !end()) ok = false;
        } __finally {
            std::memcpy(pool,before,sizeof(before));
            ++restored;
        }
    }
    return ok;
}
}
