#pragma once
#include "pose_preview_protocol.h"
#include "camera_preview_math.h"

namespace gta_thaw::pose_preview {
inline bool HasShoulderNodes(const Packet& pose) noexcept {
    unsigned found=0;
    for (const auto& node : pose.node) {
        switch (node.id) {
            case 21: found|=1; break;
            case 22: found|=2; break;
            case 31: found|=4; break;
            case 32: found|=8; break;
            case 301: found|=16; break;
            case 302: found|=32; break;
        }
    }
    return found==63;
}

inline void InverseRigid(const float* matrix, float* inverse) noexcept {
    std::memset(inverse, 0, 16*sizeof(float));
    for (int row=0; row<3; ++row) for (int col=0; col<3; ++col)
        inverse[row*4+col]=matrix[col*4+row];
    for (int col=0; col<3; ++col) for (int k=0; k<3; ++k)
        inverse[12+col]-=matrix[12+k]*inverse[k*4+col];
    inverse[15]=1.f;
}

inline void BlendRigid(const float* a, const float* b, float t, float* output) noexcept {
    if (t<=0.f) { std::memcpy(output,a,16*sizeof(float)); return; }
    if (t>=1.f) { std::memcpy(output,b,16*sizeof(float)); return; }
    camera_preview::Packet first{}, second{};
    std::memcpy(first.forward,a+4,3*sizeof(float));
    std::memcpy(first.up,a+8,3*sizeof(float));
    std::memcpy(second.forward,b+4,3*sizeof(float));
    std::memcpy(second.up,b+8,3*sizeof(float));
    float qa[4], qb[4], q[4];
    camera_preview::Quaternion(first,qa); camera_preview::Quaternion(second,qb);
    camera_preview::Slerp(qa,qb,t,q);
    std::memset(output,0,16*sizeof(float));
    camera_preview::Axes(q,output+4,output+8);
    camera_preview::Cross(output+4,output+8,output);
    for (int i=12; i<15; ++i) output[i]=a[i]+(b[i]-a[i])*t;
    output[15]=1.f;
}

inline bool SameRig(const Packet& a, const Packet& b) noexcept {
    return a.pid==b.pid && a.created==b.created && a.epoch==b.epoch && a.request==b.request &&
        !std::memcmp(&a.area,&b.area,offsetof(Packet,model)-offsetof(Packet,area)) &&
        !std::memcmp(a.board_hash,b.board_hash,sizeof(a.board_hash));
}

inline bool SamePoseSample(const Packet& a, const Packet& b) noexcept {
    return a.source_tick==b.source_tick && a.source_time_us==b.source_time_us &&
        !std::memcmp(a.model,b.model,sizeof(a.model)) &&
        !std::memcmp(a.board_palette,b.board_palette,sizeof(a.board_palette)) &&
        !std::memcmp(a.visual_delta,b.visual_delta,sizeof(a.visual_delta)) && a.board_visible==b.board_visible &&
        a.board_object_id==b.board_object_id && !std::memcmp(a.board_world,b.board_world,sizeof(a.board_world));
}

struct AimOffsetCache {
    Packet rig{};
    std::uint64_t tick_ms=0;
    std::uint32_t toggle=0;
    float offset[3]{};
    bool valid=false;

    void Clear() noexcept { valid=false; }
    bool Publish(const Packet& pose, std::uint32_t current_toggle, std::uint64_t now,
                 const float* chest, const float* paired_root) noexcept {
        Clear();
        if(!pose.request || !pose.source_tick || !Rigid(chest,100000.f) ||
           !Rigid(paired_root,100000.f)) return false;
        float length_squared=0;
        for(int i=0;i<3;++i) {
            offset[i]=chest[12+i]-paired_root[12+i];
            length_squared+=offset[i]*offset[i];
        }
        if(!std::isfinite(length_squared) || length_squared>100.f) return false;
        rig=pose;toggle=current_toggle;tick_ms=now;valid=true;return true;
    }
    bool Get(const Packet& current, std::uint32_t current_toggle, std::uint64_t now,
             std::uint64_t request, std::uint32_t ped, float* output) const noexcept {
        if(!output || !valid || !request || request!=rig.request || ped!=rig.ped ||
           toggle!=current_toggle || now<tick_ms || now-tick_ms>MaxAgeMs || !SameRig(rig,current)) return false;
        std::memcpy(output,offset,sizeof(offset));return true;
    }
};

inline bool BlendPose(const Packet& a, const Packet& b, float t, Packet& output) noexcept {
    output=b;
    if (!SameRig(a,b)) return false;
    if (t>=1.f) return true;
    if (t<=0.f) {
        std::memcpy(output.model,a.model,sizeof(a.model));
        if(a.board_object_id==b.board_object_id) {
            std::memcpy(output.board_palette,a.board_palette,sizeof(a.board_palette));
            std::memcpy(output.board_world,a.board_world,sizeof(a.board_world));
        }
        std::memcpy(output.visual_delta,a.visual_delta,sizeof(a.visual_delta));
        return true;
    }
    // HAnim traversal, not numeric matrix order, defines each parent.
    int stack[BoneCount+1]{-1}, depth=1, parent=-1;
    for (const auto& node : b.node) {
        const int index=node.index;
        float local_a[16], local_b[16], blended[16];
        if (parent<0) {
            BlendRigid(a.model[index],b.model[index],t,output.model[index]);
        } else {
            float inverse[16];
            InverseRigid(a.model[parent],inverse); Multiply(a.model[index],inverse,local_a);
            InverseRigid(b.model[parent],inverse); Multiply(b.model[index],inverse,local_b);
            BlendRigid(local_a,local_b,t,blended);
            Multiply(blended,output.model[parent],output.model[index]);
        }
        if (!Rigid(output.model[index],10.f)) return false;
        if (node.flags&2) stack[depth++]=parent;
        parent=node.flags&1 ? stack[--depth] : index;
    }
    if (b.preview_flags && a.board_object_id==b.board_object_id) for (int i=0; i<4; ++i) {
        BlendRigid(a.board_palette[i],b.board_palette[i],t,output.board_palette[i]);
        if (!Rigid(output.board_palette[i],10000.f)) return false;
    }
    if(a.board_object_id==b.board_object_id && b.board_object_id) {
        BlendRigid(a.board_world,b.board_world,t,output.board_world);
        if(!Rigid(output.board_world,20000.f)) return false;
    }
    BlendRigid(a.visual_delta,b.visual_delta,t,output.visual_delta);
    if(!Rigid(output.visual_delta,10.f)) return false;
    // Do not interpolate visibility or make a newly hidden board flash back.
    output.board_visible=b.board_visible;
    return true;
}

struct RenderHistory {
    struct Sample { Packet pose; float root[16]; bool root_valid; };
    Sample samples[camera_preview::PresentationClock::Capacity]{};
    Packet heartbeat{};
    camera_preview::PresentationClock clock;
    unsigned first=0, last=0;
    bool valid=false;

