#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <cassert>
#include <cstdio>
#include "weapon_presentation_math.h"
#include "weapon_presentation_scope.h"
using namespace gta_thaw;
using namespace gta_thaw::weapon_presentation;

Packet Rig() {
    Packet p{};const int ids[Bones]={0,1,2,3,4,5,8,6,7,31,32,33,34,35,36,21,22,23,24,25,26,302,301,201,41,42,43,44,51,52,53,54};
    const int flags[Bones]={0,0,2,2,2,2,3,3,1,2,0,0,0,0,0,0,0,0,0,0,0,3,1,1,2,0,0,1,0,0,0,1};
    for(unsigned i=0;i<Bones;++i) { p.node[i]={ids[i],static_cast<int>(i),flags[i],0};Identity(p.model[i]);p.model[i][14]=1; }
    p.node[14].flags=1;p.node[20].flags=1;
    p.model[3][14]=1.25f;p.model[4][14]=1.5f;
    p.model[5][14]=1.7f;p.model[24][12]=-.12f;p.model[28][12]=.12f;
    for(int side=0;side<2;++side) {
        const float sign=side?-1.f:1.f;const int first=side?9:15;
        for(int i=0;i<6;++i) { p.model[first+i][12]=sign*(.16f+.12f*std::min(i,3));p.model[first+i][14]=1.5f-.12f*std::min(i,3); }
    }
    for(unsigned i=0;i<Bones;++i)pose_preview::InverseRigid(p.model[i],p.bind_inverse[i]);
    return p;
}
float Distance(const float* a,const float* b) { float v[3];for(int k=0;k<3;++k)v[k]=a[12+k]-b[12+k];return std::sqrt(Dot(v,v)); }
void AimTests() {
    const auto p=Rig();int parents[Bones];assert(Parents(p,parents));
    assert(Below(Bone(p,24),Bone(p,3),parents));assert(!Below(Bone(p,41),Bone(p,3),parents));
    float anchor[16];Identity(anchor);
    for(int yaw=-180;yaw<=180;yaw+=15)for(int pitch=-60;pitch<=60;pitch+=30)for(bool hanging:{false,true}) {
        if(hanging&&std::abs(yaw)>95)continue;
        const float y=yaw*.01745329252f,t=pitch*.01745329252f;
        const float direction[3]={std::sin(y)*std::cos(t),std::cos(y)*std::cos(t),std::sin(t)};
        float bones[Bones][16];std::memcpy(bones,p.model,sizeof(bones));
        const bool left=!hanging&&std::abs(yaw)>100;
        assert(Aim(bones,p,anchor,direction,left,hanging,false));
        for(unsigned i=0;i<Bones;++i) {
            assert(pose_preview::Rigid(bones[i],10));
            if(!Below(i,Bone(p,3),parents)||(hanging&&!Below(i,Bone(p,22),parents)))assert(!std::memcmp(bones[i],p.model[i],64));
        }
        for(int tag:{22,32}) {
            const int a=Bone(p,tag),b=Bone(p,tag+1),c=Bone(p,tag+2);
            assert(std::fabs(Distance(bones[a],bones[b])-Distance(p.model[a],p.model[b]))<1e-5f);
            assert(std::fabs(Distance(bones[b],bones[c])-Distance(p.model[b],p.model[c]))<1e-5f);
        }
        const int hand=Bone(p,left?34:24);assert(Dot(direction,bones[hand])>.9999f);
        float aligned[16];assert(GunFrame(direction,aligned));
        assert(Dot(aligned+8,bones[hand]+8)*(left?-1.f:1.f)>.9999f);
        float blended[Bones][16];assert(BlendUpper(p,p.model,bones,.5f,blended,hanging?22:3));
        for(unsigned i=0;i<Bones;++i)if(!Below(i,Bone(p,hanging?22:3),parents))assert(!std::memcmp(blended[i],p.model[i],64));
    }
    float gun[16],origin[3],barrel[3];const float forward[3]={0,1,0},offset[3]={.4f,.05f,.1f};assert(GunFrame(forward,gun));
    gun[12]=10;gun[13]=20;gun[14]=30;Muzzle(gun,offset,origin,barrel);
    assert(std::fabs(origin[2]-30.25f)<1e-5f&&std::fabs(barrel[0]-9.95f)<1e-5f&&std::fabs(barrel[1]-20.4f)<1e-5f);
    float hand[16],left_gun[16];HandBasis(gun,true,hand);WeaponFromHand(hand,true,left_gun);
    assert(!std::memcmp(gun,left_gun,12*sizeof(float)));
    assert(std::fabs(left_gun[12]-10.05f)<1e-5f&&std::fabs(left_gun[13]-20.04f)<1e-5f&&left_gun[14]==30);
    float rest[16],inverse[16],animated[16],torso[16],back[16],turn[16];Identity(rest);rest[14]=1.25f;
    float anatomical[16];Identity(anatomical);
    pose_preview::InverseRigid(rest,inverse);TorsoFrame(anatomical,inverse,rest,torso);Holster(torso,back);
    assert(std::fabs(back[2]+1.f)<1e-6f&&std::fabs(back[14]-1.43f)<1e-5f);
    const float x[3]={1,0,0};Rotate(x,1.57079632679f,turn);pose_preview::Multiply(rest,turn,animated);
    TorsoFrame(anatomical,inverse,animated,torso);Holster(torso,back);
    assert(std::fabs(back[1]-1.f)<1e-6f&&std::fabs(back[2])<1e-6f); // barrel follows the backflip
}
void AnatomyTests() {
    auto p=Rig();float basis[16];assert(AnatomicalBasis(p,basis));
    assert(basis[0]>.999f&&basis[5]>.999f&&basis[10]>.999f);
    // Actual CJ bind convention: +X forward, +Y left, +Z up. Generic test
    // skeleton axes must not silently determine the holster/aim body axes.
    const float z[3]={0,0,1};float rotate[16];Rotate(z,-1.57079632679f,rotate);
    for(unsigned i=0;i<Bones;++i) {
        float native[16];pose_preview::Multiply(p.model[i],rotate,native);std::memcpy(p.model[i],native,64);
        pose_preview::InverseRigid(native,p.bind_inverse[i]);
    }
    assert(AnatomicalBasis(p,basis));assert(basis[1]<-.999f&&basis[4]>.999f&&basis[10]>.999f);
    float torso[16],gun[16];TorsoFrame(basis,p.bind_inverse[3],p.model[3],torso);Holster(torso,gun);
    assert(std::fabs(gun[12]+.23f)<1e-5f&&std::fabs(gun[13]-.12f)<1e-5f&&gun[2]<-.999f);
    float aimed[Bones][16];std::memcpy(aimed,p.model,sizeof(aimed));const float direction[3]={1,0,0};
    assert(Aim(aimed,p,basis,direction,false,false,true));
    // A forward shot leaves torso at its original yaw, even under CJ's axes.
    for(int k=0;k<16;++k)assert(std::fabs(aimed[3][k]-p.model[3][k])<1e-6f);
}
void ConvergenceTests() {
    const auto p=Rig();float body[16];Identity(body);int parents[Bones];assert(Parents(p,parents));
    for(int yaw:{-170,-90,-30,0,60,90,170})for(int pitch:{-45,0,45})for(float distance:{1.5f,3.f,20.f})
        for(bool hanging:{false,true})for(bool one_handed:{false,true}) {
        if(hanging&&std::abs(yaw)>95)continue;
        const bool left=!hanging&&std::abs(yaw)>100;const float a=yaw*.01745329252f,b=pitch*.01745329252f;
        const float target[3]={std::sin(a)*std::cos(b)*distance,std::cos(a)*std::cos(b)*distance,1.5f+std::sin(b)*distance};
        const float offset[3]={one_handed?.25f:.55f,.04f,.08f};
        float bones[Bones][16];std::memcpy(bones,p.model,sizeof(bones));
        const bool converged=AimAt(bones,p,body,target,offset,left,hanging,one_handed);
        if(!converged)std::fprintf(stderr,"Convergence failure yaw=%d pitch=%d distance=%g hanging=%d onehanded=%d\n",yaw,pitch,distance,hanging,one_handed);
        assert(converged);
        float gun[16],origin[3],barrel[3],toward[3];WeaponFromHand(bones[Bone(p,left?34:24)],left,gun);Muzzle(gun,offset,origin,barrel);
        for(int k=0;k<3;++k)toward[k]=target[k]-barrel[k];assert(Unit(toward));assert(Dot(toward,gun)>.99999f);
        for(unsigned i=0;i<Bones;++i)if(!Below(i,Bone(p,hanging?22:3),parents))assert(!std::memcmp(bones[i],p.model[i],64));
        for(int tag:{22,32}) {
            const int upper=Bone(p,tag),elbow=Bone(p,tag+1),hand=Bone(p,tag+2);
            assert(std::fabs(Distance(bones[upper],bones[elbow])-Distance(p.model[upper],p.model[elbow]))<1e-5f);
            assert(std::fabs(Distance(bones[elbow],bones[hand])-Distance(p.model[elbow],p.model[hand]))<1e-5f);
        }
    }
    float bones[Bones][16];std::memcpy(bones,p.model,sizeof(bones));
    const float invalid[3]={NAN,1,2},offset[3]={.4f,0,0};
    assert(!AimAt(bones,p,body,invalid,offset,false,false,false));assert(!std::memcmp(bones,p.model,sizeof(bones)));
}
void HeadTests() {
    auto p=Rig();const float x[3]={1,0,0},z[3]={0,0,1};float rotate[16];
    // Neck and head local axes differ from model axes (as in the captured CJ
    // bind). Changing a bone basis must not change the anatomical gaze target.
    for(int bone:{4,5}) {
        Rotate(bone==4?x:z,bone==4?.21f:1.57079632679f,rotate);
        float local[16];pose_preview::Multiply(rotate,p.model[bone],local);
        std::memcpy(p.model[bone],local,64);pose_preview::InverseRigid(local,p.bind_inverse[bone]);
    }
    float basis[16];assert(AnatomicalBasis(p,basis));int parents[Bones];assert(Parents(p,parents));
    for(int yaw:{-170,-100,-60,0,60,100,170})for(int pitch:{-80,-30,0,45,80})for(float distance:{2.f,25.f}) {
        float bones[Bones][16],torso[16],desired[16],target[3];std::memcpy(bones,p.model,sizeof(bones));
        assert(AnatomicalFrame(p,3,basis,bones[3],torso));
        GazeFrame(torso,yaw*.01745329252f,pitch*.01745329252f,desired);
        for(int k=0;k<3;++k)target[k]=bones[5][12+k]+distance*desired[4+k];
        assert(AimHead(bones,p,basis,target));
        float actual[16],expected[16],wanted_yaw,wanted_pitch;
        assert(AnatomicalFrame(p,5,basis,bones[5],actual));
        assert(GazeAngles(torso,bones[5]+12,target,wanted_yaw,wanted_pitch));
        GazeFrame(torso,wanted_yaw,wanted_pitch,expected);
        assert(Dot(actual+4,expected+4)>.99999f&&Dot(actual+8,expected+8)>.99999f);
        const float actual_yaw=std::atan2(Dot(actual+4,torso),Dot(actual+4,torso+4));
        const float actual_pitch=std::asin(std::clamp(Dot(actual+4,torso+8),-1.f,1.f));
        assert(std::fabs(actual_yaw)<=HeadYawLimit+.00001f&&actual_pitch>=-HeadPitchDown-.00001f&&actual_pitch<=HeadPitchUp+.00001f);
        if(std::abs(yaw)<=60&&pitch>=-30&&pitch<=45) {
            float direction[3];for(int k=0;k<3;++k)direction[k]=target[k]-bones[5][12+k];assert(Unit(direction));
            assert(Dot(actual+4,direction)>.99999f); // Actual target point, including close-range parallax.
        }
        for(unsigned i=0;i<Bones;++i) {
            assert(pose_preview::Rigid(bones[i],10));
            if(!HeadBranch(i,4,5,parents))assert(!std::memcmp(bones[i],p.model[i],64));
            else if(parents[i]>=0)assert(std::fabs(Distance(bones[i],bones[parents[i]])-Distance(p.model[i],p.model[parents[i]]))<1e-5f);
        }
    }
    float bones[Bones][16],mixed[Bones][16];std::memcpy(bones,p.model,sizeof(bones));
    const float direction[3]={.6f,.8f,0},target[3]={6,8,2};
    assert(Aim(bones,p,basis,direction,false,true,true));assert(AimHead(bones,p,basis,target));
    assert(BlendUpper(p,p.model,bones,.5f,mixed,22,true));
    for(unsigned i=0;i<Bones;++i)if(!Below(i,Bone(p,22),parents)&&!HeadBranch(i,4,5,parents))
        assert(!std::memcmp(mixed[i],p.model[i],64)); // Supporting arm, torso and legs stay source-exact.
    assert(std::memcmp(mixed[5],p.model[5],64));
    float invalid[3]={NAN,0,0};assert(!AimHead(bones,p,basis,invalid));
}
void SameMillisecondTests() {
    const auto p=Rig();float root[16],inverse[16],history[Bones][16];Identity(root);
    // This roundoff is admitted by the unchanged native input rigidity guard.
    for(int i:{0,5,10})root[i]=1.0001f;
    assert(pose_preview::Rigid(root,10));pose_preview::InverseRigid(root,inverse);
    for(unsigned i=0;i<Bones;++i)pose_preview::Multiply(p.model[i],inverse,history[i]);
    float body[16];Identity(body);
    for(int frame=0;frame<12000;++frame) {
        float previous[Bones][16],target[Bones][16],mixed[Bones][16];
        for(unsigned i=0;i<Bones;++i)pose_preview::Multiply(history[i],root,previous[i]);
        std::memcpy(target,p.model,sizeof(target));
        const float yaw=std::sin(frame*.004f)*3.14f,pitch=std::sin(frame*.008f)*.8f;
        const float direction[3]={std::sin(yaw)*std::cos(pitch),std::cos(yaw)*std::cos(pitch),std::sin(pitch)};
        assert(Aim(target,p,body,direction,std::fabs(yaw)>1.75f,false,false));
        float gaze[3];for(int k=0;k<3;++k)gaze[k]=target[5][12+k]+direction[k]*20.f;
        assert(AimHead(target,p,body,gaze));
        assert(BlendUpper(p,previous,target,frame%2?0.f:.14f,mixed));
        for(unsigned i=0;i<Bones;++i) {
            assert(pose_preview::Rigid(mixed[i],10));pose_preview::Multiply(mixed[i],inverse,history[i]);
        }
        for(unsigned i=24;i<Bones;++i)assert(!std::memcmp(mixed[i],p.model[i],64));
    }
    float reflected[16];Identity(reflected);reflected[0]=-1;
    assert(!NormalizeDerivedRotation(reflected));reflected[0]=1.1f;
    assert(!NormalizeDerivedRotation(reflected));
}
void Hidden(void* p) { assert(!*static_cast<std::uint32_t*>(p)); }
void Throwing(void* p) { Hidden(p);RaiseException(0xE0424242,0,0,nullptr); }
void Changed(void* p) { Hidden(p);*static_cast<std::uint32_t*>(p)=77; }
void ScopeTests() {
    std::uint32_t slot=42;assert(HideWeaponScoped(&slot,42,&slot,Hidden)&&slot==42);
    assert(!HideWeaponScoped(&slot,43,&slot,Hidden)&&slot==42);
    bool caught=false;__try { HideWeaponScoped(&slot,42,&slot,Throwing); }
    __except(GetExceptionCode()==0xE0424242?EXCEPTION_EXECUTE_HANDLER:EXCEPTION_CONTINUE_SEARCH) { caught=true; }
    assert(caught&&slot==42);assert(!HideWeaponScoped(&slot,42,&slot,Changed)&&slot==77);
}
int main() { AimTests();AnatomyTests();ConvergenceTests();HeadTests();SameMillisecondTests();ScopeTests();std::puts("Weapon presentation: anatomical CJ axes, target convergence, bounded head target tracking, limb lengths, all-angle aim, source legs, hanging support, 12000 same-millisecond feedback frames, muzzle transform and guarded scope restoration passed."); }
