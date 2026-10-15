#pragma once
#include <string>
#include "bridge_protocol.h"

namespace gta_thaw::pose_preview {
struct Stats {
    bool enabled;
    std::uint64_t applied, restored, rejected, faults;
    std::uint64_t busy_reads, busy_fallbacks;
    std::uint64_t interpolated;
    std::uint64_t active_rejected, current_root_rejected, packet_root_rejected, anchor_rejected;
    std::uint64_t source_samples, source_gap_ms_max, presentation_rebases, presentation_rebase_us_max;
    std::uint32_t fault_stage, fault_bone, fault_exception;
    float fault_matrix[16];
};
using CaptureFn = void(*)(Snapshot&) noexcept;
// Returns an error only when the explicitly enabled, guarded preview cannot install.
const char* Initialize(const std::wstring& directory, const Snapshot* state, CaptureFn read_game);
Stats GetStats() noexcept;
// World-space source chest minus its paired, interpolated actor root. The
// caller adds this to its presented origin; no camera-height constant is added.
bool GetAimOffset(std::uint64_t request, std::uint32_t ped, float* output) noexcept;
}
