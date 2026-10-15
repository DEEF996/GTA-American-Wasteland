#pragma once
#include "bridge_protocol.h"
#include "settings_menu_input_scope.h"
namespace gta_thaw::settings_menu {
using InstallFn=bool(*)(std::uintptr_t,const void*,unsigned char,const unsigned char*,std::size_t);
const char* Initialize(const Snapshot*,InstallFn);
void ProcessInputFrame(void(*game)());
const InputStats& GetInputStats() noexcept;
void Poll(const Snapshot&) noexcept;
bool Open() noexcept;
void Present() noexcept;
}
