#pragma once
#include <algorithm>
#include "camera_preview_protocol.h"

namespace gta_thaw::camera_preview {
// Quaternion basis columns are right, forward, up in GTA world coordinates.
inline void Quaternion(const Packet& p,float* q) noexcept {
    float r[3]; Cross(p.forward,p.up,r);
    const float m[3][3]={{r[0],p.forward[0],p.up[0]},
                         {r[1],p.forward[1],p.up[1]}, {r[2],p.forward[2],p.up[2]}};
    const float trace=m[0][0]+m[1][1]+m[2][2];
    if(trace>0) {
        const float s=std::sqrt(trace+1.f)*2.f;
        q[3]=.25f*s; q[0]=(m[2][1]-m[1][2])/s;
        q[1]=(m[0][2]-m[2][0])/s; q[2]=(m[1][0]-m[0][1])/s;
    } else {
        const int i=m[0][0]>m[1][1]?(m[0][0]>m[2][2]?0:2):(m[1][1]>m[2][2]?1:2);
        const int j=(i+1)%3,k=(i+2)%3;
        const float s=std::sqrt(1.f+m[i][i]-m[j][j]-m[k][k])*2.f;
        q[i]=.25f*s; q[j]=(m[j][i]+m[i][j])/s; q[k]=(m[k][i]+m[i][k])/s;
        q[3]=(m[k][j]-m[j][k])/s;
    }
    float length=0; for(int i=0;i<4;++i) length+=q[i]*q[i];
    length=std::sqrt(length); for(int i=0;i<4;++i) q[i]/=length;
}
inline void Slerp(const float* a,const float* b,float t,float* q) noexcept {
    float dot=0; for(int i=0;i<4;++i) dot+=a[i]*b[i];
    const float sign=dot<0?-1.f:1.f;
    dot=std::clamp(std::fabs(dot),0.f,1.f);
    float wa=1.f-t,wb=t;
    if(dot<.9995f) {
        const float angle=std::acos(dot), inverse=1.f/std::sin(angle);
        wa=std::sin((1.f-t)*angle)*inverse; wb=std::sin(t*angle)*inverse;
    }
    float length=0;
    for(int i=0;i<4;++i) { q[i]=wa*a[i]+wb*sign*b[i]; length+=q[i]*q[i]; }
    length=std::sqrt(length); for(int i=0;i<4;++i) q[i]/=length;
}
inline void Axes(const float* q,float* f,float* u) noexcept {
    const float x=q[0],y=q[1],z=q[2],w=q[3];
    f[0]=2.f*(x*y-z*w); f[1]=1.f-2.f*(x*x+z*z); f[2]=2.f*(y*z+x*w);
    u[0]=2.f*(x*z+y*w); u[1]=2.f*(y*z-x*w); u[2]=1.f-2.f*(x*x+y*y);
}
// Shared by camera, skeleton and actor-root presentation. Packet timestamps,
// not the two hooks' first observation times, anchor the one-sample delay.
struct PresentationClock {
    // Retain endpoints through the supported 100 ms source continuity window, without
    // increasing the target delay or the cadence/recovery observation window.
    static constexpr unsigned Capacity=8, CadenceIntervals=4;
    struct Stamp { std::uint64_t source_us, tick_ms; } samples[Capacity]{};
    unsigned count=0;
    std::uint64_t cursor_us=0, wall_us=0, rebase_us=0;
    bool started=false, rebased=false;
    double rate=1., remainder_us=0., mean_period_us=0., pacing_scale=1.;
    unsigned transition_samples=0;

