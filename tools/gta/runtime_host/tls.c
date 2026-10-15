/* Owned metadata only: Windows supplies eight static TLS bytes to every thread.
   There is no DLL entry point, TLS callback, import, or original game code. */
#include <windows.h>
#pragma section(".tls", read, write)
__declspec(allocate(".tls")) unsigned char thaw_tls_template[8] = {0};
__declspec(dllexport) DWORD thaw_tls_index = 0;
const IMAGE_TLS_DIRECTORY32 _tls_used = {
    (DWORD)thaw_tls_template,
    (DWORD)(thaw_tls_template + 8),
    (DWORD)&thaw_tls_index,
    0, 0, 0
};
