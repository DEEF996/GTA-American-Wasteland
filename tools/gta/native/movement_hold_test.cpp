#include <cassert>
#include <cstdio>
#include <initializer_list>
#include "movement_hold_scope.h"
#include "movement_command_protocol.h"
#include "movement_trajectory_protocol.h"
#include "movement_air_entry.h"

using namespace gta_thaw;
using namespace gta_thaw::movement_hold;

void MutateUnrelated(void* context) {
    auto flags=static_cast<std::uint32_t*>(context);
    assert(*flags&CollisionBypass);
    *flags|=0x400;
}
void FailDuringTick(void* context) { MutateUnrelated(context); RaiseException(0xE0424243,0,0,nullptr); }
bool Same(void*) { return true; }
bool Replaced(void*) { return false; }
void InspectCollisionForce(void* context) {
    auto& flags=*static_cast<std::uint32_t*>(context);
    assert((flags&(CollisionBypass|CollisionForceBypass))==(CollisionBypass|CollisionForceBypass));
    flags|=0x400;
}
void FailCollisionForce(void* context) {
    InspectCollisionForce(context);RaiseException(0xE0424246,0,0,nullptr);
}
void CollisionForceScopeTest() {
    constexpr auto mask=CollisionBypass|CollisionForceBypass;
    std::uint64_t sets=0,restores=0;
    for(const auto original : {0x20u,0x20u|CollisionForceBypass}) {
        auto flags=original;
        CollisionScoped(&flags,&flags,InspectCollisionForce,Same,sets,restores,mask);
        assert(flags==(original|0x400));
        flags=original;bool caught=false;
        __try { CollisionScoped(&flags,&flags,FailCollisionForce,Same,sets,restores,mask); }
        __except(GetExceptionCode()==0xE0424246?EXCEPTION_EXECUTE_HANDLER:EXCEPTION_CONTINUE_SEARCH) { caught=true; }
        assert(caught && flags==(original|0x400));
    }
    assert(sets==4 && restores==4);
    std::uint32_t replaced=0;
    CollisionScoped(&replaced,&replaced,InspectCollisionForce,Replaced,sets,restores,mask);
    assert(replaced==(mask|0x400) && restores==4); // Never restore onto a replaced actor.
    assert(SuppressesAmbientFall(GroundContact,true));
    assert(SuppressesAmbientFall(GroundContact|BufferingContactFlag,true));
    assert(!SuppressesAmbientFall(GroundContact,false));
    assert(!SuppressesAmbientFall(PrepareContact,true) && !SuppressesAmbientFall(PrepareAirContact,true));
}
void InspectMaskedPad(void* context) {
    auto pad=static_cast<unsigned char*>(context);
    for(std::size_t i=0;i<PadInputBytes;++i) assert(pad[i]==0);
    pad[96]=0xA7; // A real field outside NewState/OldState remains writable.
}
void FailMaskedPad(void* context) { InspectMaskedPad(context); RaiseException(0xE0424244,0,0,nullptr); }
void NewerPadWriter(void* context) { InspectMaskedPad(context); static_cast<unsigned char*>(context)[7]=0x55; }

struct ControllerSample {
    float matrix[16]{},headings[2]{},velocity[3]{},angular_velocity[3]{};
    std::uint32_t contact=0x200;
    float health=100;
    bool allow_restore=true,called=false;
    ControllerMovementMemory Memory() {
        return {reinterpret_cast<unsigned char*>(matrix),headings,velocity,angular_velocity,&contact};
    }
};
void NativeControllerWrite(void* context) {
    auto& sample=*static_cast<ControllerSample*>(context);
    sample.called=true;
    for(float& value : sample.matrix) value+=1;
    sample.headings[0]=2;sample.headings[1]=3;
    sample.velocity[0]=4;sample.angular_velocity[2]=5;
    sample.contact=0x10000001;sample.health=75;
}
bool RevalidateController(void* context) {
    const auto& sample=*static_cast<ControllerSample*>(context);
    assert(sample.called && sample.health==75);
    return sample.allow_restore;
}
void InterruptedControllerWrite(void* context) {
    NativeControllerWrite(context);
    RaiseException(0xE0424245,0,0,nullptr);
}
void ControllerMovementTest() {
    ControllerSample sample;
    sample.matrix[0]=sample.matrix[5]=sample.matrix[10]=1;sample.matrix[15]=7;
    sample.matrix[12]=10;sample.velocity[0]=.25f;sample.angular_velocity[2]=.1f;
    const auto original=sample;
    std::uint64_t restores=0;
    assert(ControllerMovementScoped(sample.Memory(),&sample,NativeControllerWrite,RevalidateController,restores));
    for(int row=0;row<4;++row) assert(!std::memcmp(sample.matrix+row*4,original.matrix+row*4,12));
    assert(sample.matrix[15]==8); // Matrix padding is outside owned movement.
    assert(!std::memcmp(sample.headings,original.headings,sizeof(sample.headings)));
    assert(!std::memcmp(sample.velocity,original.velocity,sizeof(sample.velocity)));
    assert(!std::memcmp(sample.angular_velocity,original.angular_velocity,sizeof(sample.angular_velocity)));
    assert(sample.contact==0x10000200 && sample.health==75 && restores==1);
    sample=original;sample.allow_restore=false;
    assert(!ControllerMovementScoped(sample.Memory(),&sample,NativeControllerWrite,RevalidateController,restores));
    assert(sample.matrix[12]==11 && sample.velocity[0]==4 && sample.angular_velocity[2]==5);
    assert(sample.contact==0x10000001 && sample.health==75 && restores==1);
    sample=original;
    bool caught=false;
    __try { ControllerMovementScoped(sample.Memory(),&sample,InterruptedControllerWrite,RevalidateController,restores); }
    __except(GetExceptionCode()==0xE0424245?EXCEPTION_EXECUTE_HANDLER:EXCEPTION_CONTINUE_SEARCH) { caught=true; }
    assert(caught && sample.velocity[0]==4 && sample.health==75 && restores==1);
    assert(MovementHealthEligible(75,100,true) && !MovementHealthEligible(75,100,false));
    assert(MovementHealthEligible(100,100,false) && MovementHealthEligible(150,100,false));
    assert(!MovementHealthEligible(0,100,true) && !MovementHealthEligible(-1,100,true));
    assert(!MovementHealthEligible(INFINITY,100,true) && !MovementHealthEligible(NAN,100,true));
}

void HitReactionOwnershipTest() {
    assert(KnownContinuousHitReaction(true,0,BeHitVtable,0,0,0,0,true));
    assert(KnownContinuousHitReaction(true,0,BeHitVtable,0,1,1,3,true));
    // Entry/preparation, bounded samples and unowned actors never get this exception.
    assert(!KnownContinuousHitReaction(false,0,BeHitVtable,0,0,1,0,true));
    for(int slot=-1;slot<11;++slot) if(slot!=0)
        assert(!KnownContinuousHitReaction(true,slot,BeHitVtable,0,0,1,0,true));
    for(auto vtable : {std::uintptr_t(0),std::uintptr_t(0x8708EC),BeHitVtable+4})
        assert(!KnownContinuousHitReaction(true,0,vtable,0,0,1,0,true));
    assert(!KnownContinuousHitReaction(true,0,BeHitVtable,0x10000,0,1,0,true));
    assert(!KnownContinuousHitReaction(true,0,BeHitVtable,0,2,1,0,true));
    assert(!KnownContinuousHitReaction(true,0,BeHitVtable,0,0,2,0,true));
    assert(!KnownContinuousHitReaction(true,0,BeHitVtable,0,0,1,4,true));
    assert(!KnownContinuousHitReaction(true,0,BeHitVtable,0,0,1,0,false));
    assert(!MovementHealthEligible(0,100,true)); // Task identity cannot allow death.
}

void VelocityOwnershipTest() {
    const float expected[]={0.f,-0.f,.25f};
    float current[]={-0.f,0.f,.25f};
    assert(SameFiniteVelocity(current,expected));
    for(int axis=0;axis<3;++axis) {
        std::memcpy(current,expected,sizeof(current));
        current[axis]=std::nextafter(expected[axis],1.f);
        assert(!SameFiniteVelocity(current,expected));
        for(float invalid : {NAN,INFINITY,-INFINITY}) {
            current[axis]=invalid;
            assert(!SameFiniteVelocity(current,expected));
            assert(!SameFiniteVelocity(expected,current));
            assert(!SameFiniteVelocity(current,current));
        }
    }
}

void RailCaptureBoundsTest() {
    const float previous[3]{};
    TrajectoryPacket packet{};
    packet.source_tick=2;packet.source_time_us=33334;packet.contact=RailContact;
    packet.position[0]=2.f;
    const auto accepts=[&](const TrajectoryPacket& p,std::uint32_t previous_contact=AirContact,
                           std::uint64_t tick=1,std::uint64_t us=16667,bool interactive=true,bool first=false) {
        return TrajectoryInBounds(p,previous,0,previous,us,first,interactive,nullptr,GroundContact,previous_contact,tick);
    };
    assert(accepts(packet));
    assert(accepts(packet,GroundContact) && accepts(packet,ConstraintContact));
    assert(!accepts(packet,RailContact) && !accepts(packet,PrepareContact));
    assert(!accepts(packet,AirContact|BufferingContactFlag));
    assert(!accepts(packet,AirContact,2) && !accepts(packet,AirContact,3));
    assert(!accepts(packet,AirContact,1,33334) && !accepts(packet,AirContact,1,33335));
    assert(!accepts(packet,AirContact,1,16667,false));
    assert(!accepts(packet,AirContact,1,16667,true,true));
    auto bad=packet;bad.lease.duration_ms=30000;assert(!accepts(bad));
    bad=packet;bad.source_time_us=116669;assert(!accepts(bad));
    for(auto contact : {GroundContact,AirContact,RailContact|BufferingContactFlag}) {
        bad=packet;bad.contact=contact;assert(!accepts(bad));
    }
    const float regular_distance=.02f+InteractiveMaxSpeed*(16667.f*.000001f);
    bad=packet;bad.position[0]=regular_distance;
    assert(accepts(bad,RailContact)); // Ordinary riding keeps the unchanged velocity bound.
    bad.position[0]=std::nextafter(regular_distance,INFINITY);
    assert(!accepts(bad,RailContact));
    bad=packet;bad.position[0]=regular_distance+RailCaptureMaxAdjustment;
    assert(accepts(bad));
    bad.position[0]=std::nextafter(bad.position[0],INFINITY);assert(!accepts(bad));
    bad.position[0]=NAN;assert(!accepts(bad));
    bad.position[0]=INFINITY;assert(!accepts(bad));
}

