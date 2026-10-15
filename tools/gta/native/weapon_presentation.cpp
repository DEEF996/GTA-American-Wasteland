#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include "weapon_presentation.h"
#include "weapon_presentation_math.h"
#include "weapon_presentation_scope.h"
#include "board_scope.h"
#include "weapon_control.h"
#include "camera_preview.h"
#include "movement_hold.h"
#include "presentation_time.h"

namespace gta_thaw::weapon_presentation {
namespace {
Stats stats;
struct History { std::uint64_t request=0,time_us=0;std::uint32_t ped=0;bool valid=false,left=false;float model[Bones][16]{}; } history;
template<class T>T Read(std::uintptr_t p) { T v;std::memcpy(&v,reinterpret_cast<void*>(p),sizeof(v));return v; }
struct RenderCall { void* ped;void(*original)(void*); };
void Original(void* context) { auto& call=*static_cast<RenderCall*>(context);call.original(call.ped); }
bool Live(const Frame& f) {
    const auto current=weapon_control::GetState();const auto move=movement_hold::GetStats();
    return f.active&&move.active&&move.request==f.request&&current.active&&current.ped==f.ped&&
        current.request==f.request&&current.weapon_clump==f.clump&&
        Read<std::uint32_t>(f.ped+0x4f4)==f.clump;
}
void FrameMatrix(std::uintptr_t matrix,const float* m) {
    for(int row=0;row<4;++row)std::memcpy(reinterpret_cast<void*>(matrix+row*16),m+row*4,12);
    reinterpret_cast<void*(__cdecl*)(void*)>(0x7F18A0)(reinterpret_cast<void*>(matrix));
}
void DrawWeaponClump(void* context) {
    const auto& f=*static_cast<const Frame*>(context);
    if(!Live(f)||!pose_preview::Address(f.clump,16)||Read<unsigned char>(f.clump)!=2)return;
    const auto frame=Read<std::uint32_t>(f.clump+4);
    if(!pose_preview::Address(frame,164))return;
    unsigned char before[64];std::memcpy(before,reinterpret_cast<void*>(frame+16),64);
    __try {
        const auto set=reinterpret_cast<board_preview::StateFn>(0x7FE420);
        const int states[6]={6,8,14,10,11,30};const std::uintptr_t values[6]={1,1,1,5,6,20};
        for(int i=0;i<6;++i)if(!set(states[i],reinterpret_cast<void*>(values[i])))return;
        FrameMatrix(frame+16,f.weapon);
        // Same native clump/frame/flash APIs as RenderWeaponPedsForPC. Inventory
        // and model ownership stay with GTA; this scope owns only placement.
        if(f.aiming)reinterpret_cast<void(__thiscall*)(void*,bool)>(0x5DF400)(reinterpret_cast<void*>(f.ped),false);
        else reinterpret_cast<void(__thiscall*)(void*)>(0x5DF4E0)(reinterpret_cast<void*>(f.ped));
        reinterpret_cast<void*(__cdecl*)(void*)>(0x7F0910)(reinterpret_cast<void*>(frame));
        reinterpret_cast<void*(__cdecl*)(void*)>(0x749B20)(reinterpret_cast<void*>(f.clump));
        reinterpret_cast<void(__thiscall*)(void*)>(0x5DF4E0)(reinterpret_cast<void*>(f.ped));
        if(f.aiming)++stats.aimed;else ++stats.holstered;
    } __finally {
        std::memcpy(reinterpret_cast<void*>(frame+16),before,64);
        reinterpret_cast<void*(__cdecl*)(void*)>(0x7F0910)(reinterpret_cast<void*>(frame));++stats.restored;
    }
}
void DrawWeapon(const Frame& f) {
    const auto engine=Read<std::uint32_t>(0xC97B24);
    if(!pose_preview::Address(engine,64)||Read<std::uint32_t>(engine+32)!=0x7FE420||Read<std::uint32_t>(engine+36)!=0x7FD810)return;
    const auto set=reinterpret_cast<board_preview::StateFn>(0x7FE420),get=reinterpret_cast<board_preview::StateFn>(0x7FD810);
    std::uintptr_t fog=0;if(!get(14,&fog))return;
    __try { if(!board_preview::DrawStateScoped(set,get,DrawWeaponClump,const_cast<Frame*>(&f),stats.restored))++stats.faults; }
    __finally { if(!set(14,reinterpret_cast<void*>(fog)))++stats.faults; }
}
}
bool Guards() noexcept {
    // Supported executable a559aa77...cbd26. Stock weapon pass copies the hand
    // into clump+4/frame+16, then invokes these exact native APIs.
    constexpr unsigned char frame_call[]={0xE8,0x90,0xD8,0x0B,0x00};
    constexpr unsigned char render_call[]={0xE8,0x94,0x6A,0x01,0x00};
    constexpr unsigned char offsets[]={0x8B,0x8B,0xF4,0x04,0x00,0x00,0x8B,0x69,0x04};
    constexpr unsigned char frame[]={0x8b,0x44,0x24,0x04,0x33,0xc9,0x8b,0x90,0xa0,0,0,0,0x8a,0x4a,0x03,0xf6};
    constexpr unsigned char render[]={0x8b,0x44,0x24,0x04,0x53,0x55,0x56,0x8b,0x70,0x08,0x8d,0x68,0x08,0x3b,0xf5,0x8b};
    constexpr unsigned char flash[]={0x53,0x8a,0x5c,0x24,0x08,0x84,0xdb,0x55,0x56,0x8b,0xf1,0x74,0x09,0x66,0x8b,0xae};
    constexpr unsigned char reset[]={0x8b,0x81,0xf8,0x04,0,0,0x85,0xc0,0x74,0x1c,0x50,0xe8,0xd0,0x53,0x15,0};
    return !std::memcmp(reinterpret_cast<void*>(0x73307B),frame_call,sizeof(frame_call))&&
        !std::memcmp(reinterpret_cast<void*>(0x733087),render_call,sizeof(render_call))&&
        !std::memcmp(reinterpret_cast<void*>(0x73301E),offsets,sizeof(offsets))&&
        !std::memcmp(reinterpret_cast<void*>(0x7F0910),frame,sizeof(frame))&&
        !std::memcmp(reinterpret_cast<void*>(0x749B20),render,sizeof(render))&&
        !std::memcmp(reinterpret_cast<void*>(0x5DF400),flash,sizeof(flash))&&
        !std::memcmp(reinterpret_cast<void*>(0x5DF4E0),reset,sizeof(reset));
}
bool Prepare(const pose_preview::Packet& packet,const float* anchor,float bones[Bones][16],Frame& out) noexcept {
    out={};const auto gun=weapon_control::GetState();const auto move=movement_hold::GetStats();const auto now=GetTickCount64();
    if(!gun.active||!gun.weapon_clump||gun.ped!=packet.ped||gun.request!=packet.request||
       !move.active||!move.trajectory||move.preparing||move.request!=packet.request||!move.source_tick||move.source_bailing||
       now<gun.tick_ms||now-gun.tick_ms>pose_preview::MaxAgeMs) { Reset();return false; }
    const int spine=Bone(packet,3),right=Bone(packet,24),left=Bone(packet,34);
    if(spine<0||right<0||left<0)return false;
    float basis[16],body[16];if(!AnatomicalBasis(packet,basis)) { ++stats.rejected;return false; }
    pose_preview::Multiply(basis,anchor,body);
    out.active=true;out.ped=gun.ped;out.clump=gun.weapon_clump;out.request=gun.request;
    const auto aim=camera_preview::GetAimState();out.aiming=gun.can_aim&&gun.aiming&&aim.active&&aim.ped==gun.ped&&
        aim.request==gun.request&&now>=aim.tick_ms&&now-aim.tick_ms<=pose_preview::MaxAgeMs;
    const auto now_us=presentation_time::NowUs();
    const bool same=history.valid&&history.request==gun.request&&history.ped==gun.ped&&now_us>=history.time_us&&now_us-history.time_us<=100000;
    if(!same)history={};
    const float yaw=std::atan2(Dot(aim.forward,body),Dot(aim.forward,body+4));
    bool alternate=same&&history.left;
    if(std::fabs(yaw)>1.75f)alternate=true;else if(std::fabs(yaw)<1.35f)alternate=false;
    out.left_hand=alternate&&!gun.hanging;float target[Bones][16];std::memcpy(target,bones,sizeof(target));
    if(out.aiming&&(!AimAt(target,packet,body,aim.target,gun.fire_offset,out.left_hand,gun.hanging,gun.one_handed)||
                    !AimHead(target,packet,basis,aim.target))) {
        ++stats.rejected;out.aiming=false;std::memcpy(target,bones,sizeof(target));
    }
    if(same) {
        float previous[Bones][16],mixed[Bones][16];for(unsigned i=0;i<Bones;++i)pose_preview::Multiply(history.model[i],anchor,previous[i]);
        const float alpha=1.f-std::exp(-static_cast<float>(now_us-history.time_us)*.000018f);
        if(BlendUpper(packet,previous,target,alpha,mixed,gun.hanging?22:3,gun.hanging)) {
            int parents[Bones];if(!Parents(packet,parents))return false;
            // Never filter the THAW legs/root or move the hand holding a ledge.
            for(unsigned i=0;i<Bones;++i)if(Below(i,spine,parents)&&(!gun.hanging||
                Below(i,Bone(packet,22),parents)||HeadBranch(i,Bone(packet,4),Bone(packet,5),parents)))
                std::memcpy(bones[i],mixed[i],64);
        }
    }else if(out.aiming)std::memcpy(bones,target,sizeof(target));
    float inverse[16];pose_preview::InverseRigid(anchor,inverse);
    for(unsigned i=0;i<Bones;++i)pose_preview::Multiply(bones[i],inverse,history.model[i]);
    history.valid=true;history.ped=gun.ped;history.request=gun.request;history.time_us=now_us;history.left=out.left_hand;
    if(out.aiming) {
        WeaponFromHand(bones[out.left_hand?left:right],out.left_hand,out.weapon);
        Muzzle(out.weapon,gun.fire_offset,out.origin,out.barrel);
    } else {
        // Gun local +X is the barrel axis (stock weapon.dat fire offsets). Its
        // holster points down, follows the animated torso, and clears the back.
        // The torso bone has a bone-local basis; use body axes and its position.
        float torso[16];TorsoFrame(basis,packet.bind_inverse[spine],bones[spine],torso);Holster(torso,out.weapon);
    }
    out.holster_board=out.aiming&&move.source_mode==1&&!packet.board_object_id;
    if(out.holster_board) {
        TorsoFrame(basis,packet.bind_inverse[spine],bones[spine],out.board);
        for(int k=0;k<3;++k)out.board[12+k]=bones[spine][12+k]-.24f*out.board[4+k]-.1f*out.board[8+k];
    }
    std::memcpy(stats.last_gun_position,out.weapon+12,12);std::memcpy(stats.last_gun_forward,out.weapon,12);
    return true;
}
void RenderPed(void* ped,void(*original)(void*),const Frame& f) noexcept {
    __try {
        if(Live(f)) {
            RenderCall call{ped,original};
            if(HideWeaponScoped(reinterpret_cast<std::uint32_t*>(f.ped+0x4f4),f.clump,&call,Original))DrawWeapon(f);
        }else original(ped);
    } __except(EXCEPTION_EXECUTE_HANDLER) { ++stats.faults; }
}
void Fire(const Frame& f) noexcept {
    if(!f.aiming)return;
    ++stats.fire_candidates;
    const auto aim=camera_preview::GetAimState();float toward[3];
    for(int k=0;k<3;++k)toward[k]=aim.target[k]-f.barrel[k];
    // Wait for the visible wrist to finish turning into the sight line. A shot
    // must never leave a gun facing a materially different direction.
    stats.last_target_dot=Unit(toward)?Dot(toward,f.weapon):-1.f;
    if(aim.active&&aim.request==f.request&&aim.ped==f.ped&&stats.last_target_dot>.985f) {
        ++stats.fire_forwarded;
        weapon_control::FireFromPose(f.ped,f.request,f.origin,f.barrel,f.weapon);
    }else ++stats.alignment_rejected;
}
void ObservePreRender() noexcept { ++stats.pre_render_calls; }
void Reset() noexcept { history={}; }
Stats GetStats() noexcept { return stats; }
}
