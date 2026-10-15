#pragma once
#include "bridge_protocol.h"

namespace gta_thaw::aim_input {
constexpr std::uint64_t MaxAgeMs=100;
enum Flag : std::uint32_t { Active=1 };
#pragma pack(push,1)
struct Packet {
    char magic[8];
    std::uint32_t version,byte_size,gta_pid,flags;
    std::uint64_t process_creation_100ns,sequence,tick_ms,request;
    std::uint32_t ped_address,epoch,current_area,ped_area,entry_f7_requests,f7_requests;
    float forward[3]; // Unit GTA world direction; horizontal projection defines walking aim.
    std::uint32_t reserved;
};
#pragma pack(pop)
struct SharedAim { volatile long begin;Packet packet;volatile long end; };
static_assert(sizeof(Packet)==96 && sizeof(SharedAim)==104);
static_assert(offsetof(Packet,request)==48 && offsetof(Packet,ped_address)==56);
static_assert(offsetof(Packet,forward)==80 && offsetof(SharedAim,end)==100);

inline bool Direction(const float* forward) noexcept {
    float length=0;
    for(unsigned i=0;i<3;++i) {
        if(!std::isfinite(forward[i])) return false;
        length+=forward[i]*forward[i];
    }
    const float horizontal=forward[0]*forward[0]+forward[1]*forward[1];
    return std::isfinite(length) && std::fabs(length-1.f)<=.005f && horizontal>1e-6f;
}

inline Packet Make(const Snapshot& state,std::uint64_t sequence,std::uint64_t now_ms,
                   std::uint64_t request,std::uint32_t entry_toggle,const float* forward) noexcept {
    Packet p{};
    std::memcpy(p.magic,"GTAAIM01",8);
    p.version=1;p.byte_size=sizeof(p);p.gta_pid=state.gta_pid;
    p.process_creation_100ns=state.process_creation_100ns;p.sequence=sequence;p.tick_ms=now_ms;
    p.ped_address=state.ped_address;p.epoch=state.epoch;
    p.current_area=state.current_area;p.ped_area=state.ped_area;
    p.entry_f7_requests=entry_toggle;p.f7_requests=state.f7_requests;
    if(forward && request && entry_toggle==state.f7_requests && ControlEligible(state) &&
       (state.status_flags&(Foreground|TrajectoryOwned|TrajectoryPreparing))==(Foreground|TrajectoryOwned) &&
       Direction(forward)) {
        p.flags=Active;p.request=request;std::memcpy(p.forward,forward,sizeof(p.forward));
    }
    return p;
}

inline bool Valid(const Packet& p,std::uint32_t pid,std::uint64_t created,std::uint64_t now) noexcept {
    if(std::memcmp(p.magic,"GTAAIM01",8) || p.version!=1 || p.byte_size!=sizeof(p) ||
       !pid || p.gta_pid!=pid || !created || p.process_creation_100ns!=created ||
       !p.sequence || p.tick_ms>now || now-p.tick_ms>MaxAgeMs || p.flags&~Active ||
       p.reserved || p.current_area>255 || p.ped_area>255) return false;
    if(p.flags&Active) return p.request && p.ped_address && p.epoch &&
        p.current_area==p.ped_area && p.entry_f7_requests==p.f7_requests && Direction(p.forward);
    return !p.request && p.forward[0]==0.f && p.forward[1]==0.f && p.forward[2]==0.f;
}
}
