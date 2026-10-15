#include <cassert>
#include <cstdio>
#include <limits>
#include "pose_preview_protocol.h"
#include "pose_preview_math.h"
#include "pose_preview_scope.h"

using namespace gta_thaw;
using namespace gta_thaw::pose_preview;

void InspectDraw(void* target) {
    auto bytes = static_cast<unsigned char*>(target);
    for (int i = 0; i < 2048; ++i) assert(bytes[i] == 0xAC);
    bytes[12] = 0x55; // Render-side flag changes must not escape the scoped draw either.
    // The native shoulder pass rewrites both helper matrices inside this scope.
    std::memset(bytes+21*64,0x31,64);
    std::memset(bytes+22*64,0x32,64);
}

void FailingDraw(void* target) {
    InspectDraw(target);
    RaiseException(0xE0424242, 0, 0, nullptr);
}

void TestScopedRestore() {
    unsigned char target[2048], before[2048], after[2048];
    for (int i = 0; i < 2048; ++i) before[i] = static_cast<unsigned char>(i);
    std::memcpy(target, before, sizeof(target));
    std::memset(after, 0xAC, sizeof(after));
    Stats stats{};
    bool active = false, caught = false;
    DrawScoped(target, before, after, target, InspectDraw, stats, active);
    assert(!active && stats.applied == 1 && stats.restored == 1 && !std::memcmp(target, before, 2048));
    __try {
        DrawScoped(target, before, after, target, FailingDraw, stats, active);
    } __except(GetExceptionCode() == 0xE0424242 ? EXCEPTION_EXECUTE_HANDLER : EXCEPTION_CONTINUE_SEARCH) {
        caught = true;
    }
    assert(caught && !active && stats.applied == 2 && stats.restored == 2 && !std::memcmp(target, before, 2048));
}

void TestPublicationCollision(const Packet& valid, const Snapshot& state) {
    assert(ReadStatus(0, 0, 0) == PacketRead::Cleared);
    assert(ReadStatus(2, 2, 0) == PacketRead::Cleared);
    assert(ReadStatus(3, 2, 3) == PacketRead::Busy);
    assert(ReadStatus(2, 4, 4) == PacketRead::Busy);
    assert(ReadStatus(2, 2, 2) == PacketRead::Ready);
    CachedPacket cache;
    Packet selected;
    assert(!SelectPacket(PacketRead::Busy, valid, cache, state, 55, selected));
    assert(SelectPacket(PacketRead::Ready, valid, cache, state, 55, selected));
    assert(!cache.valid && ValidPacket(selected, state, 1000));
    cache = {selected, 55, state.f7_requests, true}; // Commit only after the render's rig checks.
    auto unpublished = valid; unpublished.model[1][12] = .25f;
    assert(SelectPacket(PacketRead::Busy, unpublished, cache, state, 55, selected));
    assert(!std::memcmp(&selected, &valid, sizeof(valid)) && ValidPacket(selected, state, 1100));
    assert(!ValidPacket(selected, state, 1101)); // Cache never refreshes the published timestamp.
    assert(!SelectPacket(PacketRead::Busy, valid, cache, state, 0, selected));
    assert(!SelectPacket(PacketRead::Busy, valid, cache, state, 56, selected));
    auto changed = state; ++changed.f7_requests;
    assert(!SelectPacket(PacketRead::Busy, valid, cache, changed, 55, selected));
    changed = state; ++changed.epoch;
    assert(SelectPacket(PacketRead::Busy, valid, cache, changed, 55, selected));
    assert(!ValidPacket(selected, changed, 1000));
    changed = state; changed.status_flags &= ~Foreground;
    assert(SelectPacket(PacketRead::Busy, valid, cache, changed, 55, selected));
    assert(!ValidPacket(selected, changed, 1000));
    assert(!SelectPacket(PacketRead::Cleared, valid, cache, state, 55, selected) && !cache.valid);
    assert(!SelectPacket(PacketRead::Busy, valid, cache, state, 55, selected));
    cache = {valid, 55, state.f7_requests, true};
    auto invalid = valid; invalid.model[1][0] = 2;
    assert(SelectPacket(PacketRead::Ready, invalid, cache, state, 55, selected));
    assert(!cache.valid && !ValidPacket(selected, state, 1000));
    assert(!SelectPacket(PacketRead::Busy, valid, cache, state, 55, selected));
    cache = {valid, 0, state.f7_requests, true}; // Independent bounded pose preview has no MOVE lease.
    assert(SelectPacket(PacketRead::Busy, valid, cache, state, 0, selected));
    assert(!SelectPacket(PacketRead::Busy, valid, cache, state, 55, selected));
}