void ConstraintCaptureBoundsTest() {
    const float previous[]={2228.66309f,-1269.33643f,26.6940441f};
    TrajectoryPacket packet{};
    packet.source_tick=522;packet.source_time_us=8700000;packet.contact=ConstraintContact;
    const float captured[]={2228.70752f,-1269.67871f,28.6909714f};
    std::memcpy(packet.position,captured,sizeof(captured));
    const auto accepts=[&](const TrajectoryPacket& p,std::uint32_t prior=AirContact,bool interactive=true) {
        return TrajectoryInBounds(p,previous,0,previous,8683333,false,interactive,nullptr,GroundContact,prior,521);
    };
    assert(accepts(packet) && accepts(packet,GroundContact) && accepts(packet,RailContact));
    assert(!accepts(packet,ConstraintContact) && !accepts(packet,PrepareContact));
    assert(!accepts(packet,AirContact|BufferingContactFlag) && !accepts(packet,AirContact,false));
    auto bad=packet;bad.contact|=BufferingContactFlag;assert(!accepts(bad));
    bad=packet;bad.source_tick=521;assert(!accepts(bad));
    bad=packet;bad.source_time_us=8683333;assert(!accepts(bad));
    bad=packet;bad.lease.duration_ms=30000;assert(!accepts(bad));
    bad=packet;bad.position[2]+=3;assert(!accepts(bad));
}

void CeilingCorrectionTest(Snapshot state,TrajectoryPacket packet) {
    packet.lease.duration_ms=0;
    packet.source_mode=0;packet.source_state=1;packet.source_bailing=0;
    packet.contact=AirContact|CeilingCorrectionFlag;
    packet.source_tick=962;packet.source_time_us=16033333;
    packet.velocity[0]=-.348781526f;packet.velocity[1]=-.0218849704f;packet.velocity[2]=0;
    const float previous[]={989.574097f,-1359.91626f,15.9097853f};
    const float corrected[]={989.283447f,-1359.93457f,14.3801594f};
    std::memcpy(packet.position,corrected,sizeof(corrected));
    const auto accepts=[&](const TrajectoryPacket& p,std::uint32_t prior=AirContact,
                           std::uint64_t tick=961,std::uint64_t us=16016667,bool interactive=true) {
        return TrajectoryInBounds(p,previous,0,previous,us,false,interactive,nullptr,GroundContact,prior,tick);
    };
    assert(ValidTrajectory(packet,state,1000,true) && accepts(packet));
    auto bad=packet;bad.contact=AirContact;assert(!accepts(bad));
    for(auto prior : {GroundContact,RailContact,ConstraintContact,AirContact|BufferingContactFlag})
        assert(!accepts(packet,prior));
    assert(!accepts(packet,AirContact,962) && !accepts(packet,AirContact,961,16033333));
    assert(!accepts(packet,AirContact,961,16016667,false));
    bad=packet;bad.position[2]=previous[2]+1.5f;assert(!accepts(bad));
    bad=packet;bad.position[2]=previous[2]-2.001f;assert(!accepts(bad));
    bad=packet;bad.position[0]=previous[0]+1.1f;assert(!accepts(bad));
    bad=packet;bad.contact|=BufferingContactFlag;
    assert(ValidTrajectory(bad,state,1000,true) && SameSourceSample(bad,packet) && !accepts(bad));
    for(int invalid=0;invalid<7;++invalid) {
        bad=packet;
        if(invalid==0) bad.source_mode=1;
        if(invalid==1) bad.source_state=0;
        if(invalid==2) bad.source_bailing=1;
        if(invalid==3) bad.velocity[2]=.001f;
        if(invalid==4) bad.contact=GroundContact|CeilingCorrectionFlag;
        if(invalid==5) bad.contact|=RailCorrectionFlag;
        if(invalid==6) bad.lease.duration_ms=30000;
        assert(!ValidTrajectory(bad,state,1000,true) && !accepts(bad));
    }
    assert(!ValidTrajectory(packet,state,1000,false));
    assert(BaseContact(packet.contact)==AirContact && ContactFlags(0,packet.contact)==0x200);
    // Actual original wallride+ceiling proof: 36 inches off the wall and a
    // downward clearance, while the next sample remains genuinely Air.
    const float wall_previous[]={58.05511856f*.0254f,68.82060242f*.0254f,201.51052856f*.0254f};
    const float wall_after[]={22.05511856f*.0254f,78.84983063f*.0254f,151.34646606f*.0254f};
    std::memcpy(packet.position,wall_after,sizeof(wall_after));
    const auto wall_accepts=[&](const TrajectoryPacket& p,std::uint32_t mode=0,std::uint32_t source=2,
                                std::uint32_t bail=0,std::uint32_t contact=ConstraintContact) {
        return TrajectoryInBounds(p,wall_previous,0,wall_previous,16016667,false,true,nullptr,
                                  GroundContact,contact,961,mode,source,bail);
    };
    assert(wall_accepts(packet));
    bad=packet;bad.contact=AirContact;assert(!wall_accepts(bad));
    for(auto source : {0u,1u,3u,4u,5u,6u,7u}) assert(!wall_accepts(packet,0,source));
    assert(!wall_accepts(packet,1) && !wall_accepts(packet,0,2,1));
    assert(!wall_accepts(packet,0,2,0,GroundContact) && !wall_accepts(packet,0,2,0,RailContact));
    bad=packet;bad.position[0]=wall_previous[0]-2.f;assert(!wall_accepts(bad));
    bad=packet;bad.position[2]=wall_previous[2]-2.01f;assert(!wall_accepts(bad));
    bad=packet;bad.contact|=BufferingContactFlag;assert(!wall_accepts(bad));
}

void CeilingProjectionResidueTest(Snapshot state,TrajectoryPacket packet) {
    // Recorded failures in attempt-0004 sessions 50/55/59. Each original
    // Wallride->Air sample retained a tiny float32 vertical projection residue.
    struct Fixture {float before[3],after[3],velocity[3];std::uint64_t tick,us,prior_us;};
    const Fixture fixtures[]={
        {{1951.682025f,-2023.9402625f,16.4791492643f},{1952.596425f,-2023.6360578f,15.6818707975f},
         {0.f,.365663199707f,3.1005859375e-8f},149,2483333,2466667},
        {{1951.682025f,-2021.5437328f,16.3450857755f},{1952.596425f,-2021.847540625f,15.6818704435f},
         {0.f,-.365401417236f,3.1005859375e-8f},556,9266667,9250000},
        {{1951.682025f,-2015.7060984f,16.6350587736f},{1952.596425f,-2015.4693625f,15.6818704435f},
         {0.f,.284387757568f,1.55029296875e-8f},299,4983333,4966667}
    };
    packet.lease.duration_ms=0;packet.source_mode=0;packet.source_state=1;packet.source_bailing=0;
    packet.contact=AirContact|CeilingCorrectionFlag;
    for(const auto& f:fixtures) {
        std::memcpy(packet.position,f.after,sizeof(f.after));
        std::memcpy(packet.velocity,f.velocity,sizeof(f.velocity));
        packet.source_tick=f.tick;packet.source_time_us=f.us;
        const auto accepts=[&](const TrajectoryPacket& p) {
            return ValidTrajectory(p,state,1000,true) &&
                TrajectoryInBounds(p,f.before,0,f.before,f.prior_us,false,true,nullptr,
                                   GroundContact,ConstraintContact,f.tick-1,0,2,0);
        };
        assert(accepts(packet));
        assert(!std::memcmp(packet.velocity,f.velocity,sizeof(f.velocity)));
        auto bad=packet;bad.contact=AirContact;assert(!accepts(bad));
        auto negative=packet;negative.velocity[2]=-negative.velocity[2];assert(accepts(negative));
        for(float vertical:{CeilingMaxRawVerticalSpeed*1.01f,-CeilingMaxRawVerticalSpeed*1.01f,
                            .00001f,-.00001f,.01f,-.01f}) {
            bad=packet;bad.velocity[2]=vertical;assert(!accepts(bad));
        }
    }
}

