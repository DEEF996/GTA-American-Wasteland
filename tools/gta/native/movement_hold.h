#pragma once
#include <string>
#include "bridge_protocol.h"

namespace gta_thaw::movement_hold {
struct Stats {
    bool enabled, active, release_observed;
    std::uint64_t request, started_ms, accepted, completed, rejected, ticks, controls;
    std::uint64_t physics_skips, velocity_skips, position_skips, flag_sets, flag_restores, faults, busy_reads;
    std::uint64_t controller_movement_restores;
    std::uint32_t last_reason, last_gate, last_packet_state;
    float entry_position[3], entry_velocity[3], release_velocity[3];
    bool command_enabled, commanded;
    std::uint64_t command_commits, applied_sequence;
    float command_position[3], command_velocity[3];
    bool trajectory_enabled, trajectory;
    std::uint64_t source_tick, source_time_us, collision_generation, source_advanced_ms;
    std::uint32_t source_mode, source_state, source_walk_state, source_bailing;
    bool buffering;
    std::uint32_t contact, release_contact_bits, observed_contact_bits;
    std::uint32_t last_ped_state, last_task, last_task_vtable, last_task_slot;
    std::uint64_t ambient_air_suppressed;
    bool interactive_enabled;
    std::uint64_t pad_sets, pad_restores, pad_conflicts;
    std::uint32_t entry_f7_requests;
    bool preparing, prepared_entry;
    std::uint64_t preparation_started_ms, preparations, preparation_commits;
    std::uint32_t entry_contact;
    std::uint64_t air_abort_calls, air_aborts;
    std::uint64_t diagnostic_request;
    std::uint32_t diagnostic_reason;
    std::uint64_t rejected_sequence, rejected_source_tick, rejected_source_time_us;
    std::uint32_t rejected_contact;
    float rejected_position[3], rejected_velocity[3];
    std::uint64_t rejection_applied_sequence, rejection_applied_source_tick, rejection_applied_source_time_us;
    std::uint32_t rejection_applied_contact;
    float rejection_applied_position[3], rejection_applied_velocity[3];
    float expected_velocity[3], observed_velocity[3], expected_turn_speed[3], observed_turn_speed[3];
};
using CaptureFn = void(*)(Snapshot&) noexcept;
using InstallFn = bool(*)(std::uintptr_t, const void*, unsigned char, const unsigned char*, std::size_t);
const char* Initialize(const std::wstring&, const Snapshot*, CaptureFn, InstallFn);
void ProcessTick(void(*original)());
Stats GetStats() noexcept;
bool GetRenderRoot(std::uint64_t request,std::uint64_t source_tick,std::uint64_t source_time_us,float* root) noexcept;
}
