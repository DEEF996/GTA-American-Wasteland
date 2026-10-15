#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <cassert>
#include <cstdio>
#include <initializer_list>
#include <limits>
#include "aim_input_protocol.h"
using namespace gta_thaw;
int main() {
    Snapshot s{};s.gta_pid=17;s.process_creation_100ns=123;s.ped_address=0x20000;s.epoch=3;
    s.health=100;s.f7_requests=9;s.status_flags=PedPresent|OnFoot|Foreground|TrajectoryOwned;
    const float forward[3]{0,1,0};
    auto p=aim_input::Make(s,7,1000,44,9,forward);
    assert(aim_input::Valid(p,17,123,1000));assert(aim_input::Valid(p,17,123,1100));
    assert(!aim_input::Valid(p,17,123,1101));assert(!aim_input::Valid(p,17,123,999));
    assert(!aim_input::Valid(p,18,123,1000));assert(!aim_input::Valid(p,17,124,1000));
    assert(p.flags==aim_input::Active && p.request==44 && p.ped_address==s.ped_address);
    assert(p.forward[1]==1 && p.entry_f7_requests==9 && p.f7_requests==9);
    auto bad=p;bad.forward[0]=std::numeric_limits<float>::quiet_NaN();assert(!aim_input::Valid(bad,17,123,1000));
    bad=p;bad.forward[1]=0;bad.forward[2]=1;assert(!aim_input::Valid(bad,17,123,1000));
    bad=p;bad.forward[1]=2;assert(!aim_input::Valid(bad,17,123,1000));
    bad=p;bad.entry_f7_requests++;assert(!aim_input::Valid(bad,17,123,1000));
    bad=p;bad.current_area=1;assert(!aim_input::Valid(bad,17,123,1000));
    bad=p;bad.flags|=2;assert(!aim_input::Valid(bad,17,123,1000));
    bad=p;bad.reserved=1;assert(!aim_input::Valid(bad,17,123,1000));
    auto inactive=aim_input::Make(s,8,1001,44,9,nullptr);
    assert(!inactive.flags && !inactive.request && aim_input::Valid(inactive,17,123,1001));
    bad=inactive;bad.request=44;assert(!aim_input::Valid(bad,17,123,1001));
    bad=inactive;bad.forward[0]=1;assert(!aim_input::Valid(bad,17,123,1001));
    for(auto blocked : {s.status_flags&~Foreground,s.status_flags&~TrajectoryOwned,
                        s.status_flags|TrajectoryPreparing,s.status_flags|Paused}) {
        auto current=s;current.status_flags=blocked;
        assert(!aim_input::Make(current,9,1002,44,9,forward).flags);
    }
    assert(!aim_input::Make(s,9,1002,44,8,forward).flags);
    assert(!aim_input::Make(s,9,1002,0,9,forward).flags);
    std::puts("Aim input: wire sizes, identity, freshness, direction and inactive clearing passed.");
}
