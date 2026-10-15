#pragma once
#include "bridge_protocol.h"

namespace gta_thaw::movement_hold {
constexpr std::uint64_t MaxAgeMs = 100, MaxDurationMs = 2000;
constexpr std::uint32_t CollisionBypass = 0x20000;
constexpr std::uint32_t CollisionForceBypass = 0x4;
#pragma pack(push, 1)
struct Packet {
    char magic[8];
    std::uint32_t version, byte_size, pid, epoch;
    std::uint64_t created, request, sequence, tick_ms;
    std::uint32_t ped, area, ped_area, duration_ms;
};
#pragma pack(pop)
struct SharedHold { volatile long begin; Packet packet; volatile long end; };
static_assert(sizeof(Packet) == 72 && sizeof(SharedHold) == 80);
static_assert(offsetof(Packet, ped) == 56 && offsetof(SharedHold, end) == 76);
inline bool NewRequest(std::uint64_t request, std::uint64_t last) noexcept { return request > last; }
inline bool SameFiniteVelocity(const float* current,const float* expected) noexcept {
    // GTA can normalize the sign of zero without changing the owned velocity.
    for(int i=0;i<3;++i)
        if(!std::isfinite(current[i]) || !std::isfinite(expected[i]) || current[i]!=expected[i]) return false;
    return true;
}
enum class ReadResult : std::uint32_t { Cleared = 1, Busy = 2, Committed = 3 };
inline ReadResult ClassifyRead(long first, long end, long last) noexcept {
    if (!first || !last) return ReadResult::Cleared;
    return (first & 1) || first != end || first != last ? ReadResult::Busy : ReadResult::Committed;
}

inline bool ValidPacket(const Packet& p, const Snapshot& s, std::uint64_t now) noexcept {
    return !std::memcmp(p.magic, "GTAHOLD1", 8) && p.version == 1 && p.byte_size == sizeof(p) &&
        p.pid == s.gta_pid && p.created == s.process_creation_100ns && p.request && p.sequence &&
        p.tick_ms <= now && now-p.tick_ms <= MaxAgeMs && s.tick_ms <= now && now-s.tick_ms <= MaxAgeMs &&
        ValidSnapshot(s,p.pid,p.created,now) && ControlEligible(s) && (s.status_flags & Foreground) &&
        (s.ped_motion_flags & Standing) && p.epoch == s.epoch && p.ped == s.ped_address &&
        p.ped >= 0x10000 && p.ped < 0x80000000 && !(p.ped & 3) &&
        p.area == s.current_area && p.ped_area == s.ped_area && p.area <= 255 &&
        p.duration_ms > 0 && p.duration_ms <= MaxDurationMs;
}

// Only the interactive MOVE2 caller accepts a zero-duration continuous lease.
inline bool Continues(const Packet& p, const Packet& initial, std::uint64_t started,
                      std::uint64_t previous_sequence, std::uint64_t now, bool continuous=false) noexcept {
    return now >= started && ((continuous && !initial.duration_ms) || now-started < initial.duration_ms) &&
        p.request == initial.request && p.pid == initial.pid && p.created == initial.created &&
        p.epoch == initial.epoch && p.ped == initial.ped && p.area == initial.area &&
        p.ped_area == initial.ped_area && p.duration_ms == initial.duration_ms &&
        p.sequence >= previous_sequence;
}

inline bool ReadHeartbeat(ReadResult result, const Packet& incoming, Packet& cached,
                          const Snapshot& state, const Packet& initial, std::uint64_t started,
                          std::uint64_t previous_sequence, std::uint64_t now) noexcept {
    if (result == ReadResult::Cleared) return false;
    const Packet& selected = result == ReadResult::Committed ? incoming : cached;
    if (!ValidPacket(selected,state,now) || !Continues(selected,initial,started,previous_sequence,now))
        return false;
    if (result == ReadResult::Committed) cached = incoming;
    return true;
}
}