    void Clear() noexcept { valid=false; clock.Clear(); }
    bool HasRoot(const Packet& p) const noexcept {
        if(!valid) return false;
        const auto& latest=samples[clock.count-1];
        return latest.root_valid && SameRig(p,latest.pose) && SamePoseSample(p,latest.pose);
    }

    bool Present(const Packet& incoming, std::uint64_t now_us, Packet& output, float& alpha,
                 const float* actor_root=nullptr,float time_scale=1.f) noexcept {
        alpha=1.f;
        if (!incoming.request) { Clear(); output=incoming; return true; }
        if (!valid || !SameRig(incoming,heartbeat)) {
            Clear();
        } else {
            if (incoming.sequence<heartbeat.sequence || incoming.tick_ms<heartbeat.tick_ms ||
                incoming.source_tick<heartbeat.source_tick || incoming.source_time_us<heartbeat.source_time_us ||
                (incoming.sequence==heartbeat.sequence && std::memcmp(&incoming,&heartbeat,sizeof(incoming))) ||
                (incoming.source_tick==heartbeat.source_tick && !SamePoseSample(incoming,heartbeat)) ||
                (incoming.source_tick>heartbeat.source_tick && incoming.source_time_us<=heartbeat.source_time_us))
                return false;
        }
        // Advance against the previously available source endpoint before adding
        // a new sample. Buffered time cannot become a catch-up jump on arrival.
        clock.SetScale(time_scale);
        const bool new_sample=!valid || incoming.source_tick>heartbeat.source_tick;
        clock.Advance(new_sample?std::min(now_us,incoming.tick_ms*1000):now_us);
        if(new_sample) {
            bool shifted=false;
            const auto index=clock.Push(incoming.source_time_us,incoming.tick_ms,shifted);
            if(shifted) for(unsigned i=1;i<camera_preview::PresentationClock::Capacity;++i)
                samples[i-1]=samples[i];
            auto& sample=samples[index];
            sample.pose=incoming;
            sample.root_valid=actor_root && Rigid(actor_root,100000.f);
            if(sample.root_valid) std::memcpy(sample.root,actor_root,sizeof(sample.root));
        }
        heartbeat=incoming; valid=true;
        clock.Advance(now_us);
        clock.Bracket(first,last,alpha);
        if (!BlendPose(samples[first].pose,samples[last].pose,alpha,output)) return false;
        output.sequence=incoming.sequence;
        output.tick_ms=incoming.tick_ms;
        return true;
    }

    bool PresentedRoot(float alpha, float* output) const noexcept {
        if(!valid || !samples[last].root_valid || (alpha<1.f && !samples[first].root_valid)) return false;
        BlendRigid(samples[first].root,samples[last].root,alpha,output);
        return Rigid(output,100000.f);
    }
    bool Anchor(const float* observed_anchor, const float* current_actor, float alpha,
                float* output) const noexcept {
        float presented[16], inverse[16], local[16];
        if(!PresentedRoot(alpha,presented) || !Rigid(observed_anchor,100000.f) ||
           !Rigid(current_actor,100000.f)) return false;
        InverseRigid(current_actor,inverse);
        Multiply(observed_anchor,inverse,local);
        Multiply(local,presented,output);
        return Rigid(output,100000.f);
    }
};
}
