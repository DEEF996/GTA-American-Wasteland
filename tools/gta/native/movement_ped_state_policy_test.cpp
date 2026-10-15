#include <cassert>
#include <cstdio>
#include "movement_ped_state_policy.h"

using gta_thaw::movement_hold::PedStateAllowsMovement;

int main() {
    // A normal on-foot state keeps the pre-existing state gate's behavior.
    assert(PedStateAllowsMovement(1,0));
    assert(PedStateAllowsMovement(1,7));
    // Recorded vanilla transition: sniper type34, RMB, release, wheel to type37.
    // Ped state remains 12 after release; only the camera request returns to 0.
    assert(!PedStateAllowsMovement(12,7)); // Sniper requested.
    assert(PedStateAllowsMovement(12,0));  // Original camera request cleared.
    assert(!PedStateAllowsMovement(12,8)); // Rocket launcher requested again.
    assert(PedStateAllowsMovement(12,0));
    for(std::uint32_t mode=1;mode<=0xFFFF;++mode)
        assert(!PedStateAllowsMovement(12,static_cast<std::uint16_t>(mode)));
    // No other legacy state inherits this exception, including death/vehicles.
    for(std::uint32_t state=0;state<128;++state)
        if(state!=1 && state!=12) assert(!PedStateAllowsMovement(state,0));
    assert(!PedStateAllowsMovement(0xFFFFFFFFu,0));
    std::puts("movement ped-state policy tests passed");
}