void TestShoulderNodeGuard(const Packet& valid) {
    auto pose=valid;
    assert(!HasShoulderNodes(pose));
    const int required[]{21,22,31,32,301,302};
    for(int i=0;i<6;++i) pose.node[i].id=required[i];
    assert(HasShoulderNodes(pose));
    for(const auto missing:required) {
        auto changed=pose;
        for(auto& node:changed.node) if(node.id==missing) node.id=999;
        assert(!HasShoulderNodes(changed));
    }
}

void TestRenderInterpolation(const Packet& valid) {
    Packet first=valid, last=valid, output{};
    first.request=last.request=44;
    first.source_tick=1; first.source_time_us=16667;
    last.source_tick=2; last.source_time_us=33333;
    last.sequence=2; last.tick_ms=1017;
    first.preview_flags=last.preview_flags=1;
    first.board_hash[0]=last.board_hash[0]=1;
    first.board_visible=1;last.board_visible=0;
    last.visual_delta[5]=last.visual_delta[10]=0;
    last.visual_delta[6]=1;last.visual_delta[9]=-1;last.visual_delta[14]=1;
    for (int i=0; i<32; ++i) {
        first.model[i][12]=i*.025f;
        std::memcpy(last.model[i],first.model[i],64);
    }
    for (int i=1; i<32; ++i) {
        last.model[i][0]=0; last.model[i][1]=1;
        last.model[i][4]=-1; last.model[i][5]=0;
        last.model[i][12]=.025f; last.model[i][13]=(i-1)*.025f;
    }
    for (int i=0; i<4; ++i) {
        std::memcpy(first.board_palette[i],valid.model[0],64);
        std::memcpy(last.board_palette[i],last.model[1],64);
        last.board_palette[i][12]=4.f;
    }
    RenderHistory history;
    float alpha=0;
    assert(history.Present(first,(1000)*1000,output,alpha) && alpha==1.f);
    assert(history.Present(last,(1017)*1000,output,alpha) && alpha==0.f);
    assert(history.Present(last,(1025)*1000,output,alpha));
    assert(std::fabs(alpha-8000.f/16666.f)<1e-6f);
    const float angle=alpha*1.57079632679f;
    assert(std::fabs(output.model[1][0]-std::cos(angle))<1e-5f);
    assert(std::fabs(output.model[1][1]-std::sin(angle))<1e-5f);
    assert(!std::memcmp(output.model[0],valid.model[0],64));
    assert(std::fabs(output.visual_delta[5]-std::cos(angle))<1e-5f);
    assert(std::fabs(output.visual_delta[6]-std::sin(angle))<1e-5f);
    assert(std::fabs(output.visual_delta[14]-alpha)<1e-5f && !output.board_visible);
    float displayed_root[16];Multiply(output.model[0],output.visual_delta,displayed_root);
    assert(std::fabs(displayed_root[14]-alpha)<1e-5f); // Weighted root receives the display pivot too.
    for (int i=1; i<32; ++i) {
        float length2=0;
        for (int c=12; c<15; ++c) {
            const float d=output.model[i][c]-output.model[i-1][c]; length2+=d*d;
        }
        assert(std::fabs(std::sqrt(length2)-.025f)<1e-5f);
        assert(Rigid(output.model[i],10.f));
    }
    assert(std::fabs(output.board_palette[0][12]-4.f*alpha)<1e-5f);
    assert(std::fabs(output.board_palette[0][0]-output.model[1][0])<1e-6f);
    auto heartbeat=last; heartbeat.sequence=3; heartbeat.tick_ms=1026;
    assert(history.Present(heartbeat,(1030)*1000,output,alpha));
    assert(std::fabs(alpha-13000.f/16666.f)<1e-6f); // Never restart on a transport heartbeat.
    assert(history.Present(heartbeat,(1050)*1000,output,alpha) && alpha==1.f);
    assert(!std::memcmp(output.model,last.model,sizeof(last.model))); // No extrapolation.
    auto changed=heartbeat; changed.sequence=4; changed.model[1][12]+=.1f;
    assert(!history.Present(changed,(1050)*1000,output,alpha)); // Same clock cannot change pose.
    changed=heartbeat;changed.sequence=4;changed.visual_delta[12]=.1f;
    assert(!history.Present(changed,(1050)*1000,output,alpha));
    changed=heartbeat;changed.sequence=4;changed.board_visible=1;
    assert(!history.Present(changed,(1050)*1000,output,alpha));
    changed=heartbeat; changed.source_tick--; assert(!history.Present(changed,(1050)*1000,output,alpha));
    changed=heartbeat; changed.request++;
    assert(history.Present(changed,(1050)*1000,output,alpha) && alpha==1.f);
    history.Clear(); assert(!history.valid);
    assert(history.Present(last,(1030)*1000,output,alpha) && alpha==1.f);
    changed=last; changed.sequence++; changed.source_tick+=20; changed.source_time_us+=333333;
    changed.tick_ms=1400;
    assert(history.Present(changed,(1401)*1000,output,alpha) && alpha==1.f); // Long gaps restart at the source.
    assert(history.Present(valid,(1401)*1000,output,alpha) && alpha==1.f && !history.valid);

    // Opposite quaternion signs and the 180-degree branch retain a rigid shortest-path basis.
    float rotated[16], mid[16], inverse[16], identity[16];
    std::memcpy(rotated,valid.model[0],64); rotated[0]=rotated[5]=-1.f;
    BlendRigid(valid.model[0],rotated,.5f,mid);
    assert(Rigid(mid,10.f) && std::fabs(mid[0])<1e-5f && std::fabs(std::fabs(mid[1])-1.f)<1e-5f);
    mid[12]=2; mid[13]=-3; mid[14]=4;
    InverseRigid(mid,inverse); Multiply(mid,inverse,identity);
    for (int i=0; i<16; ++i) assert(std::fabs(identity[i]-valid.model[0][i])<1e-5f);

    // Numeric bone indices are not HAnim traversal order: 0 -> 2 -> 1 -> 3 ...
    std::swap(first.node[1].index,first.node[2].index);
    std::swap(last.node[1].index,last.node[2].index);
    for (int c=0; c<16; ++c) {
        std::swap(first.model[1][c],first.model[2][c]);
        std::swap(last.model[1][c],last.model[2][c]);
    }
    assert(BlendPose(first,last,.5f,output));
    assert(std::fabs(output.model[1][12]-(.025f+.025f*std::sqrt(.5f)))<1e-5f);
}

