#pragma once
#include <cstddef>
#include <cstdint>
#include <cstring>

namespace gta_thaw::settings_menu {
// CPad::UpdatePads copies 0x9c dwords per keyboard snapshot. Only its processed
// snapshots are scoped; TempKeyState and the OS input producer stay untouched.
constexpr std::size_t KeyboardBytes=0x270, ProcessedPadBytes=96;
struct InputStats { std::uint64_t sets=0,restores=0,conflicts=0; };
struct InputFrame {
    unsigned char* pad=nullptr;
    unsigned char* current=nullptr;
    unsigned char* previous=nullptr;
    unsigned char saved_pad[ProcessedPadBytes]{},saved_current[KeyboardBytes]{},saved_previous[KeyboardBytes]{};
    bool active=false;
    void Begin(unsigned char* p,unsigned char* c,unsigned char* o,InputStats& stats) noexcept {
        if(active) return;
        pad=p;current=c;previous=o;
        std::memcpy(saved_pad,p,sizeof(saved_pad));
        std::memcpy(saved_current,c,sizeof(saved_current));
        std::memcpy(saved_previous,o,sizeof(saved_previous));
        active=true;
        std::memset(p,0,sizeof(saved_pad));std::memset(c,0,sizeof(saved_current));std::memset(o,0,sizeof(saved_previous));
        ++stats.sets;
    }
    static bool RestoreRegion(unsigned char* destination,const unsigned char* saved,std::size_t size) noexcept {
        for(std::size_t i=0;i<size;++i) if(destination[i]) return false;
        std::memcpy(destination,saved,size);return true;
    }
    void End(InputStats& stats) noexcept {
        if(!active) return;
        active=false;
        // Preserve a competing writer's entire snapshot; restore independent
        // regions normally. Never overwrite a newer input state with our backup.
        const bool p=RestoreRegion(pad,saved_pad,sizeof(saved_pad));
        const bool c=RestoreRegion(current,saved_current,sizeof(saved_current));
        const bool o=RestoreRegion(previous,saved_previous,sizeof(saved_previous));
        if(p&&c&&o) ++stats.restores;else ++stats.conflicts;
    }
};
struct InputReleaseLatch {
    bool blocked[256]{};
    bool pending=false;
    void Reset() noexcept { std::memset(blocked,0,sizeof(blocked));pending=false; }
    bool Consume(bool open,const bool (&down)[256]) noexcept {
        if(open) {
            std::memcpy(blocked,down,sizeof(blocked));pending=true;return true;
        }
        const bool consume=pending;
        pending=false;
        for(std::size_t i=0;i<256;++i) { blocked[i]=blocked[i]&&down[i];pending=pending||blocked[i]; }
        // Include the release frame: a menu key-up must not become gameplay input.
        return consume;
    }
};
}
