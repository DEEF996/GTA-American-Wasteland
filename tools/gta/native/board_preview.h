#pragma once
#include <string>
#include "board_bundle.h"

namespace gta_thaw::board_preview {
struct Stats { std::uint64_t draws, state_restores, pool_restores, uploads, releases, loads, skin_updates, rejected, faults; std::uint32_t last_error; };
enum class SkinResult { Cached, Updated, Rejected };
inline bool CenterHolster(Vertex* vertices,unsigned count) noexcept {
    if(!count)return false;
    float low[3],high[3];std::memcpy(low,vertices[0].position,12);std::memcpy(high,low,12);
    for(unsigned i=0;i<count;++i)for(int k=0;k<3;++k) {
        const float v=vertices[i].position[k];if(!std::isfinite(v))return false;
        low[k]=std::min(low[k],v);high[k]=std::max(high[k],v);
    }
    int axes[3]={0,1,2};
    for(int a=0;a<3;++a)for(int b=a+1;b<3;++b)if(high[axes[a]]-low[axes[a]]>high[axes[b]]-low[axes[b]])std::swap(axes[a],axes[b]);
    if(high[axes[2]]-low[axes[2]]<.2f||high[axes[2]]-low[axes[2]]>2.f)return false;
    const int mapping[3]={axes[1],axes[0],axes[2]};int inversions=0;
    for(int a=0;a<3;++a)for(int b=a+1;b<3;++b)inversions+=mapping[a]>mapping[b];
    const float sign[3]={inversions&1?1.f:-1.f,-1.f,1.f};
    for(unsigned i=0;i<count;++i) {
        const Vertex before=vertices[i];for(int k=0;k<3;++k) {
            const int source=mapping[k];vertices[i].position[k]=sign[k]*(before.position[source]-(low[source]+high[source])*.5f);
            vertices[i].normal[k]=sign[k]*before.normal[source];
        }
    }return true;
}
inline SkinResult PrepareSkin(const Prefix& bundle, const float palette[4][16],
                              float cached_palette[4][16], bool& valid, Vertex output[558]) noexcept {
    // Transport sequences restart after F7; only the verified palette identifies a skin sample.
    if (valid && !std::memcmp(cached_palette, palette, sizeof(float)*4*16)) return SkinResult::Cached;
    valid = false;
    for (unsigned i = 0; i < 558; ++i)
        if (!SkinVertex(bundle.vertex[i], palette, bundle.header.conversion, output[i]))
            return SkinResult::Rejected;
    std::memcpy(cached_palette, palette, sizeof(float)*4*16);
    valid = true;
    return SkinResult::Updated;
}
void Initialize(const std::wstring& directory);
// Called on the render thread only, after original CPed::Render and inside its pose scope.
void Draw(const pose_preview::Packet& packet, const float* anchor,const float* holster=nullptr) noexcept;
void Idle() noexcept;
Stats GetStats() noexcept;
}
