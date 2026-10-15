#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <cassert>
#include <cstdio>
#include <initializer_list>
#include <limits>
#include "weapon_control_policy.h"
#include "weapon_control_scope.h"
using namespace gta_thaw::weapon_control;
struct ScopeCase { volatile std::uint32_t flags=0;unsigned calls=0;bool result=true; };
bool ShotScope(void* context) {
    auto& c=*static_cast<ScopeCase*>(context);assert(c.flags&1u);++c.calls;
    c.flags|=0x20u; // A separate native field update must survive restoration.
    return c.result;
}
bool FaultScope(void* context) {
    ShotScope(context);RaiseException(0xE0425049,0,0,nullptr);return false;
}
void ScopeTests() {
    ScopeCase c;
    assert(!WithAimReady(&c.flags,false,&c,ShotScope) && !c.calls && !c.flags);
    assert(WithAimReady(&c.flags,true,&c,ShotScope) && c.calls==1 && c.flags==0x20);
    c.flags=0x41;c.result=false;
    assert(!WithAimReady(&c.flags,true,&c,ShotScope) && c.calls==2 && c.flags==0x61);
    c.flags=0x40;bool caught=false;
    __try { WithAimReady(&c.flags,true,&c,FaultScope); }
    __except(EXCEPTION_EXECUTE_HANDLER) { caught=true; }
    assert(caught && c.calls==3 && c.flags==0x60);
}
struct AccuracyCase {
    std::uint8_t ped=60;
    float recoil=120,accuracy=.7f,spread=.071f;
    unsigned calls=0,ammo=10,damage=0;
    bool shotgun=false,result=true,fault=false;
    ShotAccuracy Fields() { return {&ped,&recoil,&accuracy,&spread}; }
};
bool AccuracyShot(void* context) {
    auto& c=*static_cast<AccuracyCase*>(context);
    assert(c.ped==100 && c.recoil==0 && c.spread==0);
    assert((100.f-c.ped)/c.accuracy==0); // Native directional sway input.
    if(c.shotgun) {
        assert(c.accuracy>=50.f);
        c.spread=.05f/c.accuracy;
        assert(c.spread>0 && c.spread<=.001f);
    } else assert(c.accuracy==.7f);
    ++c.calls;--c.ammo;c.damage+=25;
    c.recoil+=8.f; // Simulate native recoil updates, including a partial failure.
    if(c.fault)RaiseException(0xE042504A,0,0,nullptr);
    return c.result;
}
void AssertAccuracyRestored(const AccuracyCase& c) {
    assert(c.ped==60 && c.recoil==120 && c.accuracy==.7f && c.spread==.071f);
}
void AccuracyTests() {
    // Every firearm reaches the same scoped original call. Damage/ammo changes
    // survive, while temporary accuracy/recoil/world state never escape it.
    for(unsigned weapon=22;weapon<=38;++weapon) {
        AccuracyCase c;c.shotgun=Shotgun(weapon);
        assert(WithMinimalRecoil(c.Fields(),c.shotgun,&c,AccuracyShot));
        assert(c.calls==1 && c.ammo==9 && c.damage==25);AssertAccuracyRestored(c);
        c.result=false;
        assert(!WithMinimalRecoil(c.Fields(),c.shotgun,&c,AccuracyShot));
        assert(c.calls==2 && c.ammo==8 && c.damage==50);AssertAccuracyRestored(c);
        c.fault=true;bool caught=false;
        __try { WithMinimalRecoil(c.Fields(),c.shotgun,&c,AccuracyShot); }
        __except(GetExceptionCode()==0xE042504A?EXCEPTION_EXECUTE_HANDLER:EXCEPTION_CONTINUE_SEARCH) { caught=true; }
        assert(caught && c.calls==3 && c.ammo==7 && c.damage==75);AssertAccuracyRestored(c);
    }
    AccuracyCase narrow;narrow.shotgun=true;narrow.accuracy=80.f;
    assert(WithMinimalRecoil(narrow.Fields(),true,&narrow,AccuracyShot));
    assert(narrow.accuracy==80.f && narrow.ped==60 && narrow.recoil==120 && narrow.spread==.071f);
    AccuracyCase invalid;
    assert(!WithMinimalRecoil({},false,&invalid,AccuracyShot));
    assert(!WithMinimalRecoil(invalid.Fields(),false,&invalid,nullptr));
    for(float bad : {0.f,-1.f,std::numeric_limits<float>::infinity(),std::numeric_limits<float>::quiet_NaN()}) {
        invalid.accuracy=bad;
        assert(!WithMinimalRecoil(invalid.Fields(),false,&invalid,AccuracyShot));
        assert(!invalid.calls && invalid.ped==60 && invalid.recoil==120 && invalid.spread==.071f);
    }
    invalid.accuracy=.7f;invalid.ped=101;
    assert(!WithMinimalRecoil(invalid.Fields(),false,&invalid,AccuracyShot) && !invalid.calls);
    invalid.ped=60;invalid.recoil=-1;
    assert(!WithMinimalRecoil(invalid.Fields(),false,&invalid,AccuracyShot) && !invalid.calls);
    invalid.recoil=120;invalid.spread=std::numeric_limits<float>::quiet_NaN();
    assert(!WithMinimalRecoil(invalid.Fields(),false,&invalid,AccuracyShot) && !invalid.calls);
}
int main() {
    ScopeTests();
    AccuracyTests();
    float muzzle[3]{1,2,3},target[3]{101,2,3},forward[3]{1,0,0};
    assert(MuzzleAligned(muzzle,target,forward));forward[0]=-1;
    assert(!MuzzleAligned(muzzle,target,forward));forward[0]=1;forward[1]=.3f;
    assert(!MuzzleAligned(muzzle,target,forward));forward[1]=0;
    assert(!MuzzleAligned(muzzle,muzzle,forward));target[1]=std::numeric_limits<float>::quiet_NaN();
    assert(!MuzzleAligned(muzzle,target,forward));
    bool slots[WeaponSlots]{};slots[0]=slots[2]=slots[5]=true;
    assert(NextInventorySlot(0,1,slots)==2 && NextInventorySlot(5,1,slots)==0);
    assert(NextInventorySlot(0,-1,slots)==5 && NextInventorySlot(2,-1,slots)==0);
    assert(NextInventorySlot(13,1,slots)==-1 && NextInventorySlot(0,0,slots)==-1);
    slots[0]=slots[2]=false;assert(NextInventorySlot(5,1,slots)==-1);
    assert(Firearm(22) && Firearm(38) && !Firearm(21) && !Firearm(39));
    assert(Launcher(35) && Launcher(36) && !Launcher(34) && !Launcher(37));
    assert(Shotgun(25) && Shotgun(26) && Shotgun(27) && !Shotgun(24) && !Shotgun(28));
    assert(LauncherCameraOwned(0,15) && LauncherCameraOwned(1,15));
    assert(!LauncherCameraOwned(2,15) && !LauncherCameraOwned(0,8));
    assert(OneHanded(2) && !OneHanded(0) && !OneHanded(0x202));
    assert(OneHanded(0x3033) && OneHanded(0x3833));
    assert(ContextAllowsAim(0,0,false,false));
    assert(ContextAllowsAim(1,3,false,true) && !ContextAllowsAim(1,3,false,false));
    assert(!ContextAllowsAim(0,0,true,true) && !ContextAllowsAim(2,0,false,true));
    assert(LoopMilliseconds(.2f,.45f)==250 && !LoopMilliseconds(1,1));
    assert(!LoopMilliseconds(0,std::numeric_limits<float>::infinity()));
    assert(CanFire(0,12,500,400,450,true) && !CanFire(2,12,500,400,450,true));
    assert(!CanFire(0,0,500,400,450,true) && !CanFire(1,12,400,450,0,false));
    assert(!CanFire(1,12,500,400,550,true));
    assert(DeadlineReached(2,0xfffffff0u) && !DeadlineReached(0xfffffff0u,2));
    FireInput input;
    assert(!input.Update(true,true));assert(!input.Update(false,true));
    assert(!input.Update(true,false));assert(input.Update(true,true));
    input.Reset();assert(!input.Update(true,true));
    std::puts("Native weapon gates and per-shot recoil/spread restoration (success, false, SEH) PASS");
}
