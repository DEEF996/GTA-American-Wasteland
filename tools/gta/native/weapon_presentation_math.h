#pragma once
#include "pose_preview_math.h"

namespace gta_thaw::weapon_presentation {
using pose_preview::Packet;
constexpr unsigned Bones=pose_preview::BoneCount;
inline float Dot(const float* a,const float* b) { return a[0]*b[0]+a[1]*b[1]+a[2]*b[2]; }
inline bool Unit(float* v) { const float n=std::sqrt(Dot(v,v)); if(!std::isfinite(n)||n<1e-6f)return false; for(int i=0;i<3;++i)v[i]/=n;return true; }
inline int Bone(const Packet& p,int id) { for(const auto& n:p.node)if(n.id==id)return n.index;return -1; }
inline void Identity(float* m) { std::memset(m,0,64);m[0]=m[5]=m[10]=m[15]=1; }
inline bool AnatomicalBasis(const Packet& p,float* basis) {
    const int pelvis=Bone(p,2),head=Bone(p,5),left=Bone(p,41),right=Bone(p,51);
    if(pelvis<0||head<0||left<0||right<0)return false;
    float hip[16],top[16],a[16],b[16];
    pose_preview::InverseRigid(p.bind_inverse[pelvis],hip);pose_preview::InverseRigid(p.bind_inverse[head],top);
    pose_preview::InverseRigid(p.bind_inverse[left],a);pose_preview::InverseRigid(p.bind_inverse[right],b);
    Identity(basis);for(int k=0;k<3;++k){basis[k]=b[12+k]-a[12+k];basis[8+k]=top[12+k]-hip[12+k];}
    if(!Unit(basis)||!Unit(basis+8))return false;
    camera_preview::Cross(basis+8,basis,basis+4);if(!Unit(basis+4))return false;
    camera_preview::Cross(basis,basis+4,basis+8);return pose_preview::Rigid(basis,10.f);
}
inline bool Parents(const Packet& p,int* parents) {
    int stack[Bones+1]{-1},depth=1,parent=-1;
    for(const auto& n:p.node) {
        if(n.index<0||n.index>=static_cast<int>(Bones)||depth<1||depth>static_cast<int>(Bones))return false;
        parents[n.index]=parent;
        if(n.flags&2)stack[depth++]=parent;
        if(n.flags&1) { if(!depth)return false;parent=stack[--depth]; }else parent=n.index;
    } return true;
}
inline bool Below(int index,int root,const int* parents) {
    for(unsigned j=0;j<Bones&&index>=0;++j,index=parents[index])if(index==root)return true;
    return false;
}
inline void Around(float* rotation,const float* pivot) {
    for(int c=0;c<3;++c) { rotation[12+c]=pivot[c];for(int k=0;k<3;++k)rotation[12+c]-=pivot[k]*rotation[k*4+c]; }
}
inline void Rotate(const float* axis,float radians,float* m) {
    const float c=std::cos(radians),s=std::sin(radians),t=1-c,x=axis[0],y=axis[1],z=axis[2];
    Identity(m);
    m[0]=c+x*x*t;m[1]=x*y*t+z*s;m[2]=x*z*t-y*s;
    m[4]=y*x*t-z*s;m[5]=c+y*y*t;m[6]=y*z*t+x*s;
    m[8]=z*x*t+y*s;m[9]=z*y*t-x*s;m[10]=c+z*z*t;
}
inline bool Align(const float* from,const float* to,float* matrix) {
    float a[3],b[3],axis[3];std::memcpy(a,from,12);std::memcpy(b,to,12);
    if(!Unit(a)||!Unit(b))return false;
    camera_preview::Cross(a,b,axis);const float cosine=std::clamp(Dot(a,b),-1.f,1.f);
    if(!Unit(axis)) { if(cosine>0) { Identity(matrix);return true; }
        const float seed[3]={std::fabs(a[0])<.8f?1.f:0.f,std::fabs(a[0])<.8f?0.f:1.f,0};
        camera_preview::Cross(a,seed,axis);if(!Unit(axis))return false;
    } Rotate(axis,std::acos(cosine),matrix);return true;
}
inline void TransformBranch(float bones[Bones][16],int root,const int* parents,const float* delta) {
    for(unsigned i=0;i<Bones;++i)if(Below(i,root,parents)) {
        float result[16];pose_preview::Multiply(bones[i],delta,result);std::memcpy(bones[i],result,64);
    }
}
inline void HandBasis(const float* gun,bool left,float* hand) {
    std::memcpy(hand,gun,64);
    // Stock left-hand weapon placement rotates local X by180 degrees. Use its
    // inverse for the wrist as well; the left palm is not a second right hand.
    if(left)for(int r=1;r<3;++r)for(int c=0;c<3;++c)hand[r*4+c]=-hand[r*4+c];
}
inline void WeaponFromHand(const float* hand,bool left,float* gun) {
    HandBasis(hand,left,gun);
    if(left)for(int k=0;k<3;++k)gun[12+k]+=.04f*gun[k]-.05f*gun[4+k];
}
// Two-bone IK retains measured CJ limb lengths and all original child offsets.
inline bool Arm(float bones[Bones][16],const Packet& p,const int* parents,bool left,
                const float* desired,const float* hand_matrix,const float* bend) {
    const int upper=Bone(p,left?32:22),elbow=Bone(p,left?33:23),hand=Bone(p,left?34:24);
    if(upper<0||elbow<0||hand<0)return false;
    float first[3],second[3],dir[3];for(int k=0;k<3;++k) {
        first[k]=bones[elbow][12+k]-bones[upper][12+k];second[k]=bones[hand][12+k]-bones[elbow][12+k];dir[k]=desired[k]-bones[upper][12+k];
    }
    const float a=std::sqrt(Dot(first,first)),b=std::sqrt(Dot(second,second)),raw=std::sqrt(Dot(dir,dir));
    if(a<.05f||b<.05f||a>.8f||b>.8f||!Unit(dir))return false;
    const float distance=std::clamp(raw,std::fabs(a-b)+.005f,a+b-.005f);
    float plane[3];const float parallel=Dot(dir,bend);for(int k=0;k<3;++k)plane[k]=bend[k]-dir[k]*parallel;
    if(!Unit(plane))return false;
    const float along=(a*a-b*b+distance*distance)/(2*distance),height=std::sqrt(std::max(0.f,a*a-along*along));
    float upper_delta[3],wrist[3],rotation[16];for(int k=0;k<3;++k) {
        upper_delta[k]=dir[k]*along+plane[k]*height;wrist[k]=bones[upper][12+k]+dir[k]*distance;
    }
    if(!Align(first,upper_delta,rotation))return false;Around(rotation,bones[upper]+12);TransformBranch(bones,upper,parents,rotation);
    float next[3],target[3];for(int k=0;k<3;++k){next[k]=bones[hand][12+k]-bones[elbow][12+k];target[k]=wrist[k]-bones[elbow][12+k];}
    if(!Align(next,target,rotation))return false;Around(rotation,bones[elbow]+12);TransformBranch(bones,elbow,parents,rotation);
    float desired_hand[16],inverse[16];HandBasis(hand_matrix,left,desired_hand);std::memcpy(desired_hand+12,bones[hand]+12,12);
    pose_preview::InverseRigid(bones[hand],inverse);pose_preview::Multiply(inverse,desired_hand,rotation);
    TransformBranch(bones,hand,parents,rotation);return true;
}
inline bool GunFrame(const float* direction,float* gun) {
    Identity(gun);std::memcpy(gun,direction,12);if(!Unit(gun))return false;
    const float up[3]={0,0,1};camera_preview::Cross(up,gun,gun+4);
    if(!Unit(gun+4)) { const float side[3]={0,1,0};camera_preview::Cross(side,gun,gun+4);if(!Unit(gun+4))return false; }
    camera_preview::Cross(gun,gun+4,gun+8);return true;
}
inline bool Aim(float bones[Bones][16],const Packet& p,const float* anchor,const float* direction,
                bool left,bool hanging,bool one_handed) {
    int parents[Bones];if(!Parents(p,parents))return false;
    const int spine=Bone(p,3),shoulder=Bone(p,left?32:22);if(spine<0||shoulder<0)return false;
    float gun[16];if(!GunFrame(direction,gun))return false;
    if(!hanging) {
        const float up[3]={0,0,1};float right[3]={anchor[0],anchor[1],0},forward[3]={anchor[4],anchor[5],0};
        if(!Unit(right)||!Unit(forward))return false;
        const float yaw=std::atan2(Dot(direction,right),Dot(direction,forward));
        // Source feet and hips never turn. Alternate hand covers the far side
        // instead of rotating the spine through an impossible full revolution.
        float turn[16];Rotate(up,-std::clamp(yaw,-1.22f,1.22f),turn);Around(turn,bones[spine]+12);TransformBranch(bones,spine,parents,turn);
    }
    float target[3],bend[3];for(int k=0;k<3;++k) {
        target[k]=bones[shoulder][12+k]+gun[k]*.48f+gun[4+k]*(left?.035f:-.035f);
        bend[k]=gun[4+k]*(left?1.f:-1.f)-gun[8+k]*.65f;
    }
    if(!Arm(bones,p,parents,left,target,gun,bend))return false;
    if(!one_handed&&!hanging) {
        const int hand=Bone(p,left?34:24);for(int k=0;k<3;++k) {
            target[k]=bones[hand][12+k]+gun[k]*.22f+gun[4+k]*(left?-.055f:.055f);
            bend[k]=gun[4+k]*(left?-1.f:1.f)-gun[8+k]*.8f;
        }
        if(!Arm(bones,p,parents,!left,target,gun,bend))return false;
    }
    for(unsigned i=0;i<Bones;++i)if(!pose_preview::Rigid(bones[i],100000.f))return false;
    return true;
}
inline bool NormalizeDerivedRotation(float* matrix) {
    // Transpose-based rigid inverses can amplify the small roundoff allowed in
    // validated native matrices. In particular BlendRigid(t=0) deliberately
    // copies its endpoint, so two render phases in one millisecond otherwise
    // feed that error back through every finger ancestor. Project only our
    // newly calculated rotations, keeping source positions and lengths intact.
    for(int a=0;a<3;++a)for(int b=0;b<3;++b) {
        const float dot=Dot(matrix+a*4,matrix+b*4);
        if(!std::isfinite(dot)||std::fabs(dot-(a==b?1.f:0.f))>.02f)return false;
    }
    float cross[3];camera_preview::Cross(matrix,matrix+4,cross);const float determinant=Dot(cross,matrix+8);
    if(!std::isfinite(determinant)||determinant<.98f||determinant>1.02f)return false;
    camera_preview::Packet p{};std::memcpy(p.forward,matrix+4,12);std::memcpy(p.up,matrix+8,12);
    float q[4];camera_preview::Quaternion(p,q);camera_preview::Axes(q,matrix+4,matrix+8);
    camera_preview::Cross(matrix+4,matrix+8,matrix);return pose_preview::Rigid(matrix,100000.f);
}
constexpr float HeadYawLimit=1.4835298642f; // 85 degrees relative to the posed torso.
constexpr float HeadPitchDown=.7853981634f,HeadPitchUp=1.0471975512f;
inline bool AnatomicalFrame(const Packet& p,int bone,const float* basis,const float* world,float* output) {
    float model[16];pose_preview::Multiply(p.bind_inverse[bone],world,model);
    pose_preview::Multiply(basis,model,output);
    output[12]=output[13]=output[14]=0;
    return NormalizeDerivedRotation(output);
}
inline bool GazeAngles(const float* torso,const float* origin,const float* target,float& yaw,float& pitch) {
    float direction[3];for(int k=0;k<3;++k)direction[k]=target[k]-origin[k];
    if(!Unit(direction))return false;
    const float right=Dot(direction,torso),forward=Dot(direction,torso+4),up=Dot(direction,torso+8);
    yaw=std::clamp(std::atan2(right,forward),-HeadYawLimit,HeadYawLimit);
    pitch=std::clamp(std::atan2(up,std::sqrt(right*right+forward*forward)),-HeadPitchDown,HeadPitchUp);
    return true;
}
inline void GazeFrame(const float* torso,float yaw,float pitch,float* output) {
    Identity(output);const float sy=std::sin(yaw),cy=std::cos(yaw),sp=std::sin(pitch),cp=std::cos(pitch);
    for(int k=0;k<3;++k) {
        output[k]=torso[k]*cy-torso[4+k]*sy;
        output[4+k]=(torso[k]*sy+torso[4+k]*cy)*cp+torso[8+k]*sp;
    }
    camera_preview::Cross(output,output+4,output+8);
}
inline bool HeadBranch(int bone,int neck,int head,const int* parents) {
    // CJ clavicles are neck children too. Turning the neck must not drag the
    // arm holding a ledge or the independently solved gun arm with it.
    return bone==neck||Below(bone,head,parents);
}
inline bool AimHead(float bones[Bones][16],const Packet& p,const float* basis,const float* target) {
    int parents[Bones];if(!Parents(p,parents))return false;
    const int spine=Bone(p,3),neck=Bone(p,4),head=Bone(p,5);
    if(spine<0||neck<0||head<0||!Below(head,neck,parents)||
       Below(Bone(p,22),head,parents)||Below(Bone(p,32),head,parents))return false;
    float torso[16];if(!AnatomicalFrame(p,spine,basis,bones[spine],torso))return false;
    // Both bones have their own CJ bind axes. Aim their anatomical frames,
    // never an assumed local head axis. The neck contributes a natural share;
    // the head then finishes pointing from its new position toward the target.
    for(int bone:{neck,head}) {
        float yaw,pitch,current[16],desired[16],inverse[16],delta[16];
        if(!GazeAngles(torso,bones[head]+12,target,yaw,pitch)||
           !AnatomicalFrame(p,bone,basis,bones[bone],current))return false;
        const float share=bone==neck?.35f:1.f;GazeFrame(torso,yaw*share,pitch*share,desired);
        pose_preview::InverseRigid(current,inverse);pose_preview::Multiply(inverse,desired,delta);
        if(!NormalizeDerivedRotation(delta))return false;
        Around(delta,bones[bone]+12);
        for(unsigned i=0;i<Bones;++i)if(bone==neck?HeadBranch(i,neck,head,parents):Below(i,head,parents)) {
            float result[16];pose_preview::Multiply(bones[i],delta,result);std::memcpy(bones[i],result,64);
        }
    }
    for(unsigned i=0;i<Bones;++i)if(HeadBranch(i,neck,head,parents)&&!NormalizeDerivedRotation(bones[i]))return false;
    return true;
}
inline bool BlendUpper(const Packet& p,const float source[Bones][16],const float target[Bones][16],
                       float alpha,float output[Bones][16],int branch_id=3,bool include_head=false) {
    int parents[Bones];if(!Parents(p,parents))return false;
    const int branch=Bone(p,branch_id),neck=Bone(p,4),head=Bone(p,5);
    if(branch<0||(include_head&&(neck<0||head<0)))return false;
    for(const auto& node:p.node) {
        const int i=node.index,parent=parents[i];
        if(parent<0||(!Below(i,branch,parents)&&(!include_head||!HeadBranch(i,neck,head,parents)))) {
            std::memcpy(output[i],target[i],64);continue;
        }
        float a[16],b[16],inverse[16],mix[16];
        pose_preview::InverseRigid(source[parent],inverse);pose_preview::Multiply(source[i],inverse,a);
        pose_preview::InverseRigid(target[parent],inverse);pose_preview::Multiply(target[i],inverse,b);
        pose_preview::BlendRigid(a,b,alpha,mix);
        if(!NormalizeDerivedRotation(mix))return false;
        pose_preview::Multiply(mix,output[parent],output[i]);
        if(!NormalizeDerivedRotation(output[i]))return false;
    } return true;
}
inline void Muzzle(const float* gun,const float* offset,float* origin,float* barrel) {
    for(int c=0;c<3;++c) { origin[c]=gun[12+c];barrel[c]=gun[12+c];for(int k=0;k<3;++k)barrel[c]+=offset[k]*gun[k*4+c]; }
    origin[2]+=offset[2]+.15f;
}
inline bool AimAt(float bones[Bones][16],const Packet& p,const float* body,const float* target,const float* fire_offset,
                  bool left,bool hanging,bool one_handed) {
    const int shoulder=Bone(p,left?32:22),hand=Bone(p,left?34:24);
    if(shoulder<0||hand<0)return false;
    float direction[3];for(int k=0;k<3;++k)direction[k]=target[k]-bones[shoulder][12+k];
    if(!Unit(direction))return false;
    float solved[Bones][16];
    // Torso rotation changes the shoulder and muzzle positions. Re-solve from
    // the same original pose each time instead of accumulating torso rotations.
    for(unsigned iteration=0;iteration<12;++iteration) {
        std::memcpy(solved,bones,sizeof(solved));
        if(!Aim(solved,p,body,direction,left,hanging,one_handed))return false;
        float gun[16],origin[3],barrel[3],toward[3];WeaponFromHand(solved[hand],left,gun);Muzzle(gun,fire_offset,origin,barrel);
        for(int k=0;k<3;++k)toward[k]=target[k]-barrel[k];
        const float muzzle_distance=std::sqrt(Dot(toward,toward));
        float from_shoulder[3];for(int k=0;k<3;++k)from_shoulder[k]=target[k]-solved[shoulder][12+k];
        const float shoulder_distance=std::sqrt(Dot(from_shoulder,from_shoulder));
        if(!Unit(toward))return false;
        if(Dot(toward,gun)>.99999f) { std::memcpy(bones,solved,sizeof(solved));return true; }
        // Near targets amplify muzzle parallax; the step must shrink with the
        // remaining distance beyond the barrel instead of oscillating across it.
        const float step=.75f*std::clamp(muzzle_distance/std::max(.01f,shoulder_distance),.1f,1.f);
        for(int k=0;k<3;++k)direction[k]=toward[k]*step+direction[k]*(1-step);
        if(!Unit(direction))return false;
    }
    return false; // A point inside/beyond the reach of the barrel must not invert the arm.
}
inline void TorsoFrame(const float* basis,const float* bind_inverse,const float* bone,float* torso) {
    float model[16];pose_preview::Multiply(bind_inverse,bone,model);pose_preview::Multiply(basis,model,torso);std::memcpy(torso+12,bone+12,12);
}
inline void Holster(const float* torso,float* weapon) {
    float local[16];Identity(local);local[0]=0;local[2]=-1;local[5]=1;local[8]=1;local[10]=0;
    local[12]=-.12f;local[13]=-.23f;local[14]=.18f;pose_preview::Multiply(local,torso,weapon);
}
}