void TestDetachedBoardInterpolation(const Packet& valid) {
    auto a=valid,b=valid;Packet output{};
    a.preview_flags=b.preview_flags=1;a.board_hash[0]=b.board_hash[0]=1;
    a.board_visible=b.board_visible=1;
    for(int i=0;i<4;++i) {
        std::memcpy(a.board_palette[i],a.model[0],64);
        std::memcpy(b.board_palette[i],b.model[0],64);
    }
    a.board_object_id=b.board_object_id=789;
    a.board_world[12]=1000;b.board_world[12]=1002;
    assert(BlendPose(a,b,.5f,output) && output.board_world[12]==1001);
    assert(!SamePoseSample(a,b));
    b.board_object_id=790;b.board_palette[0][12]=9;
    assert(BlendPose(a,b,.5f,output) && output.board_world[12]==1002 && output.board_palette[0][12]==9);
    assert(BlendPose(a,b,0.f,output) && output.board_world[12]==1002 && output.board_palette[0][12]==9);
    b.board_object_id=0;std::memcpy(b.board_world,valid.board_world,64);
    assert(BlendPose(a,b,.5f,output) && output.board_world[12]==0 && !output.board_object_id);
}

void TestMoveAheadOfPose(const Packet& valid) {
    Packet first=valid, last=valid, output{};
    first.request=last.request=77;
    first.source_tick=1; first.source_time_us=16667;
    last.sequence=2; last.tick_ms=1017; last.source_tick=2; last.source_time_us=33333;
    float first_root[16], last_root[16], moved_ahead[16], observed_anchor[16], rendered[16];
    std::memcpy(first_root,valid.model[0],64);
    std::memcpy(last_root,valid.model[0],64);
    std::memcpy(moved_ahead,valid.model[0],64);
    std::memcpy(observed_anchor,valid.model[0],64);
    first_root[12]=10; last_root[12]=20; moved_ahead[12]=30;
    observed_anchor[12]=32; // Native HAnim anchor includes a two-unit bind offset.
    RenderHistory history;
    float alpha=0;
    assert(history.Present(first,(1000)*1000,output,alpha,first_root));
    assert(history.Present(last,(1017)*1000,output,alpha,last_root));
    // MOVE N+1 replaces its own N-1 root before the independent POSE transport changes.
    assert(history.Present(last,(1025)*1000,output,alpha));
    assert(history.Anchor(observed_anchor,moved_ahead,alpha,rendered));
    assert(std::fabs(rendered[12]-(12.f+10.f*alpha))<1e-5f);
    assert(history.Present(last,(1040)*1000,output,alpha) && alpha==1.f);
    assert(history.Anchor(observed_anchor,moved_ahead,alpha,rendered));
    assert(rendered[12]==22.f); // Endpoint must still use POSE N, never physical N+1.
    auto heartbeat=last; heartbeat.sequence++; heartbeat.tick_ms=1041;
    assert(history.Present(heartbeat,(1041)*1000,output,alpha));
    assert(history.Anchor(observed_anchor,moved_ahead,alpha,rendered) && rendered[12]==22.f);
    auto next=heartbeat; next.sequence++; next.tick_ms=1042; next.source_tick=3; next.source_time_us=50000;
    assert(history.Present(next,(1042)*1000,output,alpha,moved_ahead) && alpha==0.f);
    assert(history.Anchor(observed_anchor,moved_ahead,alpha,rendered) && rendered[12]==22.f);
    history.Clear();
    assert(!history.Anchor(observed_anchor,moved_ahead,alpha,rendered));
    next.request++;
    assert(history.Present(next,(1042)*1000,output,alpha) && alpha==1.f);
    assert(!history.Anchor(observed_anchor,moved_ahead,alpha,rendered)); // Never borrow another lease's roots.
}

