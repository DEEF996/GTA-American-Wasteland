#pragma once
#include <string>
#include "bridge_protocol.h"

namespace gta_thaw::game_effects {
struct Stats {
    bool enabled=false,owned=false,exiting=false;
    float time_scale=1,effect_amount=0,sfx_volume=1;
    std::uint64_t events=0,published=0,writes=0,restores=0,conflicts=0,faults=0;
    std::uint64_t draws=0,state_restores=0;
};
const char* Initialize(const std::wstring& directory,const Snapshot* snapshot);
void AfterTick() noexcept;
void Present() noexcept;
float PresentationScale(std::uint64_t request,std::uint32_t ped,std::uint32_t epoch) noexcept;
Stats GetStats() noexcept;
}