void SlopedCeilingCorrectionTest(Snapshot state,TrajectoryPacket packet) {
    // Live final02 tick7190->7191; reproduced by the original ceiling function
    // against the actual GTA roof. Vertical velocity remains +17.539in/s.
    const float previous[]={2751.85901875f,-1426.26724921875f,30.09859234292f};
    const float corrected[]={2751.85901875f,-1425.8696796875f,28.61011265542f};
    const float velocity[]={1.10250261575e-5f,.03641297894287f,.0089099842453f};
    packet.lease.duration_ms=0;
    packet.source_mode=0;packet.source_state=1;packet.source_bailing=0;
    packet.contact=AirContact|CeilingCorrectionFlag|CeilingSlopeCorrectionFlag;
    packet.source_tick=7191;packet.source_time_us=119850000;
    std::memcpy(packet.position,corrected,sizeof(corrected));
    std::memcpy(packet.velocity,velocity,sizeof(velocity));
    const auto accepts=[&](const TrajectoryPacket& p,std::uint32_t prior=AirContact,
                           std::uint32_t mode=0,std::uint32_t source=1,std::uint32_t bail=0,
                           std::uint64_t tick=7190,std::uint64_t us=119833333,bool interactive=true) {
        return ValidTrajectory(p,state,1000,interactive) &&
            TrajectoryInBounds(p,previous,0,previous,us,false,interactive,nullptr,
                               GroundContact,prior,tick,mode,source,bail);
    };
    assert(accepts(packet));
    assert(!std::memcmp(packet.position,corrected,sizeof(corrected)) &&
           !std::memcmp(packet.velocity,velocity,sizeof(velocity)));
    assert(BaseContact(packet.contact)==AirContact && ContactFlags(0,packet.contact)==0x200);
    assert(SuppressesAmbientFall(packet.contact));
    auto bad=packet;bad.contact=AirContact;assert(!accepts(bad));
    bad=packet;bad.contact=AirContact|CeilingCorrectionFlag;assert(!accepts(bad));
    bad=packet;bad.contact=AirContact|CeilingSlopeCorrectionFlag;
    assert(!ValidTrajectory(bad,state,1000,true));
    for(auto prior:{GroundContact,RailContact,ConstraintContact,PrepareContact,AirContact|BufferingContactFlag})
        assert(!accepts(packet,prior));
    assert(!accepts(packet,AirContact,1) && !accepts(packet,AirContact,0,1,1));
    for(auto source:{0u,2u,3u,4u,5u,6u,7u}) assert(!accepts(packet,AirContact,0,source));
    assert(!accepts(packet,AirContact,0,1,0,7191) &&
           !accepts(packet,AirContact,0,1,0,7190,119850000));
    assert(!accepts(packet,AirContact,0,1,0,7190,119833333,false));
    for(int invalid=0;invalid<12;++invalid) {
        bad=packet;
        if(invalid==0) bad.source_mode=1;
        if(invalid==1) bad.source_state=0;
        if(invalid==2) bad.source_bailing=1;
        if(invalid==3) bad.velocity[2]=1.21f;
        if(invalid==4) bad.contact=GroundContact|CeilingCorrectionFlag|CeilingSlopeCorrectionFlag;
        if(invalid==5) bad.contact|=RailCorrectionFlag;
        if(invalid==6) bad.lease.duration_ms=30000;
        if(invalid==7) bad.position[2]=previous[2]+1.5f;
        if(invalid==8) bad.position[2]=previous[2]-2.001f;
        if(invalid==9) bad.position[0]=previous[0]+3.f;
        if(invalid==10) bad.source_time_us=119933335;
        if(invalid==11) bad.velocity[2]=NAN;
        assert(!accepts(bad));
    }
    bad=packet;bad.contact|=BufferingContactFlag;
    assert(ValidTrajectory(bad,state,1000,true) && SameSourceSample(bad,packet) && !accepts(bad));
    auto forged=bad;forged.contact&=~CeilingSlopeCorrectionFlag;
    assert(!SameSourceSample(forged,packet));
    // Same-source heartbeats retain the proof bit and all original motion bytes.
    auto cached=packet;std::uint64_t advanced=1000;
    bad.lease.sequence=packet.lease.sequence+1;bad.lease.tick_ms=1010;
    assert(ReadTrajectoryHeartbeat(ReadResult::Committed,bad,cached,state,packet.lease,1000,
                                   packet.lease.sequence,1010,advanced,true));
    forged=bad;forged.lease.sequence++;forged.lease.tick_ms=1011;
    forged.contact=AirContact|CeilingCorrectionFlag;
    assert(!ReadTrajectoryHeartbeat(ReadResult::Committed,forged,cached,state,packet.lease,1000,
                                    bad.lease.sequence,1011,advanced,true));
}

void BailingSlopedCeilingTest(Snapshot state,TrajectoryPacket packet) {
    // airrail02 native1479->1480: unchanged original AirUpdate, hidden board,
    // already bailing on both frames; exact roof projection witnessed upstream.
    const float previous[]={2241.2592890625f,-1589.8215203125f,16.33034324471f};
    const float corrected[]={2241.305921875f,-1589.607803125f,14.63472426278f};
    const float velocity[]={.0086489255676f,.1803730987549f,.0069107254639f};
    packet.lease.duration_ms=0;
    packet.source_mode=0;packet.source_state=1;packet.source_bailing=1;
    packet.contact=AirContact|CeilingCorrectionFlag|CeilingSlopeCorrectionFlag;
    packet.source_tick=1220;packet.source_time_us=20333333;
    std::memcpy(packet.position,corrected,sizeof(corrected));
    std::memcpy(packet.velocity,velocity,sizeof(velocity));
    const auto accepts=[&](const TrajectoryPacket& p,std::uint32_t prior=AirContact,
                           std::uint32_t mode=0,std::uint32_t source=1,std::uint32_t bail=1,
                           std::uint64_t tick=1219,std::uint64_t us=20316667,bool interactive=true) {
        return ValidTrajectory(p,state,1000,interactive) &&
            TrajectoryInBounds(p,previous,0,previous,us,false,interactive,nullptr,
                               GroundContact,prior,tick,mode,source,bail);
    };
    assert(accepts(packet));
    assert(!std::memcmp(packet.position,corrected,sizeof(corrected)) &&
           !std::memcmp(packet.velocity,velocity,sizeof(velocity)));
    assert(!accepts(packet,AirContact,0,1,0)); // A new bail is not the proved branch.
    auto bad=packet;bad.source_bailing=0;assert(!accepts(bad));
    for(auto prior:{GroundContact,RailContact,ConstraintContact,PrepareContact})
        assert(!accepts(packet,prior));
    assert(!accepts(packet,AirContact,1) && !accepts(packet,AirContact,0,0));
    assert(!accepts(packet,AirContact,0,1,1,1220) &&
           !accepts(packet,AirContact,0,1,1,1219,20333333));
    assert(!accepts(packet,AirContact,0,1,1,1219,20316667,false));
    for(int invalid=0;invalid<10;++invalid) {
        bad=packet;
        if(invalid==0) bad.contact=AirContact;
        if(invalid==1) bad.contact=AirContact|CeilingCorrectionFlag;
        if(invalid==2) bad.contact=AirContact|CeilingSlopeCorrectionFlag;
        if(invalid==3) bad.source_mode=1;
        if(invalid==4) bad.source_bailing=2;
        if(invalid==5) bad.position[2]=previous[2]-2.001f;
        if(invalid==6) bad.position[0]=previous[0]+3.f;
        if(invalid==7) bad.velocity[2]=1.21f;
        if(invalid==8) bad.position[2]=NAN;
        if(invalid==9) bad.lease.duration_ms=30000;
        assert(!accepts(bad));
    }
}

void GroundBodyCorrectionTest(Snapshot state,TrajectoryPacket packet) {
    const float previous[]={2304.42456f,-1061.54895f,49.36118f};
    const float corrected[]={2305.55273f,-1060.8761f,50.44241f};
    packet.lease.duration_ms=0;packet.source_mode=0;packet.source_state=0;packet.source_bailing=1;
    packet.contact=GroundContact|BodyCorrectionFlag;
    packet.source_tick=6724;packet.source_time_us=112066667;
    std::memcpy(packet.position,corrected,sizeof(corrected));
    packet.velocity[0]=-.019078f;packet.velocity[1]=.036684f;packet.velocity[2]=.004325f;
    const auto accepts=[&](const TrajectoryPacket& p,std::uint32_t prior=GroundContact,
                           std::uint32_t mode=0,std::uint32_t source=0,std::uint32_t bail=1,
                           std::uint64_t tick=6723,std::uint64_t us=112050000,bool interactive=true) {
        return ValidTrajectory(p,state,1000,interactive) &&
            TrajectoryInBounds(p,previous,0,previous,us,false,interactive,nullptr,
                               GroundContact,prior,tick,mode,source,bail);
    };
    assert(accepts(packet) && BaseContact(packet.contact)==GroundContact);
    assert(ContactFlags(0,packet.contact)==1 && SuppressesAmbientFall(packet.contact,true));
    for(auto prior:{AirContact,RailContact,ConstraintContact,PrepareContact}) assert(!accepts(packet,prior));
    assert(!accepts(packet,GroundContact,1) && !accepts(packet,GroundContact,0,1) &&
           !accepts(packet,GroundContact,0,0,0));
    assert(!accepts(packet,GroundContact,0,0,1,6724) &&
           !accepts(packet,GroundContact,0,0,1,6723,112066667));
    assert(!accepts(packet,GroundContact,0,0,1,6723,112050000,false));
    for(int invalid=0;invalid<12;++invalid) {
        auto bad=packet;
        if(invalid==0) bad.contact=GroundContact;
        if(invalid==1) bad.contact|=CeilingCorrectionFlag;
        if(invalid==2) bad.contact|=CeilingSlopeCorrectionFlag;
        if(invalid==3) bad.contact|=RailCorrectionFlag;
        if(invalid==4) bad.source_bailing=0;
        if(invalid==5) bad.source_mode=1;
        if(invalid==6) { bad.source_state=1;bad.contact=AirContact|BodyCorrectionFlag; }
        if(invalid==7) bad.lease.duration_ms=30000;
        if(invalid==8) bad.position[0]=previous[0]+3.1f;
        if(invalid==9) bad.source_time_us=112150002;
        if(invalid==10) bad.velocity[0]=1.21f;
        if(invalid==11) bad.position[0]=NAN;
        assert(!accepts(bad));
    }
    auto buffered=packet;buffered.contact|=BufferingContactFlag;
    assert(ValidTrajectory(buffered,state,1000,true) && SameSourceSample(buffered,packet));
    buffered.contact&=~BodyCorrectionFlag;assert(!SameSourceSample(buffered,packet));
}