void TestQuantizedPacketClock(const Packet& valid,bool render_gap=false) {
    RenderHistory pose,coarse;
    camera_preview::CameraHistory camera;
    Packet source=valid,output{},old_output{};source.request=81;
    camera_preview::Packet view{},shown{};
    view.pid=valid.pid;view.created=valid.created;view.epoch=valid.epoch;view.request=81;
    view.source_aspect=16.f/9.f;view.view_window[0]=std::tan(36.f/57.295779513f);
    view.view_window[1]=view.view_window[0]/view.source_aspect;view.horizontal_fov_degrees=72;
    view.forward[1]=1;view.up[2]=1;
    float root[16],presented[16],alpha=1,previous=-1;
    std::uint64_t previous_cursor=0,previous_wall=1000000;unsigned repeated_old=0;
    for(unsigned frame=0;frame<240;++frame) {
        const auto elapsed=static_cast<std::uint64_t>(std::llround(frame*1000000./120.));
        const auto now_us=1000000+elapsed;
        if(!(frame%2)) {
            const auto source_us=static_cast<std::uint64_t>(std::llround((frame/2)*1000000./60.));
            source.source_tick=view.source_tick=frame/2+1;
            source.source_time_us=view.source_time_us=source_us;
            // Recorded Windows tick resolution is 15.625 ms, despite 120 Hz GTA.
            source.tick_ms=view.tick_ms=1000+(source_us/15625*15625)/1000;
            ++source.sequence;++view.sequence;
            std::memcpy(root,valid.model[0],64);root[12]=static_cast<float>(source_us)/100000;
            view.eye[0]=root[12]+5;
            source.preview_flags=1;source.board_hash[0]=1;
            for(auto& matrix:source.board_palette) {
                std::memcpy(matrix,valid.model[0],64);matrix[12]=root[12];
            }
            source.visual_delta[13]=static_cast<float>(source_us)/1000000;
        }
        // Source publication continues while no ped/camera draw occurs.
        if(render_gap && frame>=120 && frame<128)continue;
        assert(pose.Present(source,now_us,output,alpha,root));
        assert(pose.Anchor(root,root,alpha,presented));
        assert(camera.Present(view,now_us,shown));
        assert(std::fabs(shown.eye[0]-5-presented[12])<1e-5f);
        assert(std::fabs(output.board_palette[0][12]-presented[12])<1e-5f);
        assert(std::fabs(output.visual_delta[13]*10-presented[12])<1e-5f);
        if(frame>3) {
            assert(presented[12]>previous); // Every rendered frame advances, not every second tick.
            assert(presented[12]-previous<=(now_us-previous_wall)*.0000105f+.00002f);
            assert(!pose.clock.rebased && !camera.clock.rebased);
        }
        previous=presented[12];
        previous_wall=now_us;
        const auto coarse_now=(1000+(elapsed/15625*15625)/1000)*1000;
        assert(coarse.Present(source,coarse_now,old_output,alpha,root));
        if(frame>3 && coarse.clock.cursor_us==previous_cursor)++repeated_old;
        previous_cursor=coarse.clock.cursor_us;
    }
    assert(repeated_old>80); // Same source trace reproduces the actual old timer defect.
}

