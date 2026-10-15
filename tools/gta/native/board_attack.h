#pragma once
#include <string>
#include "bridge_protocol.h"
namespace gta_thaw::board_attack {
struct Stats { bool enabled; std::uint64_t swings,hits,misses,rejected,faults; std::uint32_t target; };
const char* Initialize(const std::wstring&,const Snapshot*);
void AfterTick() noexcept;
Stats GetStats() noexcept;
}
