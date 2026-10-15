#pragma once
#include "movement_hold_protocol.h"

namespace gta_thaw::movement_hold {
// One-second planar developer proof. This is not a gameplay/midair transport.
constexpr std::uint32_t CommandMaxDurationMs=1000;
constexpr float CommandMaxDistance=1.f, CommandMaxRawSpeed=.02f;
#pragma pack(push, 1)
struct CommandPacket {
    Packet lease;
    float position[3], heading, velocity[3];
    std::uint32_t reserved;
};
#pragma pack(pop)
struct SharedCommand { volatile long begin; CommandPacket packet; volatile long end; };
static_assert(sizeof(CommandPacket)==104 && sizeof(SharedCommand)==112);
static_assert(offsetof(CommandPacket,position)==72 && offsetof(SharedCommand,end)==108);

inline Packet HoldEnvelope(const CommandPacket& command) noexcept {
    auto p=command.lease;
    std::memcpy(p.magic,"GTAHOLD1",8); p.byte_size=sizeof(Packet);
    return p;
}
inline float HeadingDifference(float a,float b) noexcept {
    return std::remainder(a-b,6.2831853071795864769f);
}
inline float DistanceSquared(const float* a,const float* b) noexcept {
    float value=0;
    for(int i=0;i<3;++i) { const float d=a[i]-b[i]; value+=d*d; }
    return value;
}
inline bool ValidCommand(const CommandPacket& p,const Snapshot& s,std::uint64_t now) noexcept {
    if(std::memcmp(p.lease.magic,"GTAMOVE1",8) || p.lease.byte_size!=sizeof(p) || p.reserved ||
       p.lease.duration_ms>CommandMaxDurationMs || !ValidPacket(HoldEnvelope(p),s,now) ||
       !std::isfinite(p.heading) || std::fabs(p.heading)>32.f) return false;
    float speed2=0;
    for(int i=0;i<3;++i) {
        if(!std::isfinite(p.position[i]) || std::fabs(p.position[i])>100000 ||
           !std::isfinite(p.velocity[i])) return false;
        speed2+=p.velocity[i]*p.velocity[i];
    }
    return speed2<=CommandMaxRawSpeed*CommandMaxRawSpeed+1.e-10f && p.velocity[2]==0;
}
inline bool CommandInBounds(const CommandPacket& p,const float* entry,float entry_heading,
                            const float* previous,float previous_heading,std::uint64_t previous_ms,
                            bool first) noexcept {
    if(DistanceSquared(p.position,entry)>CommandMaxDistance*CommandMaxDistance ||
       p.position[2]!=entry[2] ||
       std::fabs(HeadingDifference(p.heading,entry_heading))>1.57079632679f) return false;
    // Do not jump to a stale initial observation. Subsequent steps have a 2 cm allowance
    // for sampling/float rounding and at most 1 m/s or 90 degrees/s of commanded change.
    if(p.lease.tick_ms<previous_ms) return false;
    const float seconds=static_cast<float>(p.lease.tick_ms-previous_ms)*.001f;
    const float distance=first?.02f:.02f+seconds;
    const float angle=first?.02f:.02f+seconds*1.57079632679f;
    return DistanceSquared(p.position,previous)<=distance*distance &&
        std::fabs(HeadingDifference(p.heading,previous_heading))<=angle;
}
inline bool ReadCommandHeartbeat(ReadResult result,const CommandPacket& incoming,CommandPacket& cached,
                                 const Snapshot& state,const Packet& initial,std::uint64_t started,
                                 std::uint64_t previous_sequence,std::uint64_t now) noexcept {
    if(result==ReadResult::Cleared) return false;
    const auto& selected=result==ReadResult::Committed?incoming:cached;
    if(!ValidCommand(selected,state,now) ||
       !Continues(HoldEnvelope(selected),initial,started,previous_sequence,now) ||
       selected.lease.tick_ms<cached.lease.tick_ms ||
       (selected.lease.sequence==cached.lease.sequence && std::memcmp(&selected,&cached,sizeof(cached))))
        return false;
    if(result==ReadResult::Committed) cached=incoming;
    return true;
}
}
