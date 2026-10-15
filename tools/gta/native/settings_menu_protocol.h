#pragma once
#include "bridge_protocol.h"
namespace gta_thaw::settings_menu {
constexpr unsigned Lines=24,LineBytes=100,Events=64;
constexpr std::uint64_t FreshnessMs=1500;
#pragma pack(push,1)
struct Packet {
    char magic[8];std::uint32_t version,byte_size,pid,close;
    std::uint64_t created,request,sequence,tick_ms,generation;
    std::uint32_t count,reserved;
    char line[Lines][LineBytes];
};
struct Input {
    char magic[8];std::uint32_t version,byte_size,pid,open;
    std::uint64_t created,sequence,tick_ms,generation;
    std::uint32_t event_count,reserved,event[Events];
};
#pragma pack(pop)
struct SharedMenu { volatile long begin;Packet packet;volatile long end; };
struct SharedInput { volatile long begin;Input packet;volatile long end; };
static_assert(sizeof(Packet)==2472 && sizeof(Input)==320);
static_assert(sizeof(SharedMenu)==2480 && sizeof(SharedInput)==328);
inline bool Valid(const Packet& p,const Snapshot& s,std::uint64_t now) noexcept {
    if(std::memcmp(p.magic,"GTAMENU1",8) || p.version!=1 || p.byte_size!=sizeof(p) ||
       !p.pid || p.pid!=s.gta_pid || !p.created || p.created!=s.process_creation_100ns ||
       !p.request || !p.sequence || p.close>1 || p.reserved || p.count!=Lines ||
       p.tick_ms>now || now-p.tick_ms>FreshnessMs) return false;
    for(const auto& line:p.line) {
        if(!std::memchr(line,0,LineBytes)) return false;
        for(unsigned i=0;i<LineBytes && line[i];++i)
            if(static_cast<unsigned char>(line[i])<32 || static_cast<unsigned char>(line[i])>126) return false;
    }
    return true;
}
inline bool Continues(const Packet& p,const Packet& old) noexcept {
    return p.request!=old.request || (p.sequence>=old.sequence && p.tick_ms>=old.tick_ms &&
        (p.sequence!=old.sequence || !std::memcmp(&p,&old,sizeof(p))));
}
}
