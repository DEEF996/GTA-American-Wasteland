#pragma once
#include "bridge_protocol.h"

namespace gta_thaw::pose_preview {
constexpr std::uint32_t BoneCount = 32;
constexpr std::uint64_t MaxAgeMs = 100;
#pragma pack(push, 1)
struct Node { std::int32_t id, index, flags; std::uint32_t frame; };
struct Packet {
    char magic[8];
    std::uint32_t version, byte_size, pid, epoch;
    std::uint64_t created, sequence, tick_ms;
    std::uint32_t area, ped_area, ped, clump, atomic, geometry, hierarchy, skin;
    std::uint32_t matrices, nodes, inverse_bind, atomic_plugin, geometry_plugin;
    std::uint32_t count, root_index, preview_flags;
    Node node[BoneCount];
    // Canonical row-vector affines. RW flags/padding never cross this boundary.
    float bind_inverse[BoneCount][16], model[BoneCount][16];
    unsigned char board_hash[32];
    float board_palette[4][16];
    std::uint64_t request, source_tick, source_time_us;
    // Original model display rotation/pivot after retargeting. The structural
    // root above stays anchored; this transform affects every skinned bone.
    float visual_delta[16];
    std::uint32_t board_visible;
    float board_world[16];
    std::uint32_t board_object_id;
};
#pragma pack(pop)
struct SharedPose { volatile long begin; Packet packet; volatile long end; };
static_assert(sizeof(Packet) == 5168 && sizeof(SharedPose) == 5176);
static_assert(offsetof(Packet, node) == 112 && offsetof(Packet, model) == 2672);
static_assert(offsetof(Packet, request) == 5008 && offsetof(Packet,visual_delta)==5032 &&
    offsetof(Packet,board_world)==5100 && offsetof(SharedPose, end) == 5172);

enum class PacketRead { Cleared, Busy, Ready };
inline PacketRead ReadStatus(long begin, long end, long after) noexcept {
    if (!begin || !after) return PacketRead::Cleared;
    return !(begin & 1) && begin == end && begin == after ? PacketRead::Ready : PacketRead::Busy;
}

struct CachedPacket {
    Packet packet{};
    std::uint64_t lease_request = 0;
    std::uint32_t f7_requests = 0;
    bool valid = false;
};

inline bool SelectPacket(PacketRead read, const Packet& observed, CachedPacket& cache,
                         const Snapshot& state, std::uint64_t lease_request, Packet& output) noexcept {
    if (read == PacketRead::Cleared) { cache.valid = false; return false; }
    if (read == PacketRead::Ready) {
        cache.valid = false;
        output = observed;
        return true;
    }
    if (!cache.valid || cache.f7_requests != state.f7_requests || cache.lease_request != lease_request)
        return false;
    output = cache.packet;
    return true;
}

inline bool Address(std::uint32_t value, std::uint32_t bytes = 4) noexcept {
    return value >= 0x10000 && value <= 0x80000000u - bytes && !(value & 3);
}

inline bool Rigid(const float* m, float position_bound) noexcept {
    for (int i = 0; i < 16; ++i) if (!std::isfinite(m[i])) return false;
    if (m[3] != 0 || m[7] != 0 || m[11] != 0 || m[15] != 1) return false;
    for (int i = 12; i < 15; ++i) if (std::fabs(m[i]) > position_bound) return false;
    for (int i = 0; i < 3; ++i) for (int j = 0; j < 3; ++j) {
        float dot = 0;
        for (int k = 0; k < 3; ++k) dot += m[i*4+k] * m[j*4+k];
        if (std::fabs(dot - (i == j ? 1.f : 0.f)) > .003f) return false;
    }
    const float det = m[0]*(m[5]*m[10]-m[6]*m[9]) - m[1]*(m[4]*m[10]-m[6]*m[8]) +
        m[2]*(m[4]*m[9]-m[5]*m[8]);
    return std::fabs(det - 1.f) < .003f;
}

inline void Multiply(const float* a, const float* b, float* output) noexcept {
    for (int r = 0; r < 4; ++r) for (int c = 0; c < 4; ++c) {
        float value = 0;
        for (int k = 0; k < 4; ++k) value += a[r*4+k] * b[k*4+c];
        output[r*4+c] = value;
    }
}

inline bool ValidPacket(const Packet& p, const Snapshot& s, std::uint64_t now) noexcept {
    if (std::memcmp(p.magic, "GTAPOSE5", 8) || p.version != 5 || p.byte_size != sizeof(p) ||
        p.pid != s.gta_pid || p.created != s.process_creation_100ns || !p.sequence ||
        p.tick_ms > now || now-p.tick_ms > MaxAgeMs || s.tick_ms > now || now-s.tick_ms > MaxAgeMs ||
        !ValidSnapshot(s, p.pid, p.created, now) || !ControlEligible(s) || !(s.status_flags & Foreground) ||
        p.epoch != s.epoch || p.area != s.current_area || p.ped_area != s.ped_area ||
        p.ped != s.ped_address || p.area > 255 || p.count != BoneCount || p.root_index || p.preview_flags > 1 ||
        p.board_visible > 1 || (p.board_visible && !p.preview_flags) || !Rigid(p.visual_delta,10.f) ||
        !Rigid(p.board_world,20000.f) || (p.board_object_id && !p.board_visible) ||
        (!p.request && (p.source_tick || p.source_time_us)) ||
        (p.source_tick == 0) != (p.source_time_us == 0))
        return false;
    if (!p.board_object_id) for (int i=0;i<16;++i)
        if (p.board_world[i] != (i%5==0 ? 1.f : 0.f)) return false;
    const unsigned char zero[32]{};
    if ((std::memcmp(p.board_hash, zero, 32) != 0) != (p.preview_flags == 1)) return false;
    if (p.preview_flags) {
        for (const auto& matrix : p.board_palette) if (!Rigid(matrix,10000.f)) return false;
    } else {
        const unsigned char empty[sizeof(p.board_palette)]{};
        if (std::memcmp(p.board_palette,empty,sizeof(empty))) return false;
    }
    const std::uint32_t addresses[] = {p.ped, p.clump, p.atomic, p.geometry, p.hierarchy, p.skin};
    for (auto address : addresses) if (!Address(address, 4096)) return false;
    if (!Address(p.nodes, BoneCount*16) || !Address(p.matrices, BoneCount*64) ||
        !Address(p.inverse_bind, BoneCount*64) || (p.matrices & 15) || (p.inverse_bind & 15) ||
        p.atomic_plugin < 0x70 || p.atomic_plugin > 4096 || (p.atomic_plugin & 3) ||
        p.geometry_plugin < 0x60 || p.geometry_plugin > 4096 || (p.geometry_plugin & 3)) return false;
    std::uint32_t indices = 0;
    int stack = 1;
    for (std::uint32_t i = 0; i < BoneCount; ++i) {
        const auto& n = p.node[i];
        if (n.id < 0 || n.id > 65535 || n.index < 0 || n.index >= static_cast<int>(BoneCount) ||
            n.flags < 0 || n.flags > 3 || (n.frame && !Address(n.frame, 0xA4)) || !stack) return false;
        const auto bit = 1u << n.index;
        if (indices & bit) return false;
        indices |= bit;
        for (std::uint32_t j = 0; j < i; ++j) if (p.node[j].id == n.id) return false;
        if (n.flags & 2) ++stack;
        if (n.flags & 1) --stack;
        if (!Rigid(p.bind_inverse[i], 10.f) || !Rigid(p.model[i], 10.f)) return false;
    }
    if (stack || p.node[0].id || p.node[0].index || p.node[0].flags) return false;
    // The offline proof anchors the root; the pelvis is the explicit motion joint.
    float root_delta[16];
    Multiply(p.bind_inverse[0], p.model[0], root_delta);
    return std::fabs(root_delta[12]) < .0001f && std::fabs(root_delta[13]) < .0001f &&
        std::fabs(root_delta[14]) < .0001f;
}
}
