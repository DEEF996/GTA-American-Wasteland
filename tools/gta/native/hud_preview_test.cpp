#include <cassert>
#include <cstdio>
#include <limits>
#include "hud_preview_protocol.h"
#include "hud_scope.h"

using namespace gta_thaw;
using namespace gta_thaw::hud_preview;

Packet HudPacket() {
    Packet p{};std::memcpy(p.magic,"GTAHUD01",8);p.version=1;p.byte_size=sizeof(p);
    p.pid=17;p.epoch=3;p.created=123;p.request=9;p.sequence=1;p.tick_ms=1000;
    p.ped=0x20000;p.atlas_hash[0]=1;p.canvas[0]=640;p.canvas[1]=448;p.count=1;
    p.quad[0]={0,0x80808080,{{0,0,0,0},{10,0,1,0},{10,10,1,1},{0,10,0,1}}};
    return p;
}
Snapshot Scene() {
    Snapshot s{};std::memcpy(s.magic,"GTATHAW2",8);s.version=2;s.byte_size=sizeof(s);
    s.gta_pid=17;s.process_creation_100ns=123;s.sequence=1;s.tick_ms=1000;
    s.capabilities=Telemetry|IsolatedProfile;s.ped_address=0x20000;s.epoch=3;s.health=100;
    s.status_flags=PedPresent|OnFoot|Foreground;s.ped_motion_flags=InAir;
    std::strcpy(s.profile_path,"I:\\GTA_THAW\\thaw_gta\\profile");return s;
}
void PacketTest() {
    auto p=HudPacket();auto s=Scene();
    assert(ValidPacket(p,s,1000) && ValidPacket(p,s,1100));
    assert(!ValidPacket(p,s,999) && !ValidPacket(p,s,1101));
    auto q=p;q.epoch++;assert(!ValidPacket(q,s,1000));
    q=p;q.created++;assert(!ValidPacket(q,s,1000));
    q=p;q.request=0;assert(!ValidPacket(q,s,1000));
    q=p;q.source_tick=1;assert(!ValidPacket(q,s,1000));
    q.source_time_us=16667;assert(ValidPacket(q,s,1000));
    q.source_tick=432000;q.source_time_us=7200000000ULL;assert(ValidPacket(q,s,1000));
    assert(!ValidPacket(q,s,1101));
    q=p;q.count=1025;assert(!ValidPacket(q,s,1000));
    q=p;q.atlas_hash[0]=0;assert(!ValidPacket(q,s,1000));
    q=p;q.canvas[0]=641;assert(!ValidPacket(q,s,1000));
    q=p;q.quad[1].color=1;assert(!ValidPacket(q,s,1000)); // Unused storage is not payload.
    q=p;q.quad[0].texture=64;assert(!ValidPacket(q,s,1000));
    q=p;q.quad[0].vertex[0][0]=std::numeric_limits<float>::quiet_NaN();assert(!ValidPacket(q,s,1000));
    q=p;q.quad[0].vertex[0][2]=1.01f;assert(!ValidPacket(q,s,1000));
    s.cutscene_flags=1;assert(!ValidPacket(p,s,1000));
    s=Scene();s.status_flags&=~Foreground;assert(!ValidPacket(p,s,1000));
    s=Scene();s.controls_disabled_mask=1;assert(!ValidPacket(p,s,1000));
    assert(Continues(p,p));q=p;q.sequence++;q.tick_ms++;assert(Continues(q,p));
    q.quad[0].color++;assert(!Continues(q,p)); // Same source sample is immutable.
    q.source_tick++;q.source_time_us=16667;assert(Continues(q,p) && !Continues(p,q));
    q.atlas_hash[0]++;assert(!Continues(q,p));
    q=p;q.tick_ms++;assert(!Continues(q,p)); // Repeated sequence is exactly identical.
}
void BundleTest() {
    unsigned char bytes[33]{};std::memcpy(bytes,"GTAHUDTX",8);
    const std::uint32_t fields[]={1,1,1,1,4};std::memcpy(bytes+8,fields,sizeof(fields));
    std::uint32_t count=0;
    assert(ValidBundle(bytes,32,count) && count==1);
    assert(!ValidBundle(bytes,31,count) && !ValidBundle(bytes,33,count));
    bytes[16]=0;assert(!ValidBundle(bytes,32,count));bytes[16]=1;
    const std::uint32_t bad=0xffffffff;std::memcpy(bytes+16,&bad,4);
    assert(!ValidBundle(bytes,32,count));
    assert(RwColor(0x80808080)==0xffffffff && RwColor(0x00400020)==0x00800040);
}
std::uintptr_t render_state[31];int draws=0,writes=0,fail_get=0,fail_set=0;
int __cdecl Get(int kind,void* output) {
    if(kind==fail_get) return false;
    *static_cast<std::uintptr_t*>(output)=render_state[kind];return true;
}
int __cdecl Set(int kind,void* value) {
    ++writes;render_state[kind]=reinterpret_cast<std::uintptr_t>(value);return kind!=fail_set;
}
void Draw(void*) {
    ++draws;
    for(int kind:board_preview::States) Set(kind,reinterpret_cast<void*>(123));
    Set(14,reinterpret_cast<void*>(456));
}
void Fault(void* context) { Draw(context);RaiseException(0xE0434242,0,0,nullptr); }
void ScopeTest() {
    for(int i=0;i<31;++i) render_state[i]=i+500;
    std::uint64_t restored=0;
    assert(DrawScoped(Set,Get,Draw,nullptr,restored) && restored==1 && draws==1);
    for(int i=0;i<31;++i) assert(render_state[i]==static_cast<unsigned>(i+500));
    bool caught=false;
    __try { DrawScoped(Set,Get,Fault,nullptr,restored); }
    __except(GetExceptionCode()==0xE0434242?EXCEPTION_EXECUTE_HANDLER:EXCEPTION_CONTINUE_SEARCH) { caught=true; }
    assert(caught && restored==2 && draws==2);
    for(int i=0;i<31;++i) assert(render_state[i]==static_cast<unsigned>(i+500));
    fail_get=14;const auto old_writes=writes;
    assert(!DrawScoped(Set,Get,Draw,nullptr,restored) && writes==old_writes && draws==2 && restored==2);
    fail_get=0;fail_set=14;
    assert(!DrawScoped(Set,Get,Draw,nullptr,restored) && draws==3 && restored==2);
    for(int i=0;i<31;++i) assert(render_state[i]==static_cast<unsigned>(i+500));
}
int main() {
    PacketTest();BundleTest();ScopeTest();
    std::puts("HUD bounded packets/atlases, normal/SEH fog+RW restoration and failed-get gates passed");
}
