#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <cassert>
#include <cstdio>
#include "aim_target.h"
using namespace gta_thaw::camera_preview;
namespace {
std::uint32_t ignored=99;float spread=.7f;std::uint8_t dead=0,tyres=1,bikers=0;
const AimLineOptions options{&ignored,&spread,&dead,&tyres,&bikers};
int mode=0;
bool __cdecl Query(const float* from,const float* to,AimColPoint* point,std::uint32_t* entity,
                   bool buildings,bool vehicles,bool peds,bool objects,bool dummies,bool see,bool camera,bool shoot) {
    assert(ignored==42&&spread==0&&dead==1&&tyres==1&&bikers==1);
    assert(buildings&&vehicles&&peds&&objects&&dummies&&!see&&!camera&&shoot);
    if(mode==4)RaiseException(0xE0424242,0,0,nullptr);
    if(!mode)return false;
    *entity=mode==3?42:100;
    for(int k=0;k<3;++k)point->point[k]=from[k]+(to[k]-from[k])*.25f;
    if(mode==2)point->point[0]+=5;
    return true;
}
void Restored() {assert(ignored==99&&spread==.7f&&!dead&&tyres==1&&!bikers);}
}
int main() {
    const float eye[3]={-.5f,-2,1.6f},end[3]={-.5f,38,1.6f};float target[3];bool hit=false;
    assert(AimTargetScoped(42,eye,end,options,Query,target,hit)&&!hit&&!std::memcmp(target,end,12));Restored();
    mode=1;assert(AimTargetScoped(42,eye,end,options,Query,target,hit)&&hit&&target[1]==8);Restored();
    // Crosshair hits a near surface, so the gun must converge to it, not forty metres behind it.
    assert(target[0]==eye[0]&&target[2]==eye[2]&&target[1]!=end[1]);
    mode=2;assert(!AimTargetScoped(42,eye,end,options,Query,target,hit));Restored();
    mode=3;assert(!AimTargetScoped(42,eye,end,options,Query,target,hit));Restored();
    mode=4;bool caught=false;
    __try { AimTargetScoped(42,eye,end,options,Query,target,hit); }
    __except(GetExceptionCode()==0xE0424242?EXCEPTION_EXECUTE_HANDLER:EXCEPTION_CONTINUE_SEARCH) { caught=true; }
    assert(caught);Restored();
    const float behind[3]={-.5f,-3,1.6f},beyond[3]={-.5f,40,1.6f},invalid[3]={NAN,0,0};
    assert(!RayPoint(eye,end,behind)&&!RayPoint(eye,end,beyond)&&!RayPoint(eye,end,invalid)&&!RayPoint(eye,eye,eye));
    std::puts("Aim first-hit point, native query flags, own-ped exclusion and exception restoration PASS");
}
