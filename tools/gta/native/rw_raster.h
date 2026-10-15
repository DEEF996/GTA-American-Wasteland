#pragma once
#include <windows.h>
#include <cstdint>
#include <cstring>

// Existing board upload path, also used by the original HUD atlas presentation.
// Caller pins the engine and wraps upload/release in its RW state restoration.
namespace gta_thaw::rw_raster {
template<std::size_t N> inline bool Guard(std::uintptr_t address,const char(&expected)[N]) noexcept {
    return !std::memcmp(reinterpret_cast<void*>(address),expected,N-1);
}
inline bool Guards() noexcept {
    return Guard(0x8026E0,"\xa1\x24\x7b\xc9\x00\x8b\x0d\x5c\xa6\xc9\x00\x56") &&
        Guard(0x8027A0,"\x83\xec\x08\x53\x55\x56\x8b\x74\x24\x18\x57\x8b\x4e\x0c") &&
        Guard(0x802740,"\x56\x8b\x74\x24\x08\xf6\x06\x01\x74\x21\x8b\x46\x14") &&
        Guard(0x8042C0,"\x8b\x44\x24\x08\x8b\x15\x24\x7b\xc9\x00\x83\xec\x34") &&
        Guard(0x7FB230,"\xa1\x24\x7b\xc9\x00\x8b\x0d\xd8\x80\xc9\x00\x56") &&
        Guard(0x804290,"\xa1\x24\x7b\xc9\x00\x56\x8b\x74\x24\x08\x57\x8b\x7c\x24\x10") &&
        Guard(0x7FB020,"\x56\x8b\x74\x24\x08\x56\x68\x18\x25\x8e\x00\xe8\x10\xd7\x00\x00");
}
struct Image { std::int32_t flags,width,height,depth,stride; unsigned char* pixels; void* palette; };
static_assert(sizeof(Image)==28);
struct Result { bool uploaded,image_destroyed; };
inline int chosen_depth=0;
inline bool ValidTextureFormat(int width,int height,int depth,int flags,int expected_width,int expected_height) noexcept {
    return width==expected_width && height==expected_height && (depth==16 || depth==32) && (flags&7)==4;
}
inline Result UploadRGBA(std::uint32_t width,std::uint32_t height,const unsigned char* pixels,void*& raster) {
    Result result{false,true};
    auto image=reinterpret_cast<Image*(__cdecl*)(int,int,int)>(0x8026E0)(width,height,32);
    if(!image) return result;
    __try {
        if(!reinterpret_cast<Image*(__cdecl*)(Image*)>(0x8027A0)(image) || !image->pixels ||
           image->width!=static_cast<int>(width) || image->height!=static_cast<int>(height) ||
           image->depth!=32 || image->stride<static_cast<int>(width*4) || image->stride>4096) __leave;
        for(std::uint32_t row=0;row<height;++row)
            std::memcpy(image->pixels+row*image->stride,pixels+row*width*4,width*4);
        int out_width=0,out_height=0,depth=0,flags=0;
        const auto format=reinterpret_cast<Image*(__cdecl*)(Image*,int,int*,int*,int*,int*)>(0x8042C0)
            (image,4,&out_width,&out_height,&depth,&flags);
        chosen_depth=depth;
        // The original converter supports the device-selected16-bit texture
        // format too; the source image remains32-bit RGBA in either case.
        if(!format || !ValidTextureFormat(out_width,out_height,depth,flags,image->width,image->height)) __leave;
        // Publish ownership before fill: the caller must retain the allocation
        // even if the original raster fill raises SEH rather than returning.
        raster=reinterpret_cast<void*(__cdecl*)(int,int,int,int)>(0x7FB230)(out_width,out_height,depth,flags);
        if(!raster || !reinterpret_cast<void*(__cdecl*)(void*,Image*)>(0x804290)(raster,image)) __leave;
        result.uploaded=true;
    } __finally {
        result.image_destroyed=reinterpret_cast<int(__cdecl*)(Image*)>(0x802740)(image)!=0;
    }
    return result;
}
inline bool Destroy(void* raster) { return reinterpret_cast<int(__cdecl*)(void*)>(0x7FB020)(raster)!=0; }
}