void SourceAdjustmentTest(Snapshot state,TrajectoryPacket packet) {
    // Four original airrail04 records (native369/8616/8618/8620), converted
    // from inches to GTA axes/metres with a common zero origin. The first is
    // Air -> Ground plus bail onset, not an already-hidden-board bail.
    struct Fixture { float before[3],after[3],velocity[3];std::uint64_t tick,us,prior_us;
                     std::uint32_t prior,state,prior_state,prior_bail; };
    const Fixture fixtures[]={
        {{2235.017040625f,-1290.287059375f,25.3526788574f},
         {2236.0264921875f,-1289.964003125f,25.1629121460f},
         {-.0235399566f,.0731444501f,-.0147885838f},369,6150000,6133333,AirContact,0,1,0},
        {{2221.2718703125f,-1257.3307578125f,22.8983914551f},
         {2220.9797703125f,-1256.38688984375f,24.7383551147f},
         {0,0,-0.f},8616,143600000,143583333,GroundContact,0,0,1},
        {{2220.9754046875f,-1257.31954609375f,22.8970597534f},
         {2221.065296875f,-1255.66219609375f,24.2978440186f},
         {0,0,0},8618,143633333,143616667,GroundContact,0,0,1},
        {{2220.9785796875f,-1257.33006328125f,22.8970861084f},
         {2220.9115078125f,-1255.5801421875f,24.1030838135f},
         {.00507171514f,.00000859645116f,.000181363445f},8620,143666667,143650000,GroundContact,0,0,1},
        // Earlier airrail02 existing Air bail/ceiling correction, native1480.
        {{2241.2592890625f,-1589.8215203125f,16.33034324471f},
         {2241.305921875f,-1589.607803125f,14.63472426278f},
         {.0086489255676f,.1803730987549f,.0069107254639f},1220,20333333,20316667,AirContact,1,1,1},
    };
    packet.lease.duration_ms=0;packet.source_mode=packet.source_walk_state=0;packet.source_bailing=1;
    for(const auto& f:fixtures) {
        packet.source_state=f.state;packet.source_tick=f.tick;packet.source_time_us=f.us;
        packet.contact=(f.state?AirContact:GroundContact)|SourceAdjustmentFlag;
        std::memcpy(packet.position,f.after,sizeof(f.after));std::memcpy(packet.velocity,f.velocity,sizeof(f.velocity));
        const auto original=packet;
        assert(ValidTrajectory(packet,state,1000,true));
        assert(TrajectoryInBounds(packet,f.before,0,f.before,f.prior_us,false,true,nullptr,
            GroundContact,f.prior,f.tick-1,0,f.prior_state,f.prior_bail));
        auto unflagged=packet;unflagged.contact&=~SourceAdjustmentFlag;
        assert(!TrajectoryInBounds(unflagged,f.before,0,f.before,f.prior_us,false,true,nullptr,
            GroundContact,f.prior,f.tick-1,0,f.prior_state,f.prior_bail));
        assert(!std::memcmp(&original,&packet,sizeof(packet))); // No velocity or position clamping.
    }
    const float previous[3]{};
    packet.position[0]=2.f;packet.position[1]=packet.position[2]=0;
    packet.velocity[0]=packet.velocity[1]=packet.velocity[2]=0;
    packet.source_tick=2;packet.source_time_us=33333;packet.source_mode=packet.source_state=packet.source_walk_state=0;
    packet.contact=GroundContact|SourceAdjustmentFlag;
    const auto accepts=[&](const TrajectoryPacket& p,std::uint64_t tick=1,std::uint64_t us=16667,
                           std::uint32_t prior=GroundContact,bool interactive=true,bool first=false) {
        return ValidTrajectory(p,state,1000,interactive) &&
            TrajectoryInBounds(p,previous,0,previous,us,first,interactive,nullptr,
                               GroundContact,prior,tick,0,0,0);
    };
    assert(accepts(packet));
    assert(BaseContact(packet.contact)==GroundContact && ContactFlags(0,packet.contact)==1);
    assert(SuppressesAmbientFall(packet.contact,true));
    for(auto prior:{GroundContact,AirContact,RailContact,ConstraintContact}) assert(accepts(packet,1,16667,prior));
    for(auto prior:{PrepareContact,PrepareAirContact,GroundContact|BufferingContactFlag})
        assert(!accepts(packet,1,16667,prior));
    // Every valid source mode/contact and either bail value is supported.
    constexpr std::uint32_t skating[]={GroundContact,AirContact,ConstraintContact,ConstraintContact,
                                      RailContact,ConstraintContact,ConstraintContact,ConstraintContact};
    constexpr std::uint32_t walking[]={GroundContact,AirContact,ConstraintContact,ConstraintContact,
                                      ConstraintContact,ConstraintContact,ConstraintContact,ConstraintContact};
    for(std::uint32_t mode=0;mode<2;++mode) for(std::uint32_t source=0;source<8;++source)
        for(std::uint32_t bail=0;bail<2;++bail) {
            auto p=packet;p.source_mode=mode;p.source_state=mode?0:source;p.source_walk_state=mode?source:0;
            p.source_bailing=bail;p.contact=(mode?walking[source]:skating[source])|SourceAdjustmentFlag;
            assert(accepts(p));
        }
    auto first_source=packet;first_source.source_tick=1;first_source.source_time_us=16667;
    assert(accepts(first_source,0,0)); // First genuine update after the acknowledged entry.
    assert(!accepts(first_source,0,0,GroundContact,true,true)); // Never an entry command.
    for(float displacement:{.01f,2.f}) {
        auto p=packet;p.position[0]=displacement;
        assert(!accepts(p,0) && !accepts(p,2) && !accepts(p,3));
        for(std::uint64_t dt:{0ull,1ull,16665ull,16668ull,33333ull,100001ull}) {
            p.source_time_us=16667+dt;assert(!accepts(p));
        }
    }
    auto boundary=packet;
    boundary.position[0]=.02f+InteractiveMaxSpeed*(16666.f*.000001f)+SourceMaxAdjustment;
    assert(accepts(boundary));
    boundary.position[0]=std::nextafter(boundary.position[0],INFINITY);assert(!accepts(boundary));
    assert(!accepts(packet,1,16667,GroundContact,false));
    for(int invalid=0;invalid<16;++invalid) {
        auto p=packet;
        if(invalid==0) p.contact|=RailCorrectionFlag;
        if(invalid==1) p.contact|=CeilingCorrectionFlag;
        if(invalid==2) p.contact|=CeilingSlopeCorrectionFlag;
        if(invalid==3) p.contact|=BodyCorrectionFlag;
        if(invalid==4) p.contact|=BufferingContactFlag;
        if(invalid==5) p.source_tick=p.source_time_us=0;
        if(invalid==6) p.source_state=1; // Ground contact contradicts source context.
        if(invalid==7) p.source_mode=2;
        if(invalid==8) p.position[0]=NAN;
        if(invalid==9) p.velocity[0]=INFINITY;
        if(invalid==10) p.velocity[0]=1.21f;
        if(invalid==11) p.lease.duration_ms=30000;
        if(invalid==12) ++p.lease.pid;
        if(invalid==13) ++p.lease.created;
        if(invalid==14) ++p.lease.epoch;
        if(invalid==15) p.lease.tick_ms=899;
        assert(!accepts(p));
    }
    // An accepted adjustment may be kept alive/buffered without reapplying it.
    auto buffered=packet;buffered.contact|=BufferingContactFlag;
    assert(ValidTrajectory(buffered,state,1000,true) && SameSourceSample(buffered,packet));
    assert(TrajectoryInBounds(buffered,previous,0,packet.position,packet.source_time_us,false,true,nullptr,
                             GroundContact,GroundContact,packet.source_tick,0,0,1));
    auto cached=packet;buffered.lease.sequence=2;buffered.lease.tick_ms=1001;state.tick_ms=1001;
    std::uint64_t advanced=1000;
    assert(ReadTrajectoryHeartbeat(ReadResult::Committed,buffered,cached,state,packet.lease,
                                   1000,1,1001,advanced,true));
    auto stripped=buffered;stripped.contact&=~SourceAdjustmentFlag;stripped.lease.sequence=3;
    assert(!ReadTrajectoryHeartbeat(ReadResult::Committed,stripped,cached,state,packet.lease,
                                    1000,2,1001,advanced,true));
    auto mutated=buffered;mutated.position[0]+=.001f;mutated.lease.sequence=3;
    assert(!ReadTrajectoryHeartbeat(ReadResult::Committed,mutated,cached,state,packet.lease,
                                    1000,2,1001,advanced,true));
}

void RailCorrectionTest(Snapshot state,TrajectoryPacket packet) {
    packet.lease.duration_ms=0;
    packet.source_state=4;
    packet.contact=RailContact|RailCorrectionFlag;
    packet.source_tick=379;packet.source_time_us=6316667;
    const float previous[]={2286.10327f,-1233.98633f,24.109375f};
    const float corrected[]={2284.17285f,-1233.70142f,24.109375f};
    std::memcpy(packet.position,corrected,sizeof(corrected));
    const auto accepts=[&](const TrajectoryPacket& p,std::uint32_t prior=RailContact,
                           std::uint64_t tick=378,std::uint64_t us=6300000,bool interactive=true) {
        return TrajectoryInBounds(p,previous,0,previous,us,false,interactive,nullptr,GroundContact,prior,tick);
    };
    assert(accepts(packet) && ValidTrajectory(packet,state,1000,true));
    assert(!accepts(packet,AirContact) && !accepts(packet,GroundContact) && !accepts(packet,ConstraintContact));
    assert(!accepts(packet,RailContact,379) && !accepts(packet,RailContact,378,6316667));
    assert(!accepts(packet,RailContact,378,6300000,false));
    assert(accepts(packet,RailContact,377,6283333)); // An older accepted source frame retains the bounded proof.
    auto bad=packet;bad.contact=RailContact;assert(!accepts(bad));
    bad=packet;bad.contact|=BufferingContactFlag;assert(!accepts(bad));
    assert(ValidTrajectory(bad,state,1000,true) && SameSourceSample(bad,packet));
    bad=packet;bad.contact=RailContact;assert(!SameSourceSample(bad,packet));
    for(auto contact : {GroundContact,AirContact,ConstraintContact,PrepareContact,PrepareAirContact}) {
        bad=packet;bad.contact=contact|RailCorrectionFlag;
        assert(!ValidTrajectory(bad,state,1000,true) && !accepts(bad));
    }
    bad=packet;bad.contact|=0x400;assert(!ValidTrajectory(bad,state,1000,true));
    bad=packet;bad.source_tick=bad.source_time_us=0;assert(!ValidTrajectory(bad,state,1000,true));
    bad=packet;bad.lease.duration_ms=30000;assert(!ValidTrajectory(bad,state,1000,true) && !accepts(bad));
    bad=packet;bad.position[0]-=3;assert(!accepts(bad));
    assert(!ValidTrajectory(packet,state,1000,false));
    assert(BaseContact(packet.contact)==RailContact && ContactFlags(0,packet.contact)==0x200);
    assert(SuppressesAmbientFall(packet.contact));
    // The accepted flagged sample may be renewed/buffered, never changed in place.
    auto renewed=packet;renewed.lease.sequence++;renewed.lease.tick_ms=state.tick_ms=1017;
    auto cached=packet;
    const auto initial=packet.lease;
    std::uint64_t advanced=1000;
    assert(ReadTrajectoryHeartbeat(ReadResult::Committed,renewed,cached,state,initial,1000,
                                   packet.lease.sequence,1017,advanced,true));
    auto buffered=renewed;buffered.contact|=BufferingContactFlag;buffered.lease.sequence++;
    assert(ReadTrajectoryHeartbeat(ReadResult::Committed,buffered,cached,state,initial,1000,
                                   renewed.lease.sequence,1017,advanced,true));
    auto stripped=buffered;stripped.contact=RailContact|BufferingContactFlag;stripped.lease.sequence++;
    assert(!ReadTrajectoryHeartbeat(ReadResult::Committed,stripped,cached,state,initial,1000,
                                    buffered.lease.sequence,1017,advanced,true));
}

