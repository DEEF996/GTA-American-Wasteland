#pragma once
#include <windows.h>
#include "pose_preview.h"
#include "pose_preview_protocol.h"

namespace gta_thaw::pose_preview {
inline void DrawScoped(void* destination, const void* before, const void* after,
                       void* context, void(*draw)(void*), Stats& counters, bool& active) {
    active = true;
    // No owning C++ objects: the complete RW block also restores on structured exceptions.
    __try {
        std::memcpy(destination, after, BoneCount*64);
        ++counters.applied;
        draw(context);
    } __finally {
        std::memcpy(destination, before, BoneCount*64);
        ++counters.restored;
        active = false;
    }
}
}
