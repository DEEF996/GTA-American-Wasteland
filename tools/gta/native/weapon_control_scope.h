#pragma once
#include <cmath>
#include <cstdint>

namespace gta_thaw::weapon_control {
// Original UseGun publishes CPedIK::bGunReachedTarget after checking the hand.
// Our posed hand has its own alignment check; expose that result only for the
// synchronous original shot. Restore just our bit, even on a native exception.
inline bool WithAimReady(volatile std::uint32_t* flags,bool aligned,
                         void* context,bool(*fire)(void*)) {
    if(!flags || !aligned || !fire)return false;
    const auto before=*flags&1u;
    __try { *flags|=1u;return fire(context); }
    __finally { *flags=(*flags&~1u)|before; }
}

struct ShotAccuracy {
    std::uint8_t* ped;
    float *recoil,*weapon,*world_spread;
};
// Stock UseGun normally decays the recoil accumulator. THAW owns the pose, so
// expose steady aim only to this synchronous native shot, never to GTA updates.
inline bool WithMinimalRecoil(const ShotAccuracy& fields,bool shotgun,
                              void* context,bool(*fire)(void*)) {
    if(!fields.ped || !fields.recoil || !fields.weapon || !fields.world_spread || !fire)return false;
    const auto ped=*fields.ped;
    const float recoil=*fields.recoil,accuracy=*fields.weapon,spread=*fields.world_spread;
    if(ped>100 || !std::isfinite(recoil) || recoil<0 || !std::isfinite(accuracy) || accuracy<=0 ||
       !std::isfinite(spread) || spread<0)return false;
    bool fired=false;
    __try {
        *fields.ped=100;*fields.recoil=0;*fields.world_spread=0;
        // Keep shotgun pellets, with a narrow cone (.05 / accuracy in GTA).
        if(shotgun && accuracy<50.f)*fields.weapon=50.f;
        fired=fire(context);
    } __finally {
        *fields.ped=ped;*fields.recoil=recoil;*fields.weapon=accuracy;*fields.world_spread=spread;
    }
    return fired;
}
}
