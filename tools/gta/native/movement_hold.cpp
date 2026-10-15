#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <bcrypt.h>
#include <cstdio>
#include <initializer_list>
#include "movement_hold.h"
#include "movement_hold_protocol.h"
#include "movement_hold_scope.h"
#include "movement_command_protocol.h"
#include "movement_trajectory_protocol.h"
#include "movement_air_entry.h"
#include "movement_ped_state_policy.h"
#include "pose_preview_protocol.h"
#include "settings_menu.h"

namespace gta_thaw::movement_hold {
namespace {
constexpr std::uintptr_t PlayerVtable=0x86D168, ControlSlot=0x86D190, PlayerControl=0x60EA90;
const Snapshot* state=nullptr;
CaptureFn capture=nullptr;
SharedHold* shared=nullptr;
HANDLE mapping=nullptr;
SharedCommand* command_shared=nullptr;
HANDLE command_mapping=nullptr;
SharedTrajectory* trajectory_shared=nullptr;
HANDLE trajectory_mapping=nullptr;
Stats stats{};
Packet initial{}, heartbeat{};
CommandPacket command{};
TrajectoryPacket trajectory{};
RenderRootHistory render_roots;
std::uint64_t last_request=0, previous_sequence=0;
std::uintptr_t matrix=0, clump=0, intelligence=0, default_task=0;
std::uintptr_t rw_frame=0;
float root[12]{}, headings[2]{}, turn_speed[3]{}, entry_health=0;
float expected_velocity[3]{}, entry_heading=0;
std::uint64_t applied_ms=0;
std::uint64_t applied_source_us=0;
bool in_tick=false, in_control=false;
std::uint32_t scoped_collision_added=0;
bool ContinuousControllerOwned() noexcept {
    return stats.active && stats.trajectory && stats.interactive_enabled &&
        !initial.duration_ms && !stats.preparing && stats.applied_sequence;
}
bool Pin(std::uintptr_t,std::size_t,const char*);
bool HitReactionPins() {
    return Pin(BeHitVtable,36,"d6bda2100ca03d65e75309986ac396838b018d9ed39739990d3f75cceaa4d6d8") &&
        Pin(0x620800,6,"bf3879255bc48a32363e13dcb39083242fe0faaf9e9815938438b29fbff3c0a2") &&
        Pin(0x620A20,219,"ce18d5476938e9d1827fa2270fc194e4432d674ba3b0f84ab662a69242441dba") &&
        Pin(0x620910,270,"781cd2f0469ad7bc6aaaae7243abad9f46db06c66a6961c19707e9991a9f2c1d") &&
        Pin(0x620900,16,"93b1e493edae22cd7e43f91152fde3cdc75fe264709f4340a0b4cc115ddef764");
}
bool AirPins() {
    return Pin(0x681AF0,105,"a9146a5c9e20ece422e3d78271ead898bd0e767ea36db1a202882f4c9d76c376") &&
        Pin(0x67A070,131,"d920f73ffcb42970de752dd862417d7406cfbd35ea76305e7bc29fe86126f07a") &&
        Pin(0x678DC0,155,"08d070196ddfd4f1fc41eee49fa38bf9973383dba02e149491e16d87c8b84071") &&
        Pin(0x4211B0,8,"e00f67eafc6ef148553d1e057e2482b8f19355ab579a65c1f1241b9f7e84675d") &&
        Pin(0x870570,44,"ec1321b9a3dc4f3f20b08326d2166d472d93e536cc932126d5a04494a3864863") &&
        Pin(0x8704AC,44,"f44828f73c4b955e37a5e66eda6f7e5cfcbf74258009894fb871de0228300e1f") &&
        Pin(0x8704D8,36,"ec2c952ebed6edf5681c05935568a2d99b7a9128bf32f92ff8925bb79cc568c4") &&
        Pin(0x67D960,30,"d7adc350c7b36e78d673ebe2ce2b82aead361ebe61a747b0a0b3d040d9423a58") &&
        Pin(0x67CC10,30,"eaf8b5309ec17469745d46453cb4b773b6663f818c75962b875527d8037d826f") &&
        Pin(0x67D340,30,"e57830e5344ed23f4eaa76bc5efa81dbc80f73f45246f20790ada6fd14e8f83a") &&
        Pin(0x67A060,16,"57dc3712f005ce63181195017507e953ae9322322bc5c20827a5e1fd5c208cfd") &&
        Pin(0x678CC0,16,"ffeaa66a63c4a69077f6b47345b9524e7a02e6eed1dda2f91cbe4b121ec6b470") &&
        Pin(0x678D50,112,"d6848da33a0cbf57337cbd9280e96459b1d4406c7daa26b86c229ea9b93162d4") &&
        Pin(0x61A3D0,85,"32774fb123c35f2842a1475c9215d62626c6136fadef8f313e5d759531d8fc1f") &&
        Pin(0x61A3A0,7,"6ad04eb3ad7a3f3186acbb0a22bbb8d567780b848266c7616e8e5cdad14ede1f") &&
        Pin(0x61A5B0,41,"03d6c519887304ba39453f68eba3c2a82a3d4a04e033d41f32c754b7b15d761d") &&
        Pin(0x4CEBE0,24,"83ca9630941b5926df4b0abfb4f23a62aecbd6cc290020aca1ed804ba2414c1e");
}

template<class T> T Read(std::uintptr_t address) noexcept {
    T value; std::memcpy(&value,reinterpret_cast<void*>(address),sizeof(value)); return value;
}
bool Pointer(std::uintptr_t value) noexcept { return value>=0x10000 && value<0x80000000 && !(value&3); }
void ReadRoot(float* output) noexcept {
    for(int row=0;row<4;++row) std::memcpy(output+row*3,reinterpret_cast<void*>(matrix+row*16),12);
}
bool SameActor(void*) noexcept {
    __try {
        return initial.ped && reinterpret_cast<std::uintptr_t(__cdecl*)(int)>(0x56E210)(-1)==initial.ped &&
            Read<std::uintptr_t>(initial.ped)==PlayerVtable && Read<std::uintptr_t>(initial.ped+0x14)==matrix &&
            Read<std::uintptr_t>(initial.ped+0x18)==clump &&
            Read<std::uintptr_t>(initial.ped+0x47C)==intelligence &&
            (!stats.commanded || Read<std::uintptr_t>(clump+4)==rw_frame);
    } __except(EXCEPTION_EXECUTE_HANDLER) { return false; }
}
bool SameRoot() noexcept {
    float current[12]; ReadRoot(current);
    return !std::memcmp(current,root,sizeof(root)) &&
        !std::memcmp(reinterpret_cast<void*>(initial.ped+0x558),headings,sizeof(headings));
}
bool SameVelocity() noexcept {
    float velocity[3],angular[3];
    std::memcpy(velocity,reinterpret_cast<void*>(initial.ped+0x44),12);
    std::memcpy(angular,reinterpret_cast<void*>(initial.ped+0x50),12);
    if(SameFiniteVelocity(velocity,expected_velocity) && SameFiniteVelocity(angular,turn_speed)) return true;
    std::memcpy(stats.expected_velocity,expected_velocity,12);
    std::memcpy(stats.observed_velocity,velocity,12);
    std::memcpy(stats.expected_turn_speed,turn_speed,12);
    std::memcpy(stats.observed_turn_speed,angular,12);
    return false;
}
bool ReadJumpChain(std::uintptr_t task,AirTaskNode (&nodes)[3]) noexcept {
    for(unsigned i=0;i<3;++i) {
        if(!Pointer(task)) return false;
        nodes[i]={task,Read<std::uintptr_t>(task),Read<std::uintptr_t>(task+4),
                  i<2?Read<std::uintptr_t>(task+8):0};
        task=nodes[i].child;
    }
    return KnownJumpChain(nodes,Read<unsigned char>(nodes[2].address+0x24),Read<std::uintptr_t>(nodes[2].address+0x38));
}
bool Eligible() noexcept {
    stats.last_gate=0;
    Snapshot live=*state;
    live.status_flags=0; live.ped_address=0;
    capture(live);
    DWORD foreground=0; GetWindowThreadProcessId(GetForegroundWindow(),&foreground);
    stats.last_ped_state=Read<std::uint32_t>(initial.ped+0x530);
    stats.observed_contact_bits=Read<std::uint32_t>(initial.ped+0x46C)&ContactBits;
    if (!ControlEligible(live) || foreground!=initial.pid || live.ped_address!=initial.ped ||
        live.current_area!=initial.area || live.ped_area!=initial.ped_area ||
        !MovementHealthEligible(live.health,entry_health,ContinuousControllerOwned()) ||
        (!(stats.trajectory && stats.active && !stats.preparing) &&
            !(stats.preparing && stats.entry_contact==AirContact) && !(live.ped_motion_flags&Standing)) ||
        (stats.preparing && live.ped_motion_flags!=(stats.entry_contact==AirContact?InAir:Standing)) ||
        !PedStateAllowsMovement(stats.last_ped_state,Read<std::uint16_t>(PlayerWeaponModeAddress)) ||
        Read<std::uintptr_t>(initial.ped+0xFC) || Read<std::uintptr_t>(intelligence)!=initial.ped ||
        Read<std::uintptr_t>(intelligence+0x30)!=initial.ped) { stats.last_gate=1; return false; }
    // The pinned animation-only hit task keeps running, including native damage/events.
    // It cannot acquire a lease; every other competing primary task ends ownership.
    for(int i=0;i<4;++i) if(const auto task=Read<std::uintptr_t>(intelligence+4+i*4)) {
        AirTaskNode chain[3]{};
        if(i==3 && stats.preparing && !stats.active && stats.entry_contact==AirContact && ReadJumpChain(task,chain)) continue;
        if(i==0 && ContinuousControllerOwned() && Pointer(task) && Read<std::uintptr_t>(task)==BeHitVtable &&
           KnownContinuousHitReaction(true,i,Read<std::uintptr_t>(task),Read<std::uintptr_t>(task+4),
               Read<unsigned char>(task+0xC),Read<unsigned char>(task+0xD),Read<std::uint32_t>(task+0x20),
               HitReactionPins())) continue;
        stats.last_task=task; stats.last_task_vtable=Pointer(task)?Read<std::uint32_t>(task):0;
        stats.last_task_slot=i; stats.last_gate=2; return false;
    }
    const auto task=Read<std::uintptr_t>(intelligence+0x14);
    if (!Pointer(task) || Read<std::uintptr_t>(task)!=0x8708EC || (default_task && task!=default_task)) {
        stats.last_task=task; stats.last_task_vtable=Pointer(task)?Read<std::uint32_t>(task):0;
        stats.last_task_slot=4; stats.last_gate=3; return false;
    }
    for(int i : {0,1,4}) if(const auto secondary=Read<std::uintptr_t>(intelligence+0x18+i*4)) {
        stats.last_task=secondary; stats.last_task_vtable=Pointer(secondary)?Read<std::uint32_t>(secondary):0;
        stats.last_task_slot=5+i; stats.last_gate=4; return false;
    }
    const auto focus=Read<unsigned char>(0xB7CD74);
    if(focus>1) { stats.last_gate=1; return false; }
    if(stats.trajectory && stats.interactive_enabled) {
        if(focus!=0 || Read<std::uint32_t>(initial.ped+0x598)!=0) { stats.last_gate=6; return false; }
        return true; // This pad's processed input is suppressed only inside the owned controller.
    }
    const auto pad=0xB73458+focus*0x134;
    // No control bytes are changed. Any actual controller input ends the bounded proof.
    for(int i=0;i<48;++i) if(Read<std::int16_t>(pad+i*2)) { stats.last_gate=5; return false; }
    return true;
}
bool RemoveCapturedJump() {
    if(!AirPins()) return false;
    std::uintptr_t tasks[11];std::memcpy(tasks,reinterpret_cast<void*>(intelligence+4),sizeof(tasks));
    AirTaskNode chain[3]{};
    if(!ReadJumpChain(tasks[3],chain)) return false;
    const auto captured_flags=Read<std::uint32_t>(initial.ped+0x46C);
    if((captured_flags&ContactBits)!=0x200) return false;
    bool ok=false;
    __try {
        ++stats.air_abort_calls;
        if(!reinterpret_cast<bool(__thiscall*)(void*,void*,int,void*)>(0x67A070)
            (reinterpret_cast<void*>(tasks[3]),reinterpret_cast<void*>(initial.ped),2,nullptr)) __leave;
        AirTaskNode after[3]{};
        if(!SameActor(nullptr) || !SameRoot() || !SameVelocity() ||
           !ReadJumpChain(tasks[3],after) || std::memcmp(after,chain,sizeof(chain)) ||
           std::memcmp(tasks,reinterpret_cast<void*>(intelligence+4),sizeof(tasks)) ||
           Read<std::uintptr_t>(chain[2].address+0x1C)) __leave;
        // Original owner destructor removes the whole tree. Never clear a slot
        // or delete descendants manually, nor dereference them after SetTask.
        reinterpret_cast<void(__thiscall*)(void*,void*,int,bool)>(0x681AF0)
            (reinterpret_cast<void*>(intelligence+4),nullptr,3,false);
        tasks[3]=0;
        ok=SameActor(nullptr) && SameRoot() && SameVelocity() &&
            !std::memcmp(tasks,reinterpret_cast<void*>(intelligence+4),sizeof(tasks));
    } __finally {
        std::uint32_t restored=0;
        if(SameActor(nullptr) && RestoreCapturedAirContact(Read<std::uint32_t>(initial.ped+0x46C),captured_flags,restored))
            *reinterpret_cast<std::uint32_t*>(initial.ped+0x46C)=restored;
        else ok=false;
    }
    if(ok) ++stats.air_aborts;
    return ok;
}
void End(std::uint32_t reason) noexcept {
    if(!stats.active) return;
    render_roots={};
    if(reason==8 || reason==9) {
        stats.diagnostic_request=stats.request;stats.diagnostic_reason=reason;
        stats.rejected_sequence=stats.trajectory?trajectory.lease.sequence:command.lease.sequence;
        stats.rejected_source_tick=stats.trajectory?trajectory.source_tick:0;
        stats.rejected_source_time_us=stats.trajectory?trajectory.source_time_us:0;
        stats.rejected_contact=stats.trajectory?trajectory.contact:0;
        std::memcpy(stats.rejected_position,stats.trajectory?trajectory.position:command.position,12);
        std::memcpy(stats.rejected_velocity,stats.trajectory?trajectory.velocity:command.velocity,12);
        stats.rejection_applied_sequence=stats.applied_sequence;
        stats.rejection_applied_source_tick=stats.source_tick;
        stats.rejection_applied_source_time_us=stats.source_time_us;
        stats.rejection_applied_contact=stats.contact;
        std::memcpy(stats.rejection_applied_position,root+9,12);
        std::memcpy(stats.rejection_applied_velocity,expected_velocity,12);
    }
    __try {
        if(SameActor(nullptr)) {
            // Release the last applied state; keep any newer native task/event change.
            float velocity[3]; std::memcpy(velocity,reinterpret_cast<void*>(initial.ped+0x44),12);
            bool finite=true;
            for(float v : velocity) finite=finite && std::isfinite(v) && std::fabs(v)<=10000;
            if(finite) { std::memcpy(stats.release_velocity,velocity,12); stats.release_observed=true; }
            else { ++stats.faults; reason=6; }
            stats.release_contact_bits=Read<std::uint32_t>(initial.ped+0x46C)&ContactBits;
            if(in_tick) *reinterpret_cast<std::uint32_t*>(initial.ped+0x40)&=~scoped_collision_added;
        }
    } __except(EXCEPTION_EXECUTE_HANDLER) { ++stats.faults; reason=6; }
    stats.active=false; stats.preparing=false; stats.last_reason=reason; ++stats.completed;
    stats.source_mode=stats.source_state=stats.source_walk_state=stats.source_bailing=0;
}
template<class Shared,class Body> ReadResult PacketRead(Shared* transport,Body& p) noexcept {
    if(!transport) return ReadResult::Cleared;
    const long begin=InterlockedCompareExchange(&transport->begin,0,0);
    if(!begin) return ReadResult::Cleared;
    if(begin&1) return ClassifyRead(begin,0,InterlockedCompareExchange(&transport->begin,0,0));
    std::memcpy(&p,&transport->packet,sizeof(p)); MemoryBarrier();
    const long end=InterlockedCompareExchange(&transport->end,0,0);
    return ClassifyRead(begin,end,InterlockedCompareExchange(&transport->begin,0,0));
}
bool Heartbeat(ReadResult result,const Packet& packet,std::uint64_t now) noexcept {
    stats.last_packet_state=static_cast<std::uint32_t>(result);
    if(result==ReadResult::Busy) ++stats.busy_reads;
    if(!ReadHeartbeat(result,packet,heartbeat,*state,initial,stats.started_ms,previous_sequence,now))
        return false;
    previous_sequence=heartbeat.sequence;
    return true;
}
bool NextHeartbeat(std::uint64_t& now) noexcept {
    if(stats.trajectory) {
        TrajectoryPacket incoming{}; const auto result=PacketRead(trajectory_shared,incoming);
        now=GetTickCount64();
        stats.last_packet_state=static_cast<std::uint32_t>(result);
        if(result==ReadResult::Busy) ++stats.busy_reads;
        if(stats.preparing) {
            if(!ReadPreparationHeartbeat(result,incoming,trajectory,*state,initial,stats.preparation_started_ms,
                    previous_sequence,now,stats.entry_position,entry_heading,stats.entry_velocity,stats.entry_contact)) return false;
            previous_sequence=trajectory.lease.sequence;
            return true;
        }
        if(!ReadTrajectoryHeartbeat(result,incoming,trajectory,*state,initial,stats.started_ms,
                                    previous_sequence,now,stats.source_advanced_ms,stats.interactive_enabled)) return false;
        previous_sequence=trajectory.lease.sequence;
        return true;
    }
    if(stats.commanded) {
        CommandPacket incoming{}; const auto result=PacketRead(command_shared,incoming);
        now=GetTickCount64();
        stats.last_packet_state=static_cast<std::uint32_t>(result);
        if(result==ReadResult::Busy) ++stats.busy_reads;
        if(!ReadCommandHeartbeat(result,incoming,command,*state,initial,stats.started_ms,previous_sequence,now))
            return false;
        previous_sequence=command.lease.sequence;
        return true;
    }
    Packet p{}; const auto result=PacketRead(shared,p);
    now=GetTickCount64();
    return Heartbeat(result,p,now);
}
void Begin() noexcept {
    __try {
        if(stats.active) {
            if(stats.trajectory && state->f7_requests!=stats.entry_f7_requests) { End(11); return; }
            std::uint64_t now=0;
            if(!NextHeartbeat(now)) {
                End(stats.preparing && now-stats.preparation_started_ms>=PrepareMaxMs ? 13 :
                    initial.duration_ms && now-stats.started_ms>=initial.duration_ms ? 1 : 2); return;
            }
            if(!SameActor(nullptr) || state->epoch!=initial.epoch) { End(3); return; }
            if(!Eligible()) { End(4); return; }
            if(!SameRoot()) { End(5); return; }
            if(stats.commanded && !SameVelocity()) { End(8); return; }
            return;
        }
        Packet p{}; const auto hold_result=PacketRead(shared,p);
        CommandPacket incoming{}; const auto command_result=PacketRead(command_shared,incoming);
        TrajectoryPacket incoming_trajectory{}; const auto trajectory_result=PacketRead(trajectory_shared,incoming_trajectory);
        // Sample after the copies, never before a publisher can advance the timestamp.
        const auto now=GetTickCount64();
        const bool hold_ready=hold_result==ReadResult::Committed && ValidPacket(p,*state,now) && NewRequest(p.request,last_request);
        const bool command_ready=command_result==ReadResult::Committed && ValidCommand(incoming,*state,now) && NewRequest(incoming.lease.request,last_request);
        const bool trajectory_ready=trajectory_result==ReadResult::Committed && !IsBuffering(incoming_trajectory.contact) &&
            ValidTrajectory(incoming_trajectory,*state,now,stats.interactive_enabled) && NewRequest(incoming_trajectory.lease.request,last_request);
        if(stats.faults || (!hold_ready && !command_ready && !trajectory_ready)) return;
        if(static_cast<int>(hold_ready)+command_ready+trajectory_ready>1) {
            if(hold_ready && p.request>last_request) last_request=p.request;
            if(command_ready && incoming.lease.request>last_request) last_request=incoming.lease.request;
            if(trajectory_ready && incoming_trajectory.lease.request>last_request) last_request=incoming_trajectory.lease.request;
            ++stats.rejected; stats.last_reason=9; return;
        }
        stats.trajectory=trajectory_ready; stats.commanded=command_ready || trajectory_ready;
        stats.preparing=trajectory_ready && IsPreparation(incoming_trajectory.contact);
        stats.entry_contact=trajectory_ready && incoming_trajectory.contact==PrepareAirContact?AirContact:GroundContact;
        stats.prepared_entry=false;
        if(command_ready) { command=incoming; p=HoldEnvelope(command); }
        if(trajectory_ready) { trajectory=incoming_trajectory; p=trajectory.lease; }
        last_request=p.request; initial=p; default_task=0; stats.request=p.request;
        render_roots={};
        stats.release_observed=false;
        stats.last_task=stats.last_task_vtable=stats.last_task_slot=0;
        stats.contact=stats.release_contact_bits=0;
        stats.source_tick=stats.source_time_us=stats.collision_generation=0;
        stats.source_mode=stats.source_state=stats.source_walk_state=stats.source_bailing=0;
        matrix=Read<std::uintptr_t>(p.ped+0x14); clump=Read<std::uintptr_t>(p.ped+0x18);
        rw_frame=stats.commanded && Pointer(clump)?Read<std::uintptr_t>(clump+4):0;
        intelligence=Read<std::uintptr_t>(p.ped+0x47C); entry_health=state->health;
        if(!Pointer(matrix) || !Pointer(clump) || !Pointer(intelligence) || !SameActor(nullptr) ||
            (Read<std::uint32_t>(p.ped+0x40)&CollisionBypass) || !Eligible()) {
            ++stats.rejected; stats.last_reason=4; return;
        }
        default_task=Read<std::uintptr_t>(intelligence+0x14);
        ReadRoot(root);
        float affine[16]{};
        for(int row=0;row<4;++row) std::memcpy(affine+row*4,root+row*3,12);
        affine[15]=1;
        if(!pose_preview::Rigid(affine,100000.f)) { ++stats.rejected; stats.last_reason=3; return; }
        std::memcpy(headings,reinterpret_cast<void*>(p.ped+0x558),sizeof(headings));
        entry_heading=headings[0];
        for(float h : headings) if(!std::isfinite(h)) { ++stats.rejected; stats.last_reason=3; return; }
        std::memcpy(stats.entry_position,root+9,12);
        float velocity[3]; std::memcpy(velocity,reinterpret_cast<void*>(p.ped+0x44),12);
        std::memcpy(turn_speed,reinterpret_cast<void*>(p.ped+0x50),12);
        for(float v : velocity) if(!std::isfinite(v) || std::fabs(v)>10) { ++stats.rejected; stats.last_reason=3; return; }
        for(float v : turn_speed) if(!std::isfinite(v) || std::fabs(v)>1) { ++stats.rejected; stats.last_reason=3; return; }
        std::memcpy(stats.entry_velocity,velocity,12);
        std::memcpy(expected_velocity,velocity,12);
        if(stats.commanded) {
            bool stationary=true;
            for(int i=0;i<3;++i) stationary=stationary && velocity[i]==0 && turn_speed[i]==0;
            const float speed2=velocity[0]*velocity[0]+velocity[1]*velocity[1]+velocity[2]*velocity[2];
            const float max_raw=InteractiveMaxSpeed/50.f;
            if((stats.preparing ? speed2>max_raw*max_raw+1.e-8f : !stationary) ||
               !Pointer(rw_frame) || std::fabs(root[6])>.001f || std::fabs(root[7])>.001f ||
               std::fabs(root[8]-1)>.001f ||
               (!stats.preparing && !(stats.trajectory?TrajectoryInBounds(trajectory,root+9,entry_heading,root+9,0,true,stats.interactive_enabled):
                 CommandInBounds(command,root+9,entry_heading,root+9,entry_heading,command.lease.tick_ms,true)))) {
                ++stats.rejected; stats.last_reason=9; return;
            }
        }
        if(stats.preparing && stats.entry_contact==AirContact && !RemoveCapturedJump()) {
            ++stats.rejected;stats.last_reason=14;return;
        }
        applied_ms=p.tick_ms; stats.applied_sequence=0;
        applied_source_us=0; stats.source_advanced_ms=now;
        heartbeat=p; stats.last_packet_state=static_cast<std::uint32_t>(ReadResult::Committed);
        stats.request=p.request; stats.started_ms=now; previous_sequence=p.sequence;
        stats.entry_f7_requests=state->f7_requests;
        if(stats.preparing) {
            stats.preparation_started_ms=now;++stats.preparations;
            stats.contact=stats.entry_contact;
        }
        stats.active=true; stats.last_reason=0; ++stats.accepted;
    } __except(EXCEPTION_EXECUTE_HANDLER) { ++stats.faults; stats.last_reason=6; End(6); }
}
bool Owns(void* ped) noexcept {
    if(!stats.active || !in_tick || reinterpret_cast<std::uintptr_t>(ped)!=initial.ped) return false;
    __try {
        if(stats.trajectory && state->f7_requests!=stats.entry_f7_requests) { End(11); return false; }
        auto now=GetTickCount64();
        if(initial.duration_ms && now-stats.started_ms>=initial.duration_ms) { End(1); return false; }
        // A long game frame does not extend either bound.
        if(!NextHeartbeat(now)) {
            End(stats.preparing && now-stats.preparation_started_ms>=PrepareMaxMs?13:2); return false;
        }
        if(!SameActor(nullptr)) { End(3); return false; }
        if(!Eligible()) { End(4); return false; }
        // The known controller is scoped below; its intermediate locomotion writes
        // are restored only after the same identity, task and lease gates pass.
        if(!(ContinuousControllerOwned() && in_control)) {
            if(!SameRoot()) { End(5); return false; }
            if(stats.commanded && !SameVelocity()) { End(8); return false; }
        }
        return true;
    } __except(EXCEPTION_EXECUTE_HANDLER) { ++stats.faults; End(6); return false; }
}
bool ApplyCommand() noexcept {
    if(!stats.commanded || (stats.preparing && IsPreparation(trajectory.contact))) return true;
    if(stats.preparing) {
        // Only the pre-CGameProcess apply phase can commit: accepting a packet
        // from a later controller callback must not acknowledge an unapplied00.
        stats.preparing=false;stats.prepared_entry=true;++stats.preparation_commits;
        stats.started_ms=stats.source_advanced_ms=GetTickCount64();
    }
    const auto& lease=stats.trajectory?trajectory.lease:command.lease;
    if(lease.sequence==stats.applied_sequence) return true;
    if(!SameVelocity()) { End(8); return false; }
    if(!(stats.trajectory?TrajectoryInBounds(trajectory,stats.entry_position,entry_heading,root+9,
                                            applied_source_us,stats.applied_sequence==0,stats.interactive_enabled,
                                            stats.prepared_entry?stats.entry_velocity:nullptr,
                                            stats.prepared_entry?stats.entry_contact:GroundContact,
                                            stats.contact,stats.source_tick,stats.source_mode,stats.source_state,stats.source_bailing):
         CommandInBounds(command,stats.entry_position,entry_heading,root+9,headings[0],applied_ms,
                         stats.applied_sequence==0))) { End(9); return false; }
    const auto position=stats.trajectory?trajectory.position:command.position;
    const auto velocity=stats.trajectory?trajectory.velocity:command.velocity;
    const auto heading=stats.trajectory?trajectory.heading:command.heading;
    // Called on the game thread before CGameProcess, outside world-sector traversal.
    reinterpret_cast<void(__thiscall*)(void*,float)>(0x43E0C0)(reinterpret_cast<void*>(initial.ped),heading);
    std::memcpy(reinterpret_cast<void*>(matrix+0x30),position,12);
    headings[0]=headings[1]=heading;
    std::memcpy(reinterpret_cast<void*>(initial.ped+0x558),headings,sizeof(headings));
    std::memcpy(reinterpret_cast<void*>(initial.ped+0x44),velocity,12);
    if(stats.trajectory) {
        auto flags=reinterpret_cast<std::uint32_t*>(initial.ped+0x46C);
        *flags=ContactFlags(*flags,trajectory.contact);
        stats.contact=BaseContact(trajectory.contact);
        stats.source_tick=trajectory.source_tick; stats.source_time_us=trajectory.source_time_us;
        stats.source_mode=trajectory.source_mode; stats.source_state=trajectory.source_state;
        stats.source_walk_state=trajectory.source_walk_state; stats.source_bailing=trajectory.source_bailing;
        stats.collision_generation=trajectory.collision_generation;
        applied_source_us=trajectory.source_time_us;
    }
    reinterpret_cast<void(__thiscall*)(void*)>(0x542560)(reinterpret_cast<void*>(initial.ped));
    reinterpret_cast<void(__thiscall*)(void*,void*)>(0x59AD70)(reinterpret_cast<void*>(matrix),reinterpret_cast<void*>(rw_frame+0x10));
    reinterpret_cast<void(__thiscall*)(void*)>(0x532B00)(reinterpret_cast<void*>(initial.ped));
    ReadRoot(root);
    if(stats.trajectory) {
        float affine[16]{};
        for(int row=0;row<4;++row) std::memcpy(affine+row*4,root+row*3,12);
        affine[15]=1;
        render_roots.Commit(stats.request,stats.source_tick,stats.source_time_us,affine);
    }
    std::memcpy(expected_velocity,velocity,12);
    std::memcpy(stats.command_position,position,12);
    std::memcpy(stats.command_velocity,velocity,12);
    stats.applied_sequence=lease.sequence; applied_ms=lease.tick_ms;
    ++stats.command_commits;
    return true;
}
void* __fastcall AddAmbientAir(void* group,void*,void* event,bool valid) {
    __try {
        // Only this dedicated scanner callsite emits the synthetic fall response.
        // Keep the entire scanner and all other event/damage queues running.
        if(stats.trajectory && stats.active && in_tick && SuppressesAmbientFall(stats.contact,ContinuousControllerOwned()) &&
           Pointer(reinterpret_cast<std::uintptr_t>(group)) && Pointer(reinterpret_cast<std::uintptr_t>(event)) &&
           AmbientAirIdentity(reinterpret_cast<std::uintptr_t>(group),intelligence,
                              Read<std::uintptr_t>(reinterpret_cast<std::uintptr_t>(group)+4),initial.ped,
                              Read<std::uintptr_t>(reinterpret_cast<std::uintptr_t>(event)),valid) &&
           Owns(reinterpret_cast<void*>(initial.ped))) {
            ++stats.ambient_air_suppressed;
            return nullptr;
        }
    } __except(EXCEPTION_EXECUTE_HANDLER) { ++stats.faults; End(6); }
    return reinterpret_cast<void*(__thiscall*)(void*,void*,bool)>(0x4AB420)(group,event,valid);
}
void __fastcall PhysicalControl(void* ped,void*) {
    const bool owned=Owns(ped);
    if(owned) ++stats.physics_skips;
    reinterpret_cast<void(__thiscall*)(void*)>(owned?0x5433B0:0x5485E0)(ped);
}
void __fastcall CalculateVelocity(void* ped,void*) {
    if(Owns(ped)) { ++stats.velocity_skips; return; }
    reinterpret_cast<void(__thiscall*)(void*)>(0x5E4C50)(ped);
}
void __fastcall UpdatePosition(void* ped,void*) {
    if(Owns(ped)) { ++stats.position_skips; return; }
    reinterpret_cast<void(__thiscall*)(void*)>(0x5E1B10)(ped);
}
void OriginalControl(void* ped) {
    reinterpret_cast<void(__thiscall*)(void*)>(PlayerControl)(ped);
}
struct ControllerContext { void* ped; std::uint64_t pad_conflicts; };
void OriginalMaskedControl(void* context) {
    const auto& controller=*static_cast<ControllerContext*>(context);
    PadInputScoped(reinterpret_cast<unsigned char*>(0xB73458),controller.ped,OriginalControl,
                   stats.pad_sets,stats.pad_restores,stats.pad_conflicts);
}
bool ControllerMayRestore(void* context) {
    const auto& controller=*static_cast<ControllerContext*>(context);
    if(!ContinuousControllerOwned()) return false;
    if(stats.pad_conflicts!=controller.pad_conflicts) { End(12); return false; }
    if(state->f7_requests!=stats.entry_f7_requests) { End(11); return false; }
    std::uint64_t now=0;
    if(!NextHeartbeat(now)) { End(2); return false; }
    if(!SameActor(nullptr) || state->epoch!=initial.epoch) { End(3); return false; }
    if(!Eligible()) { End(4); return false; }
    return true;
}
void __fastcall Control(void* ped,void*) {
    const bool owned=!in_control && Owns(ped);
    if(!owned) {
        if(state && settings_menu::Open() && reinterpret_cast<std::uintptr_t>(ped)==state->ped_address)
            PadInputScoped(reinterpret_cast<unsigned char*>(0xB73458),ped,OriginalControl,
                stats.pad_sets,stats.pad_restores,stats.pad_conflicts);
        else reinterpret_cast<void(__thiscall*)(void*)>(PlayerControl)(ped);
        return;
    }
    in_control=true;
    __try {
        const auto conflicts_before=stats.pad_conflicts;
        if(ContinuousControllerOwned()) {
            ControllerContext context{ped,conflicts_before};
            ControllerMovementScoped({reinterpret_cast<unsigned char*>(matrix),
                reinterpret_cast<float*>(initial.ped+0x558),reinterpret_cast<float*>(initial.ped+0x44),
                reinterpret_cast<float*>(initial.ped+0x50),reinterpret_cast<std::uint32_t*>(initial.ped+0x46C)},
                &context,OriginalMaskedControl,ControllerMayRestore,stats.controller_movement_restores);
        } else if(stats.trajectory && stats.interactive_enabled)
            PadInputScoped(reinterpret_cast<unsigned char*>(0xB73458),ped,OriginalControl,
                           stats.pad_sets,stats.pad_restores,stats.pad_conflicts);
        else OriginalControl(ped);
        ++stats.controls;
        if(stats.pad_conflicts!=conflicts_before) { End(12); return; }
        if(Owns(ped)) {
            // Even an unchanged root can have a task/event velocity write. Never erase it.
            if(!SameVelocity()) End(8);
        }
    } __finally { in_control=false; }
}
void RunGame(void* context) {
    __try {
        if(stats.commanded && Owns(reinterpret_cast<void*>(initial.ped))) ApplyCommand();
    } __except(EXCEPTION_EXECUTE_HANDLER) { ++stats.faults; End(6); }
    reinterpret_cast<void(*)()>(context)();
    __try {
        if(stats.active) {
            if(!Owns(reinterpret_cast<void*>(initial.ped))) return;
            if(!SameVelocity()) { End(8); return; }
            if(stats.trajectory && (Read<std::uint32_t>(initial.ped+0x46C)&ContactBits)!=ContactFlags(0,stats.contact)) {
                End(10); return;
            }
            ++stats.ticks;
        }
    } __except(EXCEPTION_EXECUTE_HANDLER) { ++stats.faults; End(6); }
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
}

void ProcessTick(void(*original)()) {
    if(!stats.enabled || in_tick) { original(); return; }
    Begin();
    if(!stats.active) { original(); return; }
    in_tick=true;
    const auto controls_before=stats.controls;
    const auto collision_mask=CollisionBypass |
        (stats.trajectory && stats.interactive_enabled && !initial.duration_ms?CollisionForceBypass:0u);
    scoped_collision_added=collision_mask&~Read<std::uint32_t>(initial.ped+0x40);
    __try {
        CollisionScoped(reinterpret_cast<std::uint32_t*>(initial.ped+0x40),
            reinterpret_cast<void*>(original),RunGame,SameActor,stats.flag_sets,stats.flag_restores,collision_mask);
        if(stats.active && stats.controls==controls_before) End(7);
    } __finally { scoped_collision_added=0;in_tick=false; }
}

const char* Initialize(const std::wstring& directory,const Snapshot* snapshot,CaptureFn read_game,InstallFn install) {
    const auto settings=directory+L"\\adapter.ini";
    const bool enable_hold=GetPrivateProfileIntW(L"development",L"MovementHoldPreview",0,settings.c_str())==1;
    const bool enable_command=GetPrivateProfileIntW(L"development",L"MovementCommandPreview",0,settings.c_str())==1;
    const bool enable_trajectory=GetPrivateProfileIntW(L"development",L"MovementTrajectoryPreview",0,settings.c_str())==1;
    const bool enable_interactive=enable_trajectory && GetPrivateProfileIntW(L"development",L"MovementInteractivePreview",0,settings.c_str())==1;
    if(!enable_hold && !enable_command && !enable_trajectory) return nullptr;
    // SHA pins below are generated from the independently pinned original local executable.
    if(Read<std::uintptr_t>(ControlSlot)!=PlayerControl || Read<std::uintptr_t>(ControlSlot+4)!=0x54DFB0)
        return "Movement hold player vtable differs";
    state=snapshot; capture=read_game;
    if(!Pin(0x60ea90,16,"57604806f510aa450d63a74ca63974b6c695896a3910434e1e97f8e7518ffb58") ||
        !Pin(0x60eb6d,5,"acb339b6033bdde9e87690866c665e944bd62fbe5dd2388e8896916aa71f66b1") ||
        !Pin(0x5e91a4,5,"e47db5e764c40754533720da3d68c653d95e4c9261cb3b2c0ff8dd67451b3afb") ||
        !Pin(0x5e92dd,5,"53973d8c987e70dc977d9107923eb1ca9f8ac16aeb1df042f206934ee6fb8e1b") ||
        !Pin(0x5433b0,216,"42b6ce0ff0a048cd83ef121a35f3c5b1bf23607760e4c7678543faa477d4a712") ||
        !Pin(0x54dff3,11,"205027342f97777b51f3835542231ed026fafd4c2598d4c9fbeba08ffe41eb92") ||
        !Pin(0x54e875,19,"04dafd66f7e8881de2e3306e6b831dea07346c5bb48cb30dd61a131cc79d6b95") ||
        !Pin(0x542560,663,"620a26876c9e64b94eed1dd5065cd19d72a24297973b78b70178bbe6c1952cd5") ||
        !Pin(0x59ad70,84,"39912a8c8e8177a3d9c1fb7a3f61874eeb2259cc1cb48fe0381e7fc45eb8b6c1") ||
        !Pin(0x532b00,18,"000ceb03e5d62ced83ce1e1cc7a4dc545f525b39b4d5cb518303f37640b9c70e") ||
        !Pin(0x568a26,5,"fe8b5fecc29aa312f7cce7ab4f0a2ef2125c887132600cfbe021248b6654a2b3") ||
        !Pin(0x601924,3,"1eefa9943714cfa814e5b861e99c804139c937c5e01d3b26654048d4020d4357") ||
        !Pin(0x60194e,3,"78c44094f6bcab1688c4b7f0a06794ac042160250d3bc74918762f5283bea50f") ||
        !Pin(0x8708ec,36,"5c11de62785a6be6956fc08dfcbcf1e9076a0a25258355f1daeed43b183b5136") ||
        !Pin(0x53fb80,8,"aad67d1ad07caa1850a6e0b43c5ca7a0861953c7478cba8c194b92c91ee8492a") ||
        !Pin(0x50AB10,34,"8ba59e9cef482998da40c4feb0c33871061c142191b5db76c0e72acbcb213811") ||
        !Pin(0x685B9C,9,"ad1bfbe7c007ad3f1722407cf164472e0dcb9929ac8a9d88c27d69960e194b89"))
        return "Movement hold code guards differ";
    if((enable_command || enable_trajectory) && (!Pin(0x43e0c0,24,"36d939f1362c9729411d210327bfadf898a4706963ae5ab4d40ea5d8fe8ba65f") ||
        !Pin(0x59b020,51,"9c9c0a59a6b7e767d1fe152eef7e40776fb9260105e52144863a2f271809c6f4")))
        return "Movement command heading guards differ";
    if(enable_trajectory && (!Pin(0x607ece,140,"e79a723a0ac64e231982322c2169f7a8a1a72cd37b87a22528be0df2a96a14eb") ||
        !Pin(0x4b0cb0,17,"0e1eea69924992f3e6919bd475b69e49c69850dc0d8396ab8bade8f7fb969fc8") ||
        !Pin(0x4b0cd0,6,"f98d08691646914db28cedcf7743fd82ba7c323184fd63f58d45c5c6fb791eb8") ||
        !Pin(0x85b3c0,64,"ea6bf6b3fc6bdac1ed94bc4931333333e5431f07c39e56251c5807eb1ef6dffe") ||
        !Pin(0x4ab420,370,"3160871ac6cb203535a7ec4851ad3d693cd58a19d5067f20c6030b37c70284f0")))
        return "Movement trajectory ambient-air guards differ";
    if(enable_interactive && (!Pin(0x53fb70,16,"cef4cae1e4a986576d39e92e196ac3bbc048c3f698ef09eeaa022c90a7689741") ||
        !Pin(0x541c40,36,"2c12b573043ef8d37b51443b8dd6df75cafcc881b0b52e608ec22b4fb7dbbc5d") ||
        !Pin(0x540770,46,"f4c4dec9ceb99a3e281576cebfa40717c032546098ed10f1d53c5f9e6cb2e7e7")))
        return "Movement interactive pad guards differ";
    if(enable_interactive && !AirPins()) return "Movement interactive air-entry task guards differ";
    if(enable_interactive && (!Pin(0x549614,94,"46149301d597cce5c1673acd01004de39e90bd146b1bfaf549d31c6f1d088500") ||
        !Pin(0x54a086,207,"bf9acc718db557f2cf87e714bb85b511caaa0b23f6399ff3a985913519542d9e")))
        return "Movement interactive collision-force guards differ";
    wchar_t name[80];
    if(enable_hold) {
        swprintf_s(name,L"Local\\THAW_GTA_HOLD_V1_%lu",GetCurrentProcessId());
        mapping=CreateFileMappingW(INVALID_HANDLE_VALUE,nullptr,PAGE_READWRITE,0,sizeof(SharedHold),name);
        if(!mapping || GetLastError()==ERROR_ALREADY_EXISTS) return "Movement hold mapping unavailable";
        shared=static_cast<SharedHold*>(MapViewOfFile(mapping,FILE_MAP_WRITE,0,0,sizeof(SharedHold)));
        if(!shared) return "Cannot map movement hold transport";
        std::memset(shared,0,sizeof(*shared));
    }
    if(enable_command) {
        swprintf_s(name,L"Local\\THAW_GTA_MOVE_V1_%lu",GetCurrentProcessId());
        command_mapping=CreateFileMappingW(INVALID_HANDLE_VALUE,nullptr,PAGE_READWRITE,0,sizeof(SharedCommand),name);
        if(!command_mapping || GetLastError()==ERROR_ALREADY_EXISTS) return "Movement command mapping unavailable";
        command_shared=static_cast<SharedCommand*>(MapViewOfFile(command_mapping,FILE_MAP_WRITE,0,0,sizeof(SharedCommand)));
        if(!command_shared) return "Cannot map movement command transport";
        std::memset(command_shared,0,sizeof(*command_shared));
    }
    if(enable_trajectory) {
        swprintf_s(name,L"Local\\THAW_GTA_MOVE_V3_%lu",GetCurrentProcessId());
        trajectory_mapping=CreateFileMappingW(INVALID_HANDLE_VALUE,nullptr,PAGE_READWRITE,0,sizeof(SharedTrajectory),name);
        if(!trajectory_mapping || GetLastError()==ERROR_ALREADY_EXISTS) return "Movement trajectory mapping unavailable";
        trajectory_shared=static_cast<SharedTrajectory*>(MapViewOfFile(trajectory_mapping,FILE_MAP_WRITE,0,0,sizeof(SharedTrajectory)));
        if(!trajectory_shared) return "Cannot map movement trajectory transport";
        std::memset(trajectory_shared,0,sizeof(*trajectory_shared));
    }
    constexpr unsigned char physical[]={0xE8,0x21,0xF3,0xF5,0xFF};
    constexpr unsigned char velocity[]={0xE8,0x5E,0xB9,0xFF,0xFF};
    constexpr unsigned char position[]={0xE8,0x17,0x88,0xFF,0xFF};
    constexpr unsigned char ambient_air[]={0xE8,0x0E,0x35,0xEA,0xFF};
    if(enable_trajectory && !install(0x607F0D,AddAmbientAir,0xE8,ambient_air,sizeof(ambient_air)))
        return "Cannot install movement trajectory ambient-air callsite";
    if(!install(0x5E92BA,PhysicalControl,0xE8,physical,sizeof(physical)) ||
        !install(0x5E92ED,CalculateVelocity,0xE8,velocity,sizeof(velocity)) ||
        !install(0x5E92F4,UpdatePosition,0xE8,position,sizeof(position))) return "Cannot install movement hold callsites";
    DWORD protection=0,ignored=0; auto slot=reinterpret_cast<void**>(ControlSlot);
    if(!VirtualProtect(slot,sizeof(*slot),PAGE_READWRITE,&protection)) return "Cannot protect movement hold control slot";
    InterlockedExchangePointer(slot,reinterpret_cast<void*>(Control));
    if(!VirtualProtect(slot,sizeof(*slot),protection,&ignored)) return "Cannot restore movement hold slot protection";
    stats.enabled=true; stats.command_enabled=enable_command;
    stats.trajectory_enabled=enable_trajectory;
    stats.interactive_enabled=enable_interactive;
    return nullptr;
}
Stats GetStats() noexcept {
    auto result=stats;
    // Buffering can change on a heartbeat of the same original source sample.
    result.buffering=stats.active && stats.trajectory && IsBuffering(trajectory.contact);
    return result;
}
bool GetRenderRoot(std::uint64_t request,std::uint64_t source_tick,std::uint64_t source_time_us,float* output) noexcept {
    __try {
        if(!output || !ContinuousControllerOwned() || in_control || request!=stats.request ||
           state->f7_requests!=stats.entry_f7_requests || state->epoch!=initial.epoch || !SameActor(nullptr) || !SameRoot()) return false;
        const auto now=GetTickCount64();
        if(now<applied_ms || now-applied_ms>MaxAgeMs) return false;
        Snapshot live=*state;live.status_flags=0;live.ped_address=0;capture(live);
        DWORD foreground=0;GetWindowThreadProcessId(GetForegroundWindow(),&foreground);
        if(foreground!=initial.pid || !ControlEligible(live) || live.ped_address!=initial.ped ||
           live.current_area!=initial.area || live.ped_area!=initial.ped_area || !MovementHealthEligible(live.health,entry_health,true)) return false;
        return render_roots.Get(request,source_tick,source_time_us,output);
    } __except(EXCEPTION_EXECUTE_HANDLER) { return false; }
}
}
