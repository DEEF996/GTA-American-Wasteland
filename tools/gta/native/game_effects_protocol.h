#pragma once
#include "bridge_protocol.h"
#include "game_effects_policy.h"

namespace gta_thaw::game_effects {
constexpr std::uint64_t MaxAgeMs=100;
enum Flag : std::uint32_t { LeaseActive=1,SlowMotionOwned=2,Exiting=4 };
#pragma pack(push,1)
struct Packet {
    char magic[8];
    std::uint32_t version,byte_size,gta_pid,flags;
    std::uint64_t process_creation_100ns,sequence,tick_ms,request;
    std::uint32_t ped_address,epoch,current_area,ped_area,entry_f7_requests,f7_requests;
    std::uint64_t event_sequence;
    float time_scale,effect_amount,sfx_volume;
    std::uint32_t event_kind;
};
#pragma pack(pop)
struct SharedEffects { volatile long begin;Packet packet;volatile long end; };
static_assert(sizeof(Packet)==104 && sizeof(SharedEffects)==112);
static_assert(offsetof(Packet,event_sequence)==80 && offsetof(Packet,time_scale)==88);
static_assert(offsetof(Packet,event_kind)==100 && offsetof(SharedEffects,end)==108);

inline bool Eligible(const Snapshot& s,std::uint64_t now,std::uint64_t request,std::uint32_t entry) noexcept {
    return request && s.ped_address && s.epoch && entry==s.f7_requests &&
        s.tick_ms<=now && now-s.tick_ms<=MaxAgeMs && ControlEligible(s) &&
        (s.status_flags&(Foreground|TrajectoryOwned|TrajectoryPreparing))==(Foreground|TrajectoryOwned);
}
inline Packet Make(const Snapshot& s,std::uint64_t sequence,std::uint64_t now,
                   std::uint64_t request,std::uint32_t entry,const Controller& control,float volume) noexcept {
    Packet p{};std::memcpy(p.magic,"GTAFX001",8);p.version=1;p.byte_size=sizeof(p);
    p.gta_pid=s.gta_pid;p.process_creation_100ns=s.process_creation_100ns;
    p.sequence=sequence;p.tick_ms=now;p.ped_address=s.ped_address;p.epoch=s.epoch;
    p.current_area=s.current_area;p.ped_area=s.ped_area;p.entry_f7_requests=entry;p.f7_requests=s.f7_requests;
    p.time_scale=1;p.sfx_volume=volume;p.event_sequence=control.EventSequence();
    p.event_kind=static_cast<std::uint32_t>(control.LastEvent());
    if(Eligible(s,now,request,entry)) {
        p.flags=LeaseActive;p.request=request;
        if(control.Owned()) {
            p.flags|=SlowMotionOwned;if(control.Exiting()) p.flags|=Exiting;
            p.time_scale=control.Scale();p.effect_amount=control.Amount();
        }
    }
    return p;
}
inline bool Valid(const Packet& p,std::uint32_t pid,std::uint64_t created,std::uint64_t now) noexcept {
    if(std::memcmp(p.magic,"GTAFX001",8) || p.version!=1 || p.byte_size!=sizeof(p) ||
       !pid || p.gta_pid!=pid || !created || p.process_creation_100ns!=created ||
       !p.sequence || p.tick_ms>now || now-p.tick_ms>MaxAgeMs ||
       (p.flags&~(LeaseActive|SlowMotionOwned|Exiting)) || p.current_area>255 || p.ped_area>255 ||
       !std::isfinite(p.sfx_volume) || p.sfx_volume<0 || p.sfx_volume>1 ||
       !std::isfinite(p.time_scale) || !std::isfinite(p.effect_amount) ||
       p.time_scale<TargetScale-1e-6f || p.time_scale>1 || p.effect_amount<0 || p.effect_amount>1 ||
       p.event_kind>3 || ((!p.event_sequence)!=(p.event_kind==0))) return false;
    if(p.flags&LeaseActive) {
        if(!p.request || !p.ped_address || !p.epoch || p.current_area!=p.ped_area ||
           p.entry_f7_requests!=p.f7_requests) return false;
    } else if(p.request || (p.flags&(SlowMotionOwned|Exiting))) return false;
    if(p.flags&SlowMotionOwned)
        return p.event_sequence && (p.event_kind==1 || p.event_kind==2) &&
            bool(p.flags&Exiting)==(p.event_kind==2) &&
            std::fabs(p.time_scale-(1.f-(1.f-TargetScale)*p.effect_amount))<1e-6f;
    return !(p.flags&Exiting) && p.time_scale==1.f && p.effect_amount==0.f;
}
}
