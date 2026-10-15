#pragma once
#include <cstdint>

namespace gta_thaw::movement_hold {
// Supported GTA: ClearPlayerWeaponMode (0x50AB10) writes a uint16 NONE=0
// at CCamera (0xB6F028)+0x830. It does not restore CPed's legacy sniper state.
constexpr std::uintptr_t PlayerWeaponModeAddress=0xB6F858;

// Task/attachment/contact/camera ownership checks remain the caller's guards.
// This predicate neither clears a native aim mode nor changes the ped state.
inline bool PedStateAllowsMovement(std::uint32_t ped_state,
                                   std::uint16_t player_weapon_mode) noexcept {
    return ped_state==1 || (ped_state==12 && player_weapon_mode==0);
}
}