void RenderRootHistoryTest() {
    RenderRootHistory history;
    float first[16]{},second[16]{},third[16]{},out[16]{};
    first[0]=first[5]=first[10]=first[15]=1;first[12]=10;
    std::memcpy(second,first,sizeof(first));second[12]=11;
    std::memcpy(third,first,sizeof(first));third[12]=12;
    assert(!history.Get(7,0,0,out));
    history.Commit(7,0,0,first);
    assert(history.Get(7,0,0,out) && !std::memcmp(out,first,sizeof(first)));
    history.Commit(7,1,16667,second);
    assert(history.Get(7,0,0,out) && !std::memcmp(out,first,sizeof(first)));
    assert(history.Get(7,1,16667,out) && !std::memcmp(out,second,sizeof(second)));
    history.Commit(7,1,16667,third); // Heartbeats must not replace either original sample.
    assert(history.Get(7,1,16667,out) && !std::memcmp(out,second,sizeof(second)));
    assert(history.Get(7,0,0,out) && !std::memcmp(out,first,sizeof(first)));
    assert(!history.Get(7,1,16666,out) && !history.Get(8,1,16667,out));
    history.Commit(7,2,33333,third);
    assert(!history.Get(7,0,0,out));
    assert(history.Get(7,1,16667,out) && !std::memcmp(out,second,sizeof(second)));
    history.Commit(8,0,0,third);
    assert(!history.Get(7,2,33333,out) && !history.Get(8,1,16667,out));
    assert(history.Get(8,0,0,out) && !std::memcmp(out,third,sizeof(third)));
    history={};assert(!history.Get(8,0,0,out));
}

void PreparationTest(Snapshot s,TrajectoryPacket marker) {
    marker.contact=PrepareContact;
    assert(!ValidTrajectory(marker,s,1000) && ValidTrajectory(marker,s,1000,true));
    auto bad=marker;bad.source_tick=1;bad.source_time_us=16667;
    assert(!ValidTrajectory(bad,s,1000,true));
    const float position[]={10,20,30},velocity[]={.12f,-.08f,.01f};const float heading=.4f;
    auto cached=marker;
    s.tick_ms=1499;
    assert(ReadPreparationHeartbeat(ReadResult::Busy,{},cached,s,marker.lease,1000,1,1499,position,heading,velocity));
    assert(!std::memcmp(&cached,&marker,sizeof(cached))); // A busy read does not renew or mutate the capture.
    s.tick_ms=1500;
    assert(!ReadPreparationHeartbeat(ReadResult::Busy,{},cached,s,marker.lease,1000,1,1500,position,heading,velocity));
    s.tick_ms=1400;
    bad=marker;bad.lease.sequence=2;bad.lease.tick_ms=1400;
    assert(ReadPreparationHeartbeat(ReadResult::Committed,bad,cached,s,marker.lease,1000,1,1400,position,heading,velocity));
    s.tick_ms=1500;
    assert(!ReadPreparationHeartbeat(ReadResult::Busy,{},cached,s,marker.lease,1000,2,1500,position,heading,velocity));
    cached=marker;s.tick_ms=1450;
    auto commit=marker;commit.contact=GroundContact;commit.lease.sequence=2;commit.lease.tick_ms=1450;
    std::memcpy(commit.position,position,12);std::memcpy(commit.velocity,velocity,12);commit.heading=heading;
    bad=commit;bad.position[0]+=.001f;
    assert(!ReadPreparationHeartbeat(ReadResult::Committed,bad,cached,s,marker.lease,1000,1,1450,position,heading,velocity));
    bad=commit;bad.velocity[0]=0;
    assert(!ReadPreparationHeartbeat(ReadResult::Committed,bad,cached,s,marker.lease,1000,1,1450,position,heading,velocity));
    bad=commit;bad.contact=AirContact;
    assert(!ReadPreparationHeartbeat(ReadResult::Committed,bad,cached,s,marker.lease,1000,1,1450,position,heading,velocity));
    bad=commit;bad.collision_generation++;
    assert(!ReadPreparationHeartbeat(ReadResult::Committed,bad,cached,s,marker.lease,1000,1,1450,position,heading,velocity));
    bad=commit;bad.lease.request++;
    assert(!ReadPreparationHeartbeat(ReadResult::Committed,bad,cached,s,marker.lease,1000,1,1450,position,heading,velocity));
    assert(!std::memcmp(&cached,&marker,sizeof(cached))); // Failed commit preserves the held original velocity.
    assert(!ReadPreparationHeartbeat(ReadResult::Cleared,commit,cached,s,marker.lease,1000,1,1450,position,heading,velocity));
    assert(ReadPreparationHeartbeat(ReadResult::Committed,commit,cached,s,marker.lease,1000,1,1450,position,heading,velocity));
    assert(PreparationCommitMatches(cached,position,heading,velocity));
    assert(TrajectoryInBounds(cached,position,heading,position,0,true,true,velocity));
    assert(!TrajectoryInBounds(cached,position,heading,position,0,true,true)); // Legacy direct entry remains stationary.
    std::uint64_t advanced=1450;
    s.tick_ms=1550;
    assert(ReadTrajectoryHeartbeat(ReadResult::Busy,{},cached,s,marker.lease,1450,2,1550,advanced,true));
    s.tick_ms=1551;
    assert(!ReadTrajectoryHeartbeat(ReadResult::Busy,{},cached,s,marker.lease,1450,2,1551,advanced,true));
    s.tick_ms=1451;bad=marker;bad.lease.sequence=3;bad.lease.tick_ms=1451;
    assert(!ReadTrajectoryHeartbeat(ReadResult::Committed,bad,cached,s,marker.lease,1450,2,1451,advanced,true));
    marker.contact=PrepareAirContact;cached=marker;commit.contact=AirContact;
    s.ped_motion_flags=InAir;s.tick_ms=1450;
    assert(ValidTrajectory(marker,s,1450,true) && !ValidTrajectory(marker,s,1450));
    assert(!ReadPreparationHeartbeat(ReadResult::Committed,commit,cached,s,marker.lease,1000,1,1450,position,heading,velocity));
    assert(ReadPreparationHeartbeat(ReadResult::Committed,commit,cached,s,marker.lease,1000,1,1450,position,heading,velocity,AirContact));
    assert(TrajectoryInBounds(commit,position,heading,position,0,true,true,velocity,AirContact));
    assert(!TrajectoryInBounds(commit,position,heading,position,0,true,true,velocity));
}

void AirTaskTest() {
    AirTaskNode chain[]={{0x10000,0x870570,0,0x20000},{0x20000,0x8704AC,0x10000,0x30000},
                         {0x30000,0x8704D8,0x20000,0}};
    assert(KnownJumpChain(chain,1,0));
    assert(!KnownJumpChain(chain,3,0) && !KnownJumpChain(chain,1,0x40000));
    chain[1].parent=0;assert(!KnownJumpChain(chain,1,0));chain[1].parent=0x10000;
    chain[2].vtable=0x87054C;assert(!KnownJumpChain(chain,1,0));chain[2].vtable=0x8704D8;
    chain[2].child=0x10000;assert(!KnownJumpChain(chain,1,0));chain[2].child=0;
    chain[0].address=0x10001;assert(!KnownJumpChain(chain,1,0));chain[0].address=0x10000;
    chain[2].address=chain[1].address;assert(!KnownJumpChain(chain,1,0));
    std::uint32_t restored=0;
    assert(RestoreCapturedAirContact(0x10000002,0x202,restored) && restored==0x10000202);
    assert(RestoreCapturedAirContact(0x202,0x200,restored) && restored==0x202);
    assert(!RestoreCapturedAirContact(1,0x200,restored));
    assert(!RestoreCapturedAirContact(0x400,0x200,restored));
    assert(!RestoreCapturedAirContact(0,1,restored));
}

