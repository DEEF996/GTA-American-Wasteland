#pragma once
#include "vehicle_snapshot_protocol.h"
#include <string>
namespace gta_thaw::vehicle_snapshot {
struct Stats {
    bool enabled=false;
    std::uint64_t captures=0,dropped=0,rejected=0,faults=0,max_capture_us=0;
    std::uint32_t vehicles=0;
};
const char* Initialize(const std::wstring& directory,const Snapshot* snapshot);
void AfterTick() noexcept;
Stats GetStats() noexcept;
}
