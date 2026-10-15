#include <cassert>
#include <cstdio>
#include <limits>
#include "board_attack_protocol.h"
using namespace gta_thaw;
using namespace gta_thaw::board_attack;

int main() {
    Snapshot s{};
    std::memcpy(s.magic,"GTATHAW2",8); s.version=2; s.byte_size=sizeof(s);
    s.gta_pid=17; s.process_creation_100ns=123; s.sequence=1; s.tick_ms=1000;
    s.capabilities=Telemetry|IsolatedProfile; s.ped_address=0x20000; s.epoch=3;
    s.health=100; s.status_flags=PedPresent|OnFoot|Foreground;
    std::strcpy(s.profile_path,"I:\\GTA_THAW\\thaw_gta\\profile");
    Packet p{};
    std::memcpy(p.magic,"GTAHIT01",8); p.version=1; p.byte_size=sizeof(p); p.pid=17;
    p.created=123; p.epoch=3; p.ped=0x20000; p.request=90; p.sequence=1; p.tick_ms=1000;
    p.source_tick=60; p.source_time_us=1000000; p.attack_id=1; p.whacking=p.walking=1;
    assert(ValidPacket(p,s,1000,90,60,1000000));
    assert(!ValidPacket(p,s,1101,90,60,1000000));
    assert(!ValidPacket(p,s,1000,89,60,1000000));
    assert(!ValidPacket(p,s,1000,90,59,1000000));
    assert(!ValidPacket(p,s,1000,90,60,999999));
    assert(!ValidPacket(p,s,1000,90,70,1100001));
    auto changed=p; changed.epoch++; assert(!ValidPacket(changed,s,1000,90,60,1000000));
    changed=p; changed.whacking=2; assert(!ValidPacket(changed,s,1000,90,60,1000000));
    auto blocked=s; blocked.status_flags&=~Foreground; assert(!ValidPacket(p,blocked,1000,90,60,1000000));
    blocked=s; blocked.cutscene_flags=1; assert(!ValidPacket(p,blocked,1000,90,60,1000000));
    Edges edges;
    assert(edges.Consume(p)); assert(!edges.Consume(p));
    ++p.sequence; ++p.source_tick; p.source_time_us+=16667;
    assert(!edges.Consume(p)); // Holding/republication cannot cause repeated damage.
    ++p.attack_id; ++p.sequence; ++p.source_tick; p.source_time_us+=16667;
    assert(edges.Consume(p));
    changed=p; --changed.attack_id; ++changed.sequence; assert(!edges.Consume(changed));
    ++p.attack_id; ++p.sequence; ++p.source_tick; p.source_time_us+=16667; p.walking=0;
    assert(!edges.Consume(p)); p.walking=1; assert(!edges.Consume(p));
    ++p.request; p.attack_id=0; assert(!edges.Consume(p));
    ++p.attack_id; ++p.sequence; ++p.source_tick; p.source_time_us+=16667; assert(edges.Consume(p));

    const float origin[3]{0,0,1},front[3]{0,1.5f,1},behind[3]{0,-1,1};
    const float far[3]{0,1.7f,1},above[3]{0,1,2},nan[3]{0,std::numeric_limits<float>::quiet_NaN(),1};
    float distance=0;
    assert(InReach(origin,0,front,distance) && distance==2.25f);
    assert(!InReach(origin,0,behind,distance) && !InReach(origin,0,far,distance));
    assert(!InReach(origin,0,above,distance) && !InReach(origin,0,nan,distance));
    assert(InReach(origin,3.14159265f,behind,distance));
    unsigned index=0;
    assert(PoolIndex(0x20000+0x7C4*7,0x20000,140,index) && index==7);
    assert(!PoolIndex(0x20004,0x20000,140,index));
    assert(!PoolIndex(0x20000+0x7C4*140,0x20000,140,index));
    assert(!PoolIndex(0x20000,0x20000,2049,index));
    std::puts("Board attack: lease/source clocks, one event per swing, melee reach and ped-pool bounds passed.");
}
