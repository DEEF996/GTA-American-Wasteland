#pragma once
#include "movement_command_protocol.h"

namespace gta_thaw::movement_hold {
// Independent opt-in transport for original 3D simulation samples.
constexpr std::uint32_t TrajectoryMaxDurationMs=5000;
constexpr float TrajectoryMaxDistance=64.f, TrajectoryMaxSpeed=20.f;
constexpr std::uint32_t InteractiveMaxDurationMs=30000;
// Zero is continuous only with the separate interactive configuration enabled.
constexpr std::uint32_t ContinuousDurationMs=0;
constexpr float InteractiveMaxDistance=256.f, InteractiveMaxSpeed=60.f;
constexpr float RailCaptureMaxAdjustment=2.f;
constexpr float CeilingMaxAdjustment=2.f;
constexpr float BodyPushMaxAdjustment=2.f;
// Transport tolerance for a completed owned source frame, not a claimed
// maximum of any original collision solver. Velocity remains independently bounded.
constexpr float SourceMaxAdjustment=2.f;
constexpr float WallCeilingMaxAdjustment=36.f*.0254f;
// Same 0.0001 inches/s float32 projection residue permitted by the source
// witness. GTA stores metres/s divided by 50; never zero the actual velocity.
constexpr float CeilingMaxRawVerticalSpeed=1.e-4f*.0254f/50.f;
constexpr std::uint32_t GroundContact=1, AirContact=2, RailContact=4, ConstraintContact=5, ContactBits=0x601;
constexpr std::uint32_t BufferingContactFlag=0x100;
constexpr std::uint32_t RailCorrectionFlag=0x200;
constexpr std::uint32_t CeilingCorrectionFlag=0x400;
// Original inclined-ceiling projection is independently witnessed by the
// producer. It must accompany the ceiling flag; flat clearance keeps its
// existing zero-vertical-velocity check.
constexpr std::uint32_t CeilingSlopeCorrectionFlag=0x800;
constexpr std::uint32_t BodyCorrectionFlag=0x1000;
constexpr std::uint32_t SourceAdjustmentFlag=0x2000;
inline bool IsBuffering(std::uint32_t contact) noexcept { return (contact&BufferingContactFlag)!=0; }
inline bool IsRailCorrection(std::uint32_t contact) noexcept { return (contact&RailCorrectionFlag)!=0; }
inline bool IsCeilingCorrection(std::uint32_t contact) noexcept { return (contact&CeilingCorrectionFlag)!=0; }
inline bool IsCeilingSlopeCorrection(std::uint32_t contact) noexcept { return (contact&CeilingSlopeCorrectionFlag)!=0; }
inline bool IsBodyCorrection(std::uint32_t contact) noexcept { return (contact&BodyCorrectionFlag)!=0; }
inline bool IsSourceAdjustment(std::uint32_t contact) noexcept { return (contact&SourceAdjustmentFlag)!=0; }
inline std::uint32_t BaseContact(std::uint32_t contact) noexcept { return contact&~(BufferingContactFlag|RailCorrectionFlag|CeilingCorrectionFlag|CeilingSlopeCorrectionFlag|BodyCorrectionFlag|SourceAdjustmentFlag); }
// Interactive two-phase entry: capture the actual game-thread state before the
// source engine is aligned. This marker never commands position or contact.
constexpr std::uint32_t PrepareContact=0, PrepareMaxMs=500;
constexpr std::uint32_t PrepareAirContact=3;
inline bool IsPreparation(std::uint32_t contact) noexcept { return contact==PrepareContact || contact==PrepareAirContact; }
constexpr std::uintptr_t InAirEventVtable=0x85B3C0;
#pragma pack(push, 1)
struct TrajectoryPacket {
    Packet lease;
    float position[3], heading, velocity[3];
    std::uint32_t contact;
    std::uint64_t source_tick, source_time_us, collision_generation;
    std::uint32_t source_mode, source_state, source_walk_state, source_bailing;
};
#pragma pack(pop)
struct SharedTrajectory { volatile long begin; TrajectoryPacket packet; volatile long end; };
static_assert(sizeof(TrajectoryPacket)==144 && sizeof(SharedTrajectory)==152);
static_assert(offsetof(TrajectoryPacket,source_tick)==104 && offsetof(TrajectoryPacket,source_mode)==128 &&
              offsetof(SharedTrajectory,end)==148);

struct RenderRootSample {
    std::uint64_t request=0,source_tick=0,source_time_us=0;
    float root[16]{};
};
struct RenderRootHistory {
    RenderRootSample previous{},current{};
    void Commit(std::uint64_t request,std::uint64_t tick,std::uint64_t us,const float* root) noexcept {
        if(current.request!=request) previous={};
        else if(current.source_tick==tick && current.source_time_us==us) return;
        else previous=current;
        current.request=request;current.source_tick=tick;current.source_time_us=us;
        std::memcpy(current.root,root,sizeof(current.root));
    }
    bool Get(std::uint64_t request,std::uint64_t tick,std::uint64_t us,float* root) const noexcept {
        if(!request || current.request!=request) return false;
        const auto& sample=current.source_tick==tick && current.source_time_us==us?current:previous;
        if(sample.request!=request || sample.source_tick!=tick || sample.source_time_us!=us) return false;
        std::memcpy(root,sample.root,sizeof(sample.root));return true;
    }
};

inline bool AmbientAirIdentity(std::uintptr_t group,std::uintptr_t intelligence,
                               std::uintptr_t group_ped,std::uintptr_t owned_ped,
                               std::uintptr_t event_vtable,bool valid) noexcept {
    return owned_ped && intelligence && group==intelligence+0x68 && group_ped==owned_ped &&
        event_vtable==InAirEventVtable && !valid;
}

inline std::uint32_t ContactFlags(std::uint32_t flags,std::uint32_t contact) noexcept {
    // Preserve bWasStanding and every unrelated task/vehicle/event flag.
    // GTA has no matching constraint: original THAW owns Rail/Wall/Hang support. Keep CJ
    // non-standing while owned; release resumes GTA flight with retained v.
    return (flags&~ContactBits) | (BaseContact(contact)==GroundContact?1u:0x200u);
}
inline bool SuppressesAmbientFall(std::uint32_t contact,bool continuous_owned=false) noexcept {
    const auto base=BaseContact(contact);
    return base==AirContact || base==RailContact || base==ConstraintContact ||
        (continuous_owned && base==GroundContact);
}
inline bool ValidSourceContext(const TrajectoryPacket& p) noexcept {
    if(p.source_mode>1 || p.source_state>7 || p.source_walk_state>7 || p.source_bailing>1) return false;
    const auto contact=BaseContact(p.contact);
    // Tick zero captures GTA entry, before an original source step has completed.
    if(!p.source_tick) return !p.source_mode && !p.source_state && !p.source_walk_state && !p.source_bailing &&
        (IsPreparation(p.contact) || contact==GroundContact || contact==AirContact);
    constexpr std::uint32_t skating[]={GroundContact,AirContact,ConstraintContact,ConstraintContact,
                                      RailContact,ConstraintContact,ConstraintContact,ConstraintContact};
    constexpr std::uint32_t walking[]={GroundContact,AirContact,ConstraintContact,ConstraintContact,
                                      ConstraintContact,ConstraintContact,ConstraintContact,ConstraintContact};
    return contact==(p.source_mode?walking[p.source_walk_state]:skating[p.source_state]);
}
inline bool ValidTrajectory(const TrajectoryPacket& p,const Snapshot& s,std::uint64_t now,bool interactive=false) noexcept {
    const auto& l=p.lease;
    const bool continuous=interactive && l.duration_ms==ContinuousDurationMs;
    const bool preparing=interactive && IsPreparation(p.contact) && !p.source_tick && !p.source_time_us;
    const auto contact=BaseContact(p.contact);
    if(std::memcmp(l.magic,"GTAMOVE3",8) || l.version!=3 || l.byte_size!=sizeof(p) || !ValidSourceContext(p) ||
       l.pid!=s.gta_pid || l.created!=s.process_creation_100ns || !l.request || !l.sequence ||
       l.tick_ms>now || now-l.tick_ms>(preparing?PrepareMaxMs:MaxAgeMs) || s.tick_ms>now || now-s.tick_ms>MaxAgeMs ||
       !ValidSnapshot(s,l.pid,l.created,now) || !ControlEligible(s) || !(s.status_flags&Foreground) ||
       l.epoch!=s.epoch || l.ped!=s.ped_address || l.ped<0x10000 || l.ped>=0x80000000 || (l.ped&3) ||
       l.area!=s.current_area || l.ped_area!=s.ped_area || l.area>255 || (!continuous && !l.duration_ms) ||
       l.duration_ms>(interactive?InteractiveMaxDurationMs:TrajectoryMaxDurationMs) || !p.collision_generation ||
       (IsBuffering(p.contact) && !continuous) ||
       (IsSourceAdjustment(p.contact) && (!continuous || !p.source_tick ||
        (p.contact&(RailCorrectionFlag|CeilingCorrectionFlag|CeilingSlopeCorrectionFlag|BodyCorrectionFlag)))) ||
       (IsRailCorrection(p.contact) && (!continuous || contact!=RailContact || !p.source_tick)) ||
       (IsBodyCorrection(p.contact) && (!continuous || contact!=GroundContact || !p.source_tick ||
        IsRailCorrection(p.contact) || IsCeilingCorrection(p.contact) || IsCeilingSlopeCorrection(p.contact) ||
        p.source_mode || p.source_state || p.source_bailing!=1)) ||
       (IsCeilingSlopeCorrection(p.contact) && !IsCeilingCorrection(p.contact)) ||
       (IsCeilingCorrection(p.contact) && (!continuous || contact!=AirContact || !p.source_tick ||
        IsRailCorrection(p.contact) || p.source_mode || p.source_state!=1 ||
        (!IsCeilingSlopeCorrection(p.contact) &&
         (p.source_bailing || std::fabs(p.velocity[2])>CeilingMaxRawVerticalSpeed)))) ||
       (!preparing && contact!=GroundContact && contact!=AirContact && contact!=RailContact && contact!=ConstraintContact) ||
       (!continuous && p.source_time_us>static_cast<std::uint64_t>(l.duration_ms)*1000) ||
       (p.source_tick==0)!=(p.source_time_us==0) ||
       !std::isfinite(p.heading) || std::fabs(p.heading)>32.f) return false;
    float speed2=0;
    for(int i=0;i<3;++i) {
        if(!std::isfinite(p.position[i]) || std::fabs(p.position[i])>100000 ||
           !std::isfinite(p.velocity[i])) return false;
        speed2+=p.velocity[i]*p.velocity[i];
    }
    // Raw GTA CPhysical velocity is metres/second divided by 50, irrespective of FPS.
    const float raw_limit=(interactive?InteractiveMaxSpeed:TrajectoryMaxSpeed)/50.f;
    return speed2<=raw_limit*raw_limit+1.e-8f;
}
inline bool SameSource(const TrajectoryPacket& a,const TrajectoryPacket& b) noexcept {
    return !std::memcmp(a.position,b.position,sizeof(a)-offsetof(TrajectoryPacket,position));
}
inline bool SameSourceSample(const TrajectoryPacket& a,const TrajectoryPacket& b) noexcept {
    return !std::memcmp(a.position,b.position,offsetof(TrajectoryPacket,contact)-offsetof(TrajectoryPacket,position)) &&
        (a.contact&~BufferingContactFlag)==(b.contact&~BufferingContactFlag) && a.source_tick==b.source_tick &&
        a.source_time_us==b.source_time_us && a.collision_generation==b.collision_generation &&
        a.source_mode==b.source_mode && a.source_state==b.source_state &&
        a.source_walk_state==b.source_walk_state && a.source_bailing==b.source_bailing;
}
inline bool TrajectoryInBounds(const TrajectoryPacket& p,const float* entry,float entry_heading,
                               const float* previous,std::uint64_t previous_us,bool first,bool interactive=false,
                               const float* entry_velocity=nullptr,std::uint32_t entry_contact=GroundContact,
                               std::uint32_t previous_contact=PrepareContact,std::uint64_t previous_tick=0,
                               std::uint32_t previous_mode=2,std::uint32_t previous_state=8,
                               std::uint32_t previous_bailing=1) noexcept {
    const float radius=interactive?InteractiveMaxDistance:TrajectoryMaxDistance;
    const bool continuous=interactive && p.lease.duration_ms==ContinuousDurationMs;
    if((!continuous && DistanceSquared(p.position,entry)>radius*radius) ||
       p.source_time_us<previous_us) return false;
    if(first) return !p.source_tick && !p.source_time_us && p.contact==entry_contact &&
        (entry_velocity ? !std::memcmp(p.velocity,entry_velocity,12) :
            p.velocity[0]==0 && p.velocity[1]==0 && p.velocity[2]==0) &&
        DistanceSquared(p.position,entry)<=.02f*.02f &&
        std::fabs(HeadingDifference(p.heading,entry_heading))<=.02f;
    const float seconds=static_cast<float>(p.source_time_us-previous_us)*.000001f;
    const float distance=.02f+(interactive?InteractiveMaxSpeed:TrajectoryMaxSpeed)*seconds;
    if(seconds>.100001f) return false;
    const float delta2=DistanceSquared(p.position,previous);
    const bool source_adjustment=IsSourceAdjustment(p.contact);
    if(source_adjustment && (p.source_tick!=previous_tick || p.source_time_us!=previous_us)) {
        // The producer acknowledges the preceding sample before publishing an
        // adjustment. Never use this allowance to bridge skipped source frames.
        const auto elapsed=p.source_time_us-previous_us;
        if(!continuous || IsBuffering(p.contact) ||
           p.contact!=(BaseContact(p.contact)|SourceAdjustmentFlag) ||
           (previous_contact!=GroundContact && previous_contact!=AirContact &&
            previous_contact!=RailContact && previous_contact!=ConstraintContact) ||
           previous_mode>1 || previous_state>7 || previous_bailing>1 ||
           p.source_tick<=previous_tick || p.source_tick-previous_tick!=1 ||
           (elapsed!=16666 && elapsed!=16667)) return false;
    }
    if(delta2<=distance*distance) return true;
    if(source_adjustment) {
        const float adjusted_distance=distance+SourceMaxAdjustment;
        return p.source_tick>previous_tick && p.source_time_us>previous_us &&
            delta2<=adjusted_distance*adjusted_distance;
    }
    // Native rail/wall/hang capture includes a bounded snap, not just velocity integration.
    const bool captures_support=continuous && (p.contact==RailContact || p.contact==ConstraintContact) &&
        previous_contact!=p.contact &&
        (previous_contact==GroundContact || previous_contact==AirContact ||
         previous_contact==RailContact || previous_contact==ConstraintContact) &&
        p.source_tick>previous_tick && p.source_time_us>previous_us;
    const float capture_distance=distance+RailCaptureMaxAdjustment;
    // The producer proved the owned adjacent-node reprojection during an
    // original bail. Ordinary Rail->Rail motion keeps the velocity-only bound.
    const bool rail_correction=continuous && p.contact==(RailContact|RailCorrectionFlag) &&
        previous_contact==RailContact && p.source_tick>previous_tick && p.source_time_us>previous_us;
    // A separate original query witnessed flat-ceiling clearance. Air keeps the
    // ordinary XY bound. Stock Wallride can also push 18+18 inches along its
    // validated wall normal on release; no other Constraint->Air gets this bound.
    const float dx=p.position[0]-previous[0],dy=p.position[1]-previous[1],drop=previous[2]-p.position[2];
    const bool wall_ceiling=previous_contact==ConstraintContact && !previous_mode && previous_state==2 && !previous_bailing;
    const float ceiling_xy=distance+(wall_ceiling?WallCeilingMaxAdjustment:0.f);
    const bool ceiling_correction=continuous && p.contact==(AirContact|CeilingCorrectionFlag) &&
        (previous_contact==AirContact || wall_ceiling) && !p.source_mode && p.source_state==1 && !p.source_bailing &&
        std::fabs(p.velocity[2])<=CeilingMaxRawVerticalSpeed && p.source_tick>previous_tick && p.source_time_us>previous_us &&
        drop>0 && drop<=CeilingMaxAdjustment && dx*dx+dy*dy<=ceiling_xy*ceiling_xy;
    // A distinct original ray and plane-projection witness permits a bounded
    // inclined-ceiling correction. Its normal can retain nonzero vertical
    // velocity; neither that velocity nor the original position is clamped.
    // The same stock correction also runs during an ongoing airborne bail;
    // bail transitions and wall/ground/walking samples keep their usual bounds.
    const float sloped_ceiling_distance=distance+CeilingMaxAdjustment;
    const bool sloped_ceiling=continuous &&
        p.contact==(AirContact|CeilingCorrectionFlag|CeilingSlopeCorrectionFlag) &&
        previous_contact==AirContact && !previous_mode && previous_state==1 &&
        previous_bailing==p.source_bailing && p.source_bailing<=1 &&
        !p.source_mode && p.source_state==1 &&
        p.source_tick>previous_tick && p.source_time_us>previous_us &&
        drop>0 && drop<=CeilingMaxAdjustment && delta2<=sloped_ceiling_distance*sloped_ceiling_distance;
    // Only a witnessed original body depenetration during a continuing ground
    // bail receives this bound; normal movement and new bails do not.
    const float body_distance=distance+BodyPushMaxAdjustment;
    const bool body_correction=continuous && p.contact==(GroundContact|BodyCorrectionFlag) &&
        previous_contact==GroundContact && !previous_mode && !previous_state && previous_bailing==1 &&
        !p.source_mode && !p.source_state && p.source_bailing==1 &&
        p.source_tick>previous_tick && p.source_time_us>previous_us && delta2<=body_distance*body_distance;
    return ((captures_support || rail_correction) && delta2<=capture_distance*capture_distance) ||
        ceiling_correction || sloped_ceiling || body_correction;
}
inline bool PreparationCommitMatches(const TrajectoryPacket& p,const float* position,float heading,
                                      const float* velocity,std::uint32_t contact=GroundContact) noexcept {
    return p.contact==contact && !p.source_tick && !p.source_time_us &&
        !std::memcmp(p.position,position,12) && p.heading==heading && !std::memcmp(p.velocity,velocity,12);
}
// The sole relaxed age applies to the preparation marker. Its absolute deadline
// cannot be renewed, and the first real sample must match the captured state.
inline bool ReadPreparationHeartbeat(ReadResult result,const TrajectoryPacket& incoming,TrajectoryPacket& cached,
                                     const Snapshot& state,const Packet& initial,std::uint64_t started,
                                     std::uint64_t previous_sequence,std::uint64_t now,
                                     const float* position,float heading,const float* velocity,
                                     std::uint32_t contact=GroundContact) noexcept {
    if(result==ReadResult::Cleared || now<started || now-started>=PrepareMaxMs) return false;
    const auto& selected=result==ReadResult::Committed?incoming:cached;
    if(!ValidTrajectory(selected,state,now,true) || !Continues(selected.lease,initial,started,previous_sequence,now,true) ||
       selected.lease.tick_ms<cached.lease.tick_ms || selected.collision_generation!=cached.collision_generation ||
       (selected.lease.sequence==cached.lease.sequence && std::memcmp(&selected,&cached,sizeof(cached)))) return false;
    if(IsPreparation(selected.contact)) {
        if(!SameSource(selected,cached)) return false;
    } else if(!PreparationCommitMatches(selected,position,heading,velocity,contact)) return false;
    if(result==ReadResult::Committed) cached=incoming;
    return true;
}
inline bool ReadTrajectoryHeartbeat(ReadResult result,const TrajectoryPacket& incoming,TrajectoryPacket& cached,
                                    const Snapshot& state,const Packet& initial,std::uint64_t started,
                                    std::uint64_t previous_sequence,std::uint64_t now,
                                    std::uint64_t& advanced_ms,bool interactive=false) noexcept {
    if(result==ReadResult::Cleared) return false;
    const auto& selected=result==ReadResult::Committed?incoming:cached;
    if(IsPreparation(selected.contact) || !ValidTrajectory(selected,state,now,interactive) ||
       !Continues(selected.lease,initial,started,previous_sequence,now,interactive) ||
       selected.lease.tick_ms<cached.lease.tick_ms || selected.collision_generation!=cached.collision_generation ||
       selected.source_tick<cached.source_tick || selected.source_time_us<cached.source_time_us ||
       (selected.lease.sequence==cached.lease.sequence && std::memcmp(&selected,&cached,sizeof(cached)))) return false;
    const bool advanced=selected.source_tick>cached.source_tick;
    const bool buffering=IsBuffering(selected.contact), was_buffering=IsBuffering(cached.contact);
    if(buffering && !SameSourceSample(selected,cached)) return false;
    if(advanced) {
        if(selected.source_time_us<=cached.source_time_us || selected.source_time_us-cached.source_time_us>100000 ||
           selected.source_time_us>(now-started+50)*1000) return false;
    } else if(!SameSourceSample(selected,cached)) return false;
    // Buffering holds an already accepted sample, never invents source progress.
    // Entry must be fresh; after resume the normal progression deadline restarts.
    if(now<advanced_ms || (!was_buffering && now-advanced_ms>MaxAgeMs)) return false;
    if(result==ReadResult::Committed) {
        if(advanced || (was_buffering && !buffering)) advanced_ms=now;
        cached=incoming;
    }
    return true;
}
}
