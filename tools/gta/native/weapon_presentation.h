#pragma once
#include "pose_preview_protocol.h"
namespace gta_thaw::weapon_presentation {
struct Frame {
    bool active=false,aiming=false,holster_board=false,left_hand=false;
    std::uint32_t ped=0,clump=0;
    std::uint64_t request=0;
    float weapon[16]{},board[16]{},origin[3]{},barrel[3]{};
};
struct Stats {
    std::uint64_t holstered=0,aimed=0,restored=0,rejected=0,faults=0;
    std::uint64_t pre_render_calls=0,fire_candidates=0,alignment_rejected=0,fire_forwarded=0;
    float last_gun_position[3]{},last_gun_forward[3]{},last_target_dot=0;
};
void ObservePreRender() noexcept;
bool Guards() noexcept;
// Canonical world matrices, still inside the reversible CJ render/PreRender scope.
bool Prepare(const pose_preview::Packet&,const float* anchor,float bones[32][16],Frame&) noexcept;
void RenderPed(void* ped,void(*original)(void*),const Frame&) noexcept;
void Fire(const Frame&) noexcept;
void Reset() noexcept;
Stats GetStats() noexcept;
}
