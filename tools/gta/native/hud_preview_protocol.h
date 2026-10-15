#pragma once
#include "bridge_protocol.h"

namespace gta_thaw::hud_preview {
constexpr std::uint32_t MaxQuads=1024,MaxTextures=64,MaxAtlasBytes=32*1024*1024;
#pragma pack(push,1)
struct Quad { std::uint32_t texture,color; float vertex[4][4]; };
struct Packet {
    char magic[8];
    std::uint32_t version,byte_size,pid,epoch;
    std::uint64_t created,request,sequence,tick_ms;
    std::uint32_t ped,area,ped_area,reserved;
    std::uint64_t source_tick,source_time_us;
    unsigned char atlas_hash[32];
    float canvas[2];
    std::uint32_t count;
    Quad quad[MaxQuads];
};
struct Texture { std::uint32_t width,height,bytes; };
#pragma pack(pop)
struct SharedHud { volatile long begin; Packet packet; volatile long end; };
static_assert(sizeof(Quad)==72 && sizeof(Packet)==73860 && sizeof(SharedHud)==73868);
static_assert(offsetof(Packet,quad)==132 && offsetof(SharedHud,end)==73864);
inline bool ValidQuad(const Quad& q,std::uint32_t textures=MaxTextures) noexcept {
    if(q.texture>=textures) return false;
    for(auto& v:q.vertex) {
        for(float x:v) if(!std::isfinite(x)) return false;
        if(std::fabs(v[0])>4096.f || std::fabs(v[1])>4096.f || v[2]<0 || v[2]>1 || v[3]<0 || v[3]>1) return false;
    }
    return true;
}
inline bool ValidPacket(const Packet& p,const Snapshot& s,std::uint64_t now) noexcept {
    const unsigned char zero[32]{};
    if(std::memcmp(p.magic,"GTAHUD01",8) || p.version!=1 || p.byte_size!=sizeof(p) ||
       p.pid!=s.gta_pid || p.created!=s.process_creation_100ns || !p.request || !p.sequence || p.reserved ||
       p.tick_ms>now || now-p.tick_ms>100 || s.tick_ms>now || now-s.tick_ms>100 ||
       !ValidSnapshot(s,p.pid,p.created,now) || !ControlEligible(s) || !(s.status_flags&Foreground) ||
       p.epoch!=s.epoch || p.ped!=s.ped_address || p.area!=s.current_area || p.ped_area!=s.ped_area ||
       (p.source_tick==0)!=(p.source_time_us==0) ||
       !std::memcmp(p.atlas_hash,zero,32) || p.canvas[0]!=640.f || p.canvas[1]!=448.f || p.count>MaxQuads) return false;
    for(std::uint32_t i=0;i<p.count;++i) if(!ValidQuad(p.quad[i])) return false;
    const unsigned char* padding=reinterpret_cast<const unsigned char*>(p.quad+p.count);
    for(std::size_t i=0;i<(MaxQuads-p.count)*sizeof(Quad);++i) if(padding[i]) return false;
    return true;
}
inline bool SameSession(const Packet& a,const Packet& b) noexcept {
    return a.pid==b.pid && a.created==b.created && a.request==b.request && a.epoch==b.epoch &&
        a.ped==b.ped && a.area==b.area && a.ped_area==b.ped_area;
}
inline bool Continues(const Packet& p,const Packet& old) noexcept {
    return SameSession(p,old) && p.sequence>=old.sequence && p.tick_ms>=old.tick_ms &&
        p.source_tick>=old.source_tick && p.source_time_us>=old.source_time_us &&
        !std::memcmp(p.atlas_hash,old.atlas_hash,32) &&
        (p.sequence!=old.sequence || !std::memcmp(&p,&old,sizeof(p))) &&
        (p.source_tick!=old.source_tick || !std::memcmp(&p.source_tick,&old.source_tick,
            sizeof(p)-offsetof(Packet,source_tick))) &&
        (p.source_tick==old.source_tick || p.source_time_us>old.source_time_us);
}
inline bool ValidBundle(const unsigned char* bytes,std::uint32_t size,std::uint32_t& count) noexcept {
    if(size<16 || size>MaxAtlasBytes || std::memcmp(bytes,"GTAHUDTX",8)) return false;
    std::uint32_t version=0;std::memcpy(&version,bytes+8,4);std::memcpy(&count,bytes+12,4);
    if(version!=1 || !count || count>MaxTextures) return false;
    std::uint32_t offset=16;
    for(std::uint32_t i=0;i<count;++i) {
        if(sizeof(Texture)>size-offset) return false;
        Texture t;std::memcpy(&t,bytes+offset,sizeof(t));offset+=sizeof(t);
        if(!t.width || t.width>1024 || !t.height || t.height>1024 ||
           t.bytes!=t.width*t.height*4 || t.bytes>size-offset) return false;
        offset+=t.bytes;
    }
    return offset==size;
}
// THAW's neutral per-vertex gain is128; RW uses255. Retain all source channel
// proportions/fades, saturating overbright native values at the RW boundary.
inline std::uint32_t RwColor(std::uint32_t source) noexcept {
    std::uint32_t result=0;
    for(unsigned shift=0;shift<32;shift+=8) {
        const auto value=(source>>shift)&255;
        result|=(value>=128?255:(value*255+64)/128)<<shift;
    }
    return result;
}
}
