#pragma once
#include "bridge_protocol.h"

namespace gta_thaw::board_attack {
#pragma pack(push,1)
struct Packet {
    char magic[8];
    std::uint32_t version,byte_size,pid,epoch;
    std::uint64_t created,request,sequence,tick_ms;
    std::uint32_t ped,area,ped_area,reserved;
    std::uint64_t source_tick,source_time_us,attack_id;
    std::uint32_t whacking,walking;
};
#pragma pack(pop)
struct SharedAttack { volatile long begin; Packet packet; volatile long end; };
static_assert(sizeof(Packet)==104 && sizeof(SharedAttack)==112);
static_assert(offsetof(Packet,attack_id)==88 && offsetof(SharedAttack,end)==108);

inline bool ValidPacket(const Packet& p,const Snapshot& s,std::uint64_t now,
                        std::uint64_t request,std::uint64_t committed_tick,std::uint64_t committed_us) noexcept {
    return !std::memcmp(p.magic,"GTAHIT01",8) && p.version==1 && p.byte_size==sizeof(p) &&
        p.pid==s.gta_pid && p.created==s.process_creation_100ns && p.epoch==s.epoch &&
        p.ped==s.ped_address && p.area==s.current_area && p.ped_area==s.ped_area &&
        p.request && p.request==request && p.sequence && !p.reserved &&
        p.tick_ms<=now && now-p.tick_ms<=100 && s.tick_ms<=now && now-s.tick_ms<=100 &&
        p.source_tick && p.source_time_us && p.source_tick<=committed_tick && p.source_time_us<=committed_us &&
        committed_us-p.source_time_us<=100000 && p.whacking<=1 && p.walking<=1 &&
        ValidSnapshot(s,p.pid,p.created,now) && ControlEligible(s) && (s.status_flags&Foreground);
}

struct Edges {
    std::uint64_t request=0,attack_id=0,sequence=0,tick=0,micros=0;
    bool Consume(const Packet& p) noexcept {
        if(request!=p.request) { request=p.request; attack_id=sequence=tick=micros=0; }
        if(p.sequence<sequence || p.source_tick<tick || p.source_time_us<micros || p.attack_id<attack_id)
            return false;
        const bool fresh=p.attack_id>attack_id && p.source_tick>tick && p.source_time_us>micros;
        sequence=p.sequence; tick=p.source_tick; micros=p.source_time_us; attack_id=p.attack_id;
        return fresh && p.whacking && p.walking;
    }
};

inline bool InReach(const float* origin,float heading,const float* target,float& distance2) noexcept {
    if(!std::isfinite(heading)) return false;
    float d[3];
    for(unsigned i=0;i<3;++i) {
        if(!std::isfinite(origin[i]) || !std::isfinite(target[i])) return false;
        d[i]=target[i]-origin[i];
    }
    distance2=d[0]*d[0]+d[1]*d[1];
    const auto forward=-std::sin(heading)*d[0]+std::cos(heading)*d[1];
    return distance2>=.04f && distance2<=1.6f*1.6f && std::fabs(d[2])<=.8f &&
        forward>=.25f*std::sqrt(distance2);
}

inline bool PoolIndex(std::uintptr_t object,std::uintptr_t base,std::int32_t count,std::uint32_t& index) noexcept {
    constexpr std::uintptr_t stride=0x7C4;
    if(base<0x10000 || base>=0x80000000 || count<=0 || count>2048 ||
       base+stride*count>=0x80000000 || object<base || object>=base+stride*count || (object-base)%stride)
        return false;
    index=static_cast<std::uint32_t>((object-base)/stride);
    return true;
}
}
