#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <bcrypt.h>
#include "board_preview.h"
#include "board_bundle.h"
#include "board_scope.h"
#include "rw_raster.h"

namespace gta_thaw::board_preview {
namespace {
std::wstring bundle_path;
Stats stats{};
alignas(16) unsigned char storage[MaxBytes];
unsigned char loaded_hash[32]{};
float loaded_bind[32][16]{};
float last_palette[4][16]{};
bool skin_valid = false;
Vertex skinned[558]{};
void* rasters[5]{};
std::uintptr_t resource_engine = 0;
StateFn set_state = nullptr, get_state = nullptr;
void Fault(std::uint32_t code) noexcept { ++stats.faults; stats.last_error = code; }

template<class T> T Read(std::uintptr_t address) noexcept {
    T value; std::memcpy(&value, reinterpret_cast<void*>(address), sizeof(value)); return value;
}
template<std::size_t N> bool Guard(std::uintptr_t address, const char(&expected)[N]) noexcept {
    return !std::memcmp(reinterpret_cast<void*>(address), expected, N-1);
}

bool Guards() noexcept {
    return Guard(0x7EF450, "\x8b\x44\x24\x08\x83\xec\x08\x3d\x00\x00\x01\x00") &&
        Guard(0x7EF550, "\xa1\x78\xc0\xc9\x00\x8b\x0d\x24\x7b\xc9\x00\x83\xec\x08") &&
        Guard(0x7EF520, "\x8b\x15\x78\xc0\xc9\x00\x56\x8b\x35\x24\x7b\xc9\x00") &&
        Guard(0x7EFDD0, "\xa1\x24\x7b\xc9\x00\x8b\x0d\x78\xc0\xc9\x00\x8d\x44\x01\x38\xc3") &&
        rw_raster::Guards() &&
        Guard(0x7FE420, "\x8b\x4c\x24\x04\x53\x33\xdb\x33\xc0\x49\x57\x83\xf9\x1d\x0f\x87") &&
        Guard(0x7FD810, "\x8b\x44\x24\x04\x48\x83\xf8\x1d\x77\x73\xff\x24\x85\x18\xda\x7f");
}

bool EngineReady(std::uintptr_t& engine) noexcept {
    engine = Read<std::uintptr_t>(0xC97B24);
    if (!pose_preview::Address(static_cast<std::uint32_t>(engine), 64) || !Read<std::uintptr_t>(engine) ||
        Read<std::uintptr_t>(engine+32) != 0x7FE420 || Read<std::uintptr_t>(engine+36) != 0x7FD810 || !Guards()) return false;
    set_state = reinterpret_cast<StateFn>(0x7FE420);
    get_state = reinterpret_cast<StateFn>(0x7FD810);
    return true;
}

bool Hash(const void* data, std::uint32_t size, unsigned char* output) noexcept {
    BCRYPT_ALG_HANDLE algorithm = nullptr;
    BCRYPT_HASH_HANDLE hash = nullptr;
    bool ok = BCryptOpenAlgorithmProvider(&algorithm, BCRYPT_SHA256_ALGORITHM, nullptr, 0) >= 0;
    if (ok) ok = BCryptCreateHash(algorithm, &hash, nullptr, 0, nullptr, 0, 0) >= 0;
    if (ok) ok = BCryptHashData(hash, static_cast<PUCHAR>(const_cast<void*>(data)), size, 0) >= 0;
    if (ok) ok = BCryptFinishHash(hash, output, 32, 0) >= 0;
    if (hash) BCryptDestroyHash(hash);
    if (algorithm) BCryptCloseAlgorithmProvider(algorithm, 0);
    return ok;
}

bool Load(const pose_preview::Packet& p) noexcept {
    HANDLE file = CreateFileW(bundle_path.c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING,
                             FILE_ATTRIBUTE_NORMAL | FILE_FLAG_SEQUENTIAL_SCAN, nullptr);
    if (file == INVALID_HANDLE_VALUE) return false;
    LARGE_INTEGER size{};
    DWORD count = 0;
    bool ok = GetFileSizeEx(file, &size) && size.QuadPart >= sizeof(Prefix) && size.QuadPart <= MaxBytes;
    if (ok) ok = ReadFile(file, storage, size.LowPart, &count, nullptr) && count == size.LowPart;
    CloseHandle(file);
    unsigned char digest[32];
    if (!ok || !ValidBundle(storage, count) || !Hash(storage, count, digest) ||
        std::memcmp(digest, p.board_hash, 32) || !Hash(p.bind_inverse, sizeof(p.bind_inverse), digest) ||
        std::memcmp(digest, reinterpret_cast<Prefix*>(storage)->header.bind_hash, 32)) return false;
    std::memcpy(loaded_hash, p.board_hash, 32);
    std::memcpy(loaded_bind,p.bind_inverse,sizeof(loaded_bind));
    ++stats.loads;
    return true;
}

void Release(void*) {
    for (auto& raster : rasters) if (raster) {
        auto saved = raster;
        raster = nullptr;
        if (!rw_raster::Destroy(saved)) Fault(7);
        ++stats.releases;
    }
    std::memset(loaded_hash, 0, 32);
    skin_valid = false;
    resource_engine = 0;
}

bool Upload(unsigned index) {
    const auto& t = reinterpret_cast<Prefix*>(storage)->texture[index];
    const auto result=rw_raster::UploadRGBA(t.width,t.height,storage+t.offset,rasters[index]);
    if(result.uploaded) ++stats.uploads;
    if(!result.image_destroyed) Fault(7);
    return result.uploaded && result.image_destroyed;
}

bool Set(int state, std::uintptr_t value) { return set_state(state, reinterpret_cast<void*>(value)) != 0; }
struct Batch { Prefix* p; Mesh* mesh; void* matrix; };
bool TransformAndDraw(void* opaque) {
    auto& b = *static_cast<Batch*>(opaque);
    if (!reinterpret_cast<void*(__cdecl*)(Vertex*,std::uint32_t,void*,std::uint32_t)>(0x7EF450)
            (skinned+b.mesh->first_vertex, b.mesh->vertices, b.matrix, 1|8|16)) return false;
    return reinterpret_cast<int(__cdecl*)(int,std::uint16_t*,int)>(0x7EF550)
            (3,b.p->index+b.mesh->first_index,b.mesh->indices) != 0;
}
bool End() { return reinterpret_cast<int(__cdecl*)()>(0x7EF520)() != 0; }
struct DrawContext { const pose_preview::Packet* packet; const float* anchor; std::uintptr_t engine; bool ok, rejected,holster; };
void DrawBatches(void* opaque) {
    auto& context = *static_cast<DrawContext*>(opaque);
    stats.last_error = 2;
    if (std::memcmp(context.packet->board_hash, loaded_hash, 32)) {
        Release(nullptr);
        if (!Load(*context.packet)) return;
        resource_engine = context.engine;
        for (unsigned i = 0; i < 5; ++i) if (!Upload(i)) { stats.last_error = 3; Release(nullptr); return; }
    }
    auto& p = *reinterpret_cast<Prefix*>(storage);
    if (std::memcmp(context.packet->bind_inverse,loaded_bind,sizeof(loaded_bind))) return;
    SkinResult skin=SkinResult::Updated;
    if(context.holster) {
        // The unanimated original mesh is rigid on the back. Its riding/held
        // source palette must not make it jump between hands while aiming.
        float rest[4][16]{};for(auto& m:rest)m[0]=m[5]=m[10]=m[15]=1.f;
        skin_valid=false;
        for(unsigned i=0;i<558;++i)if(!SkinVertex(p.vertex[i],rest,p.header.conversion,skinned[i]))skin=SkinResult::Rejected;
        if(skin!=SkinResult::Rejected&&!CenterHolster(skinned,558))skin=SkinResult::Rejected;
    }else skin=PrepareSkin(p,context.packet->board_palette,last_palette,skin_valid,skinned);
    if (skin == SkinResult::Rejected) {
        ++stats.rejected;
        stats.last_error = 9;
        context.rejected = true;
        return;
    }
    if (skin == SkinResult::Updated) ++stats.skin_updates;
    stats.last_error = 4;
    alignas(16) unsigned char matrix[64]{};
    for (unsigned r = 0; r < 4; ++r) std::memcpy(matrix+r*16, context.anchor+r*4, 12);
    reinterpret_cast<void*(__cdecl*)(void*)>(0x7F18A0)(matrix);
    if (!Set(6,1) || !Set(7,2) || !Set(8,1) || !Set(9,2) || !Set(10,5) || !Set(11,6) ||
        !Set(12,1) || !Set(20,2) || !Set(29,5)) return;
    for (auto& mesh : p.mesh) {
        if (!Set(1,reinterpret_cast<std::uintptr_t>(rasters[mesh.texture])) || !Set(3,mesh.address_u) ||
            !Set(4,mesh.address_v) || !Set(30,mesh.cutoff)) return;
        stats.last_error = 5;
        auto pool = reinterpret_cast<void*(__cdecl*)()>(0x7EFDD0)();
        if (!pose_preview::Address(reinterpret_cast<std::uintptr_t>(pool),60)) return;
        Batch batch{&p,&mesh,matrix};
        if (!DrawImmediateScoped(pool,TransformAndDraw,&batch,End,stats.pool_restores)) return;
    }
    ++stats.draws;
    stats.last_error = 0;
    context.ok = true;
}
}

void Initialize(const std::wstring& directory) { bundle_path = directory+L"\\board-preview.bin"; }

void Idle() noexcept {
    if (!resource_engine) return;
    __try {
        std::uintptr_t engine;
        // Never call an old RW device after shutdown/reinitialization; owned process exit releases it.
        if (!EngineReady(engine) || engine != resource_engine) { Fault(7); return; }
        if (!DrawStateScoped(set_state,get_state,Release,nullptr,stats.state_restores)) Fault(4);
    } __except(EXCEPTION_EXECUTE_HANDLER) { Fault(6); }
}

void Draw(const pose_preview::Packet& packet, const float* anchor,const float* holster) noexcept {
    if (!(packet.preview_flags & 1) || (!packet.board_visible&&!holster) || stats.faults) { Idle(); return; }
    __try {
        std::uintptr_t engine;
        if (!EngineReady(engine) || (resource_engine && resource_engine != engine)) { Fault(1); return; }
        const bool held_holster=holster&&!packet.board_object_id;
        DrawContext context{&packet,packet.board_object_id ? packet.board_world : (held_holster?holster:anchor),engine,false,false,held_holster};
        if (!DrawStateScoped(set_state,get_state,DrawBatches,&context,stats.state_restores)) Fault(4);
        else if (!context.ok && !context.rejected) Fault(stats.last_error);
    } __except(EXCEPTION_EXECUTE_HANDLER) { Fault(6); }
    if (stats.faults) Idle();
}

Stats GetStats() noexcept { return stats; }
}
