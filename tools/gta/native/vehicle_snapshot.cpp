#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include "vehicle_snapshot.h"

namespace gta_thaw::vehicle_snapshot {
namespace {
constexpr std::uintptr_t VehiclePool=0xB74494,GetColModel=0x535300;
const Snapshot* state=nullptr;
HANDLE mapping=nullptr;
Shared* shared=nullptr;
Packet packet{};
Lifetimes lifetimes;
Stats stats;
std::uint64_t last_capture=0;
LARGE_INTEGER frequency{};
struct Candidate { std::uintptr_t address; std::uint64_t id; std::uint32_t index,created,model; std::uint8_t flag; float distance; };
std::array<Candidate,MaxPool> candidates{};

template<class T> T Read(std::uintptr_t address) noexcept {
    T value;std::memcpy(&value,reinterpret_cast<const void*>(address),sizeof(value));return value;
}
bool Pointer(std::uintptr_t p) noexcept { return p>=0x10000 && p<0x80000000 && !(p&3); }
bool Readable(std::uintptr_t p,std::size_t bytes) noexcept {
    if(!bytes) return true;
    if(p<0x10000 || p>=0x80000000 || bytes>0x80000000-p) return false;
    const auto end=p+bytes;
    while(p<end) {
        MEMORY_BASIC_INFORMATION m{};
        if(!VirtualQuery(reinterpret_cast<void*>(p),&m,sizeof(m)) || m.State!=MEM_COMMIT ||
            (m.Protect&(PAGE_GUARD|PAGE_NOACCESS)) || !(m.Protect&(PAGE_READONLY|PAGE_READWRITE|PAGE_WRITECOPY|PAGE_EXECUTE_READ|PAGE_EXECUTE_READWRITE|PAGE_EXECUTE_WRITECOPY))) return false;
        const auto next=reinterpret_cast<std::uintptr_t>(m.BaseAddress)+m.RegionSize;
        if(next<=p) return false;
        p=next;
    }
    return true;
}
bool Matrix(std::uintptr_t address,float* output) noexcept {
    const auto matrix=Read<std::uintptr_t>(address+0x14);
    if(matrix) {
        if(!Pointer(matrix) || !Readable(matrix,64)) return false;
        std::memcpy(output,reinterpret_cast<void*>(matrix),64);
    } else {
        // A simple parked entity can legitimately have only CPlaceable's transform.
        const auto heading=Read<float>(address+0x10);
        if(!std::isfinite(heading)) return false;
        const float c=std::cos(heading),s=std::sin(heading);
        const float basis[12]{c,s,0,0,-s,c,0,0,0,0,1,0};
        std::memcpy(output,basis,sizeof(basis));
        std::memcpy(output+12,reinterpret_cast<void*>(address+4),12);
    }
    output[3]=output[7]=output[11]=0;output[15]=1;
    return Rigid(output);
}
bool Live(std::uintptr_t address,const Snapshot& s) noexcept {
    return (Read<unsigned char>(address+0x36)&7)==2 &&
        Eligible(Read<std::uint32_t>(address+0x1C),Read<std::uint32_t>(address+0x40),Read<unsigned char>(address+0x2F),s.current_area);
}
bool Shape(std::uintptr_t address,Vehicle& v) noexcept {
    const auto special=Read<std::int8_t>(address+0x48B);
    if(special < -1 || special>3) return false;
    const auto col=reinterpret_cast<std::uintptr_t(__thiscall*)(void*)>(GetColModel)(reinterpret_cast<void*>(address));
    if(!Pointer(col) || !Readable(col,0x30)) return false;
    v.collision_flags=Read<std::uint8_t>(col+0x29);
    if(!(v.collision_flags&1) || v.collision_flags>7) return false;
    std::memcpy(v.bounds_low,reinterpret_cast<void*>(col),24);
    if(!Bounds(v.bounds_low,v.bounds_high)) return false;
    const auto data=Read<std::uintptr_t>(col+0x2C);
    if(!Pointer(data) || !Readable(data,0x30)) return false;
    v.sphere_count=Read<std::uint16_t>(data);
    v.box_count=Read<std::uint16_t>(data+2);
    v.triangle_count=Read<std::uint16_t>(data+4);
    if(v.sphere_count>256 || v.box_count>128 || v.triangle_count>4096 ||
        !Range(v.sphere_start,v.sphere_count,MaxSpheres) || !Range(v.box_start,v.box_count,MaxBoxes) ||
        !Range(v.triangle_start,v.triangle_count,MaxTriangles) || !(v.sphere_count || v.box_count || v.triangle_count)) return false;
    const auto spheres=Read<std::uintptr_t>(data+8),boxes=Read<std::uintptr_t>(data+12);
    const auto vertices=Read<std::uintptr_t>(data+20),triangles=Read<std::uintptr_t>(data+24);
    if(!Readable(spheres,v.sphere_count*sizeof(Sphere)) || !Readable(boxes,v.box_count*sizeof(Box)) ||
        !Readable(triangles,v.triangle_count*sizeof(Triangle))) return false;
    if(v.sphere_count) std::memcpy(packet.spheres+v.sphere_start,reinterpret_cast<void*>(spheres),v.sphere_count*sizeof(Sphere));
    if(v.box_count) std::memcpy(packet.boxes+v.box_start,reinterpret_cast<void*>(boxes),v.box_count*sizeof(Box));
    if(v.triangle_count) std::memcpy(packet.triangles+v.triangle_start,reinterpret_cast<void*>(triangles),v.triangle_count*sizeof(Triangle));
    for(std::uint32_t i=0;i<v.triangle_count;++i)
        for(auto index:packet.triangles[v.triangle_start+i].index) v.vertex_count=std::max(v.vertex_count,std::uint32_t(index)+1);
    if(v.vertex_count>4096 || !Range(v.vertex_start,v.vertex_count,MaxVertices) || !Readable(vertices,v.vertex_count*6)) return false;
    // CCollisionData stores collision (not shadow) vertices as signed int16 / 128.
    for(std::uint32_t i=0;i<v.vertex_count;++i) for(unsigned axis=0;axis<3;++axis)
        packet.vertices[v.vertex_start+i].xyz[axis]=Read<std::int16_t>(vertices+6*i+2*axis)/128.f;
    return true;
}
bool CopyVehicle(const Candidate& c,std::uintptr_t flags,const Snapshot& s) noexcept {
    const auto saved=packet.header;
    bool accepted=false;
    __try {
        const auto address=c.address;
        if(Read<unsigned char>(flags+c.index)==c.flag && Live(address,s) &&
            Read<std::uint32_t>(address+0x430)==c.created && Read<std::uint16_t>(address+0x22)==c.model) {
            Vehicle v{};
            v.id=c.id;v.pool_ref=(c.index<<8)|c.flag;v.model=c.model;v.creation_time=c.created;
            v.area=Read<unsigned char>(address+0x2F);v.entity_flags=Read<std::uint32_t>(address+0x1C);
            v.physical_flags=Read<std::uint32_t>(address+0x40);v.vehicle_class=Read<std::uint32_t>(address+0x590);
            v.driver_present=Pointer(Read<std::uintptr_t>(address+0x460))?1u:0u;v.health=Read<float>(address+0x4C0);
            for(unsigned i=0;i<3;++i) {
                v.linear_velocity[i]=(Read<float>(address+0x44+4*i)+Read<float>(address+0x5C+4*i))*StepsPerSecond;
                v.angular_velocity[i]=(Read<float>(address+0x50+4*i)+Read<float>(address+0x68+4*i))*StepsPerSecond;
                v.center_of_mass[i]=(v.physical_flags&0x40)?0.f:Read<float>(address+0xA4+4*i);
            }
            v.vertex_start=saved.vertex_count;v.triangle_start=saved.triangle_count;
            v.sphere_start=saved.sphere_count;v.box_start=saved.box_count;
            if(Matrix(address,v.matrix) && Shape(address,v)) {
                auto& h=packet.header;
                h.vertex_count+=v.vertex_count;h.triangle_count+=v.triangle_count;
                h.sphere_count+=v.sphere_count;h.box_count+=v.box_count;
                if(ValidGeometry(packet,v) && Read<unsigned char>(flags+c.index)==c.flag && Live(address,s)) {
                    packet.vehicles[h.vehicle_count++]=v;accepted=true;
                }
            }
        }
    } __except(EXCEPTION_EXECUTE_HANDLER) { ++stats.faults; }
    if(!accepted) { packet.header=saved;++stats.rejected; }
    return accepted;
}
void Gather(const Snapshot& s) noexcept {
    const auto pool=Read<std::uintptr_t>(VehiclePool);
    if(!Pointer(pool) || !Readable(pool,12)) { lifetimes.Clear();return; }
    const auto objects=Read<std::uint32_t>(pool),flags=Read<std::uint32_t>(pool+4),count=Read<std::uint32_t>(pool+8);
    if(!Pointer(objects) || !Pointer(flags) || !lifetimes.Begin(static_cast<std::uint32_t>(pool),objects,flags,count,s.epoch,s.tick_ms) ||
        !Readable(objects,std::size_t(count)*VehicleStride) || !Readable(flags,count)) { lifetimes.Clear();return; }
    std::uint32_t count_near=0;
    for(std::uint32_t i=0;i<count;++i) {
        const auto flag=Read<unsigned char>(flags+i);
        if(flag&0x80) { lifetimes.Observe(i,flag,0,0);continue; }
        const auto address=objects+VehicleStride*i;
        const auto created=Read<std::uint32_t>(address+0x430);
        const auto model=Read<std::int16_t>(address+0x22);
        const auto id=lifetimes.Observe(i,flag,created,static_cast<std::uint32_t>(model));
        if(!id || model<0 || !Live(address,s)) continue;
        float m[16];if(!Matrix(address,m)) { ++stats.rejected;continue; }
        float distance=0;for(unsigned a=0;a<3;++a) {const float d=m[12+a]-s.position[a];distance+=d*d;}
        if(distance>Radius*Radius) continue;
        candidates[count_near++]={address,id,i,created,static_cast<std::uint32_t>(model),flag,distance};
    }
    std::sort(candidates.begin(),candidates.begin()+count_near,[](const Candidate& a,const Candidate& b) {
        return a.distance<b.distance || (a.distance==b.distance && a.index<b.index);
    });
    for(std::uint32_t i=0;i<count_near;++i) {
        if(packet.header.vehicle_count==MaxVehicles) { packet.header.dropped+=count_near-i;break; }
        if(!CopyVehicle(candidates[i],flags,s)) ++packet.header.dropped;
    }
    if(Read<std::uintptr_t>(VehiclePool)!=pool || Read<std::uint32_t>(pool)!=objects || Read<std::uint32_t>(pool+4)!=flags || Read<std::uint32_t>(pool+8)!=count) {
        auto& h=packet.header;h.vehicle_count=h.vertex_count=h.triangle_count=h.sphere_count=h.box_count=0;
        h.status=Fault;lifetimes.Clear();++stats.faults;return;
    }
    packet.header.status=Ready|(packet.header.dropped?Truncated:0u);
}
bool Guards() noexcept {
    // Supported executable a559aa77...: GetVehicle pins pool, slot byte and 0xA18 stride.
    constexpr unsigned char pool[]{0x8B,0x4C,0x24,0x04,0x8B,0x15,0x94,0x44,0xB7,0x00,0x53,0x56,0x8B,0x72,0x04,
        0x8B,0xC1,0xC1,0xF8,0x08,0x8A,0x1C,0x30,0x5E,0x3A,0xD9,0x5B,0x75,0x0B,0x8B,0x0A,
        0x69,0xC0,0x18,0x0A,0x00,0x00,0x03,0xC1,0xC3};
    constexpr unsigned char getter[]{0x8A,0x41,0x36,0x24,0x07,0x3C,0x02,0x75,0x19,0x90,0xE9,0x35,0xC3,0xEC,0xFF,
        0x3C,0xFF,0x7E,0x0F,0x0F,0xBE,0xC0,0x8D,0x04,0x40,0xC1,0xE0,0x04,0x05,0x78,0xCC,0xC1,0x00,0xC3,
        0x0F,0xBF,0x49,0x22,0x8B,0x14,0x8D,0xC8,0xB0,0xA9,0x00,0x8B,0x42,0x14,0xC3};
    constexpr unsigned char thunk[]{0x8A,0x81,0x8B,0x04,0x00,0x00,0xE9,0xC0,0x3C,0x13,0x00};
    return !std::memcmp(reinterpret_cast<void*>(0x54FFF0),pool,sizeof(pool)) &&
        !std::memcmp(reinterpret_cast<void*>(GetColModel),getter,sizeof(getter)) &&
        !std::memcmp(reinterpret_cast<void*>(0x401644),thunk,sizeof(thunk));
}
void ClearGeometry() noexcept {
    auto& h=packet.header;h.vehicle_count=h.vertex_count=h.triangle_count=h.sphere_count=h.box_count=0;
    lifetimes.Clear();
}
}

const char* Initialize(const std::wstring& directory,const Snapshot* snapshot) {
    if(GetPrivateProfileIntW(L"development",L"MovementInteractivePreview",0,(directory+L"\\adapter.ini").c_str())!=1) return nullptr;
    if(!Guards()) return "Vehicle pool or collision-model getter guard differs";
    if(!QueryPerformanceFrequency(&frequency) || frequency.QuadPart<=0) return "Cannot read vehicle capture performance clock";
    state=snapshot;
    wchar_t name[80];swprintf_s(name,L"Local\\THAW_GTA_VEHICLES_V1_%lu",GetCurrentProcessId());
    mapping=CreateFileMappingW(INVALID_HANDLE_VALUE,nullptr,PAGE_READWRITE,0,sizeof(Shared),name);
    if(!mapping || GetLastError()==ERROR_ALREADY_EXISTS) return "Vehicle snapshot mapping already exists or cannot be created";
    shared=static_cast<Shared*>(MapViewOfFile(mapping,FILE_MAP_WRITE,0,0,sizeof(Shared)));
    if(!shared) return "Cannot map live vehicle snapshot transport";
    std::memset(shared,0,sizeof(*shared));stats.enabled=true;return nullptr;
}
void AfterTick() noexcept {
    if(!shared || !state || !state->sequence) return;
    const auto& s=*state;
    if(s.tick_ms>=last_capture && s.tick_ms-last_capture<8) return;
    last_capture=s.tick_ms;
    LARGE_INTEGER start{},end{};QueryPerformanceCounter(&start);
    std::memset(&packet,0,sizeof(packet));
    auto& h=packet.header;
    std::memcpy(h.magic,"GTAVEH01",8);h.version=1;h.byte_size=sizeof(Packet);
    h.pid=s.gta_pid;h.epoch=s.epoch;h.area=s.current_area;h.frame_counter=s.frame_counter;h.game_timer=s.game_timer;
    h.process_creation_100ns=s.process_creation_100ns;h.sequence=s.sequence;h.tick_ms=s.tick_ms;
    std::memcpy(h.center,s.position,12);h.radius=Radius;
    __try {
        if(ControlEligible(s)) Gather(s);else ClearGeometry();
    } __except(EXCEPTION_EXECUTE_HANDLER) { ClearGeometry();h.status=Fault;++stats.faults; }
    ++stats.captures;stats.vehicles=h.vehicle_count;stats.dropped+=h.dropped;
    const auto generation=static_cast<long>((h.sequence%0x3fffffffULL+1)*2);
    InterlockedExchange(&shared->begin,generation-1);
    std::memcpy(&shared->packet,&packet,sizeof(packet));MemoryBarrier();
    InterlockedExchange(&shared->end,generation);InterlockedExchange(&shared->begin,generation);
    QueryPerformanceCounter(&end);
    const auto us=static_cast<std::uint64_t>((end.QuadPart-start.QuadPart)*1000000/frequency.QuadPart);
    stats.max_capture_us=std::max(stats.max_capture_us,us);
}
Stats GetStats() noexcept { return stats; }
}
