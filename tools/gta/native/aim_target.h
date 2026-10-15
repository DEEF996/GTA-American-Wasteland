#pragma once
#include <cmath>
#include <cstdint>
#include <cstring>

namespace gta_thaw::camera_preview {
// CWorld::ProcessLineOfSight / CColPoint, supported x86 GTA executable.
struct AimColPoint { float point[3],padding0,normal[3],padding1;std::uint8_t surface[8];float depth; };
static_assert(sizeof(AimColPoint)==44);
using AimLineFn=bool(__cdecl*)(const float*,const float*,AimColPoint*,std::uint32_t*,bool,bool,bool,bool,bool,bool,bool,bool);
struct AimLineOptions { std::uint32_t* ignore;float* spread;std::uint8_t *dead,*tyres,*bikers; };

inline bool RayPoint(const float* eye,const float* end,const float* point) noexcept {
    float direction[3],offset[3],distance2=0,along=0;
    for(int k=0;k<3;++k) {
        if(!std::isfinite(eye[k])||!std::isfinite(end[k])||!std::isfinite(point[k])||std::fabs(point[k])>100000)return false;
        direction[k]=end[k]-eye[k];offset[k]=point[k]-eye[k];distance2+=direction[k]*direction[k];along+=direction[k]*offset[k];
    }
    if(!std::isfinite(distance2)||distance2<.0001f||distance2>100000000.f)return false;
    const float t=along/distance2;
    if(t<0||t>1.00001f)return false;
    float error2=0;for(int k=0;k<3;++k){const float error=offset[k]-direction[k]*t;error2+=error*error;}
    return error2<=.0025f; // Float storage at GTA coordinates; a hit must lie on this ray.
}
inline bool AimTargetScoped(std::uint32_t ped,const float* eye,const float* end,const AimLineOptions& options,
                            AimLineFn query,float* target,bool& hit) {
    hit=false;if(!ped||!RayPoint(eye,end,end))return false;
    const auto before_ignore=*options.ignore;const auto before_spread=*options.spread;
    const auto before_dead=*options.dead,before_tyres=*options.tyres,before_bikers=*options.bikers;
    AimColPoint collision{};std::uint32_t entity=0;
    __try {
        *options.ignore=ped;*options.spread=0;*options.dead=*options.tyres=*options.bikers=1;
        hit=query(eye,end,&collision,&entity,true,true,true,true,true,false,false,true);
    } __finally {
        *options.ignore=before_ignore;*options.spread=before_spread;
        *options.dead=before_dead;*options.tyres=before_tyres;*options.bikers=before_bikers;
    }
    // Entity exists only for this synchronous query; never retain or assign a target pointer.
    if(hit && (!entity||entity==ped||!RayPoint(eye,end,collision.point)))return false;
    std::memcpy(target,hit?collision.point:end,12);return true;
}
}
