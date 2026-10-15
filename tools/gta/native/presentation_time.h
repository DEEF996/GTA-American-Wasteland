#pragma once
#include <cstdint>
#include <limits>
#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#endif

namespace gta_thaw::presentation_time {
// Preserve the packet clock's epoch, but measure rendering deltas with QPC.
// The coarse timestamp is sampled once, never used to pace individual frames.
struct CounterEpoch {
    std::uint64_t tick_us=0, counter=0, frequency=0;
    bool Read(std::uint64_t current,std::uint64_t& result) const noexcept {
        if(!frequency || current<counter) return false;
        const auto elapsed=current-counter, seconds=elapsed/frequency;
        constexpr auto maximum=std::numeric_limits<std::uint64_t>::max();
        if(seconds>(maximum-tick_us)/1000000) return false;
        const auto whole=tick_us+seconds*1000000;
        // Dividing before multiplying avoids overflow after long system uptime.
        const auto fraction=static_cast<std::uint64_t>(
            static_cast<double>(elapsed%frequency)/static_cast<double>(frequency)*1000000.);
        if(fraction>maximum-whole) return false;
        result=whole+fraction;return true;
    }
};
#ifdef _WIN32
inline std::uint64_t NowUs() noexcept {
    // A C++ inline function has one shared local static across translation units.
    // Camera, skeleton, board and upper-body blending therefore share this epoch.
    static const CounterEpoch epoch=[] {
        LARGE_INTEGER frequency{},counter{};
        const auto tick=GetTickCount64();
        if(!QueryPerformanceFrequency(&frequency) || frequency.QuadPart<=0 ||
           !QueryPerformanceCounter(&counter) || counter.QuadPart<0) return CounterEpoch{};
        return CounterEpoch{tick*1000,static_cast<std::uint64_t>(counter.QuadPart),
                            static_cast<std::uint64_t>(frequency.QuadPart)};
    }();
    LARGE_INTEGER counter{};std::uint64_t result=0;
    if(QueryPerformanceCounter(&counter) && counter.QuadPart>=0 &&
       epoch.Read(static_cast<std::uint64_t>(counter.QuadPart),result)) return result;
    return GetTickCount64()*1000;
}
#endif
}
