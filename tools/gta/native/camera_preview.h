#pragma once
#include <string>
#include "bridge_protocol.h"

namespace gta_thaw::camera_preview {
struct Stats {
    bool enabled, active;
    std::uint64_t request, accepted, applied, restored, rejected, conflicts, faults, busy_reads;
    std::uint32_t last_reason;
    std::uint32_t ownership_mismatch;
    float expected_eye[3], observed_eye[3], expected_forward[3], observed_forward[3];
    float expected_up[3], observed_up[3], expected_fov, observed_fov;
    float expected_view_window[2], observed_view_window[2];
    std::uint64_t projection_applied, projection_restored;
    std::uint64_t normal_reset_candidates, normal_reset_recovered, normal_reset_waits;
    std::uint32_t normal_reset_checks;
    std::uint64_t aim_frames, aim_collision_clips;
    std::uint64_t aim_ray_hits, aim_ray_misses, aim_ray_rejected;
};
struct AimState {
    bool active;
    std::uint64_t request, tick_ms;
    std::uint32_t ped;
    float eye[3], forward[3], target[3];
};
using CaptureFn=void(*)(Snapshot&) noexcept;
using InstallFn=bool(*)(std::uintptr_t,const void*,unsigned char,const unsigned char*,std::size_t);
using VerifyFileFn=bool(*)(const wchar_t*,const unsigned char*);
const char* Initialize(const std::wstring&,const Snapshot*,CaptureFn,InstallFn,VerifyFileFn);
// Call after every CGame::Process, including its paused branch, so lost leases release promptly.
void AfterTick() noexcept;
Stats GetStats() noexcept;
AimState GetAimState() noexcept;
}
