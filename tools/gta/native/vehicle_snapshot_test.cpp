#include "vehicle_snapshot_protocol.h"
#include <cassert>
#include <cstdio>
using namespace gta_thaw;
using namespace gta_thaw::vehicle_snapshot;
namespace {
Packet p{};
void Identity(float* m) { std::fill(m,m+16,0.f);m[0]=m[5]=m[10]=m[15]=1; }
void Fixture() {
    p={};auto& h=p.header;
    std::memcpy(h.magic,"GTAVEH01",8);h.version=1;h.byte_size=sizeof(Packet);h.pid=20;h.epoch=4;h.status=Ready;
    h.process_creation_100ns=100;h.sequence=50;h.tick_ms=1000;h.radius=Radius;
    h.vehicle_count=1;h.vertex_count=3;h.triangle_count=1;h.sphere_count=1;h.box_count=1;
    auto& v=p.vehicles[0];v.id=1;v.pool_ref=0x301;v.model=400;v.entity_flags=1;v.collision_flags=1;
    v.vertex_count=3;v.triangle_count=v.sphere_count=v.box_count=1;v.health=1000;Identity(v.matrix);
    for(unsigned i=0;i<3;++i) {v.bounds_low[i]=-2;v.bounds_high[i]=2;p.boxes[0].low[i]=-1;p.boxes[0].high[i]=1;}
    p.vertices[1].xyz[0]=1;p.vertices[2].xyz[1]=1;p.triangles[0]={{0,1,2},0,0};p.spheres[0].radius=1;
}
}
int main() {
    Fixture();Snapshot s{};s.gta_pid=20;s.process_creation_100ns=100;s.epoch=4;s.sequence=51;
    assert(ValidHeader(p.header,s,1000) && ValidGeometry(p,p.vehicles[0]));
    assert(ValidHeader(p.header,s,1250));assert(!ValidHeader(p.header,s,1251));assert(!ValidHeader(p.header,s,999));
    ++s.epoch;assert(!ValidHeader(p.header,s,1000));--s.epoch;
    --s.process_creation_100ns;assert(!ValidHeader(p.header,s,1000));++s.process_creation_100ns;
    s.current_area=1;assert(!ValidHeader(p.header,s,1000));s.current_area=0;
    p.header.vehicle_count=MaxVehicles+1;assert(!ValidHeader(p.header,s,1000));Fixture();
    p.header.status=0;assert(!ValidHeader(p.header,s,1000));Fixture();
    p.header.status=Ready|Fault;assert(!ValidHeader(p.header,s,1000));Fixture();
    auto& v=p.vehicles[0];
    v.triangle_start=UINT32_MAX;assert(!ValidGeometry(p,v));Fixture();
    p.triangles[0].index[1]=3;assert(!ValidGeometry(p,v));Fixture();
    p.vertices[0].xyz[0]=std::numeric_limits<float>::quiet_NaN();assert(!ValidGeometry(p,v));Fixture();
    p.spheres[0].radius=-1;assert(!ValidGeometry(p,v));Fixture();
    p.boxes[0].low[0]=2;assert(!ValidGeometry(p,v));Fixture();
    v.matrix[0]=-1;assert(!ValidGeometry(p,v));Fixture();
    v.matrix[0]=1.2f;assert(!ValidGeometry(p,v));Fixture();
    v.matrix[0]=1.002f;assert(!ValidGeometry(p,v));Fixture(); // Same rigid tolerance as original moving-owner input.
    v.matrix[4]=.003f;assert(!ValidGeometry(p,v));Fixture();
    v.matrix[0]=1.0001f;assert(ValidGeometry(p,v));Fixture(); // Native float roundoff remains accepted.
    v.matrix[3]=std::numeric_limits<float>::quiet_NaN();assert(!ValidGeometry(p,v));Fixture();
    v.linear_velocity[2]=std::numeric_limits<float>::infinity();assert(!ValidGeometry(p,v));Fixture();
    v.entity_flags|=0x800;assert(!ValidGeometry(p,v));Fixture();
    v.physical_flags|=0x20000000;assert(!ValidGeometry(p,v));Fixture();
    v.health=0;assert(ValidGeometry(p,v)); // A wreck is still physical collision geometry.
    assert(Eligible(1,0,0,0) && !Eligible(0,0,0,0) && !Eligible(1,0,1,0));
    assert(Range(5,3,8) && !Range(UINT32_MAX,2,8) && !Range(1,UINT32_MAX,8));

    Lifetimes ids;
    assert(ids.Begin(0x10000,0x20000,0x30000,100,1,1000));
    const auto a=ids.Observe(3,1,20,400);assert(a && ids.Observe(3,1,20,400)==a);
    assert(ids.Begin(0x10000,0x20000,0x30000,100,1,1008));assert(ids.Observe(3,1,20,400)==a);
    assert(!ids.Observe(3,0x81,0,0));const auto b=ids.Observe(3,1,20,400);assert(b>a);
    const auto c=ids.Observe(3,2,20,400);assert(c>b);
    const auto d=ids.Observe(3,2,21,400);assert(d>c);
    assert(ids.Observe(3,2,21,401)>d);
    auto previous=ids.Observe(3,2,21,401);
    assert(ids.Begin(0x10000,0x20000,0x30000,100,1,1400));assert(ids.Observe(3,2,21,401)>previous);
    previous=ids.Observe(3,2,21,401);
    assert(ids.Begin(0x10000,0x20000,0x30000,100,2,1408));assert(ids.Observe(3,2,21,401)>previous);
    previous=ids.Observe(3,2,21,401);
    assert(ids.Begin(0x10000,0x40000,0x30000,100,2,1416));assert(ids.Observe(3,2,21,401)>previous);
    assert(!ids.Begin(0x10000,0x7ffff000,0x30000,100,2,1424));
    assert(!ids.Observe(3,2,21,401));
    assert(!ids.Begin(0x10000,0x20000,0x30000,MaxPool+1,1,1500));
    assert(ids.Begin(0x10000,0x20000,0x30000,100,1,1508));assert(!ids.Observe(100,1,0,400));
    // The rigid matrix must retain moving vehicle axes; transform local X at yaw90.
    float m[16];Identity(m);m[0]=0;m[1]=1;m[4]=-1;m[5]=0;m[12]=30;m[13]=40;m[14]=5;
    assert(Rigid(m));assert(m[0]*2+m[12]==30 && m[1]*2+m[13]==42);
    std::puts("vehicle_snapshot protocol, geometry and pool lifetimes PASS");
}
