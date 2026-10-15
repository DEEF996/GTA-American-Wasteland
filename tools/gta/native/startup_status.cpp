#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <d3d9.h>
#include <cwchar>
#include "startup_status.h"
#include "startup_status_protocol.h"
#include "movement_hold_protocol.h"
#include "hud_scope.h"
#include "rw_raster.h"
#pragma comment(lib,"gdi32.lib")

namespace gta_thaw::startup_status {
namespace {
const Snapshot* state=nullptr;
SharedStatus* shared=nullptr;
HANDLE mapping=nullptr;
Stats stats{};
Packet incoming{},heartbeat{};
struct Texture { void* raster;std::uint32_t width,height,draw_width,draw_height; };
Texture textures[3]{};
unsigned char rgba[1024*64*4]{};
std::uintptr_t resource_engine=0,resource_device=0;
bool reset_pending=false;
board_preview::StateFn set_state=nullptr,get_state=nullptr;
template<class T> T Read(std::uintptr_t address) noexcept {
    T value;std::memcpy(&value,reinterpret_cast<void*>(address),sizeof(value));return value;
}
void Fault(unsigned error) noexcept { ++stats.faults;stats.last_error=error;stats.active=false; }
bool Select() noexcept {
    const long first=InterlockedCompareExchange(&shared->begin,0,0);
    if(first && !(first&1)) std::memcpy(&incoming,&shared->packet,sizeof(incoming));
    MemoryBarrier();
    const auto result=movement_hold::ClassifyRead(first,InterlockedCompareExchange(&shared->end,0,0),
        InterlockedCompareExchange(&shared->begin,0,0));
    const auto now=GetTickCount64();
    if(result==movement_hold::ReadResult::Cleared) { heartbeat={};return false; }
    if(result==movement_hold::ReadResult::Committed) {
        if(!ValidPacket(incoming,*state,now) || (heartbeat.request && !Continues(incoming,heartbeat))) {
            ++stats.rejected;return false;
        }
        if(incoming.sequence!=heartbeat.sequence || incoming.request!=heartbeat.request) ++stats.accepted;
        heartbeat=incoming;
    } else ++stats.busy_reads;
    return ValidPacket(heartbeat,*state,now) && Visible(heartbeat,now);
}
bool Engine(std::uintptr_t& engine,std::uintptr_t& device) noexcept {
    engine=Read<std::uintptr_t>(0xC97B24);device=Read<std::uintptr_t>(0xC97C28);
    if(engine<0x10000 || engine>=0x80000000 || (engine&3) || device<0x10000 || device>=0x80000000 ||
       Read<std::uintptr_t>(engine+0x20)!=0x7FE420 || Read<std::uintptr_t>(engine+0x24)!=0x7FD810 ||
       Read<std::uintptr_t>(engine+0x30)!=0x7FBB00 || !rw_raster::Guards() ||
       !rw_raster::Guard(0x7FE420,"\x8b\x4c\x24\x04\x53\x33\xdb\x33\xc0\x49\x57\x83\xf9\x1d\x0f\x87") ||
       !rw_raster::Guard(0x7FD810,"\x8b\x44\x24\x04\x48\x83\xf8\x1d\x77\x73\xff\x24\x85\x18\xda\x7f") ||
       !rw_raster::Guard(0x7FBB00,"\x83\xec\x08\xa1\xc0\xbc\xc9\x00\x53\x55\x56\x57\x33\xff\x3b\xc7") ||
       !rw_raster::Guard(0x734E90,"\xa1\x24\x7b\xc9\x00\xff\x60\x30")) return false;
    set_state=reinterpret_cast<board_preview::StateFn>(0x7FE420);
    get_state=reinterpret_cast<board_preview::StateFn>(0x7FD810);
    return true;
}
void Release(void*) {
    for(auto& texture:textures) if(texture.raster) {
        auto owned=texture.raster;texture={};
        if(!rw_raster::Destroy(owned)) Fault(3);
        ++stats.releases;
    }
    resource_engine=resource_device=0;
}
bool MakeText(const char* text,Texture& texture) {
    HDC dc=CreateCompatibleDC(nullptr);
    if(!dc) return false;
    HFONT font=CreateFontW(-18,0,0,0,FW_MEDIUM,FALSE,FALSE,FALSE,ANSI_CHARSET,
        OUT_DEFAULT_PRECIS,CLIP_DEFAULT_PRECIS,ANTIALIASED_QUALITY,DEFAULT_PITCH|FF_SWISS,L"Arial");
    if(!font) { DeleteDC(dc);return false; }
    HGDIOBJ old_font=SelectObject(dc,font),old_bitmap=nullptr;
    HBITMAP bitmap=nullptr;
    void* pixels=nullptr;
    SIZE size{};bool ok=false;
    const int length=static_cast<int>(std::strlen(text));
    if(old_font && old_font!=HGDI_ERROR && GetTextExtentPoint32A(dc,text,length,&size) &&
       size.cx>0 && size.cy>0 && size.cx<=1016 && size.cy<=56) {
        texture.draw_width=static_cast<std::uint32_t>(size.cx+8);
        texture.draw_height=static_cast<std::uint32_t>(size.cy+8);
        texture.width=texture.height=1;
        while(texture.width<texture.draw_width) texture.width*=2;
        while(texture.height<texture.draw_height) texture.height*=2;
        BITMAPINFO info{};info.bmiHeader.biSize=sizeof(BITMAPINFOHEADER);
        info.bmiHeader.biWidth=texture.width;info.bmiHeader.biHeight=-static_cast<LONG>(texture.height);
        info.bmiHeader.biPlanes=1;info.bmiHeader.biBitCount=32;info.bmiHeader.biCompression=BI_RGB;
        bitmap=CreateDIBSection(dc,&info,DIB_RGB_COLORS,&pixels,nullptr,0);
        if(bitmap && pixels) {
            old_bitmap=SelectObject(dc,bitmap);
            std::memset(pixels,0,texture.width*texture.height*4);
            if(old_bitmap && old_bitmap!=HGDI_ERROR && SetBkMode(dc,TRANSPARENT) &&
               SetTextColor(dc,RGB(255,255,255))!=CLR_INVALID && TextOutA(dc,4,4,text,length) && GdiFlush()) {
                const auto source=static_cast<const unsigned char*>(pixels);
                for(std::uint32_t i=0;i<texture.width*texture.height;++i) {
                    rgba[i*4]=rgba[i*4+1]=rgba[i*4+2]=255;
                    rgba[i*4+3]=source[i*4]; // Grayscale AA coverage, never ClearType RGB fringes.
                }
                ok=true;
            }
        }
    }
    if(old_bitmap && old_bitmap!=HGDI_ERROR) SelectObject(dc,old_bitmap);
    if(old_font && old_font!=HGDI_ERROR) SelectObject(dc,old_font);
    if(bitmap) DeleteObject(bitmap);
    DeleteObject(font);DeleteDC(dc);
    if(!ok) return false;
    const auto uploaded=rw_raster::UploadRGBA(texture.width,texture.height,rgba,texture.raster);
    if(uploaded.uploaded) ++stats.uploads;
    return uploaded.uploaded && uploaded.image_destroyed;
}
bool Set(int kind,std::uintptr_t value) { return set_state(kind,reinterpret_cast<void*>(value))!=0; }
struct Vertex { float x,y,z,rhw;std::uint32_t color;float u,v; };
static_assert(sizeof(Vertex)==28);
bool Quad(const Layout& box,float z,float rhw,float offset,std::uint32_t color,float u,float v) {
    Vertex vertices[4]={{box.left+offset,box.top+offset,z,rhw,color,0,0},
        {box.right+offset,box.top+offset,z,rhw,color,u,0},
        {box.right+offset,box.bottom+offset,z,rhw,color,u,v},
        {box.left+offset,box.bottom+offset,z,rhw,color,0,v}};
    return reinterpret_cast<int(__cdecl*)(int,Vertex*,int)>(0x734E90)(5,vertices,4)!=0;
}
struct DrawContext { std::uintptr_t engine,device;bool ok; };
void Draw(void* opaque) {
    auto& ctx=*static_cast<DrawContext*>(opaque);
    if(reset_pending || (resource_engine && (resource_engine!=ctx.engine || resource_device!=ctx.device))) {
        Release(nullptr);reset_pending=false;
    }
    if(!resource_engine) {
        resource_engine=ctx.engine;resource_device=ctx.device;
        if(!MakeText(Text(Preparing),textures[0]) || !MakeText(Text(Ready),textures[1]) ||
           !MakeText(Text(StartingGameplay),textures[2])) {
            Release(nullptr);stats.last_error=4;return;
        }
    }
    const int width=Read<int>(0xC17044),height=Read<int>(0xC17048);
    const float z=Read<float>(0xC80460),rhw=Read<float>(0xC80464);
    if(width<=0 || width>16384 || height<=0 || height>16384 || !std::isfinite(z) ||
       !std::isfinite(rhw) || rhw<=0 || rhw>10000) { stats.last_error=5;return; }
    const auto& texture=textures[heartbeat.kind-1];
    if(!Set(6,0) || !Set(8,0) || !Set(9,2) || !Set(10,5) || !Set(11,6) || !Set(12,1) ||
       !Set(14,0) || !Set(20,1) || !Set(3,3) || !Set(4,3) || !Set(29,8) || !Set(30,0) ||
       !Set(1,reinterpret_cast<std::uintptr_t>(texture.raster))) return;
    const auto box=Place(static_cast<float>(width),static_cast<float>(height),
        static_cast<float>(texture.draw_width),static_cast<float>(texture.draw_height));
    const float u=static_cast<float>(texture.draw_width)/texture.width,v=static_cast<float>(texture.draw_height)/texture.height;
    if(!Quad(box,z,rhw,height/720.f,0xd0000000,u,v) || !Quad(box,z,rhw,0,0xffffffff,u,v)) return;
    ++stats.frames;stats.last_error=0;ctx.ok=true;
}
}
const char* Initialize(const Snapshot* snapshot) {
    state=snapshot;
    wchar_t name[80];swprintf_s(name,L"Local\\THAW_GTA_STATUS_V1_%lu",GetCurrentProcessId());
    mapping=CreateFileMappingW(INVALID_HANDLE_VALUE,nullptr,PAGE_READWRITE,0,sizeof(SharedStatus),name);
    if(!mapping || GetLastError()==ERROR_ALREADY_EXISTS) {
        if(mapping) CloseHandle(mapping);mapping=nullptr;return "Cannot own startup status mapping";
    }
    shared=static_cast<SharedStatus*>(MapViewOfFile(mapping,FILE_MAP_WRITE,0,0,sizeof(SharedStatus)));
    if(!shared) { CloseHandle(mapping);mapping=nullptr;return "Cannot map startup status transport"; }
    std::memset(shared,0,sizeof(*shared));stats.enabled=true;return nullptr;
}
void Present() noexcept {
    __try {
        if(!stats.enabled || stats.faults) return;
        stats.active=Select();stats.kind=stats.active?heartbeat.kind:Hidden;
        if(!stats.active && !resource_engine) return;
        std::uintptr_t engine,device;
        if(!Engine(engine,device)) { Fault(1);return; }
        // Do not call drawing/state APIs while the device is lost. Recreate
        // owned rasters after a successful reset, within normal RW restoration.
        if(reinterpret_cast<IDirect3DDevice9*>(device)->TestCooperativeLevel()!=D3D_OK) {
            reset_pending=true;return;
        }
        if(!stats.active) {
            if(!hud_preview::DrawScoped(set_state,get_state,Release,nullptr,stats.state_restores)) Fault(2);
            return;
        }
        DrawContext context{engine,device,false};
        if(!hud_preview::DrawScoped(set_state,get_state,Draw,&context,stats.state_restores)) Fault(2);
        else if(!context.ok) Fault(stats.last_error?stats.last_error:6);
    } __except(EXCEPTION_EXECUTE_HANDLER) { Fault(7); }
}
Stats GetStats() noexcept { return stats; }
}
