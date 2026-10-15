#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <bcrypt.h>
#include "hud_preview.h"
#include "hud_preview_protocol.h"
#include "movement_hold.h"
#include "movement_hold_protocol.h"
#include "hud_scope.h"
#include "rw_raster.h"
#include "startup_status.h"
#include "settings_menu.h"
#include "camera_preview.h"
#include "game_effects.h"

namespace gta_thaw::hud_preview {
namespace {
const Snapshot* state=nullptr;
CaptureFn capture=nullptr;
SharedHud* shared=nullptr;
HANDLE mapping=nullptr;
std::wstring bundle_path;
Stats stats{};
Packet heartbeat{},incoming{};
bool in_render=false;
alignas(16) unsigned char atlas[MaxAtlasBytes];
unsigned char loaded_hash[32]{};
void* rasters[MaxTextures]{};
std::uint32_t texture_count=0;
std::uintptr_t resource_engine=0;
board_preview::StateFn set_state=nullptr,get_state=nullptr;
template<class T> T Read(std::uintptr_t address) noexcept {
    T value;std::memcpy(&value,reinterpret_cast<void*>(address),sizeof(value));return value;
}
void Fault(unsigned code) noexcept { ++stats.faults;stats.last_error=code;stats.active=false; }
void End(unsigned reason) noexcept { stats.active=false;stats.last_error=reason;heartbeat={}; }
bool Engine(std::uintptr_t& engine) noexcept {
    engine=Read<std::uintptr_t>(0xC97B24);
    if(engine<0x10000 || engine>=0x80000000 || (engine&3) ||
       Read<std::uintptr_t>(engine+0x20)!=0x7FE420 || Read<std::uintptr_t>(engine+0x24)!=0x7FD810 ||
       Read<std::uintptr_t>(engine+0x30)!=0x7FBB00 || !rw_raster::Guards() ||
       !rw_raster::Guard(0x7FE420,"\x8b\x4c\x24\x04\x53\x33\xdb\x33\xc0\x49\x57\x83\xf9\x1d\x0f\x87") ||
       !rw_raster::Guard(0x7FD810,"\x8b\x44\x24\x04\x48\x83\xf8\x1d\x77\x73\xff\x24\x85\x18\xda\x7f") ||
       !rw_raster::Guard(0x7FBB00,"\x83\xec\x08\xa1\xc0\xbc\xc9\x00\x53\x55\x56\x57\x33\xff\x3b\xc7")) return false;
    set_state=reinterpret_cast<board_preview::StateFn>(0x7FE420);
    get_state=reinterpret_cast<board_preview::StateFn>(0x7FD810);
    return !resource_engine || resource_engine==engine;
}
bool Hash(const unsigned char* bytes,std::uint32_t size,unsigned char* output) noexcept {
    BCRYPT_ALG_HANDLE algorithm=nullptr;
    if(BCryptOpenAlgorithmProvider(&algorithm,BCRYPT_SHA256_ALGORITHM,nullptr,0)<0) return false;
    const bool ok=BCryptHash(algorithm,nullptr,0,const_cast<unsigned char*>(bytes),size,output,32)>=0;
    BCryptCloseAlgorithmProvider(algorithm,0);return ok;
}
void Release(void*) {
    for(auto& raster:rasters) if(raster) {
        auto owned=raster;raster=nullptr;
        if(!rw_raster::Destroy(owned)) Fault(7);
        ++stats.releases;
    }
    texture_count=0;resource_engine=0;std::memset(loaded_hash,0,32);
}
bool Load(std::uintptr_t engine) {
    HANDLE file=CreateFileW(bundle_path.c_str(),GENERIC_READ,FILE_SHARE_READ,nullptr,OPEN_EXISTING,
                           FILE_ATTRIBUTE_NORMAL|FILE_FLAG_SEQUENTIAL_SCAN,nullptr);
    if(file==INVALID_HANDLE_VALUE) return false;
    LARGE_INTEGER size{};DWORD read=0;unsigned char hash[32]{};
    bool ok=GetFileSizeEx(file,&size) && size.QuadPart>=16 && size.QuadPart<=MaxAtlasBytes;
    if(ok) ok=ReadFile(file,atlas,size.LowPart,&read,nullptr) && read==size.LowPart;
    CloseHandle(file);
    if(!ok || !ValidBundle(atlas,read,texture_count) || !Hash(atlas,read,hash) ||
       std::memcmp(hash,heartbeat.atlas_hash,32)) { texture_count=0;return false; }
    resource_engine=engine;
    std::uint32_t offset=16;
    for(std::uint32_t i=0;i<texture_count;++i) {
        Texture texture;std::memcpy(&texture,atlas+offset,sizeof(texture));offset+=sizeof(texture);
        const auto result=rw_raster::UploadRGBA(texture.width,texture.height,atlas+offset,rasters[i]);
        if(result.uploaded) ++stats.uploads;
        if(!result.image_destroyed) Fault(7);
        if(!result.uploaded || !result.image_destroyed) { Release(nullptr);return false; }
        offset+=texture.bytes;
    }
    std::memcpy(loaded_hash,hash,32);++stats.loads;return true;
}
bool Select() noexcept {
    const long first=InterlockedCompareExchange(&shared->begin,0,0);
    if(first && !(first&1)) std::memcpy(&incoming,&shared->packet,sizeof(incoming));
    MemoryBarrier();
    const auto result=movement_hold::ClassifyRead(first,InterlockedCompareExchange(&shared->end,0,0),
                                                 InterlockedCompareExchange(&shared->begin,0,0));
    const auto now=GetTickCount64(); // Sample after copying, never before a newer producer heartbeat.
    if(result==movement_hold::ReadResult::Cleared) { End(1);return false; }
    const auto movement=movement_hold::GetStats();
    if(!movement.active || !movement.trajectory) { End(2);return false; }
    Snapshot live=*state;live.status_flags=0;live.ped_address=0;capture(live);
    DWORD foreground=0;GetWindowThreadProcessId(GetForegroundWindow(),&foreground);
    if(foreground==live.gta_pid) live.status_flags|=Foreground;
    if(result==movement_hold::ReadResult::Committed) {
        if(!ValidPacket(incoming,live,now) || incoming.request!=movement.request ||
           (stats.active && !Continues(incoming,heartbeat))) { ++stats.rejected;End(3);return false; }
        if(incoming.source_tick<=movement.source_tick && incoming.source_time_us<=movement.source_time_us) {
            if(!stats.active) { stats.active=true;stats.request=incoming.request;++stats.accepted; }
            heartbeat=incoming;
        } else if(!stats.active) return false;
    } else {
        ++stats.busy_reads;
        if(!stats.active) return false;
    }
    if(!ValidPacket(heartbeat,live,now) || heartbeat.request!=movement.request) { End(2);return false; }
    return true;
}
bool Set(int kind,std::uintptr_t value) { return set_state(kind,reinterpret_cast<void*>(value))!=0; }
struct Vertex { float x,y,z,rhw;std::uint32_t color;float u,v; };
static_assert(sizeof(Vertex)==28);
struct DrawContext { std::uintptr_t engine;bool ok; };
void Crosshair(float width,float height,float z,float rhw) {
    if(!camera_preview::GetAimState().active || !Set(1,0)) return;
    const float cx=width*.5f,cy=height*.5f,scale=height/720.f;
    // Four short bars leave the target visible at the exact centre of the aim ray.
    const float bars[4][4]={{-8,-1,-3,1},{3,-1,8,1},{-1,-8,1,-3},{-1,3,1,8}};
    for(unsigned pass=0;pass<2;++pass) for(const auto& bar:bars) {
        const float border=pass?0.f:1.f;
        const float l=cx+(bar[0]-border)*scale,r=cx+(bar[2]+border)*scale;
        const float t=cy+(bar[1]-border)*scale,b=cy+(bar[3]+border)*scale;
        const auto color=RwColor(pass?0xFFFFFFFF:0xC0000000);
        Vertex vertices[4]{{l,t,z,rhw,color,0,0},{l,b,z,rhw,color,0,0},
                           {r,t,z,rhw,color,0,0},{r,b,z,rhw,color,0,0}};
        reinterpret_cast<int(__cdecl*)(int,Vertex*,int)>(0x734E90)(5,vertices,4);
    }
}
void Draw(void* opaque) {
    auto& ctx=*static_cast<DrawContext*>(opaque);
    if(std::memcmp(loaded_hash,heartbeat.atlas_hash,32)) {
        Release(nullptr);
        if(!Load(ctx.engine)) { stats.last_error=4;return; }
    }
    const int width=Read<int>(0xC17044),height=Read<int>(0xC17048);
    const float z=Read<float>(0xC80460),rhw=Read<float>(0xC80464);
    if(width<=0 || width>16384 || height<=0 || height>16384 || !std::isfinite(z) ||
       !std::isfinite(rhw) || rhw<=0 || rhw>10000) { stats.last_error=5;return; }
    if(!Set(6,0) || !Set(8,0) || !Set(9,2) || !Set(10,5) || !Set(11,6) || !Set(12,1) ||
       !Set(14,0) || !Set(20,1) || !Set(3,3) || !Set(4,3) || !Set(29,8) || !Set(30,0)) return;
    for(std::uint32_t i=0;i<heartbeat.count;++i) {
        const auto& quad=heartbeat.quad[i];
        if(!ValidQuad(quad,texture_count) || !Set(1,reinterpret_cast<std::uintptr_t>(rasters[quad.texture]))) return;
        Vertex vertices[4];
        for(unsigned j=0;j<4;++j) {
            const auto& v=quad.vertex[j];
            vertices[j]={v[0]*width/640.f,v[1]*height/448.f,z,rhw,RwColor(quad.color),v[2],v[3]};
        }
        if(!reinterpret_cast<int(__cdecl*)(int,Vertex*,int)>(0x734E90)(5,vertices,4)) return;
        ++stats.quads;
    }
    Crosshair(static_cast<float>(width),static_cast<float>(height),z,rhw);
    ++stats.frames;stats.last_error=0;ctx.ok=true;
}
bool Scoped(void(*action)(void*),void* ctx) {
    return DrawScoped(set_state,get_state,action,ctx,stats.state_restores);
}
void Present() noexcept {
    __try {
        const bool selected=stats.enabled && !stats.faults && Select();
        if(!selected && !resource_engine) return;
        std::uintptr_t engine;
        if(!Engine(engine)) { Fault(6);return; }
        if(!selected) { if(!Scoped(Release,nullptr)) Fault(7);return; }
        DrawContext context{engine,false};
        if(!Scoped(Draw,&context)) Fault(7);
        else if(!context.ok) Fault(stats.last_error?stats.last_error:8);
    } __except(EXCEPTION_EXECUTE_HANDLER) { Fault(9); }
}
void __cdecl Render2D() {
    // Preserve the original overlay and append only during a valid MOVE2 lease.
    reinterpret_cast<void(__cdecl*)()>(0x53E230)();
    if(in_render) return;
    in_render=true;
    __try { game_effects::Present();Present();startup_status::Present();settings_menu::Present(); } __finally { in_render=false; }
}
}
const char* Initialize(const std::wstring& directory,const Snapshot* snapshot,CaptureFn read_game,InstallFn install) {
    const bool hud_enabled=GetPrivateProfileIntW(L"development",L"HudPreview",0,(directory+L"\\adapter.ini").c_str())==1;
    if(!rw_raster::Guard(0x734E90,"\xa1\x24\x7b\xc9\x00\xff\x60\x30") ||
       !rw_raster::Guard(0x53E230,"\xa1\x24\x7b\xc9\x00\x83\xec\x18\x6a\x00\x6a\x06\xff\x50\x20"))
        return "HUD original Render2d/RW dispatch guards differ";
    if(const auto error=startup_status::Initialize(snapshot)) return error;
    if(const auto error=settings_menu::Initialize(snapshot,install)) return error;
    state=snapshot;capture=read_game;bundle_path=directory+L"\\stock-hud\\hud-atlases.bin";
    if(hud_enabled) {
        wchar_t name[80];swprintf_s(name,L"Local\\THAW_GTA_HUD_V1_%lu",GetCurrentProcessId());
        mapping=CreateFileMappingW(INVALID_HANDLE_VALUE,nullptr,PAGE_READWRITE,0,sizeof(SharedHud),name);
        if(!mapping || GetLastError()==ERROR_ALREADY_EXISTS) return "Cannot own HUD mapping";
        shared=static_cast<SharedHud*>(MapViewOfFile(mapping,FILE_MAP_WRITE,0,0,sizeof(SharedHud)));
        if(!shared) return "Cannot map HUD transport";
        std::memset(shared,0,sizeof(*shared));
    }
    constexpr unsigned char expected[]={0xe8,0x19,0xf7,0xff,0xff};
    if(!install(0x53EB12,reinterpret_cast<void*>(Render2D),0xe8,expected,sizeof(expected))) return "Cannot install HUD callsite";
    stats.enabled=hud_enabled;return nullptr;
}
Stats GetStats() noexcept { return stats; }
}
