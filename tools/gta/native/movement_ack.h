#pragma once
#include "bridge_protocol.h"
#include "movement_hold.h"

namespace gta_thaw::movement_ack {
enum Flag : std::uint32_t { Active = 1, Trajectory = 2, Preparing = 4 };
#pragma pack(push, 1)
struct Packet {
    char magic[8];
    std::uint32_t version, byte_size, gta_pid, flags;
    std::uint64_t process_creation_100ns, sequence, tick_ms;
    std::uint64_t request, applied_sequence, source_tick, source_time_us, collision_generation;
    std::uint32_t ped_address, epoch, current_area, ped_area, game_timer, frame_counter;
    std::uint32_t entry_f7_requests, f7_requests;
};
#pragma pack(pop)
struct SharedAck {
    volatile long begin;
    Packet packet;
    volatile long end;
};
static_assert(sizeof(Packet) == 120 && sizeof(SharedAck) == 128);
static_assert(offsetof(Packet, request) == 48 && offsetof(Packet, source_tick) == 64);
static_assert(offsetof(Packet, ped_address) == 88 && offsetof(SharedAck, end) == 124);

inline Packet Applied(const Snapshot& state, const movement_hold::Stats& stats) noexcept {
    Packet value{};
    std::memcpy(value.magic, "GTAMACK1", 8);
    value.version = 1; value.byte_size = sizeof(value); value.gta_pid = state.gta_pid;
    value.flags = (stats.active ? Active : 0U) | (stats.trajectory ? Trajectory : 0U) |
                  (stats.preparing ? Preparing : 0U);
    value.process_creation_100ns = state.process_creation_100ns;
    value.sequence = state.sequence; value.tick_ms = state.tick_ms;
    value.request = stats.request; value.applied_sequence = stats.applied_sequence;
    value.source_tick = stats.source_tick; value.source_time_us = stats.source_time_us;
    value.collision_generation = stats.collision_generation;
    value.ped_address = state.ped_address; value.epoch = state.epoch;
    value.current_area = state.current_area; value.ped_area = state.ped_area;
    value.game_timer = state.game_timer; value.frame_counter = state.frame_counter;
    value.entry_f7_requests = stats.entry_f7_requests; value.f7_requests = state.f7_requests;
    return value;
}
}
