#pragma once
#include <string>
#include "bridge_protocol.h"
namespace gta_thaw::hud_preview {
struct Stats {
    bool enabled,active;
    std::uint64_t request,accepted,frames,quads,loads,uploads,releases,state_restores,busy_reads,rejected,faults;
    std::uint32_t last_error;
};
using CaptureFn=void(*)(Snapshot&) noexcept;
using InstallFn=bool(*)(std::uintptr_t,const void*,unsigned char,const unsigned char*,std::size_t);
const char* Initialize(const std::wstring&,const Snapshot*,CaptureFn,InstallFn);
Stats GetStats() noexcept;
}
