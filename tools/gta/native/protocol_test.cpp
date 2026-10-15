#include "bridge_protocol.h"
#include <cassert>
#include <cstdio>
#include <limits>
#include <initializer_list>

int main() {
    using namespace gta_thaw;
    Snapshot s{};
    std::memcpy(s.magic, "GTATHAW2", 8);
    s.version = Version; s.byte_size = sizeof(s); s.gta_pid = 123;
    s.capabilities = Telemetry | IsolatedProfile;
    s.process_creation_100ns = 456; s.sequence = 1; s.tick_ms = 1000;
    std::strcpy(s.profile_path, "I:\\GTA_THAW\\thaw_gta\\profile");
    assert(ValidSnapshot(s, 123, 456, 1100));
    assert(!ValidSnapshot(s, 124, 456, 1100));
    assert(!ValidSnapshot(s, 123, 457, 1100));
    assert(!ValidSnapshot(s, 123, 456, 999));
    assert(!ValidSnapshot(s, 123, 456, 2001));
    s.status_flags = PedPresent | OnFoot; s.ped_address = 0x123400; s.epoch = 1;
    s.health = 100; s.clock_hour = 18; s.clock_minute = 34; s.clock_second = 56;
    s.ped_motion_flags = Standing;
    assert(ValidSnapshot(s, 123, 456, 1100));
    assert(ControlEligible(s));
    s.status_flags |= TrajectoryOwned | NumLockOn;
    assert(ValidSnapshot(s,123,456,1100) && ControlEligible(s));
    assert((s.status_flags&TrajectoryOwned) && (s.status_flags&NumLockOn));
    s.status_flags &= ~(TrajectoryOwned | NumLockOn);
    assert(!PrerequisitesReady(s, 456, 1, 0, Telemetry | IsolatedProfile));
    const auto ready = VanillaRuntime | WorldGeometry | PedPose | OriginalCamera;
    assert(PrerequisitesReady(s, 456, 1, 0, ready));
    assert(!PrerequisitesReady(s, 456, 2, 0, ready));
    assert(!PrerequisitesReady(s, 456, 1, 1, ready));
    for (auto status : {Paused, ReadFault, InVehicle}) {
        s.status_flags = PedPresent | OnFoot | status;
        assert(!PrerequisitesReady(s, 456, 1, 0, ready));
    }
    s.status_flags = PedPresent | OnFoot;
    for (auto flags : {CutsceneRunning, CutsceneProcessing}) {
        s.cutscene_flags = flags;
        assert(ValidSnapshot(s, 123, 456, 1100));
        assert(!ControlEligible(s));
        assert(!PrerequisitesReady(s, 456, 1, 0, ready));
    }
    s.cutscene_flags = 0;
    s.controls_disabled_mask = 0x80;
    assert(ValidSnapshot(s, 123, 456, 1100));
    assert(!ControlEligible(s));
    s.controls_disabled_mask = 0;
    s.ped_motion_flags = InAir;
    assert(ControlEligible(s)); // Airborne handoff must not require Standing.
    s.ped_area = 1;
    assert(!ControlEligible(s));
    s.ped_area = 0;
    s.health = 0;
    assert(!ControlEligible(s));
    s.health = 100;
    auto invalid = s;
    invalid.clock_hour = 24; assert(!ValidSnapshot(invalid, 123, 456, 1100));
    invalid = s; invalid.clock_minute = 60; assert(!ValidSnapshot(invalid, 123, 456, 1100));
    invalid = s; invalid.clock_hour = 23; invalid.clock_minute = 59; invalid.clock_second = 60;
    assert(ValidSnapshot(invalid, 123, 456, 1100));
    invalid.clock_second = 61; assert(!ValidSnapshot(invalid, 123, 456, 1100));
    invalid = s; invalid.cutscene_flags = 4; assert(!ValidSnapshot(invalid, 123, 456, 1100));
    invalid = s; invalid.ped_motion_flags = 8; assert(!ValidSnapshot(invalid, 123, 456, 1100));
    invalid = s; invalid.controls_disabled_mask = 65536; assert(!ValidSnapshot(invalid, 123, 456, 1100));
    invalid = s; std::memcpy(invalid.magic, "GTATHAW1", 8); assert(!ValidSnapshot(invalid, 123, 456, 1100));
    invalid = s; invalid.version = 1; assert(!ValidSnapshot(invalid, 123, 456, 1100));
    invalid = s; invalid.byte_size = 248; assert(!ValidSnapshot(invalid, 123, 456, 1100));
    s.position[0] = std::numeric_limits<float>::quiet_NaN();
    assert(!ValidSnapshot(s, 123, 456, 1100));
    s.position[0] = 0;
    s.move_speed[2] = -10001;
    assert(!ValidSnapshot(s, 123, 456, 1100));
    s.move_speed[2] = 0;
    s.position[1] = 100001;
    assert(!ValidSnapshot(s, 123, 456, 1100));
    s.position[1] = 0;
    s.status_flags = 0;
    assert(!ValidSnapshot(s, 123, 456, 1100));
    s.status_flags = PedPresent | OnFoot;
    std::strcpy(s.profile_path, "relative\\profile");
    assert(!ValidSnapshot(s, 123, 456, 1100));
    std::memset(s.profile_path, 'x', sizeof(s.profile_path));
    assert(!ValidSnapshot(s, 123, 456, 1100));
    std::puts("GTA adapter V2: identity, age, clock, control/cutscene gate, airborne state and malformed data checks passed.");
}
