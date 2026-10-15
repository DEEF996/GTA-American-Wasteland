#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <bcrypt.h>
#include <cstdio>
#include <cwchar>
#include <iterator>
#include <string>
#include "bridge_protocol.h"
#include "pose_preview.h"
#include "board_preview.h"
#include "board_attack.h"
#include "vehicle_snapshot.h"
#include "weapon_control.h"
#include "game_effects.h"
#include "weapon_presentation.h"
#include "movement_hold.h"
#include "movement_ack.h"
#include "camera_preview.h"
#include "hud_preview.h"
#include "startup_status.h"
#include "settings_menu.h"
#include "rw_raster.h"

namespace {
constexpr std::uintptr_t ProcessCall = 0x53E981, ProcessGame = 0x53BEE0;
constexpr std::uintptr_t UserDirectory = 0x744FB0, FindPlayerPed = 0x56E210;
constexpr std::uintptr_t LegacyFrameDelay = 0x53E94A;
constexpr unsigned char ProcessBytes[] = {0xE8, 0x5A, 0xD5, 0xFF, 0xFF};
constexpr unsigned char DirectoryBytes[] = {0x83, 0xEC, 0x0C, 0x8D, 0x44, 0x24, 0x00};
constexpr unsigned char FrameDelayBytes[] = {0x83, 0xF8, 0x0E, 0x72, 0xE1};
constexpr unsigned char RemovedFrameDelayBytes[] = {0x83, 0xF8, 0x00, 0x72, 0xE1};
constexpr unsigned char ExeHash[] = {
    0xa5,0x59,0xaa,0x77,0x2f,0xd1,0x36,0x37,0x91,0x55,0xef,0xa7,0x1f,0x00,0xc4,0x7a,
    0xad,0x34,0xbb,0xfe,0xae,0x61,0x96,0xb0,0xfe,0x10,0x47,0xd0,0x64,0x5c,0xbd,0x26,
};
gta_thaw::SharedState* shared_state = nullptr;
HANDLE shared_mapping = nullptr;
gta_thaw::movement_ack::SharedAck* movement_ack = nullptr;
HANDLE movement_ack_mapping = nullptr;
gta_thaw::Snapshot snapshot{};
std::wstring runtime_dir;
std::uint32_t profile_calls = 0;
long initialized = 0;
bool toggle_was_down = true;
bool legacy_frame_delay_removed = false;
std::uint64_t last_status_ms = 0;
gta_thaw::movement_hold::Stats last_status_movement{};
gta_thaw::camera_preview::Stats last_status_camera{};
const char* failed_guard = "";
char expected_guard_bytes[33]{}, observed_guard_bytes[33]{};

constexpr bool LeaseStatusChanged(const gta_thaw::movement_hold::Stats& movement,
    const gta_thaw::camera_preview::Stats& camera,
    const gta_thaw::movement_hold::Stats& previous_movement,
    const gta_thaw::camera_preview::Stats& previous_camera) noexcept {
    return movement.accepted != previous_movement.accepted || movement.completed != previous_movement.completed ||
        camera.accepted != previous_camera.accepted || camera.restored != previous_camera.restored ||
        camera.conflicts != previous_camera.conflicts || camera.active != previous_camera.active;
}

static_assert([] {
    gta_thaw::movement_hold::Stats movement{}, previous_movement{};
    gta_thaw::camera_preview::Stats camera{}, previous_camera{};
    ++movement.ticks; ++camera.applied;
    if (LeaseStatusChanged(movement, camera, previous_movement, previous_camera)) return false;
    ++movement.accepted;
    if (!LeaseStatusChanged(movement, camera, previous_movement, previous_camera)) return false;
    previous_movement = movement; ++movement.completed;
    if (!LeaseStatusChanged(movement, camera, previous_movement, previous_camera)) return false;
    previous_movement = movement; ++camera.conflicts;
    if (!LeaseStatusChanged(movement, camera, previous_movement, previous_camera)) return false;
    previous_camera = camera; previous_camera.active = true;
    return LeaseStatusChanged(movement, camera, previous_movement, previous_camera);
}(), "Lease events must publish status without writing on every applied frame");

struct JsonVector {
    char text[512];
    explicit JsonVector(const float* values, unsigned count = 3) noexcept {
        std::size_t length = 1;
        text[0] = '[';
        for (unsigned i = 0; i < count && i < 16; ++i) {
            char part[32];
            if (std::isfinite(values[i])) std::snprintf(part, sizeof(part), "%.9g", values[i]);
            else std::strcpy(part, "null");
            length += std::snprintf(text + length, sizeof(text) - length, "%s%s", i ? "," : "", part);
        }
        std::strcpy(text + length, "]");
    }
};

void StatusFile(const char* stage, const char* error = "") noexcept {
    if (runtime_dir.empty()) return;
    char json[16384];
    const auto pose = gta_thaw::pose_preview::GetStats();
    const auto board = gta_thaw::board_preview::GetStats();
    const auto hold = gta_thaw::movement_hold::GetStats();
    const auto camera = gta_thaw::camera_preview::GetStats();
    const auto hud = gta_thaw::hud_preview::GetStats();
    const auto startup = gta_thaw::startup_status::GetStats();
    const auto attack = gta_thaw::board_attack::GetStats();
    const auto vehicles = gta_thaw::vehicle_snapshot::GetStats();
    const auto weapon = gta_thaw::weapon_control::GetState();
    const auto shots = gta_thaw::weapon_control::GetStats();
    const auto effects = gta_thaw::game_effects::GetStats();
    const auto weapon_pose = gta_thaw::weapon_presentation::GetStats();
    const auto aim = gta_thaw::camera_preview::GetAimState();
    const auto menu_input = gta_thaw::settings_menu::GetInputStats();
    const int length = std::snprintf(json, sizeof(json),
        "{\"schema\":1,\"stage\":\"%s\",\"error\":\"%s\",\"pid\":%lu,"
        "\"profile_active\":%s,\"profile_calls\":%u,\"sequence\":%llu,"
        "\"ped_address\":%u,\"epoch\":%u,\"area\":%u,\"capabilities\":%u,"
        "\"skating_available\":false,\"f7_requests\":%u,\"failed_guard\":\"%s\","
        "\"expected_bytes\":\"%s\",\"observed_bytes\":\"%s\","
        "\"legacy_frame_delay_removed\":%s,\"protocol\":\"GTATHAW2\","
        "\"clock_hour\":%u,\"clock_minute\":%u,\"clock_second\":%u,"
        "\"controls_disabled_mask\":%u,\"cutscene_flags\":%u,\"ped_motion_flags\":%u,"
        "\"grounded\":%s,\"control_eligible\":%s,"
        "\"pose_preview_enabled\":%s,\"pose_preview_applied\":%llu,"
        "\"pose_preview_restored\":%llu,\"pose_preview_rejected\":%llu,\"pose_preview_faults\":%llu,"
        "\"pose_busy_reads\":%llu,\"pose_busy_fallbacks\":%llu,\"pose_interpolated\":%llu,"
        "\"pose_active_rejected\":%llu,\"pose_current_root_rejected\":%llu,\"pose_packet_root_rejected\":%llu,\"pose_anchor_rejected\":%llu,"
        "\"pose_source_samples\":%llu,\"pose_source_gap_ms_max\":%llu,\"pose_presentation_rebases\":%llu,"
        "\"pose_presentation_rebase_us_max\":%llu,"
        "\"board_preview_draws\":%llu,\"board_state_restores\":%llu,\"board_pool_restores\":%llu,\"board_uploads\":%llu,"
        "\"board_releases\":%llu,\"board_loads\":%llu,\"board_skin_updates\":%llu,\"board_rejected\":%llu,\"board_faults\":%llu,\"board_last_error\":%u,"
        "\"hold_enabled\":%s,\"hold_active\":%s,\"hold_preparing\":%s,\"hold_request\":%llu,\"hold_started_ms\":%llu,"
        "\"hold_accepted\":%llu,\"hold_completed\":%llu,\"hold_rejected\":%llu,\"hold_ticks\":%llu,\"hold_controls\":%llu,"
        "\"hold_physics_skips\":%llu,\"hold_velocity_skips\":%llu,\"hold_position_skips\":%llu,"
        "\"hold_controller_movement_restores\":%llu,"
        "\"hold_flag_sets\":%llu,\"hold_flag_restores\":%llu,\"hold_faults\":%llu,\"hold_last_reason\":%u,\"hold_last_gate\":%u,"
        "\"hold_busy_reads\":%llu,\"hold_last_packet_state\":%u,"
        "\"hold_rejection\":{\"request\":%llu,\"reason\":%u,"
        "\"sequence\":%llu,\"source_tick\":%llu,\"source_time_us\":%llu,\"contact\":%u,"
        "\"position\":%s,\"velocity\":%s,"
        "\"applied_sequence\":%llu,\"applied_source_tick\":%llu,\"applied_source_time_us\":%llu,\"applied_contact\":%u,"
        "\"applied_position\":%s,\"applied_velocity\":%s,"
        "\"expected_velocity\":%s,\"observed_velocity\":%s,\"expected_turn_speed\":%s,\"observed_turn_speed\":%s},"
        "\"hold_entry_position\":[%.9g,%.9g,%.9g],\"hold_entry_velocity\":[%.9g,%.9g,%.9g],"
        "\"hold_release_velocity\":[%.9g,%.9g,%.9g],\"hold_release_observed\":%s,"
        "\"move_enabled\":%s,\"move_last_request_mode\":%s,\"move_commits\":%llu,\"move_applied_sequence\":%llu,"
        "\"move_last_position\":[%.9g,%.9g,%.9g],\"move_last_velocity\":[%.9g,%.9g,%.9g],"
        "\"trajectory_enabled\":%s,\"trajectory_last_request_mode\":%s,\"trajectory_source_tick\":%llu,"
        "\"trajectory_source_time_us\":%llu,\"trajectory_collision_generation\":%llu,\"trajectory_source_advanced_ms\":%llu,"
        "\"trajectory_contact\":%u,\"hold_release_contact_bits\":%u,\"hold_observed_contact_bits\":%u,"
        "\"hold_last_ped_state\":%u,\"hold_last_task\":%u,\"hold_last_task_vtable\":%u,\"hold_last_task_slot\":%u,"
        "\"trajectory_ambient_air_suppressed\":%llu,\"trajectory_interactive_enabled\":%s,"
        "\"trajectory_pad_sets\":%llu,\"trajectory_pad_restores\":%llu,\"trajectory_pad_conflicts\":%llu,"
        "\"trajectory_entry_f7_requests\":%u,"
        "\"entry_preparations\":%llu,\"entry_commits\":%llu,\"entry_contact\":%u,"
        "\"entry_air_abort_calls\":%llu,\"entry_air_aborts\":%llu,"
        "\"camera_enabled\":%s,\"camera_active\":%s,\"camera_request\":%llu,"
        "\"camera_accepted\":%llu,\"camera_applied\":%llu,\"camera_restored\":%llu,"
        "\"camera_rejected\":%llu,\"camera_conflicts\":%llu,\"camera_faults\":%llu,"
        "\"camera_busy_reads\":%llu,\"camera_last_reason\":%u,"
        "\"camera_normal_reset_candidates\":%llu,\"camera_normal_reset_recovered\":%llu,"
        "\"camera_normal_reset_waits\":%llu,\"camera_normal_reset_checks\":%u,"
        "\"camera_expected_eye\":[%.9g,%.9g,%.9g],\"camera_observed_eye\":[%.9g,%.9g,%.9g],"
        "\"camera_expected_forward\":[%.9g,%.9g,%.9g],\"camera_observed_forward\":[%.9g,%.9g,%.9g],"
        "\"camera_expected_up\":[%.9g,%.9g,%.9g],\"camera_observed_up\":[%.9g,%.9g,%.9g],"
        "\"camera_expected_fov\":%.9g,\"camera_observed_fov\":%.9g,"
        "\"camera_expected_view_window\":[%.9g,%.9g],\"camera_observed_view_window\":[%.9g,%.9g],"
        "\"camera_projection_applied\":%llu,\"camera_projection_restored\":%llu,\"camera_ownership_mismatch\":%lu,"
        "\"hud_enabled\":%s,\"hud_active\":%s,\"hud_request\":%llu,\"hud_accepted\":%llu,"
        "\"hud_frames\":%llu,\"hud_quads\":%llu,\"hud_loads\":%llu,\"hud_uploads\":%llu,"
        "\"hud_releases\":%llu,\"hud_state_restores\":%llu,\"hud_busy_reads\":%llu,"
        "\"hud_rejected\":%llu,\"hud_faults\":%llu,\"hud_last_error\":%u,\"raster_chosen_depth\":%d,"
        "\"startup_enabled\":%s,\"startup_active\":%s,\"startup_kind\":%u,\"startup_accepted\":%llu,"
        "\"startup_frames\":%llu,\"startup_uploads\":%llu,\"startup_releases\":%llu,\"startup_state_restores\":%llu,"
        "\"startup_busy_reads\":%llu,\"startup_rejected\":%llu,\"startup_faults\":%llu,\"startup_last_error\":%u,"
        "\"attack_enabled\":%s,\"attack_swings\":%llu,\"attack_hits\":%llu,\"attack_misses\":%llu,"
        "\"attack_rejected\":%llu,\"attack_faults\":%llu,\"attack_target\":%u,"
        "\"menu_input_sets\":%llu,\"menu_input_restores\":%llu,\"menu_input_conflicts\":%llu,"
        "\"weapon_enabled\":%s,\"weapon_active\":%s,\"weapon_can_aim\":%s,\"weapon_aiming\":%s,"
        "\"weapon_type\":%u,\"weapon_slot\":%u,\"weapon_ammo_clip\":%u,\"weapon_ammo_total\":%u,"
        "\"weapon_hanging\":%s,\"weapon_selections\":%llu,\"weapon_shots\":%llu,\"weapon_faults\":%llu,"
        "\"weapon_rejected\":%llu,\"camera_aim_frames\":%llu,\"camera_aim_collision_clips\":%llu,"
        "\"weapon_pose_aimed\":%llu,\"weapon_pose_holstered\":%llu,\"weapon_pose_rejected\":%llu,\"weapon_pose_faults\":%llu,"
        "\"weapon_pose_calls\":%llu,\"weapon_input_blocked\":%llu,\"weapon_frame_blocked\":%llu,\"weapon_gate_blocked\":%llu,"
        "\"weapon_cooldown_blocked\":%llu,\"weapon_native_calls\":%llu,\"weapon_native_false\":%llu,"
        "\"weapon_prepared_frame\":%u,\"weapon_fire_frame\":%u,\"weapon_last_gate\":%u,"
        "\"weapon_pre_render_calls\":%llu,\"weapon_fire_candidates\":%llu,\"weapon_alignment_rejected\":%llu,\"weapon_fire_forwarded\":%llu,"
        "\"weapon_gun_position\":%s,\"weapon_gun_forward\":%s,\"weapon_target_dot\":%.9g,"
        "\"pose_fault_stage\":%u,\"pose_fault_bone\":%u,\"pose_fault_exception\":%u,\"pose_fault_matrix\":%s,"
        "\"hold_source_mode\":%u,\"hold_source_state\":%u,\"hold_source_walk_state\":%u,\"hold_source_bailing\":%u,\"hold_buffering\":%s,"
        "\"vehicles_enabled\":%s,\"vehicles_captures\":%llu,\"vehicles_count\":%u,\"vehicles_dropped\":%llu,"
        "\"vehicles_rejected\":%llu,\"vehicles_faults\":%llu,\"vehicles_capture_us_max\":%llu,"
        "\"aim_ray_hits\":%llu,\"aim_ray_misses\":%llu,\"aim_ray_rejected\":%llu,"
        "\"effects_enabled\":%s,\"effects_owned\":%s,\"effects_exiting\":%s,"
        "\"effects_time_scale\":%.9g,\"effects_amount\":%.9g,\"effects_sfx_volume\":%.9g,"
        "\"effects_events\":%llu,\"effects_published\":%llu,\"effects_writes\":%llu,\"effects_restores\":%llu,"
        "\"effects_conflicts\":%llu,\"effects_faults\":%llu,\"effects_draws\":%llu,\"effects_state_restores\":%llu}\n",
        stage, error, GetCurrentProcessId(),
        (snapshot.capabilities & gta_thaw::IsolatedProfile) ? "true" : "false",
        profile_calls, static_cast<unsigned long long>(snapshot.sequence),
        snapshot.ped_address, snapshot.epoch, snapshot.current_area,
        snapshot.capabilities, snapshot.f7_requests,
        failed_guard, expected_guard_bytes, observed_guard_bytes,
        legacy_frame_delay_removed ? "true" : "false",
        snapshot.clock_hour, snapshot.clock_minute, snapshot.clock_second,
        snapshot.controls_disabled_mask, snapshot.cutscene_flags, snapshot.ped_motion_flags,
        (snapshot.ped_motion_flags & gta_thaw::Standing) ? "true" : "false",
        gta_thaw::ControlEligible(snapshot) ? "true" : "false",
        pose.enabled ? "true" : "false", static_cast<unsigned long long>(pose.applied),
        static_cast<unsigned long long>(pose.restored), static_cast<unsigned long long>(pose.rejected),
        static_cast<unsigned long long>(pose.faults),
        static_cast<unsigned long long>(pose.busy_reads), static_cast<unsigned long long>(pose.busy_fallbacks),
        static_cast<unsigned long long>(pose.interpolated),
        static_cast<unsigned long long>(pose.active_rejected),
        static_cast<unsigned long long>(pose.current_root_rejected),
        static_cast<unsigned long long>(pose.packet_root_rejected),
        static_cast<unsigned long long>(pose.anchor_rejected),
        static_cast<unsigned long long>(pose.source_samples),
        static_cast<unsigned long long>(pose.source_gap_ms_max),
        static_cast<unsigned long long>(pose.presentation_rebases),
        static_cast<unsigned long long>(pose.presentation_rebase_us_max),
        static_cast<unsigned long long>(board.draws),
        static_cast<unsigned long long>(board.state_restores), static_cast<unsigned long long>(board.pool_restores),
        static_cast<unsigned long long>(board.uploads),
        static_cast<unsigned long long>(board.releases), static_cast<unsigned long long>(board.loads),
        static_cast<unsigned long long>(board.skin_updates), static_cast<unsigned long long>(board.rejected),
        static_cast<unsigned long long>(board.faults), board.last_error,
        hold.enabled ? "true" : "false", hold.active ? "true" : "false", hold.preparing ? "true" : "false",
        static_cast<unsigned long long>(hold.request), static_cast<unsigned long long>(hold.started_ms),
        static_cast<unsigned long long>(hold.accepted), static_cast<unsigned long long>(hold.completed),
        static_cast<unsigned long long>(hold.rejected), static_cast<unsigned long long>(hold.ticks),
        static_cast<unsigned long long>(hold.controls), static_cast<unsigned long long>(hold.physics_skips),
        static_cast<unsigned long long>(hold.velocity_skips), static_cast<unsigned long long>(hold.position_skips),
        static_cast<unsigned long long>(hold.controller_movement_restores),
        static_cast<unsigned long long>(hold.flag_sets), static_cast<unsigned long long>(hold.flag_restores),
        static_cast<unsigned long long>(hold.faults), hold.last_reason, hold.last_gate,
        static_cast<unsigned long long>(hold.busy_reads), hold.last_packet_state,
        hold.diagnostic_request, hold.diagnostic_reason,
        hold.rejected_sequence, hold.rejected_source_tick, hold.rejected_source_time_us, hold.rejected_contact,
        JsonVector(hold.rejected_position).text, JsonVector(hold.rejected_velocity).text,
        hold.rejection_applied_sequence, hold.rejection_applied_source_tick, hold.rejection_applied_source_time_us,
        hold.rejection_applied_contact,
        JsonVector(hold.rejection_applied_position).text, JsonVector(hold.rejection_applied_velocity).text,
        JsonVector(hold.expected_velocity).text, JsonVector(hold.observed_velocity).text,
        JsonVector(hold.expected_turn_speed).text, JsonVector(hold.observed_turn_speed).text,
        hold.entry_position[0],hold.entry_position[1],hold.entry_position[2],
        hold.entry_velocity[0],hold.entry_velocity[1],hold.entry_velocity[2],
        hold.release_velocity[0],hold.release_velocity[1],hold.release_velocity[2],
        hold.release_observed ? "true" : "false",
        hold.command_enabled ? "true" : "false", hold.commanded ? "true" : "false",
        static_cast<unsigned long long>(hold.command_commits), static_cast<unsigned long long>(hold.applied_sequence),
        hold.command_position[0],hold.command_position[1],hold.command_position[2],
        hold.command_velocity[0],hold.command_velocity[1],hold.command_velocity[2],
        hold.trajectory_enabled ? "true" : "false", hold.trajectory ? "true" : "false",
        static_cast<unsigned long long>(hold.source_tick), static_cast<unsigned long long>(hold.source_time_us),
        static_cast<unsigned long long>(hold.collision_generation), static_cast<unsigned long long>(hold.source_advanced_ms),
        hold.contact, hold.release_contact_bits, hold.observed_contact_bits,
        hold.last_ped_state, hold.last_task, hold.last_task_vtable, hold.last_task_slot,
        static_cast<unsigned long long>(hold.ambient_air_suppressed), hold.interactive_enabled ? "true" : "false",
        static_cast<unsigned long long>(hold.pad_sets), static_cast<unsigned long long>(hold.pad_restores),
        static_cast<unsigned long long>(hold.pad_conflicts), hold.entry_f7_requests,
        static_cast<unsigned long long>(hold.preparations), static_cast<unsigned long long>(hold.preparation_commits),
        hold.entry_contact, static_cast<unsigned long long>(hold.air_abort_calls), static_cast<unsigned long long>(hold.air_aborts),
        camera.enabled ? "true" : "false", camera.active ? "true" : "false",
        static_cast<unsigned long long>(camera.request), static_cast<unsigned long long>(camera.accepted),
        static_cast<unsigned long long>(camera.applied), static_cast<unsigned long long>(camera.restored),
        static_cast<unsigned long long>(camera.rejected), static_cast<unsigned long long>(camera.conflicts),
        static_cast<unsigned long long>(camera.faults), static_cast<unsigned long long>(camera.busy_reads), camera.last_reason,
        static_cast<unsigned long long>(camera.normal_reset_candidates),
        static_cast<unsigned long long>(camera.normal_reset_recovered),
        static_cast<unsigned long long>(camera.normal_reset_waits), camera.normal_reset_checks,
        camera.expected_eye[0], camera.expected_eye[1], camera.expected_eye[2],
        camera.observed_eye[0], camera.observed_eye[1], camera.observed_eye[2],
        camera.expected_forward[0], camera.expected_forward[1], camera.expected_forward[2],
        camera.observed_forward[0], camera.observed_forward[1], camera.observed_forward[2],
        camera.expected_up[0], camera.expected_up[1], camera.expected_up[2],
        camera.observed_up[0], camera.observed_up[1], camera.observed_up[2],
        camera.expected_fov, camera.observed_fov,
        camera.expected_view_window[0], camera.expected_view_window[1],
        camera.observed_view_window[0], camera.observed_view_window[1],
        camera.projection_applied, camera.projection_restored,static_cast<unsigned long>(camera.ownership_mismatch),
        hud.enabled ? "true" : "false", hud.active ? "true" : "false", hud.request, hud.accepted,
        hud.frames, hud.quads, hud.loads, hud.uploads, hud.releases, hud.state_restores,
        hud.busy_reads, hud.rejected, hud.faults, hud.last_error, gta_thaw::rw_raster::chosen_depth,
        startup.enabled ? "true" : "false", startup.active ? "true" : "false", startup.kind,
        startup.accepted, startup.frames, startup.uploads, startup.releases, startup.state_restores,
        startup.busy_reads, startup.rejected, startup.faults, startup.last_error,
        attack.enabled ? "true" : "false", attack.swings, attack.hits, attack.misses,
        attack.rejected, attack.faults, attack.target,
        menu_input.sets, menu_input.restores, menu_input.conflicts,
        weapon.enabled?"true":"false",weapon.active?"true":"false",weapon.can_aim?"true":"false",
        aim.active?"true":"false",weapon.type,weapon.slot,weapon.ammo_clip,weapon.ammo_total,
        weapon.hanging?"true":"false",shots.selections,shots.shots,shots.faults,shots.rejected,
        camera.aim_frames,camera.aim_collision_clips,
        weapon_pose.aimed,weapon_pose.holstered,weapon_pose.rejected,weapon_pose.faults,
        shots.pose_calls,shots.input_blocked,shots.frame_blocked,shots.gate_blocked,
        shots.cooldown_blocked,shots.native_calls,shots.native_false,
        shots.prepared_frame,shots.fire_frame,shots.last_gate,
        weapon_pose.pre_render_calls,weapon_pose.fire_candidates,weapon_pose.alignment_rejected,weapon_pose.fire_forwarded,
        JsonVector(weapon_pose.last_gun_position).text,JsonVector(weapon_pose.last_gun_forward).text,weapon_pose.last_target_dot,
        pose.fault_stage,pose.fault_bone,pose.fault_exception,JsonVector(pose.fault_matrix,16).text,
        hold.source_mode,hold.source_state,hold.source_walk_state,hold.source_bailing,hold.buffering?"true":"false",
        vehicles.enabled?"true":"false",vehicles.captures,vehicles.vehicles,vehicles.dropped,
        vehicles.rejected,vehicles.faults,vehicles.max_capture_us,camera.aim_ray_hits,camera.aim_ray_misses,camera.aim_ray_rejected,
        effects.enabled?"true":"false",effects.owned?"true":"false",effects.exiting?"true":"false",
        effects.time_scale,effects.effect_amount,effects.sfx_volume,effects.events,effects.published,effects.writes,effects.restores,
        effects.conflicts,effects.faults,effects.draws,effects.state_restores);
    if (length <= 0 || static_cast<std::size_t>(length) >= sizeof(json)) return;
    const auto temporary = runtime_dir + L"\\adapter-status.tmp";
    const auto target = runtime_dir + L"\\adapter-status.json";
    HANDLE file = CreateFileW(temporary.c_str(), GENERIC_WRITE, 0, nullptr,
        CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (file == INVALID_HANDLE_VALUE) return;
    DWORD written = 0;
    const auto byte_count = static_cast<DWORD>(length);
    const bool ok = WriteFile(file, json, byte_count, &written, nullptr) && written == byte_count;
    CloseHandle(file);
    if (ok) MoveFileExW(temporary.c_str(), target.c_str(), MOVEFILE_REPLACE_EXISTING);
}

[[noreturn]] void Fail(const char* reason) {
    StatusFile("refused", reason);
    OutputDebugStringA(reason);
    // Continuing after failed profile isolation could write the user's normal saves.
    ExitProcess(86);
}

void RequireHook(const char* name, std::uintptr_t address,
                 const unsigned char* expected, std::size_t length) {
    const auto observed = reinterpret_cast<const unsigned char*>(address);
    if (!std::memcmp(observed, expected, length)) return;
    failed_guard = name;
    for (std::size_t i = 0; i < length && i < 16; ++i) {
        std::snprintf(expected_guard_bytes + 2 * i, 3, "%02x", expected[i]);
        std::snprintf(observed_guard_bytes + 2 * i, 3, "%02x", observed[i]);
    }
    Fail("Game hook bytes differ from the verified executable");
}

bool HashMatches(const wchar_t* path, const unsigned char* expected = ExeHash) {
    HANDLE file = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING,
        FILE_ATTRIBUTE_NORMAL | FILE_FLAG_SEQUENTIAL_SCAN, nullptr);
    if (file == INVALID_HANDLE_VALUE) return false;
    BCRYPT_ALG_HANDLE algorithm = nullptr;
    BCRYPT_HASH_HANDLE hash = nullptr;
    bool ok = BCryptOpenAlgorithmProvider(&algorithm, BCRYPT_SHA256_ALGORITHM, nullptr, 0) >= 0;
    if (ok) ok = BCryptCreateHash(algorithm, &hash, nullptr, 0, nullptr, 0, 0) >= 0;
    unsigned char buffer[65536], result[32];
    while (ok) {
        DWORD read = 0;
        if (!ReadFile(file, buffer, sizeof(buffer), &read, nullptr)) { ok = false; break; }
        if (!read) break;
        ok = BCryptHashData(hash, buffer, read, 0) >= 0;
    }
    if (ok) ok = BCryptFinishHash(hash, result, sizeof(result), 0) >= 0 &&
        std::memcmp(result, expected, sizeof(result)) == 0;
    if (hash) BCryptDestroyHash(hash);
    if (algorithm) BCryptCloseAlgorithmProvider(algorithm, 0);
    CloseHandle(file);
    return ok;
}

bool MakeDirectory(const std::wstring& path) {
    if (CreateDirectoryW(path.c_str(), nullptr)) return true;
    const DWORD attributes = GetFileAttributesW(path.c_str());
    return attributes != INVALID_FILE_ATTRIBUTES && (attributes & FILE_ATTRIBUTE_DIRECTORY) &&
        !(attributes & FILE_ATTRIBUTE_REPARSE_POINT);
}

template<class T> T Read(std::uintptr_t address) {
    T value{};
    std::memcpy(&value, reinterpret_cast<const void*>(address), sizeof(value));
    return value;
}

char* __cdecl LocalUserDirectory() {
    ++profile_calls;
    return reinterpret_cast<char*>(0xC92368);
}

void ReadGame(gta_thaw::Snapshot& output) noexcept {
    __try {
        output.clock_hour = Read<unsigned char>(0xB70153);
        output.clock_minute = Read<unsigned char>(0xB70152);
        output.clock_second = Read<unsigned short>(0xB70150);
        const auto running = Read<unsigned char>(0xB5F851);
        const auto processing = Read<unsigned char>(0xB5F852);
        const auto player_index = Read<unsigned char>(0xB7CD74);
        if (running > 1 || processing > 1 || player_index > 1) {
            output.status_flags |= gta_thaw::ReadFault;
            return;
        }
        output.cutscene_flags = running | (processing << 1);
        // CPad::GetPad uses this fixed array/stride; no game method is invoked.
        output.controls_disabled_mask = Read<unsigned short>(0xB73458 + player_index * 0x134 + 0x10E);
        output.current_area = Read<std::uint32_t>(0xB72914);
        output.game_timer = Read<std::uint32_t>(0xB7CB84);
        output.frame_counter = Read<std::uint32_t>(0xB7CB4C);
        output.time_step = Read<float>(0xB7CB5C);
        if (Read<unsigned char>(0xB7CB48) || Read<unsigned char>(0xB7CB49)) output.status_flags |= gta_thaw::Paused;
        const auto ped = reinterpret_cast<std::uintptr_t(__cdecl*)(int)>(FindPlayerPed)(-1);
        if (!ped) return;
        const auto matrix = Read<std::uintptr_t>(ped + 0x14);
        std::memcpy(output.position, reinterpret_cast<void*>(matrix ? matrix + 0x30 : ped + 4), 12);
        std::memcpy(output.move_speed, reinterpret_cast<void*>(ped + 0x44), 12);
        output.ped_address = static_cast<std::uint32_t>(ped);
        output.ped_area = Read<unsigned char>(ped + 0x2F);
        output.heading = Read<float>(ped + 0x558);
        output.health = Read<float>(ped + 0x540);
        output.status_flags |= gta_thaw::PedPresent;
        const auto ped_flags = Read<unsigned short>(ped + 0x46C);
        output.ped_motion_flags = (ped_flags & 1) | ((ped_flags >> 8) & 6);
        const bool in_vehicle = (ped_flags & 0x100) != 0;
        output.status_flags |= in_vehicle ? gta_thaw::InVehicle : gta_thaw::OnFoot;
    } __except(EXCEPTION_EXECUTE_HANDLER) {
        output.ped_address = 0;
        output.ped_motion_flags = 0;
        output.status_flags &= ~(gta_thaw::PedPresent | gta_thaw::InVehicle | gta_thaw::OnFoot);
        output.status_flags |= gta_thaw::ReadFault;
    }
}

void Capture() noexcept {
    gta_thaw::Snapshot next = snapshot;
    next.tick_ms = GetTickCount64();
    ++next.sequence;
    next.status_flags = 0; next.ped_address = 0; next.ped_area = 0;
    next.clock_hour = 0; next.clock_minute = 0; next.clock_second = 0;
    next.controls_disabled_mask = 0; next.cutscene_flags = 0; next.ped_motion_flags = 0;
    std::memset(next.position, 0, sizeof(next.position));
    std::memset(next.move_speed, 0, sizeof(next.move_speed));
    next.heading = 0; next.health = 0;
    ReadGame(next);
    if (next.ped_address != snapshot.ped_address || next.current_area != snapshot.current_area ||
        next.ped_area != snapshot.ped_area || next.game_timer < snapshot.game_timer ||
        next.frame_counter < snapshot.frame_counter ||
        next.controls_disabled_mask != snapshot.controls_disabled_mask ||
        next.cutscene_flags != snapshot.cutscene_flags) ++next.epoch;
    DWORD foreground_pid = 0;
    GetWindowThreadProcessId(GetForegroundWindow(), &foreground_pid);
    const bool foreground = foreground_pid == GetCurrentProcessId();
    if (foreground) next.status_flags |= gta_thaw::Foreground;
    gta_thaw::settings_menu::Poll(next);
    const bool toggle_down = (GetAsyncKeyState('J') & 0x8000) != 0;
    // Preserve the legacy wire field name; the user-facing toggle is J.
    if (foreground && !gta_thaw::settings_menu::Open() && toggle_down && !toggle_was_down) ++next.f7_requests;
    toggle_was_down = toggle_down;
    const auto movement = gta_thaw::movement_hold::GetStats();
    if (movement.active && movement.trajectory) next.status_flags |= gta_thaw::TrajectoryOwned;
    if (movement.active && movement.preparing) next.status_flags |= gta_thaw::TrajectoryPreparing;
    if (GetKeyState(VK_NUMLOCK) & 1) next.status_flags |= gta_thaw::NumLockOn;
    snapshot = next;
    const auto camera = gta_thaw::camera_preview::GetStats();
    if (LeaseStatusChanged(movement, camera, last_status_movement, last_status_camera) ||
        next.tick_ms - last_status_ms >= 1000) {
        last_status_ms = next.tick_ms;
        last_status_movement = movement;
        last_status_camera = camera;
        // Publish release reasons before the reader can observe an inactive snapshot/ACK.
        StatusFile("observing");
    }
    const long generation = (static_cast<long>(next.sequence & 0x3fffffff) + 1) * 2;
    InterlockedExchange(&shared_state->begin, generation - 1);
    std::memcpy(&shared_state->snapshot, &next, sizeof(next));
    MemoryBarrier();
    InterlockedExchange(&shared_state->end, generation);
    InterlockedExchange(&shared_state->begin, generation);
}

void __cdecl ProcessGameWithMovement() {
    gta_thaw::movement_hold::ProcessTick(reinterpret_cast<void(__cdecl*)()>(ProcessGame));
}
void __cdecl GameProcessHook() {
    gta_thaw::settings_menu::ProcessInputFrame(ProcessGameWithMovement);
    gta_thaw::camera_preview::AfterTick();
    Capture();
    gta_thaw::game_effects::AfterTick();
    gta_thaw::vehicle_snapshot::AfterTick();
    gta_thaw::board_attack::AfterTick();
    // Acknowledge only after movement validation and the complete GTA tick.
    const auto ack = gta_thaw::movement_ack::Applied(snapshot, gta_thaw::movement_hold::GetStats());
    const auto generation = static_cast<long>((ack.sequence % 0x3fffffffULL + 1) * 2);
    InterlockedExchange(&movement_ack->begin, generation - 1);
    std::memcpy(&movement_ack->packet, &ack, sizeof(ack));
    MemoryBarrier();
    InterlockedExchange(&movement_ack->end, generation);
    InterlockedExchange(&movement_ack->begin, generation);
}

bool InstallBranch(std::uintptr_t address, const void* target, unsigned char opcode,
                   const unsigned char* expected, std::size_t length) {
    if (length < 5 || length > 16 || std::memcmp(reinterpret_cast<void*>(address), expected, length)) return false;
    unsigned char patch[16];
    std::memset(patch, 0x90, sizeof(patch));
    patch[0] = opcode;
    const auto relative = static_cast<std::uint32_t>(reinterpret_cast<std::uintptr_t>(target) - address - 5);
    std::memcpy(patch + 1, &relative, 4);
    DWORD protection = 0;
    if (!VirtualProtect(reinterpret_cast<void*>(address), length, PAGE_EXECUTE_READWRITE, &protection)) return false;
    std::memcpy(reinterpret_cast<void*>(address), patch, length);
    FlushInstructionCache(GetCurrentProcess(), reinterpret_cast<void*>(address), length);
    DWORD ignored = 0;
    return VirtualProtect(reinterpret_cast<void*>(address), length, protection, &ignored) != 0;
}

void RemoveLegacyFrameDelay() {
    const auto ini = runtime_dir + L"\\adapter.ini";
    if (GetPrivateProfileIntW(L"performance", L"RemoveLegacyFrameDelay", 0, ini.c_str()) != 1) return;
    const auto instruction = reinterpret_cast<const void*>(LegacyFrameDelay);
    if (!std::memcmp(instruction, RemovedFrameDelayBytes, sizeof(RemovedFrameDelayBytes))) {
        legacy_frame_delay_removed = true;
        return;
    }
    RequireHook("Legacy frame delay at 0x53E94A", LegacyFrameDelay, FrameDelayBytes, sizeof(FrameDelayBytes));
    // The verified loop compares elapsed milliseconds with 14. Change only its
    // immediate, as FramerateVigilante does; physics and the FPS limiter stay separate.
    auto immediate = reinterpret_cast<unsigned char*>(LegacyFrameDelay + 2);
    DWORD protection = 0;
    if (!VirtualProtect(immediate, 1, PAGE_EXECUTE_READWRITE, &protection))
        Fail("Cannot change legacy frame delay protection");
    *immediate = 0;
    DWORD ignored = 0;
    const bool restored = VirtualProtect(immediate, 1, protection, &ignored) != 0;
    const bool flushed = FlushInstructionCache(GetCurrentProcess(), immediate, 1) != 0;
    if (!restored || !flushed) Fail("Cannot finalize legacy frame delay patch");
    legacy_frame_delay_removed = true;
}

void VerifySceneTelemetryLayout() {
    // Original operand guards supplement the full EXE hash and Plugin-SDK ABI.
    constexpr unsigned char hour[] = {0x8A,0x15,0x53,0x01,0xB7,0x00};
    constexpr unsigned char minute[] = {0xA0,0x52,0x01,0xB7,0x00};
    constexpr unsigned char second[] = {0x66,0x8B,0x0D,0x50,0x01,0xB7,0x00};
    constexpr unsigned char running[] = {0xA0,0x51,0xF8,0xB5,0x00,0xC3};
    constexpr unsigned char processing[] = {0xA0,0x52,0xF8,0xB5,0x00,0xC3};
    constexpr unsigned char pad[] = {0x8B,0x44,0x24,0x04,0x69,0xC0,0x34,0x01,0x00,0x00,0x05,0x58,0x34,0xB7,0x00,0xC3};
    constexpr unsigned char controls[] = {0x66,0x83,0xB9,0x0E,0x01,0x00,0x00,0x00};
    constexpr unsigned char focus[] = {0x0F,0xB6,0x05,0x74,0xCD,0xB7,0x00};
    constexpr unsigned char standing[] = {0xF6,0x81,0x6C,0x04,0x00,0x00,0x01};
    RequireHook("CClock hours operand", 0x52D02B, hour, sizeof(hour));
    RequireHook("CClock minutes operand", 0x52D036, minute, sizeof(minute));
    RequireHook("CClock seconds operand", 0x52D041, second, sizeof(second));
    RequireHook("Cutscene running getter", 0x421140, running, sizeof(running));
    RequireHook("Cutscene processing getter", 0x407390, processing, sizeof(processing));
    RequireHook("CPad array/stride", 0x53FB70, pad, sizeof(pad));
    RequireHook("CPad disabled-controls offset", 0x53FB80, controls, sizeof(controls));
    RequireHook("FindPlayerPed focus operand", 0x56E218, focus, sizeof(focus));
    RequireHook("CPed standing flag operand", 0x4BF3F5, standing, sizeof(standing));
}

void Initialize() {
    static_assert(sizeof(void*) == 4, "GTA SA requires an x86 ASI");
    wchar_t executable[32768];
    const auto count = GetModuleFileNameW(nullptr, executable, static_cast<DWORD>(std::size(executable)));
    if (!count || count >= std::size(executable)) Fail("Cannot identify executable");
    const std::wstring path(executable, count);
    const auto slash = path.find_last_of(L"\\/");
    if (slash == std::wstring::npos || _wcsicmp(path.c_str() + slash + 1, L"gta_sa.exe")) Fail("Expected gta_sa.exe");
    runtime_dir = path.substr(0, slash) + L"\\thaw_gta";
    if (!MakeDirectory(runtime_dir)) Fail("Cannot create isolated runtime directory");
    if (reinterpret_cast<std::uintptr_t>(GetModuleHandleW(nullptr)) != 0x400000 || !HashMatches(path.c_str()))
        Fail("Unsupported GTA executable; requires verified 1.0 US image");
    RequireHook("CGame::Process call at 0x53E981", ProcessCall, ProcessBytes, sizeof(ProcessBytes));
    RequireHook("User directory entry at 0x744FB0", UserDirectory, DirectoryBytes, sizeof(DirectoryBytes));
    VerifySceneTelemetryLayout();
    RemoveLegacyFrameDelay();
    if (Read<char>(0xC92368) || Read<std::uint32_t>(0xC8D4C0) > 1)
        Fail("ASI loaded after profile initialization; early loader required");
    const auto profile = runtime_dir + L"\\profile";
    for (wchar_t ch : profile) if (ch < 32 || ch > 126) Fail("Native GTA profile currently requires an ASCII path");
    if (profile.size() + 13 >= sizeof(snapshot.profile_path)) Fail("Native GTA profile path exceeds 127 bytes");
    if (!MakeDirectory(profile) || !MakeDirectory(profile + L"\\Gallery") || !MakeDirectory(profile + L"\\User Tracks"))
        Fail("Cannot create isolated profile folders");
    for (std::size_t i = 0; i < profile.size(); ++i) snapshot.profile_path[i] = static_cast<char>(profile[i]);
    std::strcpy(reinterpret_cast<char*>(0xC92368), snapshot.profile_path);
    std::snprintf(reinterpret_cast<char*>(0xC92268), 256, "%s\\Gallery", snapshot.profile_path);
    std::snprintf(reinterpret_cast<char*>(0xC92168), 256, "%s\\User Tracks", snapshot.profile_path);
    if (!InstallBranch(UserDirectory, LocalUserDirectory, 0xE9, DirectoryBytes, sizeof(DirectoryBytes)))
        Fail("Failed to install isolated profile getter");
    snapshot.capabilities = gta_thaw::Telemetry | gta_thaw::IsolatedProfile;
    std::memcpy(snapshot.magic, "GTATHAW2", 8);
    snapshot.version = gta_thaw::Version; snapshot.byte_size = sizeof(snapshot);
    snapshot.gta_pid = GetCurrentProcessId();
    FILETIME created{}, exited{}, kernel{}, user{};
    if (!GetProcessTimes(GetCurrentProcess(), &created, &exited, &kernel, &user)) Fail("Cannot identify process lifetime");
    snapshot.process_creation_100ns = (static_cast<std::uint64_t>(created.dwHighDateTime) << 32) | created.dwLowDateTime;
    wchar_t mapping_name[80];
    swprintf_s(mapping_name, L"Local\\THAW_GTA_STATE_V2_%lu", GetCurrentProcessId());
    shared_mapping = CreateFileMappingW(INVALID_HANDLE_VALUE, nullptr, PAGE_READWRITE, 0,
        sizeof(gta_thaw::SharedState), mapping_name);
    if (!shared_mapping || GetLastError() == ERROR_ALREADY_EXISTS) Fail("State mapping already exists or cannot be created");
    shared_state = static_cast<gta_thaw::SharedState*>(MapViewOfFile(shared_mapping, FILE_MAP_WRITE, 0, 0, sizeof(gta_thaw::SharedState)));
    if (!shared_state) Fail("Cannot map state transport");
    std::memset(shared_state, 0, sizeof(*shared_state));
    swprintf_s(mapping_name, L"Local\\THAW_GTA_MOVE_ACK_V1_%lu", GetCurrentProcessId());
    movement_ack_mapping = CreateFileMappingW(INVALID_HANDLE_VALUE, nullptr, PAGE_READWRITE, 0,
        sizeof(gta_thaw::movement_ack::SharedAck), mapping_name);
    if (!movement_ack_mapping || GetLastError() == ERROR_ALREADY_EXISTS)
        Fail("Movement acknowledgement mapping already exists or cannot be created");
    movement_ack = static_cast<gta_thaw::movement_ack::SharedAck*>(MapViewOfFile(
        movement_ack_mapping, FILE_MAP_WRITE, 0, 0, sizeof(gta_thaw::movement_ack::SharedAck)));
    if (!movement_ack) Fail("Cannot map movement acknowledgement transport");
    std::memset(movement_ack, 0, sizeof(*movement_ack));
    if (!InstallBranch(ProcessCall, GameProcessHook, 0xE8, ProcessBytes, sizeof(ProcessBytes)))
        Fail("Failed to install game process observer");
    if (const auto error = gta_thaw::pose_preview::Initialize(runtime_dir, &snapshot, ReadGame)) Fail(error);
    if (const auto error = gta_thaw::movement_hold::Initialize(runtime_dir, &snapshot, ReadGame, InstallBranch)) Fail(error);
    if (const auto error = gta_thaw::board_attack::Initialize(runtime_dir, &snapshot)) Fail(error);
    if (const auto error = gta_thaw::vehicle_snapshot::Initialize(runtime_dir, &snapshot)) Fail(error);
    if (const auto error = gta_thaw::weapon_control::Initialize(runtime_dir, &snapshot)) Fail(error);
    if (const auto error = gta_thaw::camera_preview::Initialize(runtime_dir, &snapshot, ReadGame, InstallBranch, HashMatches)) Fail(error);
    if (const auto error = gta_thaw::game_effects::Initialize(runtime_dir, &snapshot)) Fail(error);
    if (const auto error = gta_thaw::hud_preview::Initialize(runtime_dir, &snapshot, ReadGame, InstallBranch)) Fail(error);
    StatusFile("initialized");
}
}

extern "C" __declspec(dllexport) void __cdecl InitializeASI() {
    if (InterlockedCompareExchange(&initialized, 1, 0) != 0) return;
    try { Initialize(); }
    catch (...) { Fail("Adapter initialization exception"); }
}

BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) DisableThreadLibraryCalls(instance);
    // Ultimate ASI Loader invokes InitializeASI after LoadLibrary returns.
    return TRUE;
}
