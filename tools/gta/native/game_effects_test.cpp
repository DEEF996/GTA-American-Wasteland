#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <cassert>
#include <cstdio>
#include <initializer_list>
#include <limits>
#include "game_effects_protocol.h"
#include "game_effects_ownership.h"
using namespace gta_thaw;
using namespace gta_thaw::game_effects;
namespace {
struct World {
    Controller control;float memory=1;unsigned writes=0;
    Decision Step(std::uint64_t us,bool lease=true,bool aim=true,bool shift=false) {
        const auto d=control.Step(us,lease,aim,shift,memory);
        if(d.write) { assert(d.expected==memory);memory=d.value;++writes; }
        return d;
    }
    void Start(std::uint64_t at=1000000) { Step(at-1);Step(at,true,true,true); }
};
void Timing() {
    for(auto fps:{30u,60u,120u,240u}) {
        World w;w.Start();assert(w.control.Owned());assert(w.control.LastEvent()==Event::Enter);
        float previous=1;
        for(unsigned frame=1;frame<=5*fps;++frame) {
            const auto elapsed=static_cast<std::uint64_t>(frame)*1000000/fps;
            w.Step(1000000+elapsed,true,true,true);
            assert(w.memory>=TargetScale-1e-6f && w.memory<=1);
            if(elapsed<EnterUs) assert(w.memory<=previous);
            if(elapsed>=EnterUs && elapsed<=MaximumUs-ExitUs) assert(std::fabs(w.memory-TargetScale)<1e-6f);
            if(elapsed>MaximumUs-ExitUs) assert(w.memory>=previous);
            previous=w.memory;
        }
        assert(w.memory==1 && !w.control.Owned());
        assert(w.control.EventSequence()==3 && w.control.LastEvent()==Event::Restored);
        w.Step(7000000,true,true,true);assert(!w.control.Owned()); // A held Shift cannot retrigger.
        w.Step(7000001);w.Step(7000002,true,true,true);assert(w.control.Owned());
    }
    World jitter;jitter.Start();
    for(std::uint64_t us=1010000;us<6100000;us+=13771) jitter.Step(us,true,true,true);
    assert(!jitter.control.Owned() && jitter.memory==1);
    World stalled;stalled.Start();stalled.Step(1200000);stalled.Step(9000000,true,true,true);
    assert(!stalled.control.Owned() && stalled.memory==1); // Wall time, never number of ticks.
}
void InputAndOwnership() {
    World held;held.Step(1,true,true,true);held.Step(200000,true,true,true);assert(!held.control.Owned());
    held.Step(200001);held.Step(200002,false,true,true);held.Step(200003,true,true,true);assert(!held.control.Owned());
    held.Step(200004);held.Step(200005,true,false,true);held.Step(200006,true,true,true);assert(!held.control.Owned());
    World toggle;toggle.Start();toggle.Step(1200000);toggle.Step(1250000,true,true,true);
    assert(toggle.control.Exiting());const float first=toggle.memory;
    toggle.Step(1400000,true,true,false);assert(toggle.memory>first && toggle.memory<1);
    toggle.Step(1400001,true,true,true);assert(toggle.control.Exiting());
    toggle.Step(1550000,true,true,true);assert(toggle.memory==1 && !toggle.control.Owned());
    World released;released.Start();released.Step(1200000);released.Step(1210000,true,false);
    assert(released.control.Exiting());released.Step(1510000,true,false);assert(released.memory==1);
    for(auto foreign:{.5f,.25f,2.f,0.f,std::numeric_limits<float>::quiet_NaN()}) {
        World w;w.memory=foreign;w.Start();assert(!w.control.Owned() && w.writes==0);
    }
    World conflict;conflict.Start();conflict.Step(1200000);conflict.memory=.75f;
    assert(conflict.Step(1200001).conflict);assert(conflict.memory==.75f && !conflict.control.Owned());
    World lost;lost.Start();lost.Step(1200000);lost.Step(1200001,false);
    assert(lost.memory==1 && !lost.control.Owned());
    World clock;clock.Start();clock.Step(1200000);clock.Step(1199999);assert(clock.memory==1);
}
void Wire() {
    Snapshot s{};s.gta_pid=17;s.process_creation_100ns=123;s.ped_address=0x20000;s.epoch=3;
    s.health=100;s.f7_requests=9;s.tick_ms=1000;s.status_flags=PedPresent|OnFoot|Foreground|TrajectoryOwned;
    World w;w.Start();w.Step(1200000);
    auto p=Make(s,7,1000,44,9,w.control,.5f);
    assert(p.flags==(LeaseActive|SlowMotionOwned) && p.request==44);
    assert(Valid(p,17,123,1100) && !Valid(p,17,123,1101) && !Valid(p,17,123,999));
    assert(!Valid(p,18,123,1000) && !Valid(p,17,124,1000));
    auto bad=p;bad.time_scale=1;assert(!Valid(bad,17,123,1000));
    bad=p;bad.effect_amount=std::numeric_limits<float>::quiet_NaN();assert(!Valid(bad,17,123,1000));
    bad=p;bad.sfx_volume=1.01f;assert(!Valid(bad,17,123,1000));
    bad=p;bad.event_kind=3;assert(!Valid(bad,17,123,1000));
    bad=p;bad.flags|=Exiting;assert(!Valid(bad,17,123,1000));
    bad=p;bad.event_sequence=0;assert(!Valid(bad,17,123,1000));
    bad=p;bad.entry_f7_requests++;assert(!Valid(bad,17,123,1000));
    bad=p;bad.flags=SlowMotionOwned;assert(!Valid(bad,17,123,1000));
    for(auto flags:{s.status_flags&~Foreground,s.status_flags&~TrajectoryOwned,s.status_flags|Paused,
                    s.status_flags|TrajectoryPreparing}) {
        auto inactive=s;inactive.status_flags=flags;auto q=Make(inactive,8,1000,44,9,w.control,.7f);
        assert(!q.flags && !q.request && q.time_scale==1 && q.effect_amount==0 && q.sfx_volume==.7f);
        assert(Valid(q,17,123,1000));
    }
    w.Step(1200001,true,false);p=Make(s,8,1000,44,9,w.control,0);
    assert(p.flags==(LeaseActive|SlowMotionOwned|Exiting) && p.event_kind==2 && Valid(p,17,123,1000));
    w.Step(1500001,true,false);p=Make(s,9,1000,44,9,w.control,1);
    assert(p.flags==LeaseActive && p.event_kind==3 && p.time_scale==1 && Valid(p,17,123,1000));
    p=Make(s,10,1000,0,9,w.control,0);assert(!p.flags && Valid(p,17,123,1000));
}
struct PairMemory {
    float time=1,pitch=.5f;
    unsigned calls=0,fail_at=0;
    bool foreign_pitch_on_failure=false;
    Scalar order[32]{};
    float Read(Scalar s) noexcept { return s==Scalar::Time?time:pitch; }
    bool Exchange(Scalar s,float expected,float value) noexcept {
        assert(calls<32);order[calls++]=s;
        float& actual=s==Scalar::Time?time:pitch;
        if(calls==fail_at) {
            actual=.75f;if(foreign_pitch_on_failure) pitch=.8f;return false;
        }
        if(actual!=expected) return false;
        actual=value;return true;
    }
};
void CoupledOwnership() {
    ScaleOwnership owner;PairMemory m;
    assert(owner.Set(1,m) && owner.Held() && m.time==1 && m.pitch==1);
    assert(m.order[0]==Scalar::Pitch && m.order[1]==Scalar::Time);
    assert(owner.Set(.3f,m) && m.time==.3f && m.pitch==.3f);
    owner.Release(m);assert(m.time==1 && m.pitch==.5f && !owner.Held());
    assert(m.order[4]==Scalar::Time && m.order[5]==Scalar::Pitch);
    assert(owner.Restores()==1 && owner.Conflicts()==0);
    for(bool pitch:{false,true}) {
        ScaleOwnership foreign;PairMemory f;if(pitch) f.pitch=.8f;else f.time=.6f;
        assert(!foreign.Set(1,f) && !foreign.Held() && !f.calls);
    }
    for(bool foreign_pitch:{false,true}) {
        ScaleOwnership rollback;PairMemory r;r.fail_at=2;r.foreign_pitch_on_failure=foreign_pitch;
        assert(!rollback.Set(.9f,r) && !rollback.Held());
        assert(r.time==.75f && r.pitch==(foreign_pitch?.8f:.5f));
    }
    ScaleOwnership lost;PairMemory l;assert(lost.Set(.3f,l));l.pitch=.8f;
    assert(!lost.Set(.4f,l) && l.time==1 && l.pitch==.8f && !lost.Held());
    ScaleOwnership replaced;PairMemory r;assert(replaced.Set(.3f,r));r.fail_at=4;
    assert(!replaced.Set(.4f,r) && r.time==.75f && r.pitch==.5f && !replaced.Held());
}
}
int main() {
    Timing();InputAndOwnership();Wire();CoupledOwnership();
    std::puts("Game effects: real 5s bound, 30/60/120/240Hz ramps, Shift edges, toggle/aim release, paired time/pitch ownership and partial-CAS rollback, identity and wire validation passed.");
}