void TestJitteredPresentation(const Packet& valid) {
    RenderHistory history;
    camera_preview::CameraHistory camera;
    Packet source=valid, output{};
    source.request=81; source.preview_flags=1; source.board_hash[0]=1;
    camera_preview::Packet view{}, shown{};
    camera_preview::Packet old_previous{}, old_latest{};
    view.pid=valid.pid; view.created=valid.created; view.epoch=valid.epoch;
    view.request=source.request; view.source_aspect=16.f/9.f;
    view.horizontal_fov_degrees=72; view.view_window[0]=std::tan(36.f/57.295779513f);
    view.view_window[1]=view.view_window[0]/view.source_aspect;
    float actor[16], rendered[16], alpha=1;
    unsigned next_sample=0; std::uint64_t sequence=0;
    float previous_x=0; std::uint64_t previous_now=1000;
    float old_x=5.f; bool old_endpoint_jump=false;
    // Alternating 10/23 ms publications with the original 60 Hz sample clock;
    // rendering runs at 120 Hz. The old per-arrival alpha jumps at early samples.
    auto arrival=[](unsigned n) { return 1000u+(n/2)*33u+(n%2?10u:0u); };
    for(unsigned frame=0;frame<=180;++frame) {
        const auto now=1000+static_cast<std::uint64_t>(std::llround(frame*1000./120.));
        bool changed=false;
        while(arrival(next_sample)<=now) {
            const auto n=next_sample++;
            source.source_tick=view.source_tick=n;
            source.source_time_us=view.source_time_us=static_cast<std::uint64_t>(std::llround(n*1e6/60.));
            source.tick_ms=view.tick_ms=arrival(n);
            const float seconds=static_cast<float>(source.source_time_us)/1e6f;
            const float angle=seconds*.5f, c=std::cos(angle), s=std::sin(angle);
            std::memcpy(actor,valid.model[0],64); actor[12]=seconds*10.f;
            for(unsigned i=1;i<BoneCount;++i) {
                std::memcpy(source.model[i],valid.model[0],64);
                source.model[i][0]=source.model[i][5]=c;
                source.model[i][1]=s; source.model[i][4]=-s;
                source.model[i][12]=.025f+(i-1)*.025f*c;
                source.model[i][13]=(i-1)*.025f*s;
            }
            for(auto& board:source.board_palette) {
                std::memcpy(board,source.model[1],64); board[12]=seconds;
            }
            view.eye[0]=seconds*10.f+5.f;
            view.forward[0]=-s; view.forward[1]=c; view.up[2]=1;
            old_previous=old_latest; old_latest=view;
            changed=true;
        }
        if(!changed) source.tick_ms=view.tick_ms=now; // Lease-only heartbeat.
        source.sequence=view.sequence=++sequence;
        assert(history.Present(source,(now)*1000,output,alpha,actor));
        assert(history.Anchor(actor,actor,alpha,rendered));
        assert(camera.Present(view,(now)*1000,shown));
        assert(history.clock.count<=history.clock.Capacity && camera.clock.count<=camera.clock.Capacity);
        assert(!history.clock.rebased && !camera.clock.rebased);
        // All four visible components use one monotonic source-time position.
        const float seconds=static_cast<float>(history.clock.cursor_us)/1e6f;
        assert(std::fabs(rendered[12]-seconds*10.f)<1e-5f);
        assert(std::fabs(shown.eye[0]-5.f-rendered[12])<1e-5f);
        assert(std::fabs(output.board_palette[0][12]-seconds)<1e-5f);
        assert(std::fabs(std::atan2(output.model[1][1],output.model[1][0])-seconds*.5f)<1e-5f);
        assert(rendered[12]+1e-5f>=previous_x);
        assert(rendered[12]-previous_x<=static_cast<float>(now-previous_now)*.0105f+1e-5f);
        const auto old_shown=camera_preview::Interpolate(old_previous,old_latest,now);
        old_endpoint_jump|=old_shown.eye[0]-old_x>static_cast<float>(now-previous_now)*.01f+.01f;
        old_x=old_shown.eye[0];
        for(unsigned i=1;i<BoneCount;++i) {
            float length2=0;
            for(unsigned axis=12;axis<15;++axis) {
                const auto d=output.model[i][axis]-output.model[i-1][axis]; length2+=d*d;
            }
            assert(std::fabs(std::sqrt(length2)-.025f)<1e-5f);
        }
        previous_x=rendered[12]; previous_now=now;
    }
    assert(old_endpoint_jump); // The same trace explicitly reproduces the old defect.
    // Source buffering holds its last endpoint; heartbeat updates never replay
    // old time or move the actor, camera, board or limb beyond that endpoint.
    for(auto now:{2600ull,2700ull,2800ull}) {
        source.sequence=view.sequence=++sequence; source.tick_ms=view.tick_ms=now;
        assert(history.Present(source,(now)*1000,output,alpha,actor));
        assert(history.Anchor(actor,actor,alpha,rendered));
        assert(camera.Present(view,(now)*1000,shown));
        assert(alpha==1.f && rendered[12]==actor[12]);
        assert(std::fabs(shown.eye[0]-5.f-rendered[12])<1e-5f);
    }
    const float held=actor[12];
    source.source_tick++; view.source_tick++;
    source.source_time_us+=16667; view.source_time_us=source.source_time_us;
    source.sequence=view.sequence=++sequence; source.tick_ms=view.tick_ms=2810;
    actor[12]+=.16667f; view.eye[0]+=.16667f;
    assert(history.Present(source,(2810)*1000,output,alpha,actor));
    assert(history.Anchor(actor,actor,alpha,rendered));
    assert(camera.Present(view,(2810)*1000,shown));
    assert(std::fabs(rendered[12]-held)<1e-5f); // No buffered-time catch-up jump.
    assert(std::fabs(shown.eye[0]-5.f-rendered[12])<1e-5f);
    source.request++; view.request++;
    assert(history.Present(source,(2810)*1000,output,alpha,actor) && alpha==1.f);
    assert(camera.Present(view,(2810)*1000,shown));
    assert(history.clock.count==1 && camera.clock.count==1);
    assert(history.Anchor(actor,actor,alpha,rendered) && rendered[12]==actor[12]);
}

