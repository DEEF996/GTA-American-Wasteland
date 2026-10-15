#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <bcrypt.h>
#include <cstdio>
#include "weapon_control.h"
#include "weapon_control_policy.h"
#include "weapon_control_scope.h"
#include "board_attack_protocol.h"
#include "camera_preview.h"
#include "movement_hold.h"
#include "movement_trajectory_protocol.h"
#include "settings_menu.h"

namespace gta_thaw::weapon_control {
namespace {
constexpr std::uintptr_t Select=0x5E61F0,Skill=0x5E6580,Info=0x743C60,Fire=0x742300;
constexpr std::uintptr_t PedPool=0xB74490,MouseNew=0xB73418,MouseOld=0xB7342C;
const Snapshot* snapshot=nullptr;
State state{};
Stats stats{};
FireInput input;
bool prepared=false,wants_fire=false,attempted=false;
std::uint32_t prepared_frame=0,attempted_frame=0,cooldown_ped=0;
std::uint64_t input_request=0;
std::uint32_t input_ped=0;
struct Cooldown { bool set=false; std::uint32_t type=0,until=0; };
Cooldown cooldowns[WeaponSlots]{};
std::uint32_t loop_ms=0;
std::uintptr_t weapon_info=0;

template<class T> T Read(std::uintptr_t address) noexcept {
    T value;std::memcpy(&value,reinterpret_cast<void*>(address),sizeof(value));return value;
}
bool Pointer(std::uintptr_t address) noexcept { return address>=0x10000 && address<0x80000000 && !(address&3); }
bool Focused() noexcept {
    DWORD pid=0;GetWindowThreadProcessId(GetForegroundWindow(),&pid);
    return pid==GetCurrentProcessId();
}
bool OwnedPed(std::uintptr_t ped) noexcept {
    const auto pool=Read<std::uintptr_t>(PedPool);
    if(!Pointer(pool)) return false;
    const auto objects=Read<std::uintptr_t>(pool),flags=Read<std::uintptr_t>(pool+4);
    std::uint32_t index=0;
    return Pointer(flags) && board_attack::PoolIndex(ped,objects,Read<std::int32_t>(pool+8),index) &&
        !(Read<unsigned char>(flags+index)&0x80) && (Read<unsigned char>(ped+0x36)&7)==3 &&
        Read<float>(ped+0x540)>0 && Pointer(Read<std::uintptr_t>(ped+0x480));
}
bool Eligible(const movement_hold::Stats& movement,std::uint64_t now,bool allow_buffer=false) noexcept {
    return snapshot && movement.active && !movement.preparing && movement.trajectory && movement.interactive_enabled &&
        movement.request && movement.source_tick && !movement.source_bailing &&
        (allow_buffer || !movement.buffering) && movement.source_mode<=1 &&
        !settings_menu::Open() && Focused() && snapshot->tick_ms<=now && now-snapshot->tick_ms<=100 &&
        ControlEligible(*snapshot) && (snapshot->status_flags&Foreground) && OwnedPed(snapshot->ped_address);
}
bool Pin(std::uintptr_t address,std::size_t count,const char* expected) noexcept {
    BCRYPT_ALG_HANDLE algorithm=nullptr;unsigned char digest[32];char hex[65]{};
    if(BCryptOpenAlgorithmProvider(&algorithm,BCRYPT_SHA256_ALGORITHM,nullptr,0)<0) return false;
    const bool ok=BCryptHash(algorithm,nullptr,0,reinterpret_cast<unsigned char*>(address),
        static_cast<ULONG>(count),digest,sizeof(digest))>=0;
    BCryptCloseAlgorithmProvider(algorithm,0);
    if(!ok) return false;
    for(int i=0;i<32;++i) std::snprintf(hex+i*2,3,"%02x",digest[i]);
    return !std::strcmp(hex,expected);
}
bool Guards() noexcept {
    // Generated from the pinned supported GTA executable, not guessed SDK offsets.
    return Pin(Select,133,"d6df8f95dbd840d19f4e4a9caa160e6302cf107a0a1c76fe0dc5e860a676f37d") &&
        Pin(Skill,24,"5a085dc358cb2e8f90558e3aa6953a76db0a38139aeb6ec45707f0acf214fe4d") &&
        Pin(Info,105,"61bc6fb5f302ff16a4fdc2cb396fc6b5bf7591d10dd0a8aff5beceb1ceef1bb3") &&
        Pin(Fire,1792,"470d9e3fed496b2f0d6966d55ef98285bea4e3fdd9881b46c44ee2dbdd3125d5") &&
        Pin(0x5E5ED0,145,"d612f6453d3e982a4953f46773250d88a0eae75f3fa43ab9ea8a63ebe42d78cb") &&
        Pin(0x5DF340,112,"8da56e23528f1649d9d8217d51c7391977c2b1f7e8abfaa95edc00d7d6ab0471") &&
        Pin(0x73FDD7,51,"c81db6c5c8e5574a78550a58c17f14c292ca11c61e01edc52bfe978762c4ab52") &&
        Pin(0x73FBFD,125,"02e13040e55a821ea10f481d307d165ff7ca39aaf77e5cf2271e10c7eeeee52e") &&
        Pin(0x73FF65,85,"03b1ed5d16e6c444ef4b347eb1327d74a7c84fb20ca9079dc3f8249916fea5a1") &&
        Pin(0x7401BB,26,"37907a1b9f464230e9f70ec629145a399d6dbb5c0e49e64f7f97e842982a09d2") &&
        Read<float>(0x858628)==100.f && Read<float>(0x8D611C)==.05f &&
        Pin(0x61EC2F,51,"8fb45beb8f399fedafa86a7a6c110bbb9e0806d02d75563b64b416d2f7f0bfb0") &&
        Pin(0x741620,160,"232fb0cb721227dff9ce91d0349b48ba7dde144071bfebd2f824511e8cdb79a3") &&
        Pin(0x741A1B,221,"8c292bb5c40d40b6ad61ba624f70a9bc1019d1f9db55cd62efb25c7fa9558b59") &&
        Pin(0x73810B,200,"b0551c91de3896ae64cc9a3eea73a13414251a6b481085880dd10d68e996c926") &&
        Pin(0x53F250,41,"2fe995127e746310a8444496dca8371407e216647b01cd0a13e5678613a3802d");
}
bool SafeGuards() noexcept {
    __try { return Guards(); }
    __except(EXCEPTION_EXECUTE_HANDLER) { return false; }
}
void Clear() noexcept {
    const bool enabled=state.enabled;state={};state.enabled=enabled;wants_fire=false;
    input.Reset();input_request=0;input_ped=0;weapon_info=0;
}
std::uintptr_t Weapon(std::uint32_t ped,unsigned slot) noexcept { return ped+0x5A0+slot*0x1C; }
bool Refresh(const movement_hold::Stats& movement,std::uint64_t now) noexcept {
    const auto ped=snapshot->ped_address;
    const unsigned slot=Read<unsigned char>(ped+0x718);
    if(slot>=WeaponSlots) return false;
    const auto weapon=Weapon(ped,slot);
    const auto type=Read<std::uint32_t>(weapon);
    if(type>=47) return false;
    const auto skill=reinterpret_cast<unsigned char(__thiscall*)(void*)>(Skill)(reinterpret_cast<void*>(ped));
    if(skill>3) return false;
    const auto info=reinterpret_cast<std::uintptr_t(__cdecl*)(int,unsigned char)>(Info)(type,skill);
    if(info<0xC8AAB8 || info+0x70>0xC8AAB8+80*0x70 || (info-0xC8AAB8)%0x70) return false;
    if(Read<std::uint32_t>(info+0x14)!=slot) return false;
    State next{};next.enabled=state.enabled;next.active=true;next.request=movement.request;
    next.tick_ms=now;next.ped=ped;next.slot=slot;next.type=type;
    next.model=Read<std::uint32_t>(info+0xC);next.weapon_clump=Read<std::uint32_t>(ped+0x4F4);
    next.ammo_clip=Read<std::uint32_t>(weapon+8);next.ammo_total=Read<std::uint32_t>(weapon+12);
    next.weapon_state=Read<std::uint32_t>(weapon+4);
    next.range=Read<float>(info+8);std::memcpy(next.fire_offset,reinterpret_cast<void*>(info+0x24),12);
    next.one_handed=OneHanded(Read<std::uint32_t>(info+0x18));
    next.hanging=movement.source_mode==1 && movement.source_walk_state==3;
    if(next.weapon_state>4 || next.ammo_clip>99999 || next.ammo_total>99999 ||
       !std::isfinite(next.range) || next.range<0 || next.range>10000 ||
       (next.weapon_clump && !Pointer(next.weapon_clump))) return false;
    for(float value:next.fire_offset) if(!std::isfinite(value) || std::fabs(value)>4) return false;
    loop_ms=LoopMilliseconds(Read<float>(info+0x40),Read<float>(info+0x44));
    next.can_aim=Firearm(type) && next.weapon_clump && next.range>0 && loop_ms &&
        ContextAllowsAim(movement.source_mode,movement.source_walk_state,movement.source_bailing!=0,next.one_handed);
    state=next;weapon_info=info;return true;
}
void WheelSelection() noexcept {
    const int up=Read<unsigned char>(MouseNew+3) && !Read<unsigned char>(MouseOld+3);
    const int down=Read<unsigned char>(MouseNew+4) && !Read<unsigned char>(MouseOld+4);
    if(up==down) return;
    bool available[WeaponSlots]{};available[0]=true;
    for(unsigned i=1;i<WeaponSlots;++i) {
        const auto weapon=Weapon(state.ped,i),type=Read<std::uint32_t>(weapon);
        // Preserve native inventory; firearms with no ammunition and empty slots
        // are skipped, while melee/equipment remain selectable but cannot shoot.
        available[i]=type>0 && type<47 && (Read<std::uint32_t>(weapon+12)>0 || type<=15 || type>=44);
    }
    const auto slot=NextInventorySlot(state.slot,up?-1:1,available);
    if(slot<0) return;
    reinterpret_cast<void(__thiscall*)(void*,int)>(Select)(reinterpret_cast<void*>(state.ped),slot);
    ++stats.selections;
}
bool AimMatches(const camera_preview::AimState& aim,std::uint64_t now) noexcept {
    return state.active && state.can_aim && aim.active && aim.ped==state.ped && aim.request==state.request &&
        aim.tick_ms>=state.tick_ms && aim.tick_ms<=now && now-aim.tick_ms<=100;
}
bool Near(const float* value,const float* origin,float radius) noexcept {
    if(!value) return false;
    float distance=0;
    for(unsigned i=0;i<3;++i) {
        if(!std::isfinite(value[i]) || std::fabs(value[i])>100000) return false;
        const auto d=value[i]-origin[i];distance+=d*d;
    }
    return distance<=radius*radius;
}
bool FireNative(std::uintptr_t weapon,std::uint32_t ped,float* origin,float* barrel,float* target) {
    using NativeFire=bool(__thiscall*)(void*,void*,float*,float*,void*,float*,float*);
    const auto fire=reinterpret_cast<NativeFire>(Fire);
    if(!Launcher(state.type))
        return fire(reinterpret_cast<void*>(weapon),reinterpret_cast<void*>(ped),origin,barrel,nullptr,target,nullptr);
    // Stock player launchers require a launcher camera mode before reaching
    // original FireProjectile. Keep our actual camera front/up/target; only
    // expose MODE_ROCKETLAUNCHER during this synchronous original call.
    // FireProjectile computes the world direction from our explicit target,
    // then AddProjectile owns the real missile, collision, damage and effects.
    constexpr std::uintptr_t camera=0xB6F028;
    const auto index=Read<unsigned char>(camera+0x59);
    if(index>=2) { ++stats.rejected;return false; }
    const auto mode_address=camera+0x174+index*0x238+0xC;
    const auto saved=Read<std::uint16_t>(mode_address);
    if(!LauncherCameraOwned(index,saved)) { ++stats.rejected;return false; }
    auto* mode=reinterpret_cast<volatile std::uint16_t*>(mode_address);
    bool fired=false;
    __try {
        *mode=8;
        fired=fire(reinterpret_cast<void*>(weapon),reinterpret_cast<void*>(ped),origin,barrel,nullptr,target,nullptr);
    } __finally {
        // Restore our exact owned field even when the original call faults.
        // Never overwrite a mode changed by another camera owner.
        if(*mode==8) *mode=saved;
    }
    return fired;
}
struct Shot { std::uintptr_t weapon;std::uint32_t ped;float *origin,*barrel,*target; };
bool DispatchNativeShot(void* context) {
    const auto& shot=*static_cast<Shot*>(context);
    return FireNative(shot.weapon,shot.ped,shot.origin,shot.barrel,shot.target);
}
bool DispatchShot(void* context) {
    const auto& shot=*static_cast<Shot*>(context);
    const auto player=Read<std::uintptr_t>(shot.ped+0x480);
    if(!Pointer(player) || !weapon_info)return false;
    const ShotAccuracy fields{reinterpret_cast<std::uint8_t*>(shot.ped+0x71A),
        reinterpret_cast<float*>(player+0x2C),reinterpret_cast<float*>(weapon_info+0x38),
        reinterpret_cast<float*>(0xB7CD64)};
    return WithMinimalRecoil(fields,Shotgun(state.type),context,DispatchNativeShot);
}
}

const char* Initialize(const std::wstring& directory,const Snapshot* source) {
    if(GetPrivateProfileIntW(L"development",L"MovementInteractivePreview",0,(directory+L"\\adapter.ini").c_str())!=1) return nullptr;
    if(!SafeGuards()) return "Native GTA weapon, inventory or mouse guards differ";
    snapshot=source;state.enabled=true;return nullptr;
}
void PrepareTick() noexcept {
    if(!state.enabled || stats.faults || !snapshot) return;
    __try {
        const auto frame=Read<std::uint32_t>(0xB7CB4C);
        if(prepared && frame==prepared_frame) return;
        prepared=true;prepared_frame=frame;stats.prepared_frame=frame;++stats.prepares;
        const auto now=GetTickCount64();const auto movement=movement_hold::GetStats();
        // Buffered heartbeats also mark routine original-worker callbacks.
        // Preserve the valid selected gun, aim pose and trigger lease through
        // them; FireFromPose separately requires an unbuffered current sample.
        // Focus/menu/bail/ped/request checks remain live on every preparation.
        if(!Eligible(movement,now,true)) { Clear();return; }
        if(input_request!=movement.request || input_ped!=snapshot->ped_address) {
            input.Reset();input_request=movement.request;input_ped=snapshot->ped_address;
        }
        if(cooldown_ped!=snapshot->ped_address) {
            cooldown_ped=snapshot->ped_address;for(auto& c:cooldowns) c={};
        }
        if(!Refresh(movement,now)) { ++stats.rejected;Clear();return; }
        WheelSelection();
        if(!Refresh(movement,now)) { ++stats.rejected;Clear();return; }
        wants_fire=input.Update((GetAsyncKeyState(VK_LBUTTON)&0x8000)!=0,(GetAsyncKeyState(VK_RBUTTON)&0x8000)!=0);
    } __except(EXCEPTION_EXECUTE_HANDLER) { ++stats.faults;Clear(); }
}
State GetState() noexcept {
    auto result=state;
    result.aiming=AimMatches(camera_preview::GetAimState(),GetTickCount64());
    return result;
}
void FireFromPose(std::uint32_t ped,std::uint64_t request,const float* origin,const float* barrel,
                  const float* barrel_forward) noexcept {
    ++stats.pose_calls;
    if(!state.enabled || stats.faults || !wants_fire) { ++stats.input_blocked;stats.last_gate=1;return; }
    __try {
        const auto frame=Read<std::uint32_t>(0xB7CB4C);
        stats.fire_frame=frame;
        if(!prepared || prepared_frame!=frame || (attempted && attempted_frame==frame)) {
            ++stats.frame_blocked;stats.last_gate=2;return;
        }
        attempted=true;attempted_frame=frame;
        const auto now=GetTickCount64();const auto movement=movement_hold::GetStats();
        const auto aim=camera_preview::GetAimState();
        if(ped!=state.ped || request!=state.request || request!=movement.request ||
           !Eligible(movement,now) || !AimMatches(aim,now) ||
           !ContextAllowsAim(movement.source_mode,movement.source_walk_state,movement.source_bailing!=0,state.one_handed) ||
           !(GetAsyncKeyState(VK_LBUTTON)&0x8000) || !(GetAsyncKeyState(VK_RBUTTON)&0x8000)) {
            ++stats.gate_blocked;stats.last_gate=3;return;
        }
        if(Read<unsigned char>(ped+0x718)!=state.slot || Read<std::uint32_t>(ped+0x4F4)!=state.weapon_clump ||
           !Near(origin,snapshot->position,5) || !Near(barrel,origin,3) || !Near(aim.target,aim.eye,10001) ||
           !MuzzleAligned(barrel,aim.target,barrel_forward)) {
            ++stats.rejected;stats.last_gate=4;return;
        }
        const auto weapon=Weapon(ped,state.slot);if(Read<std::uint32_t>(weapon)!=state.type) return;
        const auto clock=Read<std::uint32_t>(0xB7CB84);auto& cooldown=cooldowns[state.slot];
        if(cooldown.type!=state.type) cooldown={false,state.type,0};
        if(!CanFire(Read<std::uint32_t>(weapon+4),Read<std::uint32_t>(weapon+12),clock,
                    Read<std::uint32_t>(weapon+16),cooldown.until,cooldown.set)) {
            ++stats.cooldown_blocked;stats.last_gate=5;return;
        }
        float start[3],muzzle[3],target[3];
        std::memcpy(start,origin,12);std::memcpy(muzzle,barrel,12);std::memcpy(target,aim.target,12);
        Shot shot{weapon,ped,start,muzzle,target};
        ++stats.native_calls;
        const bool fired=WithAimReady(reinterpret_cast<volatile std::uint32_t*>(ped+0x528),true,&shot,DispatchShot);
        if(!fired) { ++stats.native_false;stats.last_gate=6; }
        if(fired) {
            stats.last_gate=0;
            ++stats.shots;stats.last_shot_frame=frame;stats.last_shot_type=state.type;
            cooldown={true,state.type,clock+loop_ms};
            if(Read<std::uint32_t>(weapon+4)==1)
                reinterpret_cast<bool(__thiscall*)(void*,int,bool)>(0x5DF340)(reinterpret_cast<void*>(ped),250,false);
            state.ammo_clip=Read<std::uint32_t>(weapon+8);state.ammo_total=Read<std::uint32_t>(weapon+12);
            state.weapon_state=Read<std::uint32_t>(weapon+4);
        }
        // CPlayerPed::ProcessControl continues calling CWeapon::Update. It owns
        // native reload completion/audio; a second update here would duplicate it.
    } __except(EXCEPTION_EXECUTE_HANDLER) { ++stats.faults;Clear(); }
}
Stats GetStats() noexcept { return stats; }
}
