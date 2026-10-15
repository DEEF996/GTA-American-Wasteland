#include <cassert>
#include <cstdio>
#include "startup_status_protocol.h"
using namespace gta_thaw;
using namespace gta_thaw::startup_status;

Packet Message() {
    Packet p{};std::memcpy(p.magic,"GTASTAT1",8);p.version=1;p.byte_size=sizeof(p);
    p.pid=7;p.kind=Preparing;p.created=123;p.request=11;p.sequence=1;p.tick_ms=1000;p.since_ms=800;
    return p;
}
void IdentityAndFreshness() {
    Snapshot s{};s.gta_pid=7;s.process_creation_100ns=123;
    auto p=Message();
    // Rendering readiness never requires MOVE2, an actor, foreground or THAW textures.
    assert(ValidPacket(p,s,1000) && ValidPacket(p,s,2500));
    assert(!ValidPacket(p,s,999) && !ValidPacket(p,s,2501));
    auto q=p;q.pid++;assert(!ValidPacket(q,s,1000));
    q=p;q.created++;assert(!ValidPacket(q,s,1000));
    q=p;q.version++;assert(!ValidPacket(q,s,1000));
    q=p;q.byte_size++;assert(!ValidPacket(q,s,1000));
    q=p;q.magic[0]^=1;assert(!ValidPacket(q,s,1000));
    q=p;q.kind=StartingGameplay;assert(ValidPacket(q,s,1000));
    q=p;q.kind=4;assert(!ValidPacket(q,s,1000));
    q=p;q.request=0;assert(!ValidPacket(q,s,1000));
    q=p;q.sequence=0;assert(!ValidPacket(q,s,1000));
    q=p;q.since_ms=1001;assert(!ValidPacket(q,s,1000));
    q=p;q.since_ms=0;assert(!ValidPacket(q,s,1000));
    q.kind=Hidden;assert(ValidPacket(q,s,1000) && !Visible(q,1000));
}
void LifetimeAndMonotonicity() {
    auto p=Message(),q=p;
    assert(Continues(q,p) && Visible(p,1000));
    q.sequence++;q.tick_ms++;assert(Continues(q,p));
    q.since_ms++;assert(!Continues(q,p)); // Heartbeats cannot restart a display interval.
    q=p;q.kind=Ready;q.sequence++;q.since_ms=q.tick_ms;
    assert(Continues(q,p) && Visible(q,1000) && Visible(q,5999) && !Visible(q,6000));
    p=q;q.sequence++;q.tick_ms=5999;
    assert(Continues(q,p) && Visible(q,5999));
    q.tick_ms=6000;assert(Continues(q,p) && !Visible(q,6000));
    q.since_ms=6000;assert(!Continues(q,p));
    q.request++;q.sequence=1;assert(Continues(q,p) && Visible(q,6000));
    p=Message();q=p;q.tick_ms++;assert(!Continues(q,p));
    q=p;q.sequence=0;assert(!Continues(q,p));
    q=p;q.tick_ms--;assert(!Continues(q,p));
    q=p;q.sequence++;q.kind=Hidden;q.since_ms=q.tick_ms;assert(Continues(q,p));
    p=Message();q=p;q.kind=StartingGameplay;q.sequence++;q.since_ms=q.tick_ms;
    assert(Continues(q,p) && Visible(q,1000) && Visible(q,30000));
    p=q;q.sequence++;q.tick_ms=30000;assert(Continues(q,p));
    q.since_ms=30000;assert(!Continues(q,p));
    q.kind=Ready;assert(Continues(q,p) && Visible(q,34999) && !Visible(q,35000));
}
void PlacementAndText() {
    auto box=Place(1280,720,400,30);
    assert(box.left==872 && box.top==150 && box.right==1272 && box.bottom==180);
    box=Place(1920,1080,400,30);
    assert(box.right==1908 && box.top==225 && box.bottom==270);
    box=Place(320,720,400,30);
    assert(box.left>=8 && box.right==312 && box.bottom>box.top);
    assert(!std::strcmp(Text(Preparing),"Preparing map for grinding..."));
    assert(!std::strcmp(Text(StartingGameplay),"Starting THAW gameplay..."));
    assert(!std::strcmp(Text(Ready),"Map ready - press J to enter THAW mode."));
    assert(!*Text(Hidden));
}
int main() {
    IdentityAndFreshness();LifetimeAndMonotonicity();PlacementAndText();
    std::puts("Startup status identity/freshness, five-second one-shot readiness and viewport layout passed");
}