void TestAimOffset(Packet p) {
    p.request=44;p.source_tick=1;p.source_time_us=16667;
    float first[16]{},last[16]{},paired[16],displayed[16],chest[16],offset[3];
    for(int i=0;i<16;++i)first[i]=last[i]=i%5==0?1.f:0.f;
    first[12]=10;first[14]=28.7f;last[12]=110;last[14]=30.7f;
    RenderHistory poses;poses.valid=true;poses.first=0;poses.last=1;
    std::memcpy(poses.samples[0].root,first,sizeof(first));poses.samples[0].root_valid=true;
    std::memcpy(poses.samples[1].root,last,sizeof(last));poses.samples[1].root_valid=true;
    assert(poses.PresentedRoot(.5f,paired));
    assert(std::fabs(paired[12]-60.f)<1e-5f&&std::fabs(paired[14]-29.7f)<1e-5f);
    // Original hanging visual pivot places the chest below the ledge/root.
    // Both matrices use the same presentation bracket, even with MOVE ahead.
    p.model[3][14]=.5f;p.visual_delta[14]=-2.4f;
    Multiply(p.model[3],p.visual_delta,displayed);Multiply(displayed,paired,chest);
    AimOffsetCache cache;
    assert(cache.Publish(p,7,1000,chest,paired));
    assert(cache.Get(p,7,1100,44,p.ped,offset));
    assert(std::fabs(offset[0])<1e-5f&&std::fabs(offset[2]+1.9f)<1e-5f);
    // A cached displacement follows the current camera root, not old world XYZ.
    assert(std::fabs(last[14]+offset[2]-28.8f)<1e-5f);
    assert(!cache.Get(p,7,1101,44,p.ped,offset));
    assert(!cache.Get(p,7,999,44,p.ped,offset));
    assert(!cache.Get(p,8,1000,44,p.ped,offset));
    assert(!cache.Get(p,7,1000,45,p.ped,offset));
    assert(!cache.Get(p,7,1000,44,p.ped+4,offset));
    auto changed=p;changed.clump+=4;assert(!cache.Get(changed,7,1000,44,p.ped,offset));
    changed=p;changed.epoch++;assert(!cache.Get(changed,7,1000,44,p.ped,offset));
    changed=p;changed.bind_inverse[3][12]=.1f;assert(!cache.Get(changed,7,1000,44,p.ped,offset));
    cache.Clear();assert(!cache.Get(p,7,1000,44,p.ped,offset));
    chest[14]+=100;assert(!cache.Publish(p,7,1000,chest,paired));
    chest[14]=std::numeric_limits<float>::quiet_NaN();assert(!cache.Publish(p,7,1000,chest,paired));
}

