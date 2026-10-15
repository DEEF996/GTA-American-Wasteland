#pragma once
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstring>

namespace gta_thaw {
constexpr std::uint32_t Version = 2;
enum Capability : std::uint32_t {
    Telemetry = 1, IsolatedProfile = 2, VanillaRuntime = 4,
    WorldGeometry = 8, PedPose = 16, OriginalCamera = 32,
};
enum Status : std::uint32_t {
    PedPresent = 1, Paused = 2, InVehicle = 4, Foreground = 8,
    OnFoot = 16, ReadFault = 32,
    TrajectoryOwned = 64, NumLockOn = 128,
    TrajectoryPreparing = 256,
};
#pragma pack(push, 1)
struct Snapshot {
    char magic[8];
    std::uint32_t version, byte_size, gta_pid, capabilities;
    std::uint64_t process_creation_100ns, sequence, tick_ms;
    std::uint32_t ped_address, epoch, current_area, ped_area;
    std::uint32_t game_timer, frame_counter, f7_requests, status_flags;
    float position[3], move_speed[3]; // GTA world coordinates; raw CPhysical velocity.
    float heading, health, time_step; // radians; GTA's unconverted timer step.
    std::uint32_t reserved;
    char profile_path[128];
    // Raw CClock fields: strict elapsed > period rollover permits exactly second 60.
    std::uint32_t clock_hour, clock_minute, clock_second;
    std::uint32_t controls_disabled_mask, cutscene_flags, ped_motion_flags;
};
#pragma pack(pop)
struct SharedState {
    volatile long begin;
    Snapshot snapshot;
    volatile long end;
};
static_assert(sizeof(Snapshot) == 272 && sizeof(SharedState) == 280);
static_assert(offsetof(Snapshot, position) == 80);
static_assert(offsetof(Snapshot, profile_path) == 120);
static_assert(offsetof(Snapshot, clock_hour) == 248 && offsetof(Snapshot, ped_motion_flags) == 268);
static_assert(offsetof(SharedState, snapshot) == 4 && offsetof(SharedState, end) == 276);

enum Cutscene : std::uint32_t { CutsceneRunning = 1, CutsceneProcessing = 2 };
enum PedMotion : std::uint32_t { Standing = 1, InAir = 2, Landing = 4 };

inline bool ValidProfile(const char (&path)[128]) noexcept {
    if (!std::memchr(path, '\0', sizeof(path))) return false;
    if (!((path[0] >= 'A' && path[0] <= 'Z') || (path[0] >= 'a' && path[0] <= 'z')) ||
        path[1] != ':' || path[2] != '\\' || !path[3]) return false;
    for (const char* character = path; *character; ++character)
        if (*character < 32 || *character > 126 || *character == '/') return false;
    const char* segment = path + 3;
    for (const char* character = segment; ; ++character) {
        if (*character != '\\' && *character) continue;
        const auto length = character - segment;
        if (!length || (length == 1 && segment[0] == '.') ||
            (length == 2 && segment[0] == '.' && segment[1] == '.')) return false;
        if (!*character) break;
        segment = character + 1;
    }
    return true;
}

inline bool ValidSnapshot(const Snapshot& s, std::uint32_t pid,
                          std::uint64_t created, std::uint64_t now) noexcept {
    if (std::memcmp(s.magic, "GTATHAW2", 8) || s.version != Version ||
        s.byte_size != sizeof(Snapshot) || s.gta_pid != pid || !pid ||
        !created || s.process_creation_100ns != created || s.reserved ||
        !s.sequence || s.tick_ms > now || now - s.tick_ms > 1000 ||
        (s.capabilities & (Telemetry | IsolatedProfile)) != (Telemetry | IsolatedProfile) ||
        !ValidProfile(s.profile_path) || s.clock_hour > 23 || s.clock_minute > 59 ||
        s.clock_second > 60 || s.controls_disabled_mask > 0xffff ||
        s.cutscene_flags > 3 || s.ped_motion_flags > 7) return false;
    if ((s.status_flags & PedPresent) == 0) return s.ped_address == 0 && s.ped_motion_flags == 0;
    if (!s.ped_address || !s.epoch || !std::isfinite(s.heading) ||
        !std::isfinite(s.health) || !std::isfinite(s.time_step)) return false;
    for (float x : s.position) if (!std::isfinite(x) || std::fabs(x) > 100000) return false;
    for (float x : s.move_speed) if (!std::isfinite(x) || std::fabs(x) > 10000) return false;
    return true;
}

// This is only an observed eligibility gate, not permission to own movement.
inline bool ControlEligible(const Snapshot& s) noexcept {
    return (s.status_flags & (PedPresent | OnFoot)) == (PedPresent | OnFoot) &&
        !(s.status_flags & (Paused | ReadFault | InVehicle)) &&
        s.health > 0 && s.current_area == s.ped_area &&
        !s.controls_disabled_mask && !s.cutscene_flags;
}

// A future runtime must match this session and scene and supply both prerequisites.
// Independent developer preview commands do not satisfy gameplay prerequisites.
inline bool PrerequisitesReady(const Snapshot& s, std::uint64_t created,
                               std::uint32_t epoch, std::uint32_t area,
                               std::uint32_t runtime_capabilities) noexcept {
    constexpr auto required = VanillaRuntime | WorldGeometry | PedPose | OriginalCamera;
    return s.process_creation_100ns == created && s.epoch == epoch && s.current_area == area &&
        ControlEligible(s) &&
        (runtime_capabilities & required) == required;
}
}
