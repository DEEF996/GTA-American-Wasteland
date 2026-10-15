#pragma once
#include "bridge_protocol.h"

namespace gta_thaw::camera_preview {
constexpr std::uint64_t MaxAgeMs=100;
inline bool BlocksRequestAfterEnd(std::uint32_t reason,bool restored) noexcept {
    // A missing heartbeat may recover on the same MOVE lease after a clean release.
    // Protocol, projection, native and foreign-camera failures still require a new lease.
    return !restored || (reason!=1 && reason!=2);
}
inline bool NormalResetCandidate(std::uint32_t reason,std::uint32_t mismatch,
                                 bool projection_unchanged) noexcept {
    constexpr std::uint32_t mode_and_zoom=4u|256u|512u;
    return reason==4 && (mismatch&4u) && !(mismatch&~mode_and_zoom) &&
        projection_unchanged;
}
struct FollowPedResetTransition {
    std::uint32_t mismatch, active_mode, desired_mode, target, ped, duration_ms, elapsed_ms;
    float stop_moving, stop_catch_up;
    bool special_interpolation, script_interpolation;
};
inline bool KnownFollowPedReset(const FollowPedResetTransition& t) noexcept {
    // Original StartTransition(FollowPed), coming from our fixed camera. This
    // exception only removes its 1350 ms blend; every ordinary-camera effect,
    // projection, live lease and fresh packet guard is still required by the caller.
    return NormalResetCandidate(4,t.mismatch,true) && t.active_mode==4 && t.desired_mode==4 &&
        t.target==t.ped && t.ped>=0x10000 && t.duration_ms==1350 && t.elapsed_ms<=1350 &&
        t.stop_moving==.05f && t.stop_catch_up==.95f &&
        !t.special_interpolation && !t.script_interpolation;
}
#pragma pack(push,1)
struct Packet {
    char magic[8];
    std::uint32_t version, byte_size, pid, epoch;
    std::uint64_t created, request, sequence, tick_ms;
    std::uint32_t ped, area, ped_area, reserved;
    std::uint64_t source_tick, source_time_us;
    float eye[3], forward[3], up[3], horizontal_fov_degrees;
    float view_window[2], source_aspect;
};
#pragma pack(pop)
struct SharedCamera { volatile long begin; Packet packet; volatile long end; };
static_assert(sizeof(Packet)==140 && sizeof(SharedCamera)==148);
static_assert(offsetof(Packet,source_tick)==72 && offsetof(Packet,eye)==88);
static_assert(offsetof(SharedCamera,end)==144);

inline float Dot(const float* a,const float* b) noexcept {
    return a[0]*b[0]+a[1]*b[1]+a[2]*b[2];
}
inline void Cross(const float* a,const float* b,float* c) noexcept {
    c[0]=a[1]*b[2]-a[2]*b[1]; c[1]=a[2]*b[0]-a[0]*b[2]; c[2]=a[0]*b[1]-a[1]*b[0];
}
inline bool ValidPose(const Packet& p) noexcept {
    for(int i=0;i<3;++i) if(!std::isfinite(p.eye[i]) || std::fabs(p.eye[i])>100000.f ||
        !std::isfinite(p.forward[i]) || !std::isfinite(p.up[i])) return false;
    return std::fabs(Dot(p.forward,p.forward)-1.f)<.005f &&
        std::fabs(Dot(p.up,p.up)-1.f)<.005f && std::fabs(Dot(p.forward,p.up))<.005f &&
        std::isfinite(p.horizontal_fov_degrees) && p.horizontal_fov_degrees>=10.f &&
        p.horizontal_fov_degrees<=150.f && std::isfinite(p.source_aspect) &&
        p.source_aspect>1.f && p.source_aspect<=10.f &&
        std::isfinite(p.view_window[0]) && p.view_window[0]>.01f && p.view_window[0]<10.f &&
        std::isfinite(p.view_window[1]) && p.view_window[1]>.01f && p.view_window[1]<10.f &&
        std::fabs(2.f*std::atan(p.view_window[0])*57.295779513f-p.horizontal_fov_degrees)<.01f;
}
inline bool ProjectionMatchesTarget(const Packet& p,int width,int height) noexcept {
    return width>height && height>0 &&
        std::fabs(p.source_aspect-static_cast<float>(width)/height)<.0001f;
}
inline bool ValidPacket(const Packet& p,const Snapshot& s,std::uint64_t now) noexcept {
    if(std::memcmp(p.magic,"GTACAM02",8) || p.version!=2 || p.byte_size!=sizeof(p) ||
       p.pid!=s.gta_pid || p.created!=s.process_creation_100ns || !p.request || !p.sequence || p.reserved ||
       p.tick_ms>now || now-p.tick_ms>MaxAgeMs || s.tick_ms>now || now-s.tick_ms>MaxAgeMs ||
       !ValidSnapshot(s,p.pid,p.created,now) || !ControlEligible(s) || !(s.status_flags&Foreground) ||
       p.epoch!=s.epoch || p.ped!=s.ped_address || p.ped<0x10000 || p.ped>=0x80000000 || (p.ped&3) ||
       p.area!=s.current_area || p.ped_area!=s.ped_area || p.area>255 ||
       (p.source_tick==0)!=(p.source_time_us==0)) return false;
    float distance2=0;
    for(int i=0;i<3;++i) { const float d=p.eye[i]-s.position[i]; distance2+=d*d; }
    return distance2<=100.f*100.f && ValidPose(p);
}
inline bool SameSession(const Packet& a,const Packet& b) noexcept {
    return a.pid==b.pid && a.created==b.created && a.request==b.request && a.epoch==b.epoch &&
        a.ped==b.ped && a.area==b.area && a.ped_area==b.ped_area;
}
inline bool SameSample(const Packet& a,const Packet& b) noexcept {
    return !std::memcmp(&a.source_tick,&b.source_tick,sizeof(a)-offsetof(Packet,source_tick));
}
inline bool Continues(const Packet& p,const Packet& previous) noexcept {
    return SameSession(p,previous) && p.sequence>=previous.sequence && p.tick_ms>=previous.tick_ms &&
        p.source_tick>=previous.source_tick && p.source_time_us>=previous.source_time_us &&
        (p.sequence!=previous.sequence || !std::memcmp(&p,&previous,sizeof(p))) &&
        (p.source_tick!=previous.source_tick || SameSample(p,previous)) &&
        (p.source_tick==previous.source_tick || p.source_time_us>previous.source_time_us);
}
inline bool NormalResetSampleReady(const Packet& p,const Packet& previous,
                                   const Snapshot& s,std::uint64_t now) noexcept {
    return p.sequence>previous.sequence && Continues(p,previous) && ValidPacket(p,s,now);
}
inline bool NormalResetReady(const Packet& p,const Packet& previous,const Snapshot& s,
                             std::uint64_t now,bool ordinary) noexcept {
    return ordinary && NormalResetSampleReady(p,previous,s,now);
}
}
