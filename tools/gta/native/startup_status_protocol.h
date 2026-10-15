#pragma once
#include "bridge_protocol.h"

namespace gta_thaw::startup_status {
enum Kind : std::uint32_t { Hidden=0, Preparing=1, Ready=2, StartingGameplay=3 };
constexpr std::uint64_t FreshnessMs=1500,ReadyDisplayMs=5000;
#pragma pack(push,1)
struct Packet {
    char magic[8];
    std::uint32_t version,byte_size,pid,kind;
    std::uint64_t created,request,sequence,tick_ms,since_ms;
};
#pragma pack(pop)
struct SharedStatus { volatile long begin;Packet packet;volatile long end; };
static_assert(sizeof(Packet)==64 && sizeof(SharedStatus)==72);
static_assert(offsetof(Packet,created)==24 && offsetof(Packet,since_ms)==56);
static_assert(offsetof(SharedStatus,end)==68);
inline bool ValidPacket(const Packet& p,const Snapshot& s,std::uint64_t now) noexcept {
    return !std::memcmp(p.magic,"GTASTAT1",8) && p.version==1 && p.byte_size==sizeof(p) &&
        p.pid && p.pid==s.gta_pid && p.created && p.created==s.process_creation_100ns &&
        p.request && p.sequence && p.kind<=StartingGameplay && p.tick_ms<=now && now-p.tick_ms<=FreshnessMs &&
        p.since_ms<=p.tick_ms && (p.kind==Hidden || p.since_ms);
}
inline bool Continues(const Packet& p,const Packet& old) noexcept {
    if(p.request!=old.request) return true;
    return p.sequence>=old.sequence && p.tick_ms>=old.tick_ms && p.since_ms>=old.since_ms &&
        (p.kind!=old.kind || p.since_ms==old.since_ms) &&
        (p.sequence!=old.sequence || !std::memcmp(&p,&old,sizeof(p)));
}
inline bool Visible(const Packet& p,std::uint64_t now) noexcept {
    return p.kind==Preparing || p.kind==StartingGameplay ||
        (p.kind==Ready && now>=p.since_ms && now-p.since_ms<ReadyDisplayMs);
}
inline const char* Text(std::uint32_t kind) noexcept {
    return kind==Preparing ? "Preparing map for grinding..." :
        kind==StartingGameplay ? "Starting THAW gameplay..." :
        kind==Ready ? "Map ready - press J to enter THAW mode." : "";
}
struct Layout { float left,top,right,bottom; };
inline Layout Place(float width,float height,float texture_width,float texture_height) noexcept {
    // Logical720p coordinates: below GTA's clock/weapon/health/money block.
    const float vertical_scale=height/720.f,right=width-8.f*vertical_scale;
    const float available=width-16.f*vertical_scale;
    const float scale=texture_width*vertical_scale>available?available/texture_width:vertical_scale;
    return {right-texture_width*scale,150.f*vertical_scale,right,150.f*vertical_scale+texture_height*scale};
}
}
