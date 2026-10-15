#include <cassert>
#include <cstdio>
#include <limits>
#include "camera_preview_math.h"
#include "weapon_aim_math.h"
#include "movement_hold_protocol.h"

using namespace gta_thaw;
using namespace gta_thaw::camera_preview;

bool Near(float a,float b) { return std::fabs(a-b)<.0001f; }
Packet CameraPacket() {
    Packet p{}; std::memcpy(p.magic,"GTACAM02",8); p.version=2; p.byte_size=sizeof(p);
    p.pid=17; p.epoch=3; p.created=123; p.request=9; p.sequence=1; p.tick_ms=1000;
    p.ped=0x20000; p.forward[1]=1; p.up[2]=1; p.horizontal_fov_degrees=72;
    p.view_window[0]=1.f/1.376381874f; p.view_window[1]=1.f/2.224455595f; p.source_aspect=1280.f/720.f;
    return p;
}
Snapshot Scene() {
    Snapshot s{}; std::memcpy(s.magic,"GTATHAW2",8); s.version=2; s.byte_size=sizeof(s);
    s.gta_pid=17; s.process_creation_100ns=123; s.sequence=1; s.tick_ms=1000;
    s.capabilities=Telemetry|IsolatedProfile; s.ped_address=0x20000; s.epoch=3; s.health=100;
    s.status_flags=PedPresent|OnFoot|Foreground; s.ped_motion_flags=InAir;
    std::strcpy(s.profile_path,"I:\\GTA_THAW\\thaw_gta\\profile"); return s;
}
void TestGates() {
    auto p=CameraPacket(); auto s=Scene();
    assert(ValidPacket(p,s,1000) && ValidPacket(p,s,1100));
    assert(!ValidPacket(p,s,999) && !ValidPacket(p,s,1101));
    auto q=p; q.epoch++; assert(!ValidPacket(q,s,1000));
    q=p; q.created++; assert(!ValidPacket(q,s,1000));
    q=p; q.request=0; assert(!ValidPacket(q,s,1000));
    q=p; q.reserved=1; assert(!ValidPacket(q,s,1000));
    q=p; q.source_tick=1; assert(!ValidPacket(q,s,1000));
    q.source_time_us=16667; assert(ValidPacket(q,s,1000));
    q.source_tick=432000;q.source_time_us=7200000000ULL; assert(ValidPacket(q,s,1000));
    assert(!ValidPacket(q,s,1101));
    q=p; q.up[2]=0; q.up[1]=1; assert(!ValidPacket(q,s,1000));
    q=p; q.horizontal_fov_degrees=std::numeric_limits<float>::quiet_NaN(); assert(!ValidPacket(q,s,1000));
    q=p; q.eye[0]=100.01f; assert(!ValidPacket(q,s,1000));
    q=p; q.view_window[0]=1; assert(!ValidPacket(q,s,1000));
    q=p; q.view_window[1]=0; assert(!ValidPacket(q,s,1000));
    q=p; q.source_aspect=std::numeric_limits<float>::quiet_NaN(); assert(!ValidPacket(q,s,1000));
    assert(ProjectionMatchesTarget(p,1280,720) && ProjectionMatchesTarget(p,1920,1080));
    assert(!ProjectionMatchesTarget(p,1280,800) && !ProjectionMatchesTarget(p,720,1280));
    assert(!ProjectionMatchesTarget(p,0,0));
    s.cutscene_flags=1; assert(!ValidPacket(p,s,1000));
    s=Scene(); s.status_flags&=~Foreground; assert(!ValidPacket(p,s,1000));
    s=Scene(); s.controls_disabled_mask=1; assert(!ValidPacket(p,s,1000));
}
void TestHistory() {
    auto a=CameraPacket(),b=a;
    assert(Continues(a,a));
    b.sequence++; b.tick_ms++; assert(Continues(b,a));
    b.eye[0]=1; assert(!Continues(b,a)); // A heartbeat cannot alter an old original sample.
    b=a; b.tick_ms++; assert(!Continues(b,a)); // Same committed sequence must be byte-identical.
    b=a; b.sequence++; b.request++; assert(!Continues(b,a));
    b=a; b.sequence++; b.source_tick++; b.source_time_us=16667; b.eye[0]=1;
    assert(Continues(b,a) && !Continues(a,b));
    b.source_time_us=0; assert(!Continues(b,a));
    using movement_hold::ClassifyRead;
    using movement_hold::ReadResult;
    assert(ClassifyRead(3,2,3)==ReadResult::Busy);
    assert(ClassifyRead(2,4,4)==ReadResult::Busy);
    assert(ClassifyRead(2,2,2)==ReadResult::Committed);
    assert(ClassifyRead(3,2,0)==ReadResult::Cleared);
}
void TestHeartbeatRecovery() {
    auto p=CameraPacket(); auto s=Scene();
    assert(!ValidPacket(p,s,1101));
    assert(!BlocksRequestAfterEnd(1,true) && !BlocksRequestAfterEnd(2,true));
    // A fresh native camera sample can resume the existing MOVE request.
    auto next=p; next.sequence++; next.tick_ms=1101;
    next.source_tick=1; next.source_time_us=16667; next.eye[0]=1;
    s.tick_ms=1101;
    assert(next.request==p.request && Continues(next,p) && ValidPacket(next,s,1101));
    assert(NormalResetSampleReady(next,p,s,1101));
    assert(!NormalResetSampleReady(p,p,Scene(),1000)); // The reset needs a new committed packet.
    assert(!NormalResetSampleReady(next,p,s,1202)); // A new sequence cannot bypass freshness.
    for(auto reason : {1u,2u,3u,4u,6u,7u}) assert(BlocksRequestAfterEnd(reason,false));
    for(auto reason : {0u,3u,4u,6u,7u}) assert(BlocksRequestAfterEnd(reason,true));
    // Recovery never admits a changed rig, process or source sample identity.
    next.ped+=4; assert(!ValidPacket(next,s,1101) && !Continues(next,p));
    assert(!NormalResetSampleReady(next,p,s,1101));
    next=p; next.created++; assert(!ValidPacket(next,s,1000) && !Continues(next,p));
    assert(!NormalResetSampleReady(next,p,s,1000));
    for(auto mask : {4u,260u,516u,772u}) assert(NormalResetCandidate(4,mask,true));
    for(unsigned shift=0;shift<32;++shift) {
        const auto bit=1u<<shift;
        if(!(772u&bit)) assert(!NormalResetCandidate(4,772u|bit,true));
    }
    assert(!NormalResetCandidate(4,768,true)); // Zoom alone is not a normal camera reset.
    assert(!NormalResetCandidate(4,772,false)); // Foreign projection changes stay blocked.
    for(auto reason : {1u,2u,3u,6u,7u}) assert(!NormalResetCandidate(reason,772,true));
}
void TestDelayedNormalReset() {
    auto p=CameraPacket(), next=p; auto s=Scene();
    assert(NormalResetCandidate(4,772,true));
    next.sequence++; next.source_tick=1; next.source_time_us=16667; next.tick_ms=1017;
    s.tick_ms=1017;
    assert(!NormalResetReady(next,p,s,1017,false)); // FollowPed not active in the reset's first cycle.
    next.sequence++; next.tick_ms=1025; s.tick_ms=1025;
    assert(!NormalResetReady(next,p,s,1025,false)); // Never write while a script/cinematic owns it.
    next.sequence++; next.tick_ms=1033; s.tick_ms=1033;
    assert(NormalResetReady(next,p,s,1033,true)); // Same lease can recover after multiple cycles.
    assert(!NormalResetReady(p,p,Scene(),1000,true)); // A reset cannot replay its old packet.
    auto changed=next; changed.request++;
    assert(!NormalResetReady(changed,p,s,1033,true)); // F7's next lease cannot inherit the old recovery.
    changed=next; changed.epoch++;
    assert(!NormalResetReady(changed,p,s,1033,true));
    changed=next; changed.source_tick=0;
    assert(!NormalResetReady(changed,p,s,1033,true));
    assert(!NormalResetReady(next,p,s,1134,true));
}
void TestOwnedFollowPedTransition() {
    FollowPedResetTransition t{772,4,4,0x20000,0x20000,1350,8,.05f,.95f,false,false};
    assert(KnownFollowPedReset(t));
    // A fresh packet can replace this known blend immediately, without waiting
    // for 163 presentation frames. Initial takeover and other blends remain gated.
    auto p=CameraPacket(),next=p; auto s=Scene();
    next.sequence++; next.tick_ms=1008; s.tick_ms=1008;
    assert(NormalResetReady(next,p,s,1008,KnownFollowPedReset(t)));
    assert(!NormalResetReady(p,p,Scene(),1000,KnownFollowPedReset(t)));
    next.request++; assert(!NormalResetReady(next,p,s,1008,KnownFollowPedReset(t)));
    auto q=t; q.active_mode=15; assert(!KnownFollowPedReset(q));
    q=t; q.desired_mode=15; assert(!KnownFollowPedReset(q));
    q=t; q.target+=4; assert(!KnownFollowPedReset(q));
    q=t; q.duration_ms=1000; assert(!KnownFollowPedReset(q));
    q=t; q.elapsed_ms=1351; assert(!KnownFollowPedReset(q));
    q=t; q.elapsed_ms=0xffffffff; assert(!KnownFollowPedReset(q));
    q=t; q.stop_moving=.25f; assert(!KnownFollowPedReset(q));
    q=t; q.stop_catch_up=.75f; assert(!KnownFollowPedReset(q));
    q=t; q.stop_moving=std::numeric_limits<float>::quiet_NaN(); assert(!KnownFollowPedReset(q));
    q=t; q.special_interpolation=true; assert(!KnownFollowPedReset(q));
    q=t; q.script_interpolation=true; assert(!KnownFollowPedReset(q));
    for(unsigned shift=0;shift<32;++shift) if(!(772u&(1u<<shift))) {
        q=t; q.mismatch|=1u<<shift; assert(!KnownFollowPedReset(q));
    }
}
void TestRecoveredResetKeepsActorTimelineAndAim() {
    CameraHistory history;
    AimController aim;
    auto source=CameraPacket(); auto scene=Scene(); Packet shown,aimed;
    float root[3]{}, rendered[3];
    std::uint64_t now=1000000;
    for(unsigned frame=0;frame<120;++frame) {
        now=1000000+frame*8333;
        if(!(frame%2)) {
            source.source_tick=frame/2+1;
            source.source_time_us=source.source_tick*16667;
            source.eye[0]=static_cast<float>(source.source_time_us)*.00001f;
            root[0]=source.eye[0];
        }
        source.sequence++; source.tick_ms=now/1000;
        assert(history.Present(source,now,shown,root));
        assert(aim.Present(shown,root,true,1,0,.002f,.002f,now,aimed));
    }
    const auto previous=source;
    const auto cursor=history.clock.cursor_us;
    const auto yaw=aim.yaw;
    assert(aim.blend==1 && history.clock.count==history.clock.Capacity);
    // The original game resets only its own FollowPed/FOV fields. The old
    // packet is rejected, then a fresh same-lease heartbeat allows recovery.
    FollowPedResetTransition reset{772,4,4,source.ped,source.ped,1350,8,.05f,.95f,false,false};
    assert(KnownFollowPedReset(reset));
    scene.tick_ms=source.tick_ms;
    assert(!NormalResetReady(source,previous,scene,source.tick_ms,true));
    now+=8333; source.sequence++; source.tick_ms=now/1000; scene.tick_ms=source.tick_ms;
    assert(NormalResetReady(source,previous,scene,source.tick_ms,KnownFollowPedReset(reset)));
    auto discarded=history; discarded.Clear();
    Packet snapped;
    assert(discarded.Present(source,now,snapped,root));
    assert(history.Present(source,now,shown,root) && history.Origin(rendered));
    assert(history.clock.cursor_us>=cursor && history.clock.cursor_us-cursor<=8750);
    assert(Near(shown.eye[0],rendered[0]));
    assert(discarded.clock.cursor_us>history.clock.cursor_us+2000);
    assert(aim.Present(shown,rendered,true,0,0,.002f,.002f,now,aimed));
    assert(Near(aim.yaw,yaw) && aim.blend==1);
    // J changes still reset both histories through their ordinary identity gates.
    source.request++; source.sequence++; source.tick_ms++;
    assert(!NormalResetReady(source,previous,scene,source.tick_ms,true));
    assert(history.Present(source,now+1000,shown,root) && history.clock.count==1);
    assert(aim.Present(shown,root,true,0,0,.002f,.002f,now+1000,aimed));
    assert(aim.blend==0 && Near(aim.yaw,std::atan2(source.forward[1],source.forward[0])));
}
void TestInterpolation() {
    auto a=CameraPacket(),b=a;
    b.source_tick=1; b.source_time_us=16000; b.tick_ms=1016; b.sequence=2;
    b.eye[0]=16; b.forward[0]=-1; b.forward[1]=0; b.horizontal_fov_degrees=88;
    b.view_window[0]=std::tan(44.f/57.295779513f); b.view_window[1]=.6f;
    auto c=Interpolate(a,b,1016);
    assert(Near(c.eye[0],0) && Near(c.forward[1],1) && Near(c.horizontal_fov_degrees,72));
    c=Interpolate(a,b,1024);
    assert(Near(c.eye[0],8) && Near(c.forward[0],-std::sqrt(.5f)) &&
           Near(c.forward[1],std::sqrt(.5f)) && Near(c.up[2],1));
    assert(Near(c.view_window[0],(a.view_window[0]+b.view_window[0])*.5f));
    assert(Near(c.view_window[1],(a.view_window[1]+b.view_window[1])*.5f));
    assert(ValidPose(c));
    c=Interpolate(a,b,1200); assert(Near(c.eye[0],16) && Near(c.forward[0],-1)); // No extrapolation.
    b.request++; c=Interpolate(a,b,1024); assert(Near(c.eye[0],16)); // New lease never interpolates old scene.
}
void TestRollAndHalfTurn() {
    auto p=CameraPacket();
    p.forward[1]=-1; p.up[2]=-1; // 180 degrees about right: quaternion trace branch.
    float q[4],negated[4],middle[4],f[3],u[3];
    Quaternion(p,q); Axes(q,f,u);
    assert(Near(f[1],-1) && Near(u[2],-1));
    for(int i=0;i<4;++i) negated[i]=-q[i];
    Slerp(q,negated,.5f,middle); Axes(middle,f,u);
    assert(Near(f[1],-1) && Near(u[2],-1)); // Antipodal representation is the same orientation.
    auto a=CameraPacket(),b=a; b.up[2]=0; b.up[0]=1;
    b.source_tick=1; b.source_time_us=16000; b.tick_ms=1016;
    const auto c=Interpolate(a,b,1024);
    assert(Near(c.forward[1],1) && Near(c.up[0],std::sqrt(.5f)) && Near(c.up[2],std::sqrt(.5f)));
    assert(ValidPose(c));
}
void TestPresentationClockIdentityAndBounds() {
    CameraHistory early_hook,late_hook;
    auto first=CameraPacket(),next=first;
    first.source_tick=1; first.source_time_us=16667;
    next.source_tick=2; next.source_time_us=33333; next.sequence=2; next.tick_ms=1010; next.eye[0]=10;
    Packet a{},b{};
    assert(early_hook.Present(first,(1000)*1000,a));
    assert(late_hook.Present(first,(1007)*1000,b));
    assert(early_hook.Present(next,(1010)*1000,a));
    assert(late_hook.Present(next,(1013)*1000,b));
    assert(early_hook.Present(next,(1025)*1000,a) && late_hook.Present(next,(1025)*1000,b));
    assert(early_hook.clock.cursor_us==late_hook.clock.cursor_us && Near(a.eye[0],b.eye[0]));
    auto heartbeat=next; heartbeat.sequence++; heartbeat.tick_ms=1026;
    assert(early_hook.Present(heartbeat,(1030)*1000,a) && late_hook.Present(heartbeat,(1030)*1000,b));
    assert(early_hook.clock.cursor_us==late_hook.clock.cursor_us && Near(a.eye[0],b.eye[0]));
    auto invalid=heartbeat; invalid.eye[0]++;
    assert(!early_hook.Present(invalid,(1030)*1000,a)); // A duplicate source clock stays immutable.
    heartbeat.request++;
    assert(early_hook.Present(heartbeat,(1030)*1000,a) && early_hook.clock.count==1);
    assert(Near(a.eye[0],heartbeat.eye[0]));
    bool rebased=false;
    for(unsigned n=1;n<=10;++n) {
        heartbeat.source_tick++; heartbeat.source_time_us+=16667;
        heartbeat.sequence++; heartbeat.tick_ms=1030+n; heartbeat.eye[0]+=1;
        assert(early_hook.Present(heartbeat,(1030+n)*1000,a));
        rebased|=early_hook.clock.rebased;
        const auto& clock=early_hook.clock;
        assert(clock.count<=PresentationClock::Capacity);
        assert(clock.cursor_us>=clock.samples[0].source_us &&
               clock.cursor_us<=clock.samples[clock.count-1].source_us);
    }
    assert(rebased); // An impossible long burst cannot grow history/delay without a bound.
}
void TestAiming() {
    const auto source=CameraPacket();
    const float origin[3]{1,2,3};
    Packet result;
    AimController aim;
    assert(aim.Present(source,origin,true,0,0,.002f,.002f,(1000)*1000,result));
    for(unsigned tick=1;tick<=24;++tick)
        assert(aim.Present(source,origin,true,1,0,.002f,.002f,(1000+tick*8)*1000,result));
    assert(Near(aim.blend,1) && ValidPose(result));
    assert(Near(aim.yaw,AimPi*.5f-24*.006f));
    assert(Near(result.horizontal_fov_degrees,60));
    // Same total mouse distance gives the same direction at a different FPS.
    AimController slower;
    assert(slower.Present(source,origin,true,0,0,.002f,.002f,(1000)*1000,result));
    for(unsigned tick=1;tick<=12;++tick)
        assert(slower.Present(source,origin,true,2,0,.002f,.002f,(1000+tick*16)*1000,result));
    assert(Near(slower.yaw,aim.yaw));
    assert(slower.Present(source,origin,true,1000,1000,.002f,.002f,(1199)*1000,result,true,3.f));
    assert(std::fabs(WrapAim(slower.yaw-3.f))<=95.f*AimPi/180.f+.00001f && slower.pitch<=AimPi/3.f);
    // A hanging actor's gameplay root can be above the ledge. A measured chest
    // origin must not receive the standing character's extra height.
    auto hanging=slower;
    const float chest[3]{origin[0],origin[1],origin[2]-2.f};
    assert(hanging.Present(source,chest,true,0,0,.002f,.002f,(1200)*1000,result,true,3.f,0.f));
    assert(Near(result.eye[2]+result.forward[2]*2.f,chest[2]));
    assert(slower.Present(source,origin,true,1000,1000,.002f,.002f,(1200)*1000,result));
    assert(slower.pitch<=1.15f && std::fabs(slower.yaw)<=AimPi && ValidPose(result));
    const auto old=slower;
    assert(!slower.Present(source,origin,true,std::numeric_limits<float>::quiet_NaN(),0,.002f,.002f,(1210)*1000,result));
    assert(Near(slower.yaw,old.yaw));
    for(unsigned tick=1;tick<=24;++tick)
        assert(aim.Present(source,origin,false,0,0,.002f,.002f,(1200+tick*8)*1000,result));
    assert(!aim.held && Near(aim.blend,0));
    assert(std::memcmp(&result,&source,sizeof(result))==0);
    auto next=source; ++next.request;
    assert(aim.Present(next,origin,false,0,0,.002f,.002f,(1500)*1000,result));
    assert(!aim.held && !aim.blend);
}
void TestCameraAndActorSharePresentationClock() {
    CameraHistory h;
    auto source=CameraPacket(); source.source_tick=1; source.source_time_us=16667;
    float root[3]{0,0,0},actual[3]; Packet shown;
    assert(h.Present(source,(1000)*1000,shown,root) && h.Origin(actual) && Near(actual[0],0));
    source.source_tick=2; source.source_time_us=32667; source.tick_ms=1016; ++source.sequence;
    source.eye[0]=16;root[0]=16;
    assert(h.Present(source,(1016)*1000,shown,root) && h.Origin(actual));
    assert(Near(shown.eye[0],actual[0]));
    assert(h.Present(source,(1024)*1000,shown,root) && h.Origin(actual));
    assert(Near(actual[0],8) && Near(shown.eye[0],actual[0]));
    assert(h.Present(source,(1090)*1000,shown,root) && h.Origin(actual));
    assert(Near(actual[0],16)); // Stalls hold both at the same source position.
    h.Clear(); assert(!h.Origin(actual));
    assert(h.Present(source,(1091)*1000,shown) && !h.Origin(actual));
}
int main() {
    TestGates(); TestHistory(); TestHeartbeatRecovery(); TestDelayedNormalReset(); TestOwnedFollowPedTransition();
    TestRecoveredResetKeepsActorTimelineAndAim();
    TestInterpolation(); TestRollAndHalfTurn();
    TestPresentationClockIdentityAndBounds();
    TestAiming(); TestCameraAndActorSharePresentationClock();
    std::puts("Camera packet, history, interpolation and roll tests passed");
}
