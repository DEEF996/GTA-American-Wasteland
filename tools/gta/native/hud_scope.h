#pragma once
#include "board_scope.h"

namespace gta_thaw::hud_preview {
// Im2D additionally disables fog. Preserve it and the shared texture/alpha state
// even if the original drawing routine raises SEH. Never draw after a failed get.
inline bool DrawScoped(board_preview::StateFn set,board_preview::StateFn get,
                       void(*action)(void*),void* context,std::uint64_t& restored) {
    board_preview::SavedState common{};
    std::uintptr_t fog=0;
    if(!board_preview::SaveState(get,common) || !get(14,&fog)) return false;
    bool ok=false;
    __try { action(context); }
    __finally {
        const bool fog_ok=set(14,reinterpret_cast<void*>(fog))!=0;
        ok=board_preview::RestoreState(set,common) && fog_ok;
        if(ok) ++restored;
    }
    return ok;
}
}
