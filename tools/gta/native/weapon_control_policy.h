#pragma once
#include <cmath>
#include <cstdint>

namespace gta_thaw::weapon_control {
constexpr unsigned WeaponSlots=13;
inline bool Firearm(std::uint32_t type) noexcept { return type>=22 && type<=38; }
inline bool Launcher(std::uint32_t type) noexcept { return type==35 || type==36; }
inline bool Shotgun(std::uint32_t type) noexcept { return type>=25 && type<=27; }
inline bool LauncherCameraOwned(unsigned index,unsigned mode) noexcept { return index<2 && mode==15; }
inline bool MuzzleAligned(const float* muzzle,const float* target,const float* forward) noexcept {
    if(!muzzle || !target || !forward)return false;
    float direction2=0,forward2=0,dot=0;
    for(unsigned i=0;i<3;++i) {
        if(!std::isfinite(muzzle[i]) || !std::isfinite(target[i]) || !std::isfinite(forward[i]))return false;
        const float direction=target[i]-muzzle[i];
        direction2+=direction*direction;forward2+=forward[i]*forward[i];dot+=direction*forward[i];
    }
    return direction2>.0001f && direction2<100020001.f && forward2>.97f && forward2<1.03f &&
        dot/std::sqrt(direction2*forward2)>.985f;
}
// One physical gun is rendered/fired, including pro-skill twin-pistol entries.
// The second gun belongs to UseGun task sequencing, which we never invoke.
inline bool OneHanded(std::uint32_t flags) noexcept { return (flags&2)!=0 && (flags&0x200)==0; }
inline bool ContextAllowsAim(std::uint32_t mode,std::uint32_t walk_state,
                             bool bailing,bool one_handed) noexcept {
    return !bailing && (mode==0 || (mode==1 && (walk_state!=3 || one_handed)));
}
inline int NextInventorySlot(unsigned current,int direction,const bool (&available)[WeaponSlots]) noexcept {
    if(current>=WeaponSlots || (direction!=1 && direction!=-1)) return -1;
    for(unsigned offset=1;offset<WeaponSlots;++offset) {
        const auto next=(static_cast<int>(current)+direction*static_cast<int>(offset)+WeaponSlots)%WeaponSlots;
        if(available[next]) return next;
    }
    return -1;
}
inline bool DeadlineReached(std::uint32_t now,std::uint32_t deadline) noexcept {
    return static_cast<std::int32_t>(now-deadline)>=0;
}
// On-foot guns delegate cadence to the original UseGun animation loop, while
// CWeapon::Fire owns ammunition, damage and reload deadlines. Read that loop
// from the selected skill's actual WeaponInfo; never use render-frame counts.
inline std::uint32_t LoopMilliseconds(float begin,float end) noexcept {
    const auto seconds=end-begin;
    if(!std::isfinite(begin) || !std::isfinite(end) || begin<0 || seconds<=0 || seconds>10) return 0;
    return static_cast<std::uint32_t>(std::ceil(seconds*1000.f));
}
inline bool CanFire(std::uint32_t weapon_state,std::uint32_t ammo,std::uint32_t now,
                    std::uint32_t native_deadline,std::uint32_t own_deadline,bool has_deadline) noexcept {
    return weapon_state<=1 && ammo && (!native_deadline || DeadlineReached(now,native_deadline)) &&
        (!has_deadline || DeadlineReached(now,own_deadline));
}
class FireInput {
    bool released=false;
public:
    void Reset() noexcept { released=false; }
    bool Update(bool left,bool right) noexcept {
        if(!left) released=true;
        return released && left && right;
    }
};
}
