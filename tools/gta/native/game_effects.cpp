#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <cwchar>
#include <iterator>
#include "game_effects.h"
#include "game_effects_protocol.h"
#include "game_effects_ownership.h"
#include "camera_preview.h"
#include "weapon_control.h"
#include "movement_hold.h"
#include "settings_menu.h"
#include "presentation_time.h"
#include "hud_scope.h"
#include "rw_raster.h"

namespace gta_thaw::game_effects {
namespace {
constexpr std::uintptr_t TimeScale=0xB7CB64,SfxVolume=0xB5FCCC,AudioPitch=0x8CBA6C;
const Snapshot* snapshot=nullptr;
HANDLE mapping=nullptr;
SharedEffects* shared=nullptr;
Controller control;
Stats stats;
std::uint64_t owner_request=0,last_tick_ms=0,sequence=0;
std::uint32_t owner_ped=0,owner_epoch=0;
ScaleOwnership ownership;

template<class T> T Read(std::uintptr_t address) noexcept {
    T value;std::memcpy(&value,reinterpret_cast<void*>(address),sizeof(value));return value;
}
bool Focused() noexcept {
    DWORD pid=0;GetWindowThreadProcessId(GetForegroundWindow(),&pid);return pid==GetCurrentProcessId();
}
bool Guards() noexcept {
    // Supported EXE is fully hashed by InitializeASI. These exact stock operands
    // also prove the runtime scalar and its native CTimer/audio consumers.
    return rw_raster::Guard(0x561AD0,"\xd9\x05\x64\xcb\xb7\x00\xd8\x1d\x24\x86\x85\x00\xdf\xe0\xf6\xc4\x05\x7a\x03\xb0\x01\xc3\x32\xc0\xc3") &&
        rw_raster::Guard(0x561BE8,"\xd9\x05\x64\xcb\xb7\x00") &&
        rw_raster::Guard(0x506E10,"\x0f\xbe\x44\x24\x04\x89\x44\x24\x04\x51\xb9\xb8\xf8\xb5\x00\xdb\x44\x24\x08\xd8\x0d\xd4\xa7\x85\x00\xd9\x1c\x24\xe8\x0f\x27\xfd\xff\xc2\x04\x00") &&
        rw_raster::Guard(0x4D9540,"\x8b\x44\x24\x04\x89\x81\x14\x04\x00\x00\xc2\x04\x00") &&
        Read<float>(0x85A7D4)==1.f/64.f &&
        rw_raster::Guard(0x4EF440,"\xf6\x41\x56\x10\x75\x27\xe8\x85\x26\x07\x00\x84\xc0\x74\x1e\x0f\xb6\x05\x81\xf0\xb6\x00\x69\xc0\x38\x02\x00\x00\x66\x83\xb8\xa8\xf1\xb6\x00\x2e\x74\x07\xd9\x05\x6c\xba\x8c\x00\xc3\xd9\x05\x24\x86\x85\x00\xc3") &&
        rw_raster::Guard(0x4F0710,"\xd9\x05\x6c\xba\x8c\x00\xeb\x06\xd9\x05\x24\x86\x85\x00\xd8\x4c\x24\x18") &&
        rw_raster::Guard(0x4F096B,"\xd9\x05\x6c\xba\x8c\x00\xeb\x06\xd9\x05\x24\x86\x85\x00\xd8\x4c\x24\x18") &&
        rw_raster::Guard(0x4F0A12,"\xd9\x05\x6c\xba\x8c\x00\xeb\x06\xd9\x05\x24\x86\x85\x00\xd8\x4c\x24\x18");
}
bool SafeGuards() noexcept {
    __try { return Guards(); } __except(EXCEPTION_EXECUTE_HANDLER) { return false; }
}
struct NativeMemory {
    static std::uintptr_t Address(Scalar scalar) noexcept { return scalar==Scalar::Time?TimeScale:AudioPitch; }
    float Read(Scalar scalar) noexcept { return game_effects::Read<float>(Address(scalar)); }
    bool Exchange(Scalar scalar,float expected,float value) noexcept {
        long from=0,to=0;std::memcpy(&from,&expected,4);std::memcpy(&to,&value,4);
        return InterlockedCompareExchange(reinterpret_cast<volatile long*>(Address(scalar)),to,from)==from;
    }
} memory;
void EmergencyRestore() noexcept {
    __try { ownership.Release(memory); } __except(EXCEPTION_EXECUTE_HANDLER) { ++stats.faults; }
    control.Abandon();
}
bool Lease(const movement_hold::Stats& movement,std::uint64_t now) noexcept {
    return snapshot && movement.active && movement.trajectory && movement.interactive_enabled &&
        !movement.preparing && movement.source_tick && movement.source_mode<=1 &&
        Eligible(*snapshot,now,movement.request,movement.entry_f7_requests) &&
        !settings_menu::Open() && Focused() && !Read<unsigned char>(0xB7CB48) && !Read<unsigned char>(0xB7CB49);
}
void Publish(const movement_hold::Stats& movement,std::uint64_t now,bool lease) noexcept {
    const float volume=Read<float>(SfxVolume);
    if(std::isfinite(volume) && volume>=0 && volume<=1) stats.sfx_volume=volume;
    const auto packet=Make(*snapshot,++sequence,now,lease?movement.request:0,
        movement.entry_f7_requests,control,stats.sfx_volume);
    const long generation=static_cast<long>((sequence%0x3ffffffe+1)*2);
    InterlockedExchange(&shared->begin,generation-1);
    std::memcpy(&shared->packet,&packet,sizeof(packet));MemoryBarrier();
    InterlockedExchange(&shared->end,generation);InterlockedExchange(&shared->begin,generation);
    ++stats.published;stats.owned=control.Owned();stats.exiting=control.Exiting();
    stats.time_scale=control.Scale();stats.effect_amount=control.Amount();stats.events=control.EventSequence();
}
void Tick() noexcept {
    const auto now=GetTickCount64();const auto movement=movement_hold::GetStats();
    const bool lease=Lease(movement,now);
    bool effect_lease=lease && !movement.source_bailing;
    if(control.Owned() && (movement.request!=owner_request || snapshot->ped_address!=owner_ped ||
                          snapshot->epoch!=owner_epoch)) effect_lease=false;
    const auto weapon=weapon_control::GetState();const auto aim=camera_preview::GetAimState();
    const bool aiming=effect_lease && weapon.active && weapon.can_aim && weapon.request==movement.request &&
        weapon.ped==snapshot->ped_address && weapon.tick_ms<=now && now-weapon.tick_ms<=MaxAgeMs &&
        aim.active && aim.request==movement.request && aim.ped==snapshot->ped_address &&
        aim.tick_ms<=now && now-aim.tick_ms<=MaxAgeMs && (GetAsyncKeyState(VK_RBUTTON)&0x8000);
    const bool was_owned=control.Owned();
    const bool scalar_available=ownership.Held()?ownership.Matches(memory):
        (memory.Read(Scalar::Time)==1.f && memory.Read(Scalar::Pitch)==.5f);
    control.Step(presentation_time::NowUs(),effect_lease && scalar_available,aiming,
        (GetAsyncKeyState(VK_SHIFT)&0x8000)!=0,Read<float>(TimeScale));
    if(control.Owned()) {
        if(!ownership.Set(control.Scale(),memory)) control.Abandon();
    } else ownership.Release(memory);
    if(control.Owned() && !was_owned) {
        owner_request=movement.request;owner_ped=snapshot->ped_address;owner_epoch=snapshot->epoch;
    }
    last_tick_ms=now;Publish(movement,now,lease);
}

using StateFn=board_preview::StateFn;
bool Engine() noexcept {
    const auto engine=Read<std::uintptr_t>(0xC97B24);
    return engine>=0x10000 && engine<0x80000000 && !(engine&3) &&
        Read<std::uintptr_t>(engine+0x20)==0x7FE420 && Read<std::uintptr_t>(engine+0x24)==0x7FD810 &&
        Read<std::uintptr_t>(engine+0x30)==0x7FBB00 &&
        rw_raster::Guard(0x7FE420,"\x8b\x4c\x24\x04\x53\x33\xdb\x33\xc0\x49\x57\x83\xf9\x1d\x0f\x87") &&
        rw_raster::Guard(0x7FD810,"\x8b\x44\x24\x04\x48\x83\xf8\x1d\x77\x73\xff\x24\x85\x18\xda\x7f") &&
        rw_raster::Guard(0x7FBB00,"\x83\xec\x08\xa1\xc0\xbc\xc9\x00\x53\x55\x56\x57\x33\xff\x3b\xc7") &&
        rw_raster::Guard(0x734E90,"\xa1\x24\x7b\xc9\x00\xff\x60\x30");
}
struct DrawContext { float amount;bool drawn; };
struct Vertex { float x,y,z,rhw;std::uint32_t color;float u,v; };
static_assert(sizeof(Vertex)==28);
void Draw(void* opaque) {
    auto& context=*static_cast<DrawContext*>(opaque);
    const auto width=Read<std::uint32_t>(0xC17044),height=Read<std::uint32_t>(0xC17048);
    const float z=Read<float>(0xC80460),rhw=Read<float>(0xC80464);
    if(width<64 || height<64 || width>16384 || height>16384 || !std::isfinite(z) ||
       !std::isfinite(rhw) || rhw<=0) return;
    const auto set=reinterpret_cast<StateFn>(0x7FE420);
    constexpr int kinds[]{6,8,9,10,11,12,14,20,3,4,29,30,1};
    constexpr std::uintptr_t values[]{0,0,2,5,6,1,0,1,3,3,8,0,0};
    for(unsigned i=0;i<std::size(kinds);++i) if(!set(kinds[i],reinterpret_cast<void*>(values[i]))) return;
    // Untextured cool tint, maximum alpha 14/255. No render target, texture or
    // persistent post-effect resources; all modified RW states are scoped.
    const auto alpha=static_cast<std::uint32_t>(context.amount*14.f+.5f);
    const std::uint32_t color=(alpha<<24)|0x00304460;
    const float w=static_cast<float>(width),h=static_cast<float>(height);
    Vertex vertices[4]={{0,0,z,rhw,color,0,0},{w,0,z,rhw,color,0,0},
                        {w,h,z,rhw,color,0,0},{0,h,z,rhw,color,0,0}};
    context.drawn=reinterpret_cast<int(__cdecl*)(int,Vertex*,int)>(0x734E90)(5,vertices,4)!=0;
}
}

const char* Initialize(const std::wstring&,const Snapshot* state) {
    if(!SafeGuards()) return "Slow motion timer/audio operands differ";
    snapshot=state;
    wchar_t name[80];swprintf_s(name,L"Local\\THAW_GTA_EFFECTS_V1_%lu",GetCurrentProcessId());
    mapping=CreateFileMappingW(INVALID_HANDLE_VALUE,nullptr,PAGE_READWRITE,0,sizeof(SharedEffects),name);
    if(!mapping || GetLastError()==ERROR_ALREADY_EXISTS) return "Game effects mapping already exists or cannot be created";
    shared=static_cast<SharedEffects*>(MapViewOfFile(mapping,FILE_MAP_WRITE,0,0,sizeof(SharedEffects)));
    if(!shared) return "Cannot map game effects transport";
    std::memset(shared,0,sizeof(*shared));stats.enabled=true;return nullptr;
}
void AfterTick() noexcept {
    if(!stats.enabled) return;
    __try { Tick(); }
    __except(EXCEPTION_EXECUTE_HANDLER) {
        ++stats.faults;EmergencyRestore();stats.enabled=false;
        // An invalid/cleared transport makes the source scheduler return to1x.
        if(shared) { InterlockedExchange(&shared->begin,0);InterlockedExchange(&shared->end,0); }
    }
}
float PresentationScale(std::uint64_t request,std::uint32_t ped,std::uint32_t epoch) noexcept {
    if(!stats.enabled || !control.Owned() || request!=owner_request || ped!=owner_ped || epoch!=owner_epoch) return 1;
    __try {
        const auto now=GetTickCount64();
        const auto movement=movement_hold::GetStats();
        if(now<last_tick_ms || now-last_tick_ms>MaxAgeMs || movement.request!=owner_request ||
           snapshot->ped_address!=owner_ped || snapshot->epoch!=owner_epoch ||
           movement.source_bailing || !Lease(movement,now) || !ownership.Matches(memory)) return 1;
        return control.Scale();
    } __except(EXCEPTION_EXECUTE_HANDLER) { return 1; }
}
void Present() noexcept {
    if(!stats.enabled || !control.Owned()) return;
    __try {
        if(PresentationScale(owner_request,owner_ped,owner_epoch)==1.f && control.Scale()!=1.f) {
            EmergencyRestore();Publish(movement_hold::GetStats(),GetTickCount64(),false);return;
        }
        if(control.Amount()<=0 || !Engine()) return;
        DrawContext context{control.Amount(),false};
        const bool restored=hud_preview::DrawScoped(reinterpret_cast<StateFn>(0x7FE420),
            reinterpret_cast<StateFn>(0x7FD810),Draw,&context,stats.state_restores);
        if(!restored || !context.drawn) { ++stats.faults;EmergencyRestore();return; }
        ++stats.draws;
    } __except(EXCEPTION_EXECUTE_HANDLER) { ++stats.faults;EmergencyRestore(); }
}
Stats GetStats() noexcept {
    auto result=stats;result.owned=control.Owned();result.exiting=control.Exiting();
    result.time_scale=control.Scale();result.effect_amount=control.Amount();result.events=control.EventSequence();
    result.writes=ownership.Writes();result.restores=ownership.Restores();result.conflicts=ownership.Conflicts();
    return result;
}
}