void ContinuousTest(Snapshot state,TrajectoryPacket packet) {
    packet.lease.duration_ms=ContinuousDurationMs;
    assert(ValidTrajectory(packet,state,1000,true) && !ValidTrajectory(packet,state,1000));
    assert(!Continues(packet.lease,packet.lease,1000,1,1000));
    assert(Continues(packet.lease,packet.lease,1000,1,1000,true));
    auto wrong=packet;
    wrong.lease.duration_ms=1;
    assert(!Continues(wrong.lease,packet.lease,1000,1,1000,true));
    PreparationTest(state,packet); // The capture still has its non-renewable 500ms deadline.
    const auto initial=packet.lease;
    auto cached=packet;
    const float entry[3]{};
    float previous[3]{};
    std::uint64_t advanced=1000;
    for(std::uint64_t tick=1;tick<=72000;++tick) {
        const auto now=1000+tick*100;
        state.tick_ms=now;
        packet.lease.sequence=tick+1;packet.lease.tick_ms=now;
        packet.source_tick=tick;packet.source_time_us=tick*100000;
        packet.position[0]=static_cast<float>(tick)*.1f;packet.velocity[0]=.02f;
        assert(ValidTrajectory(packet,state,now,true));
        assert(TrajectoryInBounds(packet,entry,0,previous,(tick-1)*100000,false,true));
        assert(ReadTrajectoryHeartbeat(ReadResult::Committed,packet,cached,state,initial,1000,tick,now,advanced,true));
        std::memcpy(previous,packet.position,12);
    }
    assert(packet.source_time_us==7200000000ULL && packet.position[0]==7200.f);
    wrong=packet;wrong.lease.duration_ms=30000;
    assert(!ValidTrajectory(wrong,state,packet.lease.tick_ms,true));
    wrong=packet;wrong.source_time_us+=100002;
    assert(!TrajectoryInBounds(wrong,entry,0,previous,packet.source_time_us,false,true));
    wrong=packet;wrong.position[0]+=10;
    assert(!TrajectoryInBounds(wrong,entry,0,previous,packet.source_time_us,false,true));
    wrong=packet;wrong.lease.sequence++;wrong.lease.tick_ms++;
    state.tick_ms++;
    wrong.collision_generation++;
    assert(!ReadTrajectoryHeartbeat(ReadResult::Committed,wrong,cached,state,initial,1000,packet.lease.sequence,
                                    state.tick_ms,advanced,true));
    wrong=packet;wrong.lease.sequence++;wrong.lease.tick_ms=state.tick_ms;
    wrong.lease.epoch++;
    assert(!ReadTrajectoryHeartbeat(ReadResult::Committed,wrong,cached,state,initial,1000,packet.lease.sequence,
                                    state.tick_ms,advanced,true));
    assert(!ReadTrajectoryHeartbeat(ReadResult::Cleared,packet,cached,state,initial,1000,packet.lease.sequence,
                                    state.tick_ms,advanced,true));
    state.tick_ms=packet.lease.tick_ms+101;
    wrong=packet;wrong.lease.sequence++;wrong.lease.tick_ms=state.tick_ms;
    assert(!ReadTrajectoryHeartbeat(ReadResult::Committed,wrong,cached,state,initial,1000,packet.lease.sequence,
                                    state.tick_ms,advanced,true)); // A fresh publisher cannot revive stopped physics.
    assert(!ReadTrajectoryHeartbeat(ReadResult::Busy,{},cached,state,initial,1000,packet.lease.sequence,
                                    state.tick_ms,advanced,true));
}

void BufferingTest(Snapshot state,TrajectoryPacket packet) {
    packet.lease.duration_ms=ContinuousDurationMs;
    const auto initial=packet.lease;
    const float zero[3]{};
    auto first=packet;first.contact|=BufferingContactFlag;
    assert(ValidTrajectory(first,state,1000,true));
    assert(!TrajectoryInBounds(first,zero,0,zero,0,true,true));
    assert(!PreparationCommitMatches(first,zero,0,zero));
    auto wrong=first;wrong.contact=BufferingContactFlag|PrepareContact;
    assert(!ValidTrajectory(wrong,state,1000,true));
    wrong.contact=BufferingContactFlag|PrepareAirContact;
    assert(!ValidTrajectory(wrong,state,1000,true));
    wrong=first;wrong.lease.duration_ms=5000;
    assert(!ValidTrajectory(wrong,state,1000,true));
    wrong=first;wrong.contact|=0x200;
    assert(!ValidTrajectory(wrong,state,1000,true));
    assert(!ValidTrajectory(first,state,1000));

    auto cached=packet;
    std::uint64_t advanced=1000;
    packet.lease.sequence=2;packet.lease.tick_ms=1017;state.tick_ms=1017;
    packet.source_tick=1;packet.source_time_us=16667;packet.position[0]=.1f;packet.velocity[0]=.12f;
    packet.contact=ConstraintContact;packet.source_state=2;
    assert(ReadTrajectoryHeartbeat(ReadResult::Committed,packet,cached,state,initial,1000,1,1017,advanced,true));
    auto buffered=packet;buffered.lease.sequence++;buffered.lease.tick_ms=1018;buffered.contact|=BufferingContactFlag;
    state.tick_ms=1018;
    wrong=buffered;wrong.source_tick++;wrong.source_time_us+=16667;
    assert(!ReadTrajectoryHeartbeat(ReadResult::Committed,wrong,cached,state,initial,1000,2,1018,advanced,true));
    wrong=buffered;wrong.position[0]+=.001f;
    assert(!ReadTrajectoryHeartbeat(ReadResult::Committed,wrong,cached,state,initial,1000,2,1018,advanced,true));
    wrong=buffered;wrong.velocity[0]=0;
    assert(!ReadTrajectoryHeartbeat(ReadResult::Committed,wrong,cached,state,initial,1000,2,1018,advanced,true));
    wrong=buffered;wrong.contact=AirContact|BufferingContactFlag;
    assert(!ReadTrajectoryHeartbeat(ReadResult::Committed,wrong,cached,state,initial,1000,2,1018,advanced,true));
    wrong=buffered;wrong.lease.tick_ms=1118;state.tick_ms=1118;
    assert(!ReadTrajectoryHeartbeat(ReadResult::Committed,wrong,cached,state,initial,1000,2,1118,advanced,true));
    state.tick_ms=1018;
    assert(ReadTrajectoryHeartbeat(ReadResult::Committed,buffered,cached,state,initial,1000,2,1018,advanced,true));
    for(unsigned i=0;i<20;++i) {
        const auto previous=buffered.lease.sequence;
        buffered.lease.sequence++;buffered.lease.tick_ms+=75;state.tick_ms=buffered.lease.tick_ms;
        assert(ReadTrajectoryHeartbeat(ReadResult::Committed,buffered,cached,state,initial,1000,previous,state.tick_ms,advanced,true));
        assert(advanced==1017 && cached.source_tick==1 && cached.source_time_us==16667);
    }
    assert(!ReadTrajectoryHeartbeat(ReadResult::Cleared,buffered,cached,state,initial,1000,buffered.lease.sequence,
                                    state.tick_ms,advanced,true));
    wrong=buffered;wrong.lease.sequence++;wrong.lease.epoch++;
    assert(!ReadTrajectoryHeartbeat(ReadResult::Committed,wrong,cached,state,initial,1000,buffered.lease.sequence,
                                    state.tick_ms,advanced,true));
    state.tick_ms=buffered.lease.tick_ms+101;
    assert(!ReadTrajectoryHeartbeat(ReadResult::Busy,{},cached,state,initial,1000,buffered.lease.sequence,
                                    state.tick_ms,advanced,true));
    state.tick_ms=buffered.lease.tick_ms;

    // Resume may publish the unchanged sample or the next genuine source frame.
    const auto saved=cached;
    auto resumed=buffered;resumed.contact=BaseContact(resumed.contact);resumed.lease.sequence++;
    assert(ReadTrajectoryHeartbeat(ReadResult::Committed,resumed,cached,state,initial,1000,buffered.lease.sequence,
                                    state.tick_ms,advanced,true));
    assert(advanced==state.tick_ms);
    state.tick_ms+=100;
    assert(ReadTrajectoryHeartbeat(ReadResult::Busy,{},cached,state,initial,1000,resumed.lease.sequence,state.tick_ms,advanced,true));
    state.tick_ms++;
    assert(!ReadTrajectoryHeartbeat(ReadResult::Busy,{},cached,state,initial,1000,resumed.lease.sequence,state.tick_ms,advanced,true));
    cached=saved;advanced=1017;state.tick_ms=buffered.lease.tick_ms;
    resumed.source_tick++;resumed.source_time_us+=16667;resumed.position[0]+=.1f;
    assert(ReadTrajectoryHeartbeat(ReadResult::Committed,resumed,cached,state,initial,1000,buffered.lease.sequence,
                                    state.tick_ms,advanced,true));
    assert(advanced==state.tick_ms && cached.source_tick==2);
    assert(ContactFlags(0,GroundContact|BufferingContactFlag)==1);
    assert(ContactFlags(0,AirContact|BufferingContactFlag)==0x200);
    assert(SuppressesAmbientFall(RailContact|BufferingContactFlag));
    assert(ContactFlags(0,ConstraintContact|BufferingContactFlag)==0x200);
    assert(SuppressesAmbientFall(ConstraintContact|BufferingContactFlag));
    assert(!SuppressesAmbientFall(GroundContact|BufferingContactFlag));
}

void SourceContextTest(Snapshot state,TrajectoryPacket packet) {
    auto old=packet;
    std::memcpy(old.lease.magic,"GTAMOVE2",8);old.lease.version=2;old.lease.byte_size=128;
    assert(!ValidTrajectory(old,state,1000,true));
    for(auto field : {&TrajectoryPacket::source_mode,&TrajectoryPacket::source_state,
                      &TrajectoryPacket::source_walk_state,&TrajectoryPacket::source_bailing}) {
        auto invalid=packet;invalid.*field=1;
        assert(!ValidTrajectory(invalid,state,1000,true)); // Entry has no completed source context.
        invalid.contact=PrepareContact;
        assert(!ValidTrajectory(invalid,state,1000,true));
    }
    packet.lease.duration_ms=0;packet.source_tick=1;packet.source_time_us=16667;
    packet.lease.sequence=2;packet.lease.tick_ms=state.tick_ms=1017;
    for(unsigned mode=0;mode<2;++mode) for(unsigned active=0;active<8;++active)
        for(unsigned inactive=0;inactive<8;++inactive) for(unsigned bail=0;bail<2;++bail) {
            auto sample=packet;sample.source_mode=mode;sample.source_bailing=bail;
            sample.source_state=mode?inactive:active;sample.source_walk_state=mode?active:inactive;
            sample.contact=active==0?GroundContact:active==1?AirContact:
                !mode && active==4?RailContact:ConstraintContact;
            assert(ValidTrajectory(sample,state,1017,true));
            sample.contact=sample.contact==GroundContact?AirContact:GroundContact;
            assert(!ValidTrajectory(sample,state,1017,true));
        }
    packet.contact=ConstraintContact;packet.source_state=2;packet.source_walk_state=3;
    auto invalid=packet;invalid.source_mode=2;assert(!ValidTrajectory(invalid,state,1017,true));
    invalid=packet;invalid.source_state=8;assert(!ValidTrajectory(invalid,state,1017,true));
    invalid=packet;invalid.source_walk_state=8;assert(!ValidTrajectory(invalid,state,1017,true));
    invalid=packet;invalid.source_bailing=2;assert(!ValidTrajectory(invalid,state,1017,true));
    for(auto field : {&TrajectoryPacket::source_mode,&TrajectoryPacket::source_state,
                      &TrajectoryPacket::source_walk_state,&TrajectoryPacket::source_bailing}) {
        auto changed=packet,cached=packet;
        changed.lease.sequence++;changed.lease.tick_ms=state.tick_ms=1018;changed.*field+=1;
        assert(ValidTrajectory(changed,state,1018,true));
        std::uint64_t advanced=1017;
        assert(!ReadTrajectoryHeartbeat(ReadResult::Committed,changed,cached,state,packet.lease,1000,2,1018,advanced,true));
        assert(SameSourceSample(cached,packet) && advanced==1017);
        changed.contact|=BufferingContactFlag;
        assert(!ReadTrajectoryHeartbeat(ReadResult::Committed,changed,cached,state,packet.lease,1000,2,1018,advanced,true));
        changed.contact=BaseContact(changed.contact);changed.source_tick=2;changed.source_time_us=33333;
        assert(ReadTrajectoryHeartbeat(ReadResult::Committed,changed,cached,state,packet.lease,1000,2,1018,advanced,true));
        assert(cached.*field==changed.*field && cached.source_tick==2 && advanced==1018);
    }
}

