#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include "board_attack.h"
#include "board_attack_protocol.h"
#include "movement_hold.h"
#include "settings_menu.h"

namespace gta_thaw::board_attack {
namespace {
constexpr std::uintptr_t PedPool=0xB74490,LineClear=0x56A490,DamageEvent=0x73A530;
constexpr int Bat=5,Torso=3,Damage=25;
const Snapshot* state=nullptr;
HANDLE mapping=nullptr;
SharedAttack* shared=nullptr;
Stats stats{};
Edges edges;

template<class T> T Read(std::uintptr_t address) noexcept {
    T value; std::memcpy(&value,reinterpret_cast<void*>(address),sizeof(value)); return value;
}
bool Pointer(std::uintptr_t value) noexcept { return value>=0x10000 && value<0x80000000 && !(value&3); }
bool Position(std::uintptr_t ped,float* xyz) noexcept {
    const auto matrix=Read<std::uintptr_t>(ped+0x14);
    if(matrix && !Pointer(matrix)) return false;
    std::memcpy(xyz,reinterpret_cast<void*>(matrix?matrix+0x30:ped+4),12);
    for(unsigned i=0;i<3;++i) if(!std::isfinite(xyz[i]) || std::fabs(xyz[i])>100000.f) return false;
    return true;
}
bool LivePed(std::uintptr_t ped,const Snapshot& s) noexcept {
    const float health=Read<float>(ped+0x540);
    return ped!=s.ped_address && (Read<unsigned char>(ped+0x36)&7)==3 &&
        Read<unsigned char>(ped+0x2F)==s.ped_area && !(Read<unsigned short>(ped+0x46C)&0x100) &&
        std::isfinite(health) && health>0 && health<=100000 && Pointer(Read<std::uintptr_t>(ped+0x47C));
}

bool HitNearest(const Snapshot& s) noexcept {
    const auto pool=Read<std::uintptr_t>(PedPool);
    if(!Pointer(pool)) return false;
    const auto objects=Read<std::uintptr_t>(pool),flags=Read<std::uintptr_t>(pool+4);
    const auto count=Read<std::int32_t>(pool+8);
    std::uint32_t own_index=0;
    if(!Pointer(flags) || !PoolIndex(s.ped_address,objects,count,own_index) ||
       (Read<unsigned char>(flags+own_index)&0x80)) return false;
    float origin[3]; if(!Position(s.ped_address,origin)) return false;
    std::uintptr_t victim=0; unsigned char victim_slot=0;
    float nearest=1.6f*1.6f+1.f;
    for(int i=0;i<count;++i) {
        const auto slot=Read<unsigned char>(flags+i);
        if(slot&0x80) continue;
        const auto ped=objects+0x7C4u*i;
        if(!LivePed(ped,s)) continue;
        float target[3],distance2=0;
        if(!Position(ped,target) || !InReach(origin,s.heading,target,distance2) || distance2>=nearest) continue;
        float from[3]{origin[0],origin[1],origin[2]+.35f};
        target[2]+=.35f;
        // Exclude peds from this obstruction test; the candidate is already
        // bounded to a short melee arc. Walls, cars and objects still block it.
        const bool clear=reinterpret_cast<bool(__cdecl*)(const float*,const float*,bool,bool,bool,bool,bool,bool,bool)>(LineClear)(
            from,target,true,true,false,true,true,false,false);
        if(clear) { victim=ped; victim_slot=slot; nearest=distance2; }
    }
    if(!victim) return false;
    std::uint32_t index=0;
    if(Read<std::uintptr_t>(PedPool)!=pool || Read<std::uintptr_t>(pool)!=objects ||
       Read<std::uintptr_t>(pool+4)!=flags || Read<std::int32_t>(pool+8)!=count ||
       !PoolIndex(victim,objects,count,index) || Read<unsigned char>(flags+index)!=victim_slot ||
       !LivePed(victim,s)) return false;
    // Native damage processing owns armour, health, hit reactions and crime.
    // No inventory swap, victim pointer retention or direct health writes.
    const bool accepted=reinterpret_cast<bool(__cdecl*)(void*,void*,int,int,int,unsigned char)>(DamageEvent)(
        reinterpret_cast<void*>(victim),reinterpret_cast<void*>(s.ped_address),Bat,Damage,Torso,0);
    if(accepted) stats.target=static_cast<std::uint32_t>(victim);
    return accepted;
}

bool Guards() noexcept {
    constexpr unsigned char damage[]{0x6A,0xFF,0x68,0x10,0x8E,0x84,0x00,0x64,0xA1,0x00,0x00,0x00,0x00,0x50,
        0x64,0x89,0x25,0x00,0x00,0x00,0x00,0x83,0xEC,0x5C};
    constexpr unsigned char line[]{0x83,0xEC,0x5C,0x66,0x81,0x3D,0x78,0xCD,0xB7,0x00,0xFF,0xFF,0x73,0x09,
        0x66,0xFF,0x05,0x78,0xCD,0xB7,0x00};
    // Actual CPools::GetPed instructions pin the pool address and 0x7C4 stride.
    constexpr unsigned char pool[]{0x8B,0x4C,0x24,0x04,0x8B,0x15,0x90,0x44,0xB7,0x00,0x53,0x56,0x8B,0x72,0x04,
        0x8B,0xC1,0xC1,0xF8,0x08,0x8A,0x1C,0x30,0x5E,0x3A,0xD9,0x5B,0x75,0x0B,0x8B,0x0A,
        0x69,0xC0,0xC4,0x07,0x00,0x00,0x03,0xC1,0xC3};
    return !std::memcmp(reinterpret_cast<void*>(DamageEvent),damage,sizeof(damage)) &&
        !std::memcmp(reinterpret_cast<void*>(LineClear),line,sizeof(line)) &&
        !std::memcmp(reinterpret_cast<void*>(0x54FF90),pool,sizeof(pool));
}
}

const char* Initialize(const std::wstring& directory,const Snapshot* snapshot) {
    // Board attacks require the same explicitly enabled interactive gameplay.
    if(GetPrivateProfileIntW(L"development",L"MovementInteractivePreview",0,(directory+L"\\adapter.ini").c_str())!=1) return nullptr;
    if(!Guards()) return "Board attack native damage, collision or ped-pool guards differ";
    state=snapshot;
    wchar_t name[80]; swprintf_s(name,L"Local\\THAW_GTA_ATTACK_V1_%lu",GetCurrentProcessId());
    mapping=CreateFileMappingW(INVALID_HANDLE_VALUE,nullptr,PAGE_READWRITE,0,sizeof(SharedAttack),name);
    if(!mapping || GetLastError()==ERROR_ALREADY_EXISTS) return "Board attack mapping already exists or cannot be created";
    shared=static_cast<SharedAttack*>(MapViewOfFile(mapping,FILE_MAP_WRITE,0,0,sizeof(SharedAttack)));
    if(!shared) return "Cannot map board attack transport";
    std::memset(shared,0,sizeof(*shared)); stats.enabled=true;
    return nullptr;
}

void AfterTick() noexcept {
    if(!shared || stats.faults || settings_menu::Open()) return;
    __try {
        const auto movement=movement_hold::GetStats();
        if(!movement.active || !movement.trajectory || !movement.interactive_enabled) return;
        const auto begin=InterlockedCompareExchange(&shared->begin,0,0);
        if(!begin || (begin&1)) return;
        Packet p; std::memcpy(&p,&shared->packet,sizeof(p)); MemoryBarrier();
        if(begin!=InterlockedCompareExchange(&shared->end,0,0) ||
           begin!=InterlockedCompareExchange(&shared->begin,0,0)) return;
        const auto now=GetTickCount64();
        // A newer attack publication can arrive before its MOVE sample commits.
        if(p.request==movement.request && (p.source_tick>movement.source_tick || p.source_time_us>movement.source_time_us)) return;
        if(!ValidPacket(p,*state,now,movement.request,movement.source_tick,movement.source_time_us)) { ++stats.rejected; return; }
        if(!edges.Consume(p)) return;
        ++stats.swings; stats.target=0;
        if(HitNearest(*state)) ++stats.hits; else ++stats.misses;
    } __except(EXCEPTION_EXECUTE_HANDLER) { ++stats.faults; }
}
Stats GetStats() noexcept { return stats; }
}
