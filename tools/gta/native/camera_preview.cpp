#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <bcrypt.h>
#include <cstdio>
#include <initializer_list>
#include "camera_preview.h"
#include "camera_preview_math.h"
#include "movement_hold.h"
#include "movement_hold_protocol.h"
#include "weapon_aim_math.h"
#include "weapon_control.h"
#include "settings_menu.h"
#include "pose_preview.h"
#include "aim_input_protocol.h"
#include "aim_target.h"
#include "presentation_time.h"
#include "game_effects.h"

namespace gta_thaw::camera_preview {
namespace {
constexpr std::uintptr_t Camera=0xB6F028, Process=0x52B730, CallSite=0x53C104;
const Snapshot* state=nullptr;
CaptureFn capture=nullptr;
SharedCamera* shared=nullptr;
HANDLE mapping=nullptr;
aim_input::SharedAim* aim_shared=nullptr;
HANDLE aim_mapping=nullptr;
std::uint64_t aim_sequence=0;
Stats stats{};
Packet heartbeat{}, applied{};
CameraHistory history;
AimController aim_controller;
AimState aim_state{};
std::uint64_t blocked_request=0;
std::uintptr_t rw_camera=0;
float zoom_times[2]{};
float foreign_times[6]{};
float previous_view[2]{}, owned_view[2]{};
bool projection_owned=false, normal_reset_pending=false;
constexpr int ForeignTimes[]={0xC5C,0xC60,0xC94,0xC98,0xCBC,0xCC0};
bool in_process=false;

template<class T> T Read(std::uintptr_t address) noexcept {
    T value; std::memcpy(&value,reinterpret_cast<const void*>(address),sizeof(value)); return value;
}
bool Pointer(std::uintptr_t p) noexcept { return p>=0x10000 && p<0x80000000 && !(p&3); }
bool Equal(std::uintptr_t address,const void* value,std::size_t n) noexcept {
    return !std::memcmp(reinterpret_cast<void*>(address),value,n);
}
bool Pin(std::uintptr_t address,std::size_t bytes,const char* expected) {
    BCRYPT_ALG_HANDLE algorithm=nullptr; unsigned char digest[32]; char hex[65]{};
    if(BCryptOpenAlgorithmProvider(&algorithm,BCRYPT_SHA256_ALGORITHM,nullptr,0)<0) return false;
    const bool ok=BCryptHash(algorithm,nullptr,0,reinterpret_cast<unsigned char*>(address),
        static_cast<ULONG>(bytes),digest,sizeof(digest))>=0;
    BCryptCloseAlgorithmProvider(algorithm,0);
    if(!ok) return false;
    for(int i=0;i<32;++i) std::snprintf(hex+i*2,3,"%02x",digest[i]);
    return !std::strcmp(hex,expected);
}
bool OriginalProcessOrHighFpsFix(const std::wstring& directory,VerifyFileFn verify_file) {
    if(Pin(Process,16,"689822e58a6c102d2d958ba8e64fd6b7718a0bb181a62269dd411659525a9b6c")) return true;
    // HighFpsFixes 1.9.0 may install its camera trampoline before or after this
    // ASI initializes. Chain its verified entry instead of depending on load order.
    const auto module=GetModuleHandleW(L"HighFpsFixes.asi");
    const auto base=reinterpret_cast<std::uintptr_t>(module);
    wchar_t filename[32768];
    const auto count=GetModuleFileNameW(module,filename,32768);
    const auto expected_path=directory.substr(0,directory.find_last_of(L"\\/"))+L"\\scripts\\HighFpsFixes.asi";
    constexpr unsigned char digest[]={0xff,0x9e,0xe0,0x97,0xd5,0xd1,0xf7,0x85,0x8e,0xbb,0x35,0xe5,0x7f,0x6c,0xbc,0x4f,
        0xfa,0x7d,0x5a,0x81,0x82,0xcc,0x6b,0xb1,0x7f,0x5f,0x3f,0xa1,0x77,0xa5,0xb7,0x03};
    constexpr unsigned char tail[]={0x00,0x53,0x55,0x56,0x57,0x8b,0xf1,0xe8,0xff,0x9d,0xee};
    constexpr unsigned char entry[]={0x55,0x8b,0xec,0x6a,0xfe};
    return module && count && count<32768 && !_wcsicmp(filename,expected_path.c_str()) &&
        verify_file(filename,digest) && Read<unsigned char>(Process)==0xe9 &&
        Process+5+Read<std::int32_t>(Process+1)==base+0x8860 &&
        Equal(Process+5,tail,sizeof(tail)) && Equal(base+0x8860,entry,sizeof(entry));
}
bool OwnsCamera(std::uint32_t* current_mismatch=nullptr) noexcept;
bool OrdinaryCamera(std::uintptr_t reset_ped=0) noexcept {
    const auto active=Read<unsigned char>(Camera+0x59);
    const auto transition=Read<unsigned char>(Camera+0x58);
    bool owned_transition=false;
    if(reset_ped && active<=1 && transition==1) {
        std::uint32_t mismatch=0; OwnsCamera(&mismatch);
        owned_transition=KnownFollowPedReset({mismatch,
            Read<std::uint16_t>(Camera+0x174+active*0x238+0xC),
            Read<std::uint16_t>(Camera+0xC38),Read<std::uint32_t>(Camera+0x958),
            static_cast<std::uint32_t>(reset_ped),Read<std::uint32_t>(Camera+0x7C),
            Read<std::uint32_t>(0xB7CB84)-Read<std::uint32_t>(Camera+0x78),
            Read<float>(Camera+0xC10),Read<float>(Camera+0xC14),
            Read<unsigned char>(Camera+0x4F)!=0,Read<unsigned char>(Camera+0x50)!=0});
    }
    if(active>1 || Read<std::uint32_t>(Camera+0xB0) || !Read<unsigned char>(Camera+0x2B) ||
       Read<unsigned char>(Camera+0x2C) || Read<unsigned char>(Camera+0x32) ||
       (transition && !owned_transition) || Read<unsigned char>(Camera+0x3D) ||
       Read<unsigned char>(Camera+0x2E) || Read<unsigned char>(Camera+0x2F) ||
       Read<std::uint16_t>(Camera+0x174+active*0x238+0xC)!=4 ||
       Read<unsigned char>(0xB6EC2C)) return false;
    // Never reset another script's live tracking/movement/FOV/shake or persistent state.
    const auto now=static_cast<float>(Read<std::uint32_t>(0xB7CB84));
    for(auto offset : {0xC60,0xC98,0xCA8,0xCC0}) {
        const auto end=Read<float>(Camera+offset);
        if(!std::isfinite(end) || end>=now) return false;
    }
    for(auto offset : {0xC8C,0xCB5,0xCEC,0xCED,0xCEE,0xCEF})
        if(Read<unsigned char>(Camera+offset)) return false;
    for(int i=0;i<6;++i) foreign_times[i]=Read<float>(Camera+ForeignTimes[i]);
    rw_camera=Read<std::uintptr_t>(Camera+0x954);
    // Initial projection contract is landscape horizontal FOV, matching CameraSize72FC70.
    const bool ready=Pointer(rw_camera) && rw_camera==Read<std::uintptr_t>(0xC1703C) &&
        Read<int>(0xC17044)>Read<int>(0xC17048) && Read<int>(0xC17048)>0;
    if(ready && owned_transition) stats.normal_reset_checks|=8u;
    return ready;
}
bool OwnsCamera(std::uint32_t* current_mismatch) noexcept {
    std::uint32_t mismatch=0;
    for(int i=0;i<6;++i) if(!Equal(Camera+ForeignTimes[i],foreign_times+i,4)) mismatch|=1;
    for(auto offset : {0xC8C,0xCEC,0xCED,0xCEE,0xCEF})
        if(Read<unsigned char>(Camera+offset)) mismatch|=2;
    float target[3],offset[3];
    for(int i=0;i<3;++i) { target[i]=applied.eye[i]+applied.forward[i]; offset[i]=applied.up[i]-(i==2?1.f:0.f); }
    if(Read<std::uint32_t>(Camera+0xB0)!=1 || Read<unsigned char>(Camera+0x2C)!=1 ||
       Read<unsigned char>(Camera+0x2B) || Read<std::uint16_t>(Camera+0xC38)!=15) mismatch|=4;
    if(Read<std::uintptr_t>(Camera+0x954)!=rw_camera || rw_camera!=Read<std::uintptr_t>(0xC1703C)) mismatch|=8;
    if(Read<unsigned char>(0xB6EC2C)) mismatch|=16;
    if(!Equal(Camera+0x860,target,12)) mismatch|=32;
    if(!Equal(Camera+0x86C,applied.eye,12)) mismatch|=64;
    if(!Equal(Camera+0x878,offset,12)) mismatch|=128;
    if(!Equal(Camera+0xCA4,zoom_times,8)) mismatch|=256;
    if(!Equal(Camera+0xCAC,&applied.horizontal_fov_degrees,4) ||
       !Equal(Camera+0xCB0,&applied.horizontal_fov_degrees,4)) mismatch|=512;
    if(mismatch) stats.ownership_mismatch=mismatch;
    if(current_mismatch) *current_mismatch=mismatch;
    return mismatch==0;
}
void RestoreProjection() noexcept {
    if(!projection_owned) return;
    projection_owned=false;
    if(Pointer(rw_camera) && rw_camera==Read<std::uintptr_t>(0xC1703C) &&
       Equal(rw_camera+0x68,owned_view,sizeof(owned_view))) {
        reinterpret_cast<void*(__cdecl*)(void*,const float*)>(0x7EE410)
            (reinterpret_cast<void*>(rw_camera),previous_view);
        ++stats.projection_restored;
    }
}
void End(std::uint32_t reason) noexcept {
    aim_state={};
    if(!stats.active) {
        // A cleared mapping, lost MOVE/focus or invalid packet cancels even a
        // pending reset. Merely waiting for the next valid reset packet does
        // not call End, so that bounded recovery keeps its presentation state.
        normal_reset_pending=false;
        history.Clear(); aim_controller.Clear(); return;
    }
    stats.active=false; stats.last_reason=reason;
    bool restored=false, normal_reset=false;
    __try {
        std::uint32_t mismatch=0;
        const bool owned=OwnsCamera(&mismatch);
        if(!owned && reason==4) {
            const bool projection_unchanged=projection_owned && Pointer(rw_camera) &&
                rw_camera==Read<std::uintptr_t>(0xC1703C) &&
                Equal(rw_camera+0x68,owned_view,sizeof(owned_view));
            normal_reset=NormalResetCandidate(reason,mismatch,projection_unchanged);
            stats.normal_reset_checks=(NormalResetCandidate(reason,mismatch,true)?1u:0u) |
                (projection_unchanged?2u:0u);
            if(normal_reset) {
                ++stats.normal_reset_candidates;
                if(OrdinaryCamera()) stats.normal_reset_checks|=4u;
            }
        }
        RestoreProjection();
        if(owned) {
            reinterpret_cast<void(__thiscall*)(void*)>(0x50BAB0)(reinterpret_cast<void*>(Camera));
            reinterpret_cast<void(__thiscall*)(void*)>(0x50D2D0)(reinterpret_cast<void*>(Camera));
            ++stats.restored;
            restored=true;
        } else ++stats.conflicts;
    } __except(EXCEPTION_EXECUTE_HANDLER) { ++stats.faults; stats.last_reason=6; }
    // Mode flags can reset before GTA activates FollowPed. A verified transition
    // from our own fixed camera can be replaced by the next fresh packet's jumpcut;
    // all other transitions wait for the full ordinary-camera gate.
    normal_reset_pending=normal_reset && !stats.faults;
    // A verified reset changes GTA's camera ownership, not the source timeline
    // or the user's aim direction. Public aim remains inactive until recovery.
    if(!normal_reset_pending) aim_controller.Clear();
    if((BlocksRequestAfterEnd(reason,restored) && !normal_reset) || stats.faults)
        blocked_request=stats.request;
}
movement_hold::ReadResult ReadPacket(Packet& p) noexcept {
    const long first=InterlockedCompareExchange(&shared->begin,0,0);
    if(first && !(first&1)) std::memcpy(&p,&shared->packet,sizeof(p));
    MemoryBarrier();
    return movement_hold::ClassifyRead(first,InterlockedCompareExchange(&shared->end,0,0),
                                      InterlockedCompareExchange(&shared->begin,0,0));
}
bool Select(Packet& output) noexcept {
    Packet incoming{};
    const auto read=ReadPacket(incoming);
    const auto now=GetTickCount64(); // Read after packet copy; never reject a just-published timestamp.
    if(read==movement_hold::ReadResult::Cleared) { End(1); return false; }
    Snapshot live=*state; live.status_flags=0; live.ped_address=0; capture(live);
    const auto movement=movement_hold::GetStats();
    if(!movement.active || !movement.trajectory || movement.request==blocked_request) { End(2); return false; }
    DWORD foreground=0; GetWindowThreadProcessId(GetForegroundWindow(),&foreground);
    if(foreground!=live.gta_pid) { End(2); return false; }
    live.status_flags|=Foreground;
    if(read==movement_hold::ReadResult::Committed) {
        // A coherent old heartbeat expires while an original worker stage runs.
        // Preserve protocol rejection for any changed or malformed incoming packet.
        if(stats.active && !std::memcmp(&incoming,&heartbeat,sizeof(incoming)) &&
           incoming.tick_ms<=now && now-incoming.tick_ms>MaxAgeMs) { End(2); return false; }
        if(!ValidPacket(incoming,live,now) || incoming.request!=movement.request ||
           ((stats.active || incoming.request==stats.request) &&
            !Continues(incoming,heartbeat))) { End(3); return false; }
        // MOVE and CAMERA are separate seqlocks. A newer source sample may await the next MOVE commit.
        if(incoming.source_tick<=movement.source_tick && incoming.source_time_us<=movement.source_time_us) {
            // Detect a native ordinary reset before advancing heartbeat. This lets
            // the same frame recover with a new verified packet, before its single
            // Camera::Process call, rather than displaying a GTA camera in between.
            if(stats.active && !OwnsCamera()) {
                End(4);
                if(!normal_reset_pending) return false;
            }
            if(!stats.active) {
                const bool reset_sample=normal_reset_pending && incoming.request==stats.request &&
                    NormalResetSampleReady(incoming,heartbeat,live,now);
                const bool ordinary=OrdinaryCamera(reset_sample?live.ped_address:0);
                if(normal_reset_pending && incoming.request==stats.request) {
                    if(!NormalResetReady(incoming,heartbeat,live,now,ordinary)) {
                        ++stats.rejected; ++stats.normal_reset_waits; return false;
                    }
                    ++stats.normal_reset_recovered;
                } else if(!ordinary) { ++stats.rejected; return false; }
                // Keep camera/actor interpolation paired across an ordinary
                // reset on the same validated lease; a new takeover starts fresh.
                if(!normal_reset_pending || incoming.request!=stats.request) history.Clear();
                normal_reset_pending=false;
            }
            heartbeat=incoming;
        } else if(!stats.active) return false;
    } else {
        ++stats.busy_reads;
        if(!stats.active) return false;
    }
    if(!ValidPacket(heartbeat,live,now) || heartbeat.request!=movement.request) { End(2); return false; }
    if(stats.active && !OwnsCamera()) { End(4); return false; }
    float root[16];
    const bool have_root=movement_hold::GetRenderRoot(heartbeat.request,heartbeat.source_tick,
        heartbeat.source_time_us,root);
    return history.Present(heartbeat,presentation_time::NowUs(),output,have_root?root+12:nullptr,
        game_effects::PresentationScale(heartbeat.request,heartbeat.ped,heartbeat.epoch));
}
bool ClearCameraLine(const float* from,const float* to) {
    return reinterpret_cast<bool(__cdecl*)(const float*,const float*,bool,bool,bool,bool,bool,bool,bool)>(0x56A490)(
        from,to,true,true,false,true,true,false,false);
}
void Aim(Packet& p) {
    aim_state={};
    const auto weapon=weapon_control::GetState();
    float origin[3];
    if(!weapon.active || !weapon.can_aim || weapon.request!=p.request || weapon.ped!=p.ped ||
       settings_menu::Open() || !history.Origin(origin)) { aim_controller.Clear();return; }
    const bool pressed=(GetAsyncKeyState(VK_RBUTTON)&0x8000)!=0;
    float body[16]{};
    if(weapon.hanging && !movement_hold::GetRenderRoot(p.request,p.source_tick,p.source_time_us,body)) {
        aim_controller.Clear();return;
    }
    const float height=weapon.hanging?0.f:.5f;
    if(weapon.hanging) {
        float offset[3];
        if(!pose_preview::GetAimOffset(p.request,p.ped,offset)) { aim_controller.Clear();return; }
        for(unsigned i=0;i<3;++i)origin[i]+=offset[i];
    }
    Packet aiming;
    if(!aim_controller.Present(p,origin,pressed,Read<float>(0xB73424),Read<float>(0xB73428),
            Read<float>(0xB6EC1C),Read<float>(0xB6EC18),presentation_time::NowUs(),aiming,
            weapon.hanging,std::atan2(body[5],body[4]),height)) {
        aim_controller.Clear();return;
    }
    p=aiming;
    if(aim_controller.blend>0) {
        // Keep the over-shoulder camera on CJ's side of solid map geometry.
        float from[3]{origin[0],origin[1],origin[2]+height};
        if(!ClearCameraLine(from,p.eye)) {
            float low=0,high=1,candidate[3];
            for(unsigned step=0;step<9;++step) {
                const float mid=(low+high)*.5f;
                for(unsigned i=0;i<3;++i) candidate[i]=from[i]+(p.eye[i]-from[i])*mid;
                if(ClearCameraLine(from,candidate)) low=mid; else high=mid;
            }
            for(unsigned i=0;i<3;++i) p.eye[i]=from[i]+(p.eye[i]-from[i])*std::max(0.f,low-.06f);
            ++stats.aim_collision_clips;
        }
    }
    if(!pressed) return;
    float end[3],target[3];bool hit=false;
    for(unsigned i=0;i<3;++i)end[i]=p.eye[i]+p.forward[i]*weapon.range;
    const AimLineOptions options{reinterpret_cast<std::uint32_t*>(0xB7CD68),reinterpret_cast<float*>(0xB7CD64),
        reinterpret_cast<std::uint8_t*>(0xB7CD71),reinterpret_cast<std::uint8_t*>(0xB7CD70),reinterpret_cast<std::uint8_t*>(0xB7CD6F)};
    if(!AimTargetScoped(p.ped,p.eye,end,options,reinterpret_cast<AimLineFn>(0x56BA00),target,hit)) {
        ++stats.aim_ray_rejected;return;
    }
    if(hit)++stats.aim_ray_hits;else ++stats.aim_ray_misses;
    aim_state.active=true; aim_state.ped=p.ped; aim_state.request=p.request;
    aim_state.tick_ms=GetTickCount64();
    for(unsigned i=0;i<3;++i) {
        aim_state.eye[i]=p.eye[i]; aim_state.forward[i]=p.forward[i];
        aim_state.target[i]=target[i];
    }
    ++stats.aim_frames;
}
bool Prepare() noexcept {
    __try {
        weapon_control::PrepareTick();
        Packet p;
        if(!Select(p)) return false;
        Aim(p);
        float target[3],offset[3];
        for(int i=0;i<3;++i) { target[i]=p.eye[i]+p.forward[i]; offset[i]=p.up[i]-(i==2?1.f:0.f); }
        auto camera=reinterpret_cast<void*>(Camera);
        reinterpret_cast<void(__thiscall*)(void*,const float*,const float*)>(0x50BEC0)(camera,p.eye,offset);
        reinterpret_cast<void(__thiscall*)(void*,const float*,short,int)>(0x50C8B0)(camera,target,2,1);
        reinterpret_cast<void(__thiscall*)(void*,float,float,float,bool)>(0x50D280)
            (camera,p.horizontal_fov_degrees,p.horizontal_fov_degrees,1000.f,false);
        applied=p;
        std::memcpy(zoom_times,reinterpret_cast<void*>(Camera+0xCA4),8);
        if(!stats.active) { stats.active=true; stats.request=p.request; ++stats.accepted; }
        stats.last_reason=0;
        return true;
    } __except(EXCEPTION_EXECUTE_HANDLER) { ++stats.faults; End(6); return false; }
}
void Observe() noexcept {
    __try {
        const auto index=Read<unsigned char>(Camera+0x59);
        if(index>1 || !OwnsCamera()) { End(4); return; }
        const auto cam=Camera+0x174+index*0x238;
        if(Read<std::uint16_t>(cam+0xC)!=15) { End(4); return; }
        std::memcpy(stats.expected_eye,applied.eye,12); std::memcpy(stats.observed_eye,reinterpret_cast<void*>(cam+0x19C),12);
        std::memcpy(stats.expected_forward,applied.forward,12); std::memcpy(stats.observed_forward,reinterpret_cast<void*>(cam+0x190),12);
        std::memcpy(stats.expected_up,applied.up,12); std::memcpy(stats.observed_up,reinterpret_cast<void*>(cam+0x1B4),12);
        stats.expected_fov=applied.horizontal_fov_degrees; stats.observed_fov=Read<float>(cam+0xB4);
        ++stats.applied;
    } __except(EXCEPTION_EXECUTE_HANDLER) { ++stats.faults; End(6); }
}
void __fastcall ProcessCamera(void* camera,void*) {
    if(in_process || camera!=reinterpret_cast<void*>(Camera)) {
        reinterpret_cast<void(__thiscall*)(void*)>(Process)(camera); return;
    }
    in_process=true;
    __try {
        const bool prepared=stats.enabled && !stats.faults && Prepare();
        reinterpret_cast<void(__thiscall*)(void*)>(Process)(camera);
        if(prepared) Observe();
    } __finally { in_process=false; }
}
void __cdecl SizeCamera(void* camera,void* rectangle,float view,float aspect) {
    // Preserve GTA's raster sizing. Override only the owned view extents before
    // visibility/frustum construction and BeginUpdate, using the real source projection.
    reinterpret_cast<void(__cdecl*)(void*,void*,float,float)>(0x72FC70)(camera,rectangle,view,aspect);
    if(!stats.active || stats.faults) return;
    __try {
        if(camera!=reinterpret_cast<void*>(rw_camera) || !OwnsCamera() ||
           !ProjectionMatchesTarget(applied,Read<int>(0xC17044),Read<int>(0xC17048)) ||
           GetTickCount64()-heartbeat.tick_ms>MaxAgeMs) { End(7); return; }
        std::memcpy(previous_view,reinterpret_cast<void*>(rw_camera+0x68),sizeof(previous_view));
        std::memcpy(owned_view,applied.view_window,sizeof(owned_view));
        projection_owned=true;
        const auto result=reinterpret_cast<void*(__cdecl*)(void*,const float*)>(0x7EE410)(camera,owned_view);
        if(result!=camera || !Equal(rw_camera+0x68,owned_view,sizeof(owned_view))) { End(7); return; }
        std::memcpy(stats.expected_view_window,owned_view,sizeof(owned_view));
        std::memcpy(stats.observed_view_window,reinterpret_cast<void*>(rw_camera+0x68),sizeof(owned_view));
        ++stats.projection_applied;
    } __except(EXCEPTION_EXECUTE_HANDLER) { ++stats.faults; End(6); }
}
}

void AfterTick() noexcept {
    if(!stats.enabled) return;
    if(stats.active) __try {
        const auto movement=movement_hold::GetStats();
        const auto now=GetTickCount64();
        Snapshot live=*state; live.status_flags=0; live.ped_address=0; capture(live);
        DWORD foreground=0; GetWindowThreadProcessId(GetForegroundWindow(),&foreground);
        if(foreground==live.gta_pid) live.status_flags|=Foreground;
        if(!InterlockedCompareExchange(&shared->begin,0,0) || !movement.active || !movement.trajectory ||
           movement.request!=stats.request || !ValidPacket(heartbeat,live,now)) End(2);
    } __except(EXCEPTION_EXECUTE_HANDLER) { ++stats.faults; End(6); }
    // Publish once per GTA tick, including an explicit inactive sample on release.
    // The input bridge must never infer aim direction from the one-second status log.
    if(aim_shared) {
        const auto aim=GetAimState();
        const auto movement=movement_hold::GetStats();
        const auto packet=aim_input::Make(*state,++aim_sequence,GetTickCount64(),aim.request,
            movement.entry_f7_requests,aim.active?aim.forward:nullptr);
        const long generation=static_cast<long>((aim_sequence%0x3ffffffe+1)*2);
        InterlockedExchange(&aim_shared->begin,generation-1);
        std::memcpy(&aim_shared->packet,&packet,sizeof(packet));
        MemoryBarrier();
        InterlockedExchange(&aim_shared->end,generation);
        InterlockedExchange(&aim_shared->begin,generation);
    }
}

const char* Initialize(const std::wstring& directory,const Snapshot* snapshot,CaptureFn read_game,InstallFn install,VerifyFileFn verify_file) {
    const auto settings=directory+L"\\adapter.ini";
    if(GetPrivateProfileIntW(L"development",L"CameraPreview",0,settings.c_str())!=1) return nullptr;
    // Complete entry functions and the two original image relays; never replace a foreign hook.
    if(!OriginalProcessOrHighFpsFix(directory,verify_file) ||
       !Pin(0x50EEBC,11,"d3cd2b16fb2961144e73de17cbbf7d70f26ea3c27f1528634bb1be60ccfe5760") ||
       !Pin(0x50F03A,6,"f795270c9329cf1eb178399bb6cbe74c50ba2951913670e428c7b279a945d164") ||
       !Pin(0x50F046,6,"994e1d0267e7463774057bd4bc3101091d82181bce173f4adc4fe15ffcde480b") ||
       !Pin(0x53F270,78,"427da33366703e367c2e9b7292369bacfb4866fda77683727f83dd7d70d02e76") ||
       !Pin(0x56A490,32,"ee9f1696bb075f4b6b671bb4920205f4ee9e13a841c122f87a8ab75310ee3842") ||
       !Pin(0x56BA00,80,"6bd40f1325feee8a60bf824fcf59ee2694f1f121b1df1e4474580989198d0c8a") ||
       !Pin(0x5631C0,32,"5a92a98f80cef6e5b1a8e3489f3454ff8fda2cf53b657d7931e6d1262cb4910b") ||
       !Pin(0x50BEC0,61,"9da85e6fa17281759affad422a323b49cbca891323d382a2b5cf5fdba7416cb3") ||
       !Pin(0x401D1C,11,"6954a5b10db8642e4eaa68884742a0ec448bf69bb87eb52641c4ca40e6da26af") ||
       !Pin(0x50C8B0,92,"1c3b6c426e7736a276d43f7401cb1d8c6862df3d69937d237d8f81b59f3287b9") ||
       !Pin(0x52B339,36,"8637b037a0748dab2d3d5a289b8ce75d8deaa679b5e0aad3f10684b242d1300c") ||
       !Pin(0x515679,17,"4688ac35c7be34a98258d23ffc0a16dd84ef7a43655d0846d5adce8ac8ee93a9") ||
       !Pin(0x515710,20,"d80dd71fd600b4f178481cb3fab2c2721a1365adb6ef9f03e738fc0baf99d029") ||
       !Pin(0x50D280,72,"10e470b99ee1efc360172af41e852992300edcb7e7a9dd3c9be1dc0e7d88742c") ||
       !Pin(0x50BAB0,621,"856b4fb626997f3f54cd348c8928d37dff5b5e733d42c4bee367420fb1723191") ||
       !Pin(0x51D470,754,"ac06212fa097a9dd4d19a13a2b2993cee2329c0644e8e375bacba14e06386829") ||
       !Pin(0x50D2D0,125,"cc60884bf6398ba528636777737d6de157ca7bd118bd4845f65147d868cee258") ||
       !Pin(0x4010F8,10,"3436301435fe8a5880fdcb4cb375e5bc7a40bd1f3d5e3559ebf873707ebec359") ||
       !Pin(0x527E26,28,"b578042aed5b6cea9257b2f925e8ffb5163135ac9e29056c9b5299c279898254") ||
       !Pin(0x72FC70,273,"6a0af5c3c845367cac6f096f1fe68404e305cd899d6be80612f99a161aede6f1") ||
       !Pin(0x7EE410,64,"68d9862696d81c28c92f759c9e1ffaca98d3dd3024ecd39dc5b177ba77a3db1d"))
        return "Camera preview entry/projection guards differ";
    state=snapshot; capture=read_game;
    wchar_t name[80]; swprintf_s(name,L"Local\\THAW_GTA_CAMERA_V2_%lu",GetCurrentProcessId());
    mapping=CreateFileMappingW(INVALID_HANDLE_VALUE,nullptr,PAGE_READWRITE,0,sizeof(SharedCamera),name);
    if(!mapping || GetLastError()==ERROR_ALREADY_EXISTS) return "Camera preview mapping already exists or cannot be created";
    shared=static_cast<SharedCamera*>(MapViewOfFile(mapping,FILE_MAP_WRITE,0,0,sizeof(SharedCamera)));
    if(!shared) return "Cannot map camera preview transport";
    std::memset(shared,0,sizeof(*shared));
    swprintf_s(name,L"Local\\THAW_GTA_AIM_V1_%lu",GetCurrentProcessId());
    aim_mapping=CreateFileMappingW(INVALID_HANDLE_VALUE,nullptr,PAGE_READWRITE,0,sizeof(aim_input::SharedAim),name);
    if(!aim_mapping || GetLastError()==ERROR_ALREADY_EXISTS) return "Aim input mapping already exists or cannot be created";
    aim_shared=static_cast<aim_input::SharedAim*>(MapViewOfFile(aim_mapping,FILE_MAP_WRITE,0,0,sizeof(aim_input::SharedAim)));
    if(!aim_shared) return "Cannot map aim input transport";
    std::memset(aim_shared,0,sizeof(*aim_shared));
    constexpr unsigned char expected[]={0xe8,0x27,0xf6,0xfe,0xff};
    if(!install(CallSite,reinterpret_cast<void*>(ProcessCamera),0xe8,expected,sizeof(expected)))
        return "Cannot install camera preview callsite";
    constexpr unsigned char sizing[]={0xe8,0x98,0x24,0x1f,0x00};
    if(!install(0x53D7D3,reinterpret_cast<void*>(SizeCamera),0xe8,sizing,sizeof(sizing)))
        return "Cannot install camera projection callsite";
    stats.enabled=true;
    return nullptr;
}
Stats GetStats() noexcept { return stats; }
AimState GetAimState() noexcept {
    if(!stats.active || !aim_state.active || GetTickCount64()-aim_state.tick_ms>MaxAgeMs ||
       settings_menu::Open() || !(GetAsyncKeyState(VK_RBUTTON)&0x8000)) return {};
    const auto movement=movement_hold::GetStats();
    if(!movement.active || movement.preparing || movement.request!=aim_state.request ||
       movement.source_bailing) return {};
    DWORD foreground=0; GetWindowThreadProcessId(GetForegroundWindow(),&foreground);
    return state && foreground==state->gta_pid?aim_state:AimState{};
}
}
