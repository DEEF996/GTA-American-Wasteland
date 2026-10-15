#pragma once
#include <cstdint>
#include <cstring>
#include <cmath>
#include "pose_preview_protocol.h"

namespace gta_thaw::board_preview {
#pragma pack(push, 1)
struct Header {
    char magic[8];
    std::uint32_t version, byte_size, mesh_count, vertex_count, index_count, texture_count;
    unsigned char bind_hash[32], rig_hash[32], source_model_hash[32];
    float conversion[16];
};
struct Mesh { std::uint32_t first_vertex, vertices, first_index, indices, texture, address_u, address_v, cutoff; };
struct Texture { std::uint32_t width, height, offset, bytes; };
// Verified RW D3D9 immediate vertex layout; ARGB is the neutral source color gain.
struct Vertex { float position[3], normal[3]; std::uint32_t color; float uv[2]; };
struct SourceVertex { Vertex vertex; unsigned char bone[4]; float weight[4]; };
struct Prefix { Header header; Mesh mesh[5]; Texture texture[5]; SourceVertex vertex[558]; std::uint16_t index[1656]; };
#pragma pack(pop)
constexpr std::uint32_t MaxBytes = 1048576;
static_assert(sizeof(Header) == 192 && sizeof(Vertex) == 36 && sizeof(SourceVertex) == 56 && sizeof(Prefix) == 34992);

inline bool ValidBundle(const unsigned char* data, std::uint32_t size) noexcept {
    if (size < sizeof(Prefix) || size > MaxBytes) return false;
    const auto& p = *reinterpret_cast<const Prefix*>(data);
    const auto& h = p.header;
    if (std::memcmp(h.magic, "GTABRD02", 8) || h.version != 2 || h.byte_size != size ||
        h.mesh_count != 5 || h.vertex_count != 558 || h.index_count != 1656 || h.texture_count != 5) return false;
    const unsigned char zero[32]{};
    constexpr unsigned char SourceModelHash[] = {0xc2,0x96,0x2d,0x8d,0xd7,0x8b,0xf3,0x5c,0x8b,0xad,0xc4,0x06,0x27,0x73,0xb3,0xc9,
        0xc9,0xaf,0x2f,0x05,0x99,0x30,0x40,0x0c,0x28,0x53,0x10,0x7f,0xe1,0xa6,0xc2,0xdc};
    if (!std::memcmp(h.bind_hash, zero, 32) || !std::memcmp(h.rig_hash, zero, 32) ||
        std::memcmp(h.source_model_hash, SourceModelHash, 32)) return false;
    float conversion[16]; std::memcpy(conversion,h.conversion,sizeof(conversion));
    const float scale = std::sqrt(conversion[0]*conversion[0]+conversion[1]*conversion[1]+conversion[2]*conversion[2]);
    if (!std::isfinite(scale) || scale < .0001f || scale > 1.f) return false;
    for (unsigned r = 0; r < 3; ++r) for (unsigned c = 0; c < 3; ++c) conversion[r*4+c] /= scale;
    if (!pose_preview::Rigid(conversion,10.f)) return false;
    std::uint32_t vertices = 0, indices = 0;
    for (const auto& m : p.mesh) {
        if (m.first_vertex != vertices || m.first_index != indices || !m.vertices || m.vertices > 558-vertices ||
            !m.indices || m.indices > 1656-indices || m.indices % 3 || m.texture >= 5 ||
            (m.address_u != 1 && m.address_u != 3) || (m.address_v != 1 && m.address_v != 3) || m.cutoff > 1) return false;
        for (std::uint32_t i = 0; i < m.indices; ++i) if (p.index[indices+i] >= m.vertices) return false;
        vertices += m.vertices;
        indices += m.indices;
    }
    if (vertices != 558 || indices != 1656) return false;
    for (const auto& source : p.vertex) {
        const auto& v = source.vertex;
        for (auto x : v.position) if (!std::isfinite(x) || std::fabs(x) > 10000.f) return false;
        float norm = 0;
        for (auto x : v.normal) { if (!std::isfinite(x)) return false; norm += x*x; }
        if (norm <= .000001f || norm >= 4.f || v.color != 0x80808080) return false;
        for (auto x : v.uv) if (!std::isfinite(x) || std::fabs(x) > 128.f) return false;
        float weights = 0;
        for (unsigned i = 0; i < 4; ++i) {
            const float w = source.weight[i];
            if (!std::isfinite(w) || w < 0 || w > 1 || (w && (source.bone[i] < 48 || source.bone[i] > 51))) return false;
            weights += w;
        }
        if (std::fabs(weights-1.f) > .000001f) return false;
    }
    std::uint32_t offset = sizeof(Prefix);
    for (const auto& t : p.texture) {
        if (!t.width || t.width > 256 || (t.width & (t.width-1)) ||
            !t.height || t.height > 256 || (t.height & (t.height-1)) ||
            t.offset != offset || t.bytes != t.width*t.height*4 || t.bytes > size-offset) return false;
        offset += t.bytes;
    }
    return offset == size;
}

inline bool SkinVertex(const SourceVertex& source, const float palette[4][16], const float* conversion,
                       Vertex& output) noexcept {
    float position[3]{}, normal[3]{};
    for (unsigned b = 0; b < 4; ++b) {
        const float weight = source.weight[b];
        if (!weight) continue;
        if (source.bone[b] < 48 || source.bone[b] > 51) return false;
        const auto m = palette[source.bone[b]-48];
        for (unsigned c = 0; c < 3; ++c) {
            float p = m[12+c], n = 0;
            for (unsigned k = 0; k < 3; ++k) {
                p += source.vertex.position[k]*m[k*4+c];
                n += source.vertex.normal[k]*m[k*4+c];
            }
            position[c] += weight*p;
            normal[c] += weight*n;
        }
    }
    output = source.vertex;
    output.color = 0xffffffff; // Original neutral 128 gain -> RW neutral 255.
    float norm = 0;
    for (unsigned c = 0; c < 3; ++c) {
        float p = conversion[12+c], n = 0;
        for (unsigned k = 0; k < 3; ++k) {
            p += position[k]*conversion[k*4+c];
            n += normal[k]*conversion[k*4+c];
        }
        if (!std::isfinite(p) || std::fabs(p) > 10.f || !std::isfinite(n)) return false;
        output.position[c] = p; output.normal[c] = n; norm += n*n;
    }
    if (!std::isfinite(norm) || norm < 1e-12f) return false;
    const float length = std::sqrt(norm);
    for (auto& n : output.normal) n /= length;
    return true;
}
}
