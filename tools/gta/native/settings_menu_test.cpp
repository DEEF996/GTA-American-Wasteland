#include <cassert>
#include <cstdio>
#include <windows.h>
#include "settings_menu_protocol.h"
#include "settings_menu_input_scope.h"
using namespace gta_thaw;
using namespace gta_thaw::settings_menu;
void TestInputScope() {
    unsigned char pad[ProcessedPadBytes+1],current[KeyboardBytes+1],previous[KeyboardBytes+1];
    std::memset(pad,0x71,sizeof(pad));std::memset(current,0x82,sizeof(current));std::memset(previous,0x93,sizeof(previous));
    InputFrame frame;InputStats stats;
    frame.End(stats);assert(!stats.sets && !stats.restores);
    frame.Begin(pad,current,previous,stats);
    for(std::size_t i=0;i<ProcessedPadBytes;++i) assert(!pad[i]);
    for(std::size_t i=0;i<KeyboardBytes;++i) assert(!current[i] && !previous[i]);
    assert(pad[ProcessedPadBytes]==0x71 && current[KeyboardBytes]==0x82 && previous[KeyboardBytes]==0x93);
    frame.Begin(pad,current,previous,stats); // Nested consumers cannot replace the real backup with zeros.
    assert(stats.sets==1);
    frame.End(stats);frame.End(stats);
    assert(stats.restores==1 && !stats.conflicts);
    for(std::size_t i=0;i<ProcessedPadBytes;++i) assert(pad[i]==0x71);
    for(std::size_t i=0;i<KeyboardBytes;++i) assert(current[i]==0x82 && previous[i]==0x93);
    frame.Begin(pad,current,previous,stats);
    current[7]=0x55;
    frame.End(stats);
    assert(stats.conflicts==1 && current[7]==0x55 && current[0]==0);
    assert(pad[0]==0x71 && previous[0]==0x93); // Other independent snapshots are restored.
}
void FailingGame(InputFrame* frame,InputStats* stats,unsigned char* pad,unsigned char* current,unsigned char* previous) {
    __try {
        frame->Begin(pad,current,previous,*stats);
        RaiseException(0xE0424245,0,0,nullptr);
    } __finally { frame->End(*stats); }
}
void TestInputFailure() {
    unsigned char pad[ProcessedPadBytes],current[KeyboardBytes],previous[KeyboardBytes];
    std::memset(pad,1,sizeof(pad));std::memset(current,2,sizeof(current));std::memset(previous,3,sizeof(previous));
    InputFrame frame;InputStats stats;bool caught=false;
    __try { FailingGame(&frame,&stats,pad,current,previous); }
    __except(EXCEPTION_EXECUTE_HANDLER) { caught=true; }
    assert(caught && !frame.active && stats.sets==1 && stats.restores==1 && !stats.conflicts);
    for(auto b:pad) assert(b==1);for(auto b:current) assert(b==2);for(auto b:previous) assert(b==3);
}
void TestInputRelease() {
    InputReleaseLatch latch;bool down[256]{};
    assert(!latch.Consume(false,down));
    down[VK_RETURN]=down[VK_ESCAPE]=down[VK_F8]=true;
    assert(latch.Consume(true,down));
    assert(latch.Consume(false,down) && latch.pending); // Closing the panel never forwards held Enter/Escape.
    down[VK_F8]=down[VK_ESCAPE]=false;
    assert(latch.Consume(false,down) && latch.pending);
    down[VK_RETURN]=false;
    assert(latch.Consume(false,down) && !latch.pending); // Swallow the release frame too.
    down['W']=true;
    assert(!latch.Consume(false,down)); // A new gameplay key after release remains available.
    assert(latch.Consume(true,down));latch.Reset();
    assert(!latch.Consume(false,down)); // Lost foreground/stale owner fails open.
}
int main() {
    TestInputScope();TestInputFailure();TestInputRelease();
    Snapshot s{};s.gta_pid=7;s.process_creation_100ns=123;
    Packet p{};std::memcpy(p.magic,"GTAMENU1",8);p.version=1;p.byte_size=sizeof(p);
    p.pid=7;p.created=123;p.request=9;p.sequence=1;p.tick_ms=1000;p.count=Lines;
    std::strcpy(p.line[0],"GTA THAW - SETTINGS");
    assert(Valid(p,s,1000) && Valid(p,s,2500));
    assert(!Valid(p,s,999) && !Valid(p,s,2501));
    auto q=p;q.pid++;assert(!Valid(q,s,1000));
    q=p;q.created++;assert(!Valid(q,s,1000));
    q=p;q.close=2;assert(!Valid(q,s,1000));
    q=p;q.count++;assert(!Valid(q,s,1000));
    q=p;q.reserved=1;assert(!Valid(q,s,1000));
    q=p;std::memset(q.line[3],'x',LineBytes);assert(!Valid(q,s,1000));
    q=p;q.line[3][0]='\n';assert(!Valid(q,s,1000));
    q=p;q.sequence++;q.tick_ms++;assert(Continues(q,p));
    q=p;q.line[0][0]='x';assert(!Continues(q,p));
    q=p;q.tick_ms--;assert(!Continues(q,p));
    q=p;q.request++;assert(Continues(q,p));
    std::puts("settings menu protocol tests passed");
}
