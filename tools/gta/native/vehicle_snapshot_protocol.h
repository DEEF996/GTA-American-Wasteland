#pragma once
#include "bridge_protocol.h"
#include <algorithm>
#include <array>
#include <limits>

namespace gta_thaw::vehicle_snapshot {
constexpr std::uint32_t MaxVehicles=16,MaxVertices=8192,MaxTriangles=8192,MaxSpheres=512,MaxBoxes=256;
constexpr std::uint32_t MaxPool=4096,VehicleStride=0xA18;
constexpr float Radius=80.f,StepsPerSecond=50.f;
enum Status : std::uint32_t { Ready=1,Truncated=2,Fault=4 };
#pragma pack(push,1)
struct Vertex { float xyz[3]; };
struct Triangle { std::uint16_t index[3]; std::uint8_t material,lighting; };
struct Sphere { float center[3],radius; std::uint8_t material,piece,lighting,light; };
struct Box { float low[3],high[3]; std::uint8_t material,piece,lighting,light; };
struct Vehicle {
    std::uint64_t id;
    std::uint32_t pool_ref,model,area,creation_time,entity_flags,physical_flags,vehicle_class,collision_flags;
    // GTA model axes in world space, affine row-vector convention. Padding is canonical, not RW flags.
    float matrix[16];
    // World velocity at the centre of mass in m/s, angular velocity in rad/s.
    // GTA GetSpeed also adds its pending friction velocities; these are included here.
    float linear_velocity[3],angular_velocity[3],center_of_mass[3];
    float bounds_low[3],bounds_high[3];
    std::uint32_t vertex_start,vertex_count,triangle_start,triangle_count,sphere_start,sphere_count,box_start,box_count;
    std::uint32_t driver_present;
    float health;
    std::uint32_t reserved;
};
struct Header {
    char magic[8];
    std::uint32_t version,byte_size,pid,epoch,area,status,frame_counter,game_timer;
    std::uint64_t process_creation_100ns,sequence,tick_ms;
    std::uint32_t vehicle_count,vertex_count,triangle_count,sphere_count,box_count,dropped;
    float center[3],radius;
    std::uint32_t reserved[2];
};
struct Packet {
    Header header;
    Vehicle vehicles[MaxVehicles];
    Vertex vertices[MaxVertices];
    Triangle triangles[MaxTriangles];
    Sphere spheres[MaxSpheres];
    Box boxes[MaxBoxes];
};
#pragma pack(pop)
struct Shared { volatile long begin; Packet packet; volatile long end; };
static_assert(sizeof(Vertex)==12 && sizeof(Triangle)==8 && sizeof(Sphere)==20 && sizeof(Box)==28);
static_assert(sizeof(Vehicle)==208 && sizeof(Header)==112 && sizeof(Packet)==184688);
static_assert(offsetof(Packet,vertices)==3440 && offsetof(Packet,triangles)==101744);
static_assert(offsetof(Packet,spheres)==167280 && offsetof(Packet,boxes)==177520);
static_assert(sizeof(Shared)==184696 && offsetof(Shared,end)==184692);

inline bool Finite(const float* p,unsigned count,float limit) noexcept {
    for(unsigned i=0;i<count;++i) if(!std::isfinite(p[i]) || std::fabs(p[i])>limit) return false;
    return true;
}
inline bool Bounds(const float* low,const float* high) noexcept {
    if(!Finite(low,3,256.f) || !Finite(high,3,256.f)) return false;
    for(unsigned i=0;i<3;++i) if(low[i]>high[i]) return false;
    return true;
}
inline bool Rigid(const float* m) noexcept {
    if(!Finite(m,16,100000.f) || m[3]!=0 || m[7]!=0 || m[11]!=0 || m[15]!=1) return false;
    for(unsigned a=0;a<3;++a) for(unsigned b=a;b<3;++b) {
        float dot=0; for(unsigned i=0;i<3;++i) dot+=m[a*4+i]*m[b*4+i];
        if(std::fabs(dot-(a==b?1.f:0.f))>=.002f) return false;
    }
    const float det=m[0]*(m[5]*m[10]-m[6]*m[9])-m[1]*(m[4]*m[10]-m[6]*m[8])+m[2]*(m[4]*m[9]-m[5]*m[8]);
    return std::fabs(det-1.f)<.002f;
}
inline bool Range(std::uint32_t start,std::uint32_t count,std::uint32_t total) noexcept {
    return start<=total && count<=total-start;
}
inline bool Eligible(std::uint32_t entity_flags,std::uint32_t physical_flags,std::uint32_t area,std::uint32_t current_area) noexcept {
    // bUsesCollision, !bRemoveFromWorld, !bDestroyed. Wrecked cars remain solid.
    return (entity_flags&1) && !(entity_flags&0x800) && !(physical_flags&0x20000000) && area==current_area;
}
inline bool ValidVehicle(const Vehicle& v,const Header& h) noexcept {
    return v.id && (v.pool_ref>>8)<MaxPool && !(v.pool_ref&0x80) && v.model<=32767 &&
        Eligible(v.entity_flags,v.physical_flags,v.area,h.area) && v.vehicle_class<=11 &&
        v.collision_flags<=7 && (v.collision_flags&1) && v.driver_present<=1 && !v.reserved &&
        std::isfinite(v.health) && std::fabs(v.health)<=100000.f && Rigid(v.matrix) &&
        Finite(v.linear_velocity,3,2000.f) && Finite(v.angular_velocity,3,200.f) &&
        Finite(v.center_of_mass,3,256.f) && Bounds(v.bounds_low,v.bounds_high) &&
        Range(v.vertex_start,v.vertex_count,h.vertex_count) && Range(v.triangle_start,v.triangle_count,h.triangle_count) &&
        Range(v.sphere_start,v.sphere_count,h.sphere_count) && Range(v.box_start,v.box_count,h.box_count) &&
        v.vertex_count<=4096 && v.triangle_count<=4096 && v.sphere_count<=256 && v.box_count<=128 &&
        (v.triangle_count || v.sphere_count || v.box_count) && (!v.triangle_count || v.vertex_count>=3);
}
inline bool ValidGeometry(const Packet& p,const Vehicle& v) noexcept {
    if(!ValidVehicle(v,p.header)) return false;
    for(std::uint32_t i=0;i<v.vertex_count;++i) if(!Finite(p.vertices[v.vertex_start+i].xyz,3,256.f)) return false;
    for(std::uint32_t i=0;i<v.triangle_count;++i) {
        const auto& t=p.triangles[v.triangle_start+i];
        for(auto index:t.index) if(index>=v.vertex_count) return false;
    }
    for(std::uint32_t i=0;i<v.sphere_count;++i) {
        const auto& s=p.spheres[v.sphere_start+i];
        if(!Finite(s.center,3,256.f) || !std::isfinite(s.radius) || s.radius<=0 || s.radius>256.f) return false;
    }
    for(std::uint32_t i=0;i<v.box_count;++i) {
        const auto& b=p.boxes[v.box_start+i];
        if(!Bounds(b.low,b.high)) return false;
    }
    return true;
}
inline bool ValidHeader(const Header& h,const Snapshot& s,std::uint64_t now) noexcept {
    return !std::memcmp(h.magic,"GTAVEH01",8) && h.version==1 && h.byte_size==sizeof(Packet) &&
        h.pid==s.gta_pid && h.process_creation_100ns==s.process_creation_100ns && h.epoch==s.epoch && h.area==s.current_area &&
        h.sequence && h.sequence<=s.sequence && h.tick_ms<=now && now-h.tick_ms<=250 &&
        !(h.status&~7u) && !((h.status&Ready) && (h.status&Fault)) && !h.reserved[0] && !h.reserved[1] &&
        h.vehicle_count<=MaxVehicles && h.vertex_count<=MaxVertices && h.triangle_count<=MaxTriangles &&
        h.sphere_count<=MaxSpheres && h.box_count<=MaxBoxes && h.radius==Radius && Finite(h.center,3,100000.f) &&
        ((h.status&Ready) || (!h.vehicle_count && !h.vertex_count && !h.triangle_count && !h.sphere_count && !h.box_count));
}

// Track every allocated pool slot, including vehicles outside the capture radius.
// A recycled 7-bit game handle must never become the previous THAW moving owner.
class Lifetimes {
    struct Slot { std::uint64_t id=0; std::uint32_t created=0,model=0; std::uint8_t flag=0; };
    std::array<Slot,MaxPool> slots{};
    std::uint64_t next=0,last_ms=0;
    std::uint32_t pool=0,objects=0,flags=0,count=0,epoch=0;
public:
    void Clear() noexcept { slots.fill({}); pool=objects=flags=count=epoch=0; last_ms=0; }
    bool Begin(std::uint32_t p,std::uint32_t o,std::uint32_t f,std::uint32_t n,std::uint32_t e,std::uint64_t ms) noexcept {
        if(!n || n>MaxPool || o<0x10000 || o>=0x80000000 || std::uint64_t(o)+std::uint64_t(n)*VehicleStride>0x80000000) { Clear(); return false; }
        if(p!=pool || o!=objects || f!=flags || n!=count || e!=epoch || ms<last_ms || ms-last_ms>250) Clear();
        pool=p;objects=o;flags=f;count=n;epoch=e;last_ms=ms;return true;
    }
    std::uint64_t Observe(std::uint32_t index,std::uint8_t flag,std::uint32_t created,std::uint32_t model) noexcept {
        if(index>=count) return 0;
        auto& s=slots[index];
        if(flag&0x80) { s={}; return 0; }
        if(!s.id || s.flag!=flag || s.created!=created || s.model!=model) {
            if(next==std::numeric_limits<std::uint64_t>::max()) return 0;
            s={++next,created,model,flag};
        }
        return s.id;
    }
};
}
