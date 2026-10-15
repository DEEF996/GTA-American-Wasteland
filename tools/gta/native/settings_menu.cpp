#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <d3d9.h>
#include <cwchar>
#include <algorithm>
#include "settings_menu.h"
#include "settings_menu_protocol.h"
#include "movement_hold_protocol.h"
#include "hud_scope.h"
#include "rw_raster.h"
#pragma comment(lib,"gdi32.lib")

namespace gta_thaw::settings_menu {
namespace {
const Snapshot* state=nullptr;
SharedMenu* shared=nullptr;
SharedInput* input_shared=nullptr;
HANDLE mapping=nullptr,input_mapping=nullptr;
Packet heartbeat{},incoming{};
Input input{};
bool held[256]{},active=false,enabled=false,reset_pending=false;
bool in_input_frame=false;
InputFrame input_frame;
InputReleaseLatch release_latch;
InputStats input_stats;
constexpr std::uintptr_t UpdatePads=0x541DD0,UpdatePadsCall=0x53BEE6;
constexpr unsigned char UpdatePadsBytes[]={0xE8,0xE5,0x5E,0x00,0x00};
std::uint64_t repeats[256]{},last_input_ms=0,state_restores=0;
void* raster=nullptr;
std::uintptr_t resource_engine=0,resource_device=0;
char rendered[Lines][LineBytes]{};
unsigned char rgba[1024*1024*4]{};
board_preview::StateFn set_state=nullptr,get_state=nullptr;
template<class T> T Read(std::uintptr_t address) noexcept {
    T value;std::memcpy(&value,reinterpret_cast<void*>(address),sizeof(value));return value;
}
bool Select(std::uint64_t now) noexcept {
    const long first=InterlockedCompareExchange(&shared->begin,0,0);
    if(first && !(first&1)) std::memcpy(&incoming,&shared->packet,sizeof(incoming));
    MemoryBarrier();
    const auto result=movement_hold::ClassifyRead(first,InterlockedCompareExchange(&shared->end,0,0),
        InterlockedCompareExchange(&shared->begin,0,0));
    if(result==movement_hold::ReadResult::Cleared) { heartbeat={};return false; }
    if(result==movement_hold::ReadResult::Committed) {
        if(!Valid(incoming,*state,now) || (heartbeat.request && !Continues(incoming,heartbeat))) return false;
        heartbeat=incoming;
    }
    return Valid(heartbeat,*state,now);
}
void Publish(std::uint64_t now) noexcept {
    input.open=active;input.tick_ms=now;++input.sequence;
    const long generation=static_cast<long>((input.sequence&0x3fffffff)+1)*2;
    InterlockedExchange(&input_shared->begin,generation-1);
    std::memcpy(&input_shared->packet,&input,sizeof(input));MemoryBarrier();
    InterlockedExchange(&input_shared->end,generation);InterlockedExchange(&input_shared->begin,generation);
}
void Change(bool open) noexcept { if(active!=open) { active=open;++input.generation; } }
void __cdecl UpdatePadsHook() {
    reinterpret_cast<void(__cdecl*)()>(UpdatePads)();
    if(!in_input_frame || !enabled || (!Open() && !release_latch.pending)) return;
    __try {
        DWORD foreground=0;GetWindowThreadProcessId(GetForegroundWindow(),&foreground);
        if(foreground!=state->gta_pid || !Select(GetTickCount64())) { release_latch.Reset();return; }
        bool down[256]{};
        for(int key=8;key<256;++key) down[key]=(GetAsyncKeyState(key)&0x8000)!=0;
        if(release_latch.Consume(Open(),down))
            input_frame.Begin(reinterpret_cast<unsigned char*>(0xB73458),
                reinterpret_cast<unsigned char*>(0xB73190),reinterpret_cast<unsigned char*>(0xB72F20),input_stats);
    } __except(EXCEPTION_EXECUTE_HANDLER) { Change(false);enabled=false;release_latch.Reset(); }
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
    get_state=reinterpret_cast<board_preview::StateFn>(0x7FD810);return true;
}
void Release(void*) {
    if(raster) { auto owned=raster;raster=nullptr;rw_raster::Destroy(owned); }
    resource_engine=resource_device=0;std::memset(rendered,0,sizeof(rendered));
}
bool MakePanel() {
    HDC dc=CreateCompatibleDC(nullptr);if(!dc) return false;
    HFONT font=CreateFontW(-21,0,0,0,FW_MEDIUM,FALSE,FALSE,FALSE,ANSI_CHARSET,
        OUT_DEFAULT_PRECIS,CLIP_DEFAULT_PRECIS,ANTIALIASED_QUALITY,DEFAULT_PITCH|FF_SWISS,L"Arial");
    if(!font) { DeleteDC(dc);return false; }
    BITMAPINFO info{};info.bmiHeader.biSize=sizeof(BITMAPINFOHEADER);
    info.bmiHeader.biWidth=1024;info.bmiHeader.biHeight=-1024;info.bmiHeader.biPlanes=1;
    info.bmiHeader.biBitCount=32;info.bmiHeader.biCompression=BI_RGB;
    void* pixels=nullptr;HBITMAP bitmap=CreateDIBSection(dc,&info,DIB_RGB_COLORS,&pixels,nullptr,0);
    HGDIOBJ old_font=SelectObject(dc,font),old_bitmap=bitmap?SelectObject(dc,bitmap):nullptr;
    bool ok=pixels && old_font && old_font!=HGDI_ERROR && old_bitmap && old_bitmap!=HGDI_ERROR;
    if(ok) {
        std::memset(pixels,0,1024*1024*4);ok=SetBkMode(dc,TRANSPARENT)!=0;
        for(unsigned i=0;i<Lines && ok;++i) {
            const auto& line=heartbeat.line[i];
            const auto color=i<2 || line[0]=='>'?RGB(85,220,255):i>=20?RGB(180,193,203):RGB(242,246,249);
            ok=SetTextColor(dc,color)!=CLR_INVALID && TextOutA(dc,24,20+i*25,line,static_cast<int>(std::strlen(line)));
        }
        ok=ok && GdiFlush();
        const auto source=static_cast<const unsigned char*>(pixels);
        for(unsigned y=0;y<1024;++y) for(unsigned x=0;x<1024;++x) {
            const auto i=(y*1024+x)*4;
            const bool border=y<3 || (y>=647 && y<650) || x<3 || x>=1021;
            const unsigned char bg[3]={static_cast<unsigned char>(border?36:15),
                static_cast<unsigned char>(border?122:22),static_cast<unsigned char>(border?145:31)};
            for(unsigned c=0;c<3;++c) rgba[i+c]=static_cast<unsigned char>(bg[c]+source[i+2-c]*(255-bg[c])/255);
            rgba[i+3]=y<650?245:0;
        }
    }
    if(old_bitmap && old_bitmap!=HGDI_ERROR) SelectObject(dc,old_bitmap);
    if(old_font && old_font!=HGDI_ERROR) SelectObject(dc,old_font);
    if(bitmap) DeleteObject(bitmap);DeleteObject(font);DeleteDC(dc);
    if(!ok) return false;
    const auto result=rw_raster::UploadRGBA(1024,1024,rgba,raster);
    return result.uploaded && result.image_destroyed;
}
bool Set(int kind,std::uintptr_t value) { return set_state(kind,reinterpret_cast<void*>(value))!=0; }
struct Vertex { float x,y,z,rhw;std::uint32_t color;float u,v; };
struct DrawContext { std::uintptr_t engine,device;bool ok; };
void Draw(void* opaque) {
    auto& context=*static_cast<DrawContext*>(opaque);
    if(reset_pending || resource_engine!=context.engine || resource_device!=context.device ||
       std::memcmp(rendered,heartbeat.line,sizeof(rendered))) { Release(nullptr);reset_pending=false; }
    if(!raster) {
        if(!MakePanel()) return;
        resource_engine=context.engine;resource_device=context.device;
        std::memcpy(rendered,heartbeat.line,sizeof(rendered));
    }
    const int width=Read<int>(0xC17044),height=Read<int>(0xC17048);
    const float z=Read<float>(0xC80460),rhw=Read<float>(0xC80464);
    if(width<=0 || width>16384 || height<=0 || height>16384 || !std::isfinite(z) ||
       !std::isfinite(rhw) || rhw<=0 || rhw>10000) return;
    if(!Set(6,0) || !Set(8,0) || !Set(9,2) || !Set(10,5) || !Set(11,6) || !Set(12,1) ||
       !Set(14,0) || !Set(20,1) || !Set(3,3) || !Set(4,3) || !Set(29,8) || !Set(30,0) ||
       !Set(1,reinterpret_cast<std::uintptr_t>(raster))) return;
    const float scale=std::min(width*.92f/1024.f,height*.9f/650.f),w=1024*scale,h=650*scale;
    const float x=(width-w)*.5f,y=(height-h)*.5f;
    Vertex quad[4]={{x,y,z,rhw,0xffffffff,0,0},{x+w,y,z,rhw,0xffffffff,1,0},
        {x+w,y+h,z,rhw,0xffffffff,1,650.f/1024},{x,y+h,z,rhw,0xffffffff,0,650.f/1024}};
    context.ok=reinterpret_cast<int(__cdecl*)(int,Vertex*,int)>(0x734E90)(5,quad,4)!=0;
}
}
const char* Initialize(const Snapshot* snapshot,InstallFn install) {
    // Pin the existing CGame::Process call and the original snapshot copies.
    // Compatible input mods may still wrap UpdatePads itself; always call it.
    if(!rw_raster::Guard(0x541E04,"\x6a\x00\xb9\x58\x34\xb7\x00\xe8\x30\xfe\xff\xff") ||
       !rw_raster::Guard(0x541C54,"\x8d\x7b\x30\xb9\x0c\x00\x00\x00\x8b\xf3\x8d\x44\x24\x18\xf3\xa5") ||
       !rw_raster::Guard(0x541E1C,"\xb9\x9c\x00\x00\x00\xbe\x90\x31\xb7\x00\xbf\x20\x2f\xb7\x00\xf3\xa5\xb9\x9c\x00\x00\x00\xbe\xb0\x2c\xb7\x00\xbf\x90\x31\xb7\x00\xf3\xa5\x5f\x5e\xc3") ||
       !rw_raster::Guard(0x53FB70,"\x8b\x44\x24\x04\x69\xc0\x34\x01\x00\x00\x05\x58\x34\xb7\x00\xc3") ||
       std::memcmp(reinterpret_cast<void*>(UpdatePadsCall),UpdatePadsBytes,sizeof(UpdatePadsBytes)))
        return "Settings input scope code guards differ";
    state=snapshot;wchar_t name[96];
    swprintf_s(name,L"Local\\THAW_GTA_MENU_V1_%lu",GetCurrentProcessId());
    mapping=CreateFileMappingW(INVALID_HANDLE_VALUE,nullptr,PAGE_READWRITE,0,sizeof(SharedMenu),name);
    if(!mapping || GetLastError()==ERROR_ALREADY_EXISTS) return "Cannot own settings menu mapping";
    shared=static_cast<SharedMenu*>(MapViewOfFile(mapping,FILE_MAP_WRITE,0,0,sizeof(SharedMenu)));
    if(!shared) return "Cannot map settings menu transport";
    swprintf_s(name,L"Local\\THAW_GTA_MENU_INPUT_V1_%lu",GetCurrentProcessId());
    input_mapping=CreateFileMappingW(INVALID_HANDLE_VALUE,nullptr,PAGE_READWRITE,0,sizeof(SharedInput),name);
    if(!input_mapping || GetLastError()==ERROR_ALREADY_EXISTS) return "Cannot own settings input mapping";
    input_shared=static_cast<SharedInput*>(MapViewOfFile(input_mapping,FILE_MAP_WRITE,0,0,sizeof(SharedInput)));
    if(!input_shared) return "Cannot map settings input transport";
    std::memset(shared,0,sizeof(*shared));std::memset(input_shared,0,sizeof(*input_shared));
    std::memcpy(input.magic,"GTAMIN01",8);input.version=1;input.byte_size=sizeof(input);
    input.pid=GetCurrentProcessId();input.created=snapshot->process_creation_100ns;
    if(!install(UpdatePadsCall,UpdatePadsHook,0xE8,UpdatePadsBytes,sizeof(UpdatePadsBytes)))
        return "Cannot install settings input scope";
    for(auto& value:held) value=true;
    enabled=true;Publish(GetTickCount64());return nullptr;
}
void ProcessInputFrame(void(*game)()) {
    if(in_input_frame) { game();return; }
    in_input_frame=true;
    __try { game(); }
    __finally {
        input_frame.End(input_stats);in_input_frame=false;
        if(input_stats.conflicts) { Change(false);enabled=false;release_latch.Reset(); }
    }
}
const InputStats& GetInputStats() noexcept { return input_stats; }
void Poll(const Snapshot& live) noexcept {
    __try {
        if(!enabled) return;
        const auto now=GetTickCount64();
        const bool fresh=Select(now),foreground=(live.status_flags&Foreground)!=0;
        if(!fresh || !foreground || !(live.status_flags&PedPresent)) Change(false);
        if(fresh && heartbeat.close && heartbeat.generation==input.generation) Change(false);
        const bool toggle=(GetAsyncKeyState(VK_F8)&0x8000)!=0;
        if(foreground && toggle && !held[VK_F8] && fresh && (active || ControlEligible(live))) {
            Change(!active);
            if(active) for(unsigned key=8;key<256;++key) {
                held[key]=(GetAsyncKeyState(key)&0x8000)!=0;repeats[key]=now+400;
            }
        }
        held[VK_F8]=toggle;
        if(!active) { last_input_ms=now;Publish(now);return; }
        const auto layout=GetKeyboardLayout(0);
        for(unsigned key=8;key<256;++key) {
            if(key==VK_F8) continue;
            const bool down=(GetAsyncKeyState(key)&0x8000)!=0;
            const bool edge=down && !held[key];
            const bool repeat=down && active && (key==VK_UP || key==VK_DOWN || key==VK_PRIOR || key==VK_NEXT) && now>=repeats[key];
            if(foreground && (edge || repeat) &&
                    key!=VK_SHIFT && key!=VK_CONTROL && key!=VK_MENU && key!=VK_LMENU && key!=VK_RMENU) {
                const auto scan=MapVirtualKeyExW(key,MAPVK_VK_TO_VSC_EX,layout);
                input.event[input.event_count%Events]=key|(scan<<8);++input.event_count;
            }
            if(edge) repeats[key]=now+400;
            else if(repeat) repeats[key]=now+80;
            held[key]=down;
        }
        last_input_ms=now;Publish(now);
    } __except(EXCEPTION_EXECUTE_HANDLER) { active=false;enabled=false; }
}
bool Open() noexcept { return enabled && active && GetTickCount64()-last_input_ms<=FreshnessMs; }
void Present() noexcept {
    __try {
        if(!enabled || (!Open() && !raster)) return;
        std::uintptr_t engine,device;
        if(!Engine(engine,device)) { active=false;enabled=false;return; }
        if(reinterpret_cast<IDirect3DDevice9*>(device)->TestCooperativeLevel()!=D3D_OK) { reset_pending=true;return; }
        if(!Open()) {
            if(!hud_preview::DrawScoped(set_state,get_state,Release,nullptr,state_restores)) enabled=false;
            return;
        }
        DrawContext context{engine,device,false};
        if(!hud_preview::DrawScoped(set_state,get_state,Draw,&context,state_restores) || !context.ok) {
            active=false;enabled=false;
        }
    } __except(EXCEPTION_EXECUTE_HANDLER) { active=false;enabled=false; }
}
}