    void Clear() noexcept {
        count=0; started=false; rebased=false; rebase_us=0;
        rate=pacing_scale=1.; remainder_us=0.; mean_period_us=0.; transition_samples=0;
    }
    void SetScale(double scale) noexcept {
        if(!std::isfinite(scale) || scale<.2999 || scale>1.) scale=1.;
        if(scale!=pacing_scale) {
            // Arrival intervals still include the previous ramp speed. Do not
            // reinterpret those intervals as a new, slower producer cadence.
            rate=scale;mean_period_us=0.;transition_samples=CadenceIntervals;
        }
        pacing_scale=scale;
    }
    void Advance(std::uint64_t now_us) noexcept {
        if(started && now_us>wall_us) {
            const auto available=samples[count-1].source_us-cursor_us;
            const double elapsed=(now_us-wall_us)*rate+remainder_us;
            const auto advance=static_cast<std::uint64_t>(std::min(static_cast<double>(available),elapsed));
            cursor_us+=advance;
            remainder_us=advance==available?0.:elapsed-advance;
            wall_us=now_us; // Discard elapsed time while starved; never catch up by extrapolating.
        }
    }
    unsigned Push(std::uint64_t source_us,std::uint64_t tick_ms,bool& shifted) noexcept {
        shifted=false; rebased=false; rebase_us=0;
        if(!count || source_us-samples[count-1].source_us>100000) {
            const auto scale=pacing_scale;
            Clear(); SetScale(scale); cursor_us=source_us; wall_us=tick_ms*1000;
        } else if(!started) {
            const auto interval=source_us-samples[0].source_us;
            wall_us=std::max(tick_ms*1000,samples[0].tick_ms*1000+
                static_cast<std::uint64_t>(interval/pacing_scale));
            started=true;
        }
        const unsigned rate_count=std::min(count,CadenceIntervals), oldest=count-rate_count;
        if(count>=3 && tick_ms>samples[count-1].tick_ms && (pacing_scale<1. || transition_samples)) {
            const double source_period=static_cast<double>(source_us-samples[oldest].source_us)/rate_count;
            const double target=source_period*1.5;
            const double reserve=static_cast<double>(source_us-cursor_us);
            rate=pacing_scale*std::clamp(1.+.08*(reserve-target)/target,.85,1.05);
            if(transition_samples) --transition_samples;
        } else if(count>=3 && tick_ms>samples[count-1].tick_ms) {
            const auto wall_span=(tick_ms-samples[oldest].tick_ms)*1000;
            const auto source_span=source_us-samples[oldest].source_us;
            if(wall_span<=100000/pacing_scale && source_span<=100000) {
                // Several publications average the coarse 15.625 ms packet clock.
                // A small reservoir absorbs jitter without changing source physics.
                const double period=static_cast<double>(wall_span)/rate_count;
                mean_period_us=mean_period_us?mean_period_us*.875+period*.125:period;
                const double source_period=static_cast<double>(source_span)/rate_count;
                const double target=source_period*1.5;
                const double reserve=static_cast<double>(source_us-cursor_us);
                const double cadence=std::min(source_period/mean_period_us,pacing_scale);
                rate=std::clamp(cadence+.08*pacing_scale*(reserve-target)/target,
                                .25*pacing_scale,1.05*pacing_scale);
            } else {
                rate=pacing_scale; mean_period_us=0.;
            }
        }
        if(count==Capacity) {
            for(unsigned i=1;i<count;++i) samples[i-1]=samples[i];
            --count; shifted=true;
            if(cursor_us<samples[0].source_us) {
                rebase_us=samples[0].source_us-cursor_us;
                cursor_us=samples[0].source_us; rebased=true;
            }
        }
        samples[count]={source_us,tick_ms};
        return count++;
    }
    void Bracket(unsigned& first,unsigned& last,float& alpha) const noexcept {
        first=last=count-1; alpha=1.f;
        for(unsigned i=1;i<count;++i) if(cursor_us<samples[i].source_us) {
            first=i-1; last=i;
            alpha=static_cast<float>(cursor_us-samples[first].source_us)/
                static_cast<float>(samples[last].source_us-samples[first].source_us);
            return;
        }
    }
};

inline Packet BlendCamera(const Packet& a,const Packet& b,float t) noexcept {
    Packet result=b;
    for(int i=0;i<3;++i) result.eye[i]=a.eye[i]+(b.eye[i]-a.eye[i])*t;
    for(int i=0;i<2;++i) result.view_window[i]=a.view_window[i]+(b.view_window[i]-a.view_window[i])*t;
    result.horizontal_fov_degrees=2.f*std::atan(result.view_window[0])*57.295779513f;
    float qa[4],qb[4],q[4]; Quaternion(a,qa); Quaternion(b,qb); Slerp(qa,qb,t,q);
    Axes(q,result.forward,result.up);
    return result;
}
inline Packet Interpolate(const Packet& a,const Packet& b,std::uint64_t now) noexcept {
    if(!SameSession(a,b) || b.source_time_us<=a.source_time_us || b.tick_ms<a.tick_ms) return b;
    const auto interval=b.source_time_us-a.source_time_us;
    if(interval>100000 || now<b.tick_ms) return b;
    return BlendCamera(a,b,std::clamp(static_cast<float>(now-b.tick_ms)*1000.f/static_cast<float>(interval),0.f,1.f));
}

struct CameraHistory {
    Packet samples[PresentationClock::Capacity]{}, heartbeat{};
    float roots[PresentationClock::Capacity][3]{};
    bool root_valid[PresentationClock::Capacity]{};
    PresentationClock clock;
    void Clear() noexcept { clock.Clear(); }
    bool Present(const Packet& incoming,std::uint64_t now_us,Packet& output,const float* root=nullptr,
                 float time_scale=1.f) noexcept {
        if(clock.count && !SameSession(incoming,heartbeat)) Clear();
        if(clock.count && !Continues(incoming,heartbeat)) return false;
        clock.SetScale(time_scale);
        const bool new_sample=!clock.count || incoming.source_tick>heartbeat.source_tick;
        clock.Advance(new_sample?std::min(now_us,incoming.tick_ms*1000):now_us);
        if(new_sample) {
            bool shifted=false;
            const auto index=clock.Push(incoming.source_time_us,incoming.tick_ms,shifted);
            if(shifted) for(unsigned i=1;i<PresentationClock::Capacity;++i) {
                samples[i-1]=samples[i]; root_valid[i-1]=root_valid[i];
                std::memcpy(roots[i-1],roots[i],sizeof(roots[i]));
            }
            samples[index]=incoming;
            root_valid[index]=root!=nullptr;
            if(root) std::memcpy(roots[index],root,sizeof(roots[index]));
        }
        heartbeat=incoming;
        clock.Advance(now_us);
        unsigned first,last; float alpha;
        clock.Bracket(first,last,alpha);
        output=BlendCamera(samples[first],samples[last],alpha);
        output.sequence=incoming.sequence; output.tick_ms=incoming.tick_ms;
        return ValidPose(output);
    }
    bool Origin(float* output) const noexcept {
        if(!clock.count) return false;
        unsigned first,last; float alpha;
        clock.Bracket(first,last,alpha);
        if(!root_valid[first] || !root_valid[last]) return false;
        for(unsigned i=0;i<3;++i) output[i]=roots[first][i]+(roots[last][i]-roots[first][i])*alpha;
        return true;
    }
};
}