int main() {
    ControllerMovementTest();
    HitReactionOwnershipTest();
    CollisionForceScopeTest();
    ConstraintCaptureBoundsTest();
    RenderRootHistoryTest();
    Snapshot s{};
    std::memcpy(s.magic,"GTATHAW2",8); s.version=2; s.byte_size=sizeof(s);
    s.gta_pid=17; s.process_creation_100ns=123; s.sequence=1; s.tick_ms=1000;
    s.capabilities=Telemetry|IsolatedProfile; s.ped_address=0x20000; s.epoch=3; s.health=100;
    s.status_flags=PedPresent|OnFoot|Foreground; s.ped_motion_flags=Standing;
    std::strcpy(s.profile_path,"I:\\GTA_THAW\\thaw_gta\\profile");
    Packet p{}; std::memcpy(p.magic,"GTAHOLD1",8); p.version=1; p.byte_size=sizeof(p);
    p.pid=17; p.created=123; p.epoch=3; p.ped=0x20000; p.request=9;
    p.sequence=1; p.tick_ms=1000; p.duration_ms=2000;
    assert(ValidPacket(p,s,1000) && ValidPacket(p,s,1100));
    assert(!ValidPacket(p,s,999) && !ValidPacket(p,s,1101));
    auto q=p; q.ped+=4; assert(!ValidPacket(q,s,1000));
    q=p; q.created++; assert(!ValidPacket(q,s,1000));
    q=p; q.epoch++; assert(!ValidPacket(q,s,1000));
    q=p; q.duration_ms=2001; assert(!ValidPacket(q,s,1000));
    q=p; q.request=0; assert(!ValidPacket(q,s,1000));
    auto changed=s; changed.cutscene_flags=1; assert(!ValidPacket(p,changed,1000));
    changed=s; changed.ped_motion_flags=InAir; assert(!ValidPacket(p,changed,1000));
    changed=s; changed.status_flags|=Paused; assert(!ValidPacket(p,changed,1000));
    changed=s; changed.status_flags&=~Foreground; assert(!ValidPacket(p,changed,1000));
    changed=s; changed.controls_disabled_mask=1; assert(!ValidPacket(p,changed,1000));
    assert(Continues(p,p,1000,1,2999));
    assert(!Continues(p,p,1000,1,3000));
    q=p; q.tick_ms=2999; q.sequence=120;
    assert(Continues(q,p,1000,1,2999) && !Continues(q,p,1000,1,3000));
    q.request++; assert(!Continues(q,p,1000,1,1001));
    q=p; q.duration_ms=1; assert(!Continues(q,p,1000,1,1001));
    assert(!Continues(p,p,1000,2,1001));
    assert(NewRequest(9,8) && !NewRequest(9,9) && !NewRequest(8,10) && !NewRequest(0,0));
    // In-progress publication can use only the last committed heartbeat's original age.
    assert(ClassifyRead(3,2,3)==ReadResult::Busy);
    assert(ClassifyRead(2,4,4)==ReadResult::Busy);
    assert(ClassifyRead(2,2,2)==ReadResult::Committed);
    assert(ClassifyRead(0,2,0)==ReadResult::Cleared);
    assert(ClassifyRead(2,2,0)==ReadResult::Cleared);
    assert(ClassifyRead(3,0,0)==ReadResult::Cleared);
    Packet cached=p, incomplete{};
    assert(ReadHeartbeat(ReadResult::Busy,incomplete,cached,s,p,1000,1,1100));
    changed=s; changed.tick_ms=1101;
    assert(!ReadHeartbeat(ReadResult::Busy,incomplete,cached,changed,p,1000,1,1101));
    assert(cached.tick_ms==1000); // Reading busy state never renews freshness.
    assert(!ReadHeartbeat(ReadResult::Cleared,p,cached,s,p,1000,1,1001));
    q=p; q.created++;
    assert(!ReadHeartbeat(ReadResult::Committed,q,cached,s,p,1000,1,1001));
    assert(cached.created==p.created); // No fallback after malformed committed data.
    q=p; q.request++;
    assert(!ReadHeartbeat(ReadResult::Committed,q,cached,s,p,1000,1,1001));
    q=p; q.sequence=2; q.tick_ms=1001;
    assert(!ReadHeartbeat(ReadResult::Committed,q,cached,s,p,1000,1,1000));
    assert(ReadHeartbeat(ReadResult::Committed,q,cached,s,p,1000,1,1001));
    assert(cached.sequence==2 && cached.tick_ms==1001); // Time sampled after copy.
    assert(!ReadHeartbeat(ReadResult::Committed,p,cached,s,p,1000,2,1001));
    q=p; q.sequence=120; q.tick_ms=2999; changed.tick_ms=2999;
    assert(ReadHeartbeat(ReadResult::Committed,q,cached,changed,p,1000,2,2999));
    changed.tick_ms=3000;
    assert(!ReadHeartbeat(ReadResult::Busy,incomplete,cached,changed,p,1000,120,3000));
    CommandPacket move{};
    move.lease=p; std::memcpy(move.lease.magic,"GTAMOVE1",8); move.lease.byte_size=sizeof(move);
    move.lease.duration_ms=1000;
    assert(ValidCommand(move,s,1000));
    auto invalid=move; invalid.lease.duration_ms=1001; assert(!ValidCommand(invalid,s,1000));
    invalid=move; invalid.velocity[0]=.021f; assert(!ValidCommand(invalid,s,1000));
    invalid=move; invalid.velocity[2]=.001f; assert(!ValidCommand(invalid,s,1000));
    invalid=move; invalid.position[0]=NAN; assert(!ValidCommand(invalid,s,1000));
    invalid=move; invalid.heading=INFINITY; assert(!ValidCommand(invalid,s,1000));
    invalid=move; invalid.reserved=1; assert(!ValidCommand(invalid,s,1000));
    float entry[3]{};
    assert(CommandInBounds(move,entry,0,entry,0,1000,true));
    invalid=move; invalid.position[0]=.021f;
    assert(!CommandInBounds(invalid,entry,0,entry,0,1000,true));
    invalid=move; invalid.position[2]=.001f;
    assert(!CommandInBounds(invalid,entry,0,entry,0,1000,false));
    invalid=move; invalid.lease.tick_ms=1100; invalid.position[0]=.1f; invalid.heading=.1f;
    assert(CommandInBounds(invalid,entry,0,entry,0,1000,false));
    invalid.position[0]=.2f; assert(!CommandInBounds(invalid,entry,0,entry,0,1000,false));
    invalid.position[0]=0; invalid.heading=.3f; assert(!CommandInBounds(invalid,entry,0,entry,0,1000,false));
    invalid=move; invalid.lease.tick_ms=2000; invalid.position[0]=1.01f;
    assert(!CommandInBounds(invalid,entry,0,entry,0,1000,false));
    invalid=move; invalid.heading=1.58f;
    assert(!CommandInBounds(invalid,entry,0,entry,0,1000,false));
    invalid=move; invalid.lease.tick_ms=999;
    assert(!CommandInBounds(invalid,entry,0,entry,0,1000,false));
    assert(std::fabs(HeadingDifference(-3.14f,3.14f))<.004f);
    auto move_cache=move; auto envelope=HoldEnvelope(move);
    assert(ReadCommandHeartbeat(ReadResult::Busy,{},move_cache,s,envelope,1000,1,1100));
    assert(!ReadCommandHeartbeat(ReadResult::Busy,{},move_cache,s,envelope,1000,1,1101));
    assert(move_cache.lease.tick_ms==1000);
    assert(!ReadCommandHeartbeat(ReadResult::Cleared,move,move_cache,s,envelope,1000,1,1001));
    invalid=move; invalid.position[0]=.001f; // A repeated sequence cannot mutate its command.
    assert(!ReadCommandHeartbeat(ReadResult::Committed,invalid,move_cache,s,envelope,1000,1,1001));
    invalid=move; invalid.lease.sequence=2; invalid.lease.tick_ms=1001;
    assert(ReadCommandHeartbeat(ReadResult::Committed,invalid,move_cache,s,envelope,1000,1,1001));
    assert(!ReadCommandHeartbeat(ReadResult::Committed,move,move_cache,s,envelope,1000,2,1001));
    invalid.lease.sequence=3; invalid.lease.tick_ms=2000; changed=s; changed.tick_ms=2000;
    assert(!ReadCommandHeartbeat(ReadResult::Committed,invalid,move_cache,changed,envelope,1000,2,2000));
    TrajectoryPacket trajectory{};
    trajectory.lease=p; std::memcpy(trajectory.lease.magic,"GTAMOVE3",8);
    trajectory.lease.version=3; trajectory.lease.byte_size=sizeof(trajectory); trajectory.lease.duration_ms=5000;
    trajectory.contact=GroundContact; trajectory.collision_generation=7;
    assert(ValidTrajectory(trajectory,s,1000));
    assert(TrajectoryInBounds(trajectory,entry,0,entry,0,true));
    SourceContextTest(s,trajectory);
    PreparationTest(s,trajectory);
    VelocityOwnershipTest();
    RailCaptureBoundsTest();
    RailCorrectionTest(s,trajectory);
    CeilingCorrectionTest(s,trajectory);
    SlopedCeilingCorrectionTest(s,trajectory);
    BailingSlopedCeilingTest(s,trajectory);
    GroundBodyCorrectionTest(s,trajectory);
    SourceAdjustmentTest(s,trajectory);
    CeilingProjectionResidueTest(s,trajectory);
    ContinuousTest(s,trajectory);
    BufferingTest(s,trajectory);
    AirTaskTest();
    auto bad=trajectory;
    bad.lease.duration_ms=5001; assert(!ValidTrajectory(bad,s,1000));
    assert(ValidTrajectory(bad,s,1000,true));
    bad.lease.duration_ms=30000; assert(ValidTrajectory(bad,s,1000,true));
    bad.lease.duration_ms=30001; assert(!ValidTrajectory(bad,s,1000,true));
    bad=trajectory; bad.velocity[0]=.35194016f; bad.velocity[1]=.01586678f; bad.velocity[2]=.22925956f;
    assert(!ValidTrajectory(bad,s,1000)); assert(ValidTrajectory(bad,s,1000,true));
    bad.velocity[0]=1.21f; assert(!ValidTrajectory(bad,s,1000,true));
    bad=trajectory; bad.velocity[2]=.401f; assert(!ValidTrajectory(bad,s,1000));
    bad=trajectory; bad.contact=3; assert(!ValidTrajectory(bad,s,1000));
    bad=trajectory; bad.collision_generation=0; assert(!ValidTrajectory(bad,s,1000));
    bad=trajectory; bad.source_tick=1; assert(!ValidTrajectory(bad,s,1000));
    bad=trajectory; bad.position[2]=INFINITY; assert(!ValidTrajectory(bad,s,1000));
    bad=trajectory; bad.velocity[2]=.001f; assert(!TrajectoryInBounds(bad,entry,0,entry,0,true));
    auto airborne=trajectory;
    airborne.source_tick=1; airborne.source_time_us=16667; airborne.contact=AirContact;airborne.source_state=1;
    airborne.velocity[2]=.2286f; airborne.position[2]=.18f;
    changed=s; changed.ped_motion_flags=InAir;
    assert(ValidTrajectory(airborne,changed,1000));
    assert(TrajectoryInBounds(airborne,entry,0,entry,0,false));
    assert(!TrajectoryInBounds(airborne,entry,0,entry,0,true));
    auto rail=airborne; rail.contact=RailContact;rail.source_state=4;
    assert(ValidTrajectory(rail,changed,1000));
    assert(TrajectoryInBounds(rail,entry,0,entry,0,false));
    assert(!TrajectoryInBounds(rail,entry,0,entry,0,true));
    assert(!PreparationCommitMatches(rail,entry,0,entry));
    auto constrained=airborne;constrained.contact=ConstraintContact;constrained.source_state=2;
    assert(ValidTrajectory(constrained,changed,1000));
    assert(TrajectoryInBounds(constrained,entry,0,entry,0,false));
    assert(!TrajectoryInBounds(constrained,entry,0,entry,0,true));
    assert(!PreparationCommitMatches(constrained,entry,0,entry));
    bad=airborne; bad.position[2]=.36f; assert(!TrajectoryInBounds(bad,entry,0,entry,0,false));
    bad=airborne; bad.position[2]=64.01f; bad.source_time_us=4000000;
    assert(!TrajectoryInBounds(bad,entry,0,bad.position,3999999,false));
    assert(TrajectoryInBounds(bad,entry,0,bad.position,3999999,false,true));
    bad.position[2]=256.01f;
    assert(!TrajectoryInBounds(bad,entry,0,bad.position,3999999,false,true));
    auto trajectory_cache=trajectory;
    std::uint64_t advanced=1000;
    assert(ReadTrajectoryHeartbeat(ReadResult::Busy,{},trajectory_cache,s,trajectory.lease,1000,1,1100,advanced));
    changed=s; changed.tick_ms=1101;
    bad=trajectory; bad.lease.sequence=2; bad.lease.tick_ms=1101;
    assert(!ReadTrajectoryHeartbeat(ReadResult::Committed,bad,trajectory_cache,changed,trajectory.lease,1000,1,1101,advanced));
    assert(advanced==1000); // Repeated source, even with a fresh publisher heartbeat, expires.
    airborne.lease.sequence=2; airborne.lease.tick_ms=1017;
    assert(ReadTrajectoryHeartbeat(ReadResult::Committed,airborne,trajectory_cache,s,trajectory.lease,1000,1,1017,advanced));
    assert(advanced==1017 && trajectory_cache.source_tick==1);
    bad=airborne; bad.lease.sequence=3; bad.lease.tick_ms=1018; bad.position[0]=.001f;
    assert(!ReadTrajectoryHeartbeat(ReadResult::Committed,bad,trajectory_cache,s,trajectory.lease,1000,2,1018,advanced));
    bad=airborne; bad.lease.sequence=3; bad.collision_generation++;
    assert(!ReadTrajectoryHeartbeat(ReadResult::Committed,bad,trajectory_cache,s,trajectory.lease,1000,2,1018,advanced));
    bad=airborne; bad.lease.sequence=3; bad.source_tick++;
    assert(!ReadTrajectoryHeartbeat(ReadResult::Committed,bad,trajectory_cache,s,trajectory.lease,1000,2,1018,advanced));
    bad=airborne; bad.lease.sequence=3; bad.source_time_us++;
    assert(!ReadTrajectoryHeartbeat(ReadResult::Committed,bad,trajectory_cache,s,trajectory.lease,1000,2,1018,advanced));
    assert(!ReadTrajectoryHeartbeat(ReadResult::Cleared,airborne,trajectory_cache,s,trajectory.lease,1000,2,1018,advanced));
    bad=airborne; bad.lease.sequence=3; bad.source_tick=0; bad.source_time_us=0;
    assert(!ReadTrajectoryHeartbeat(ReadResult::Committed,bad,trajectory_cache,s,trajectory.lease,1000,2,1018,advanced));
    changed=s; changed.tick_ms=6000; bad=airborne; bad.lease.tick_ms=6000;
    assert(!ReadTrajectoryHeartbeat(ReadResult::Committed,bad,trajectory_cache,changed,trajectory.lease,1000,2,6000,advanced));
    assert(ContactFlags(0x12345678,GroundContact)==((0x12345678u&~ContactBits)|1u));
    assert(ContactFlags(0x12345678,AirContact)==((0x12345678u&~ContactBits)|0x200u));
    assert(ContactFlags(0x12345678,RailContact)==((0x12345678u&~ContactBits)|0x200u));
    assert(ContactFlags(0x12345678,ConstraintContact)==((0x12345678u&~ContactBits)|0x200u));
    assert((ContactFlags(0x602,RailContact)&2)==2);
    assert(SuppressesAmbientFall(RailContact) && SuppressesAmbientFall(AirContact));
    assert(SuppressesAmbientFall(ConstraintContact));
    assert(!SuppressesAmbientFall(GroundContact) && !SuppressesAmbientFall(PrepareAirContact));
    assert((ContactFlags(0x602,AirContact)&2)==2); // Historical bWasStanding is not owned.
    assert(AmbientAirIdentity(0x40068,0x40000,0x20000,0x20000,InAirEventVtable,false));
    assert(!AmbientAirIdentity(0x50068,0x40000,0x20000,0x20000,InAirEventVtable,false));
    assert(!AmbientAirIdentity(0x40068,0x40000,0x30000,0x20000,InAirEventVtable,false));
    assert(!AmbientAirIdentity(0x40068,0x40000,0x20000,0x20000,0x85AB38,false));
    assert(!AmbientAirIdentity(0x40068,0x40000,0x20000,0x20000,InAirEventVtable,true));
    assert(!AmbientAirIdentity(0x40068,0x40000,0,0,InAirEventVtable,false));
    unsigned char pad[112], original_pad[112];
    for(std::size_t i=0;i<sizeof(pad);++i) pad[i]=static_cast<unsigned char>(i+1);
    std::memcpy(original_pad,pad,sizeof(pad));
    std::uint64_t pad_sets=0,pad_restores=0,pad_conflicts=0;
    PadInputScoped(pad,pad,InspectMaskedPad,pad_sets,pad_restores,pad_conflicts);
    assert(!std::memcmp(pad,original_pad,PadInputBytes) && pad[96]==0xA7);
    assert(!std::memcmp(pad+97,original_pad+97,sizeof(pad)-97));
    assert(pad_sets==1 && pad_restores==1 && pad_conflicts==0);
    bool pad_caught=false;
    __try { PadInputScoped(pad,pad,FailMaskedPad,pad_sets,pad_restores,pad_conflicts); }
    __except(GetExceptionCode()==0xE0424244?EXCEPTION_EXECUTE_HANDLER:EXCEPTION_CONTINUE_SEARCH) { pad_caught=true; }
    assert(pad_caught && !std::memcmp(pad,original_pad,PadInputBytes));
    assert(pad_sets==2 && pad_restores==2 && pad_conflicts==0);
    PadInputScoped(pad,pad,NewerPadWriter,pad_sets,pad_restores,pad_conflicts);
    assert(pad[7]==0x55 && pad[0]==0 && pad[96]==0xA7);
    assert(!std::memcmp(pad+97,original_pad+97,sizeof(pad)-97));
    assert(pad_sets==3 && pad_restores==2 && pad_conflicts==1);
    std::uint32_t flags=2; std::uint64_t sets=0,restores=0;
    CollisionScoped(&flags,&flags,MutateUnrelated,Same,sets,restores);
    assert(flags==0x402 && sets==1 && restores==1);
    bool caught=false;
    __try { CollisionScoped(&flags,&flags,FailDuringTick,Same,sets,restores); }
    __except(GetExceptionCode()==0xE0424243?EXCEPTION_EXECUTE_HANDLER:EXCEPTION_CONTINUE_SEARCH) { caught=true; }
    assert(caught && flags==0x402 && sets==2 && restores==2);
    CollisionScoped(&flags,&flags,MutateUnrelated,Replaced,sets,restores);
    assert((flags&CollisionBypass) && sets==3 && restores==2);
    std::puts("Movement HOLD1/MOVE1/MOVE3: identity, 3D bounds, atomic source context/clocks, contact, heartbeat/deadline and scoped-bit restoration passed.");
}
