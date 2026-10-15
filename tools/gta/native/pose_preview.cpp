#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include "pose_preview.h"
#include "pose_preview_protocol.h"
#include "pose_preview_math.h"
#include "pose_preview_scope.h"
#include "board_preview.h"
#include "movement_hold.h"
#include "weapon_presentation.h"
#include "weapon_control.h"
#include "presentation_time.h"
#include "game_effects.h"

namespace gta_thaw::pose_preview {
namespace {
constexpr std::uintptr_t PlayerVtable = 0x86D168, RenderSlot = 0x86D1B0;
constexpr std::uintptr_t PedRender = 0x5E7680, MatrixUpdate = 0x7F18A0;
constexpr std::uintptr_t PreRenderSlot=0x86D1AC,PedPreRender=0x5E8A20;
constexpr std::uintptr_t ShoulderBoneRotation = 0x5DF560;
const Snapshot* state = nullptr;
CaptureFn capture = nullptr;
HANDLE mapping = nullptr;
SharedPose* shared = nullptr;
Stats stats{};
bool rendering = false;
CachedPacket cached;
RenderHistory history;
AimOffsetCache aim_offset;
std::uint32_t history_f7=0;

template<class T> T Read(std::uintptr_t address) noexcept {
    T value;
    std::memcpy(&value, reinterpret_cast<const void*>(address), sizeof(value));
    return value;
}

void ReadAffine(const void* rw, float* output) noexcept {
    for (int row = 0; row < 4; ++row) {
        std::memcpy(output+row*4, static_cast<const unsigned char*>(rw)+row*16, 12);
        output[row*4+3] = row == 3 ? 1.f : 0.f;
    }
}

struct Work {
    std::uint32_t matrices;
    unsigned char before[BoneCount][64], after[BoneCount][64];
    Packet packet;
    float anchor[16];
};

bool Prepare(void* ped, Work& work) noexcept {
    __try {
        Packet incoming, p;
        const long begin = InterlockedCompareExchange(&shared->begin, 0, 0);
        PacketRead read = begin ? PacketRead::Busy : PacketRead::Cleared;
        if (begin && !(begin & 1)) {
            std::memcpy(&incoming, &shared->packet, sizeof(incoming));
            MemoryBarrier();
            const long end = InterlockedCompareExchange(&shared->end, 0, 0);
            const long after = InterlockedCompareExchange(&shared->begin, 0, 0);
            read = ReadStatus(begin, end, after);
        }
        if (read == PacketRead::Busy) ++stats.busy_reads;
        const auto movement = movement_hold::GetStats();
        const auto lease_request = movement.active ? movement.request : 0;
        if (!SelectPacket(read, incoming, cached, *state, lease_request, p)) return false;
        const auto now = GetTickCount64();
        if (!ValidPacket(p, *state, now) || !HasShoulderNodes(p) ||
            reinterpret_cast<std::uintptr_t>(ped) != p.ped) return false;
        if (p.request) {
            if (!movement.active || !movement.trajectory || movement.request!=p.request) return false;
            // MOVE and POSE publish separately; never draw an uncommitted future source sample.
            if (p.source_tick>movement.source_tick || p.source_time_us>movement.source_time_us) {
                if (!history.valid || !SameRig(p,history.heartbeat) ||
                    !ValidPacket(history.heartbeat,*state,now)) return false;
                p=history.heartbeat;
            }
        }
        DWORD foreground = 0;
        GetWindowThreadProcessId(GetForegroundWindow(), &foreground);
        if (foreground != p.pid) return false;
        Snapshot live = *state;
        live.status_flags = 0;
        live.ped_address = 0;
        capture(live);
        if (!ControlEligible(live) || live.ped_address != p.ped || live.current_area != p.area ||
            live.ped_area != p.ped_area || Read<std::uintptr_t>(p.ped) != PlayerVtable ||
            Read<std::uint32_t>(p.ped+0x18) != p.clump) return false;
        // First proof is the observed single-atomic CJ rig. Clothes/rebuilds require a fresh import.
        if (Read<unsigned char>(p.clump) != 2 || Read<std::uint32_t>(p.clump+8) != p.atomic+0x40 ||
            Read<std::uint32_t>(p.clump+12) != p.atomic+0x40 || Read<unsigned char>(p.atomic) != 1 ||
            Read<std::uint32_t>(p.atomic+0x3C) != p.clump ||
            Read<std::uint32_t>(p.atomic+0x40) != p.clump+8 ||
            Read<std::uint32_t>(p.atomic+0x44) != p.clump+8 ||
            Read<std::uint32_t>(p.atomic+0x18) != p.geometry || Read<unsigned char>(p.geometry) != 8 ||
            Read<std::uint32_t>(0xC978A4) != p.atomic_plugin || Read<std::uint32_t>(0xC978A8) != p.geometry_plugin ||
            Read<std::uint32_t>(p.atomic+p.atomic_plugin) != p.hierarchy ||
            Read<std::uint32_t>(p.geometry+p.geometry_plugin) != p.skin ||
            Read<std::uint32_t>(p.hierarchy) != 0x3000 || Read<std::uint32_t>(p.hierarchy+4) != BoneCount ||
            Read<std::uint32_t>(p.hierarchy+8) != p.matrices || Read<std::uint32_t>(p.hierarchy+16) != p.nodes ||
            Read<std::uint32_t>(p.skin) != BoneCount || Read<std::uint32_t>(p.skin+12) != p.inverse_bind ||
            std::memcmp(reinterpret_cast<void*>(p.nodes), p.node, sizeof(p.node))) return false;
        float observed[16];
        for (std::uint32_t i = 0; i < BoneCount; ++i) {
            ReadAffine(reinterpret_cast<void*>(p.inverse_bind+i*64), observed);
            if (std::memcmp(observed, p.bind_inverse[i], sizeof(observed))) return false;
        }
        work.matrices = p.matrices;
        std::memcpy(work.before, reinterpret_cast<void*>(p.matrices), sizeof(work.before));
        float root[16], anchor[16], world[16];
        ReadAffine(work.before[0], root);
        if (!Rigid(root, 100000.f)) return false;
        // Target bind root * anchor = this render's native root; never reuse old world position.
        Multiply(p.bind_inverse[0], root, anchor);
        if (history_f7!=state->f7_requests) history.Clear();
        history_f7=state->f7_requests;
        float packet_root[16], current_root[16];
        const float* accepted_root=nullptr;
        if (p.request) {
            if (!movement_hold::GetRenderRoot(p.request,movement.source_tick,
                    movement.source_time_us,current_root)) { ++stats.current_root_rejected; return false; }
            if (movement_hold::GetRenderRoot(p.request,p.source_tick,p.source_time_us,packet_root))
                accepted_root=packet_root;
            else if (!history.HasRoot(p)) { ++stats.packet_root_rejected; return false; }
        }
        const bool new_sample=p.request && (!history.valid || !SameRig(p,history.heartbeat) ||
            p.source_tick>history.heartbeat.source_tick);
        if(new_sample && history.valid && SameRig(p,history.heartbeat)) {
            const auto stamp=history.samples[history.clock.count-1].pose.tick_ms;
            if(p.tick_ms>=stamp) stats.source_gap_ms_max=std::max(stats.source_gap_ms_max,p.tick_ms-stamp);
        }
        float alpha=1.f;
        if (!history.Present(p,presentation_time::NowUs(),work.packet,alpha,accepted_root,
                game_effects::PresentationScale(p.request,p.ped,p.epoch))) return false;
        if(new_sample) {
            ++stats.source_samples;
            if(history.clock.rebased) {
                ++stats.presentation_rebases;
                stats.presentation_rebase_us_max=std::max(stats.presentation_rebase_us_max,history.clock.rebase_us);
            }
        }
        // Actor roots travel with accepted POSE history, even if MOVE commits the next sample first.
        if (p.request && !history.Anchor(anchor,current_root,alpha,anchor)) { ++stats.anchor_rejected; return false; }
        if (alpha<1.f) ++stats.interpolated;
        std::memcpy(work.anchor, anchor, sizeof(anchor));
        std::memcpy(work.after, work.before, sizeof(work.after));
        for (std::uint32_t i = 0; i < BoneCount; ++i) {
            float displayed[16];
            Multiply(work.packet.model[i], work.packet.visual_delta, displayed);
            Multiply(displayed, anchor, world);
            if (!Rigid(world, 100000.f)) return false;
            for (int row = 0; row < 4; ++row) std::memcpy(work.after[i]+row*16, world+row*4, 12);
            // Only classification bits are changed; original flags/padding are restored below.
            reinterpret_cast<void*(__cdecl*)(void*)>(MatrixUpdate)(work.after[i]);
        }
        // Publish from the original interpolated pose before procedural aiming.
        // Its chest includes the original visual pivot (notably while hanging),
        // while the paired MOVE root removes old world position from the cache.
        aim_offset.Clear();
        float presented_root[16], chest[16];
        if(p.request && !movement.preparing && history.PresentedRoot(alpha,presented_root)) {
            for(const auto& node:work.packet.node) if(node.id==3) {
                ReadAffine(work.after[node.index],chest);
                aim_offset.Publish(work.packet,history_f7,now,chest,presented_root);
                break;
            }
        }
        // A publication collision keeps only a fully checked pose under its original short lease.
        if (read == PacketRead::Ready) cached = {p, lease_request, state->f7_requests, true};
        else ++stats.busy_fallbacks;
        return true;
    } __except(EXCEPTION_EXECUTE_HANDLER) {
        stats.fault_stage=1;stats.fault_exception=GetExceptionCode();
        ++stats.faults;
        return false;
    }
}

void OriginalRender(void* ped) {
    reinterpret_cast<void(__thiscall*)(void*)>(PedRender)(ped);
}

struct RenderContext { void* ped; Work* work; bool fire_only=false; };
bool CorrectShoulders(Work& work) noexcept {
    __try {
        // CJ's shoulder skin helpers are driven by the current upper arms, not
        // their bind offsets. Reapply GTA's own correction inside the pose scope.
        reinterpret_cast<void(__cdecl*)(void*)>(ShoulderBoneRotation)(
            reinterpret_cast<void*>(work.packet.clump));
        float matrix[16];
        for (unsigned i=0; i<BoneCount; ++i) {
            ReadAffine(reinterpret_cast<void*>(work.matrices+i*64),matrix);
            if (!Rigid(matrix,100000.f)) {
                stats.fault_stage=3;stats.fault_bone=i;std::memcpy(stats.fault_matrix,matrix,64);
                ++stats.faults; return false;
            }
        }
        return true;
    } __except(EXCEPTION_EXECUTE_HANDLER) {
        stats.fault_stage=2;stats.fault_exception=GetExceptionCode();
        ++stats.faults;
        return false;
    }
}

void RenderWithBoard(void* opaque) {
    auto& context = *static_cast<RenderContext*>(opaque);
    float bones[BoneCount][16];
    for(unsigned i=0;i<BoneCount;++i)ReadAffine(reinterpret_cast<void*>(context.work->matrices+i*64),bones[i]);
    weapon_presentation::Frame weapon;
    if(weapon_presentation::Prepare(context.work->packet,context.work->anchor,bones,weapon)) {
        for(unsigned i=0;i<BoneCount;++i) {
            for(int row=0;row<4;++row)std::memcpy(reinterpret_cast<void*>(context.work->matrices+i*64+row*16),bones[i]+row*4,12);
            reinterpret_cast<void*(__cdecl*)(void*)>(MatrixUpdate)(reinterpret_cast<void*>(context.work->matrices+i*64));
        }
    }
    if (!CorrectShoulders(*context.work)) return;
    if(context.fire_only) { weapon_presentation::Fire(weapon);return; }
    weapon_presentation::RenderPed(context.ped,OriginalRender,weapon);
    board_preview::Draw(context.work->packet, context.work->anchor,weapon.holster_board?weapon.board:nullptr);
}

void __fastcall PreRender(void* ped,void*) {
    weapon_presentation::ObservePreRender();
    reinterpret_cast<void(__thiscall*)(void*)>(PedPreRender)(ped);
    if(rendering||stats.faults||!weapon_control::GetState().aiming)return;
    Work work;if(!Prepare(ped,work)) { aim_offset.Clear();return; }
    RenderContext context{ped,&work,true};
    DrawScoped(reinterpret_cast<void*>(work.matrices),work.before,work.after,
               &context,RenderWithBoard,stats,rendering);
}

void __fastcall Render(void* ped, void*) {
    Work work;
    if (rendering || stats.faults || !Prepare(ped, work)) {
        ++stats.rejected;
        if(!rendering && movement_hold::GetStats().active) ++stats.active_rejected;
        if (!rendering) { history.Clear(); aim_offset.Clear(); board_preview::Idle(); weapon_presentation::Reset(); }
        OriginalRender(ped);
        return;
    }
    RenderContext context{ped,&work};
    DrawScoped(reinterpret_cast<void*>(work.matrices), work.before, work.after,
               &context, RenderWithBoard, stats, rendering);
}

bool Guards() noexcept {
    constexpr unsigned char render[] = {0x83,0xEC,0x14,0x53,0x55,0x8B,0xE9,0x8B,0x85,0x98,0x05,0x00,0x00,0x85,0xC0,0xC7};
    constexpr unsigned char update[] = {0x8B,0x44,0x24,0x04,0x81,0x60,0x0C,0xFC,0xFF,0xFD,0xFF,0xC3};
    constexpr unsigned char hierarchy[] = {0x8B,0x44,0x24,0x04,0x8B,0x0D,0xA4,0x78,0xC9,0x00,0x8B,0x04,0x01,0xC3};
    constexpr unsigned char skin[] = {0x8B,0x44,0x24,0x04,0x8B,0x0D,0xA8,0x78,0xC9,0x00,0x8B,0x04,0x01,0xC3};
    constexpr unsigned char count[] = {0x8B,0x44,0x24,0x04,0x8B,0x00,0xC3};
    constexpr unsigned char inverse[] = {0x8B,0x44,0x24,0x04,0x8B,0x40,0x0C,0xC3};
    constexpr unsigned char matrices[] = {0x8B,0x44,0x24,0x04,0x8B,0x40,0x08,0xC3};
    // Verified pinned executable: a static cdecl clump argument, plain RET.
    constexpr unsigned char shoulders[] = {0x6A,0xFF,0x68,0x75,0xD8,0x83,0x00,0x64,0xA1,
        0x00,0x00,0x00,0x00,0x50,0x64,0x89,0x25,0x00,0x00,0x00,0x00,0x81,0xEC,0x2C,0x01,0x00,0x00,
        0x8B,0x84,0x24,0x3C,0x01,0x00,0x00,0x53,0x55,0x56,0x57,0x50,0xE8,0xB4,0x54,0x15,0x00};
    constexpr unsigned char prerender[]={0x83,0xB9,0x30,0x05,0,0,0x32,0x74,0x05,0xE9,0x72,0xDB,0xFF,0xFF,0xC3};
    return Read<std::uintptr_t>(RenderSlot) == PedRender && Read<std::uintptr_t>(PreRenderSlot)==PedPreRender &&
        !std::memcmp(reinterpret_cast<void*>(PedPreRender),prerender,sizeof(prerender)) && weapon_presentation::Guards() &&
        !std::memcmp(reinterpret_cast<void*>(ShoulderBoneRotation), shoulders, sizeof(shoulders)) &&
        Read<unsigned char>(0x5DF8C9)==0xC3 &&
        !std::memcmp(reinterpret_cast<void*>(PedRender), render, sizeof(render)) &&
        !std::memcmp(reinterpret_cast<void*>(MatrixUpdate), update, sizeof(update)) &&
        !std::memcmp(reinterpret_cast<void*>(0x7C7540), hierarchy, sizeof(hierarchy)) &&
        !std::memcmp(reinterpret_cast<void*>(0x7C7550), skin, sizeof(skin)) &&
        !std::memcmp(reinterpret_cast<void*>(0x7C77E0), count, sizeof(count)) &&
        !std::memcmp(reinterpret_cast<void*>(0x7C7810), inverse, sizeof(inverse)) &&
        !std::memcmp(reinterpret_cast<void*>(0x7C5120), matrices, sizeof(matrices));
}
}

const char* Initialize(const std::wstring& directory, const Snapshot* snapshot, CaptureFn read_game) {
    const auto ini = directory + L"\\adapter.ini";
    if (GetPrivateProfileIntW(L"development", L"PosePreview", 0, ini.c_str()) != 1) return nullptr;
    if (!Guards()) return "Pose preview render/rig guards differ";
    state = snapshot;
    capture = read_game;
    board_preview::Initialize(directory);
    wchar_t name[80];
    swprintf_s(name, L"Local\\THAW_GTA_POSE_V5_%lu", GetCurrentProcessId());
    mapping = CreateFileMappingW(INVALID_HANDLE_VALUE, nullptr, PAGE_READWRITE, 0, sizeof(SharedPose), name);
    if (!mapping || GetLastError() == ERROR_ALREADY_EXISTS) return "Pose preview mapping already exists or cannot be created";
    shared = static_cast<SharedPose*>(MapViewOfFile(mapping, FILE_MAP_WRITE, 0, 0, sizeof(SharedPose)));
    if (!shared) return "Cannot map pose preview transport";
    std::memset(shared, 0, sizeof(*shared));
    DWORD protection = 0, ignored = 0;
    auto slot = reinterpret_cast<void**>(RenderSlot);
    if (!VirtualProtect(slot, sizeof(*slot), PAGE_READWRITE, &protection)) return "Cannot protect player render slot";
    InterlockedExchangePointer(slot, reinterpret_cast<void*>(Render));
    if (!VirtualProtect(slot, sizeof(*slot), protection, &ignored)) return "Cannot restore player render slot protection";
    slot=reinterpret_cast<void**>(PreRenderSlot);
    if(!VirtualProtect(slot,sizeof(*slot),PAGE_READWRITE,&protection))return "Cannot protect player PreRender slot";
    InterlockedExchangePointer(slot,reinterpret_cast<void*>(PreRender));
    if(!VirtualProtect(slot,sizeof(*slot),protection,&ignored))return "Cannot restore player PreRender slot protection";
    stats.enabled = true;
    return nullptr;
}

Stats GetStats() noexcept { return stats; }
bool GetAimOffset(std::uint64_t request,std::uint32_t ped,float* output) noexcept {
    __try {
        const auto move=movement_hold::GetStats();
        const auto now=GetTickCount64();
        float current_root[16];
        if(!state || stats.faults || !history.valid || !move.active || !move.trajectory || move.preparing ||
           !move.source_tick || move.request!=request || state->ped_address!=ped ||
           !ValidPacket(history.heartbeat,*state,now) || Read<std::uintptr_t>(ped)!=PlayerVtable ||
           Read<std::uint32_t>(ped+0x18)!=history.heartbeat.clump ||
           Read<std::uint32_t>(history.heartbeat.hierarchy+8)!=history.heartbeat.matrices ||
           !movement_hold::GetRenderRoot(request,move.source_tick,move.source_time_us,current_root)) {
            aim_offset.Clear();return false;
        }
        return aim_offset.Get(history.heartbeat,state->f7_requests,now,request,ped,output);
    } __except(EXCEPTION_EXECUTE_HANDLER) { aim_offset.Clear();return false; }
}
}
