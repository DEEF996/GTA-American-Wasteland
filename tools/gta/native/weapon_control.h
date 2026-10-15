#pragma once
#include <string>
#include "bridge_protocol.h"

namespace gta_thaw::weapon_control {
struct State {
    bool enabled=false,active=false,can_aim=false,aiming=false,one_handed=false,hanging=false;
    std::uint64_t request=0,tick_ms=0;
    std::uint32_t ped=0,slot=0,type=0,model=0,weapon_clump=0;
    std::uint32_t ammo_clip=0,ammo_total=0,weapon_state=0;
    float range=0,fire_offset[3]{};
};
struct Stats {
    std::uint64_t prepares=0,selections=0,shots=0,rejected=0,faults=0;
    std::uint64_t pose_calls=0,input_blocked=0,frame_blocked=0,gate_blocked=0;
    std::uint64_t cooldown_blocked=0,native_calls=0,native_false=0;
    std::uint32_t last_shot_frame=0,last_shot_type=0;
    std::uint32_t prepared_frame=0,fire_frame=0,last_gate=0;
};
const char* Initialize(const std::wstring&,const Snapshot*);
// Called once after the current movement sample and GTA pad update, before camera aiming.
void PrepareTick() noexcept;
// Called with the current posed hand, inside the owned PreRender pose scope.
void FireFromPose(std::uint32_t ped,std::uint64_t request,
                  const float* origin,const float* barrel,const float* barrel_forward) noexcept;
State GetState() noexcept;
Stats GetStats() noexcept;
}
