#pragma once
#include "bridge_protocol.h"
namespace gta_thaw::startup_status {
struct Stats {
    bool enabled,active;
    std::uint64_t accepted,frames,uploads,releases,state_restores,busy_reads,rejected,faults;
    std::uint32_t kind,last_error;
};
const char* Initialize(const Snapshot*);
void Present() noexcept;
Stats GetStats() noexcept;
}
