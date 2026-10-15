#pragma once
#include "movement_trajectory_protocol.h"

namespace gta_thaw::movement_hold {
struct AirTaskNode { std::uintptr_t address,vtable,parent,child; };
inline bool KnownJumpChain(const AirTaskNode (&nodes)[3],std::uint32_t glide_flags,
                          std::uintptr_t climb_entity) noexcept {
    constexpr std::uintptr_t tables[]={0x870570,0x8704AC,0x8704D8};
    for(unsigned i=0;i<3;++i) {
        const auto& n=nodes[i];
        if(n.address<0x10000 || n.address>=0x80000000 || (n.address&3) || n.vtable!=tables[i] ||
           n.parent!=(i?nodes[i-1].address:0) || n.child!=(i<2?nodes[i+1].address:0)) return false;
        for(unsigned j=0;j<i;++j) if(n.address==nodes[j].address) return false;
    }
    return (glide_flags&7)==1 && !climb_entity;
}
inline bool RestoreCapturedAirContact(std::uint32_t current,std::uint32_t captured,std::uint32_t& result) noexcept {
    // The pinned original abort clears only air/landing. Do not replace a new
    // ground/landing result, nor any flag outside the already-owned contact bits.
    if((captured&ContactBits)!=0x200 || ((current&ContactBits)!=0 && (current&ContactBits)!=0x200)) return false;
    result=(current&~ContactBits)|(captured&ContactBits);return true;
}
}
