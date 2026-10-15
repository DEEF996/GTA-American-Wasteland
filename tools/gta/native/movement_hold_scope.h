#pragma once
#include <windows.h>
#include "movement_trajectory_protocol.h"

namespace gta_thaw::movement_hold {
inline bool MovementHealthEligible(float health,float entry_health,bool continuous) noexcept {
    return std::isfinite(health) && health>0 && (continuous || health>=entry_health);
}
constexpr std::uintptr_t BeHitVtable=0x86D844;
inline bool KnownContinuousHitReaction(bool continuous_owned,int slot,std::uintptr_t vtable,
                                      std::uintptr_t parent,unsigned char finished,unsigned char added,
                                      std::uint32_t direction,bool code_matches) noexcept {
    return continuous_owned && slot==0 && vtable==BeHitVtable && !parent &&
        finished<=1 && added<=1 && direction<=3 && code_matches;
}
struct ControllerMovementMemory {
    unsigned char* matrix;
    float* headings;
    float* velocity;
    float* angular_velocity;
    std::uint32_t* contact_flags;
};
inline bool ControllerMovementScoped(const ControllerMovementMemory& memory,void* context,
                                     void(*body)(void*),bool(*may_restore)(void*),
                                     std::uint64_t& restores) {
    float root[12],headings[2],velocity[3],angular_velocity[3];
    for(int row=0;row<4;++row) std::memcpy(root+row*3,memory.matrix+row*16,12);
    std::memcpy(headings,memory.headings,sizeof(headings));
    std::memcpy(velocity,memory.velocity,sizeof(velocity));
    std::memcpy(angular_velocity,memory.angular_velocity,sizeof(angular_velocity));
    const auto contact=*memory.contact_flags&ContactBits;
    body(context);
    if(!may_restore(context)) return false;
    // THAW owns these movement fields. Health, tasks and unrelated flags remain native.
    for(int row=0;row<4;++row) std::memcpy(memory.matrix+row*16,root+row*3,12);
    std::memcpy(memory.headings,headings,sizeof(headings));
    std::memcpy(memory.velocity,velocity,sizeof(velocity));
    std::memcpy(memory.angular_velocity,angular_velocity,sizeof(angular_velocity));
    *memory.contact_flags=(*memory.contact_flags&~ContactBits)|contact;
    ++restores;
    return true;
}
constexpr std::size_t PadInputBytes=96;
inline bool RestorePadInput(unsigned char* pad,const unsigned char* saved) noexcept {
    const unsigned char neutral[PadInputBytes]{};
    if(std::memcmp(pad,neutral,PadInputBytes)) return false;
    std::memcpy(pad,saved,PadInputBytes);
    return true;
}
inline void PadInputScoped(unsigned char* pad,void* context,void(*body)(void*),
                           std::uint64_t& sets,std::uint64_t& restores,std::uint64_t& conflicts) {
    unsigned char saved[PadInputBytes];
    std::memcpy(saved,pad,PadInputBytes);
    std::memset(pad,0,PadInputBytes);
    ++sets;
    __try { body(context); }
    __finally {
        // The native game thread polls outside this scope. Preserve any unexpected
        // newer writer in its entirety instead of replacing it with stale input.
        if(RestorePadInput(pad,saved)) ++restores;
        else ++conflicts;
    }
}
// Restore only the bit owned by this scope; unrelated native flag changes survive.
inline void RestoreCollisionBit(std::uint32_t* flags, std::uint32_t previous,std::uint32_t mask=CollisionBypass) noexcept {
    *flags = (*flags & ~mask) | (previous & mask);
}
inline void CollisionScoped(std::uint32_t* flags, void* context, void(*body)(void*),
                            bool(*still_same)(void*), std::uint64_t& sets, std::uint64_t& restores,
                            std::uint32_t mask=CollisionBypass) {
    const auto previous = *flags;
    *flags |= mask;
    ++sets;
    __try { body(context); }
    __finally {
        // Never write a deleted/replaced actor merely to restore an old flag.
        if (still_same(context)) { RestoreCollisionBit(flags,previous,mask); ++restores; }
    }
}
}