int main() {
    Snapshot s{};
    std::memcpy(s.magic, "GTATHAW2", 8);
    s.version = 2; s.byte_size = sizeof(s); s.gta_pid = 17;
    s.process_creation_100ns = 123; s.sequence = 1; s.tick_ms = 1000;
    s.capabilities = Telemetry | IsolatedProfile;
    s.ped_address = 0x20000; s.epoch = 3; s.health = 100;
    s.status_flags = PedPresent | OnFoot | Foreground;
    std::strcpy(s.profile_path, "I:\\GTA_THAW\\thaw_gta\\profile");
    Packet p{};
    std::memcpy(p.magic, "GTAPOSE5", 8);
    p.version = 5; p.byte_size = sizeof(p); p.pid = 17; p.created = 123;
    p.sequence = 1; p.tick_ms = 1000; p.epoch = 3;
    p.ped = s.ped_address;
    p.clump = p.atomic = p.geometry = p.hierarchy = p.skin = 0x30000;
    p.matrices = p.nodes = p.inverse_bind = 0x40000;
    p.atomic_plugin = 0x78; p.geometry_plugin = 0x64; p.count = BoneCount;
    for(int i=0;i<16;++i) p.visual_delta[i]=p.board_world[i]=i%5==0?1.f:0.f;
    for (int i = 0; i < 32; ++i) {
        p.node[i] = {i, i, i == 31 ? 1 : 0, 0};
        for (int j = 0; j < 16; ++j) p.bind_inverse[i][j] = p.model[i][j] = j % 5 == 0 ? 1.f : 0.f;
    }
    assert(ValidPacket(p, s, 1100));
    assert(!ValidPacket(p, s, 1101));
    assert(!ValidPacket(p, s, 999));
    auto changed = p; changed.created++; assert(!ValidPacket(changed, s, 1000));
    changed=p;changed.version=4;std::memcpy(changed.magic,"GTAPOSE4",8);assert(!ValidPacket(changed,s,1000));
    changed=p;changed.visual_delta[0]=2;assert(!ValidPacket(changed,s,1000));
    changed=p;changed.visual_delta[12]=11;assert(!ValidPacket(changed,s,1000));
    changed=p;changed.visual_delta[12]=1;assert(ValidPacket(changed,s,1000));
    changed=p;changed.board_visible=1;assert(!ValidPacket(changed,s,1000));
    changed=p;changed.board_world[12]=1;assert(!ValidPacket(changed,s,1000));
    changed = p; changed.preview_flags = 1; assert(!ValidPacket(changed, s, 1000));
    changed.board_hash[0] = 1; assert(!ValidPacket(changed, s, 1000));
    for (auto& matrix : changed.board_palette) for (int i = 0; i < 16; ++i) matrix[i] = i%5 == 0 ? 1.f : 0.f;
    assert(ValidPacket(changed, s, 1000));
    changed.board_visible=1;assert(ValidPacket(changed,s,1000));
    changed.board_object_id=99;changed.board_world[12]=1500;assert(ValidPacket(changed,s,1000));
    changed.board_world[12]=20001;assert(!ValidPacket(changed,s,1000));changed.board_world[12]=1500;
    changed.board_visible=0;assert(!ValidPacket(changed,s,1000));changed.board_visible=1;
    changed.board_visible=2;assert(!ValidPacket(changed,s,1000));changed.board_visible=1;
    changed.board_palette[0][0] = 2; assert(!ValidPacket(changed,s,1000)); changed.board_palette[0][0] = 1;
    changed.preview_flags = 0; assert(!ValidPacket(changed, s, 1000));
    changed = p; changed.epoch++; assert(!ValidPacket(changed, s, 1000));
    changed = p; changed.ped += 4; assert(!ValidPacket(changed, s, 1000));
    changed = p; changed.matrices++; assert(!ValidPacket(changed, s, 1000));
    changed = p; changed.node[2].index = 1; assert(!ValidPacket(changed, s, 1000));
    changed = p; changed.node[31].flags = 0; assert(!ValidPacket(changed, s, 1000));
    changed = p; changed.node[1].flags = 1; assert(!ValidPacket(changed, s, 1000));
    changed = p; changed.model[1][0] = -1; assert(!ValidPacket(changed, s, 1000));
    changed = p; changed.model[1][0] = 2; assert(!ValidPacket(changed, s, 1000));
    changed = p; changed.model[1][12] = 11; assert(!ValidPacket(changed, s, 1000));
    changed = p; changed.model[1][6] = std::numeric_limits<float>::quiet_NaN(); assert(!ValidPacket(changed, s, 1000));
    changed = p; changed.model[0][12] = .1f; assert(!ValidPacket(changed, s, 1000));
    changed = p; changed.model[1][12] = .1f; assert(ValidPacket(changed, s, 1000));
    changed = p; changed.source_tick=1; changed.source_time_us=16667; assert(!ValidPacket(changed,s,1000));
    changed.request=44; assert(ValidPacket(changed,s,1000));
    changed.source_time_us=0; assert(!ValidPacket(changed,s,1000));
    auto blocked = s; blocked.cutscene_flags = 1; assert(!ValidPacket(p, blocked, 1000));
    blocked = s; blocked.status_flags &= ~Foreground; assert(!ValidPacket(p, blocked, 1000));
    blocked = s; blocked.status_flags |= Paused; assert(!ValidPacket(p, blocked, 1000));
    blocked = s; blocked.controls_disabled_mask = 1; assert(!ValidPacket(p, blocked, 1000));

    // Row-vector composition: local +X under a 90-degree root becomes world +Y.
    float anchor[16] = {0,1,0,0, -1,0,0,0, 0,0,1,0, 100,200,300,1};
    float local[16] = {1,0,0,0, 0,1,0,0, 0,0,1,0, 2,0,0,1}, result[16];
    Multiply(local, anchor, result);
    assert(result[12] == 100 && result[13] == 202 && result[14] == 300);
    assert(Rigid(result, 100000));
    TestPublicationCollision(p, s);
    TestRenderInterpolation(p);
    TestDetachedBoardInterpolation(p);
    TestMoveAheadOfPose(p);
    TestJitteredPresentation(p);
    TestQuantizedPacketClock(p);
    TestQuantizedPacketClock(p,true);
    TestScopedRestore();
    TestShoulderNodeGuard(p);
    TestAimOffset(p);
    std::puts("Pose preview: packet gates, source-clock interpolation, fixed bone lengths, board phase, paired hanging aim origin, cache and scoped restore passed.");
}
