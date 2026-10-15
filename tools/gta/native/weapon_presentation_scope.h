#pragma once
#include <cstdint>
namespace gta_thaw::weapon_presentation {
inline bool HideWeaponScoped(std::uint32_t* slot,std::uint32_t expected,void* context,void(*render)(void*)) {
    if(!expected||*slot!=expected)return false;
    bool restored=false;
    __try { *slot=0;render(context); }
    __finally {
        // A third-party render hook may replace the selection. Do not overwrite
        // a new pointer or draw the now-unowned previous clump in that case.
        if(!*slot) { *slot=expected;restored=true; }
    }
    return restored;
}
}
