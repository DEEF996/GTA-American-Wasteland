#include <cassert>
#include <cstdio>
#include <limits>
#include "board_preview.h"
#include "board_scope.h"
#include "rw_raster.h"

using namespace gta_thaw::board_preview;
alignas(16) unsigned char bytes[sizeof(Prefix)+20];
std::uintptr_t render_state[31];
int reads = 0, writes = 0, draws = 0, fail_get = 0;
unsigned char pool[60];
int ends = 0;

int __cdecl Get(int state, void* output) {
    ++reads;
    if (state == fail_get) return false;
    *static_cast<std::uintptr_t*>(output) = render_state[state];
    return true;
}
int __cdecl Set(int state, void* value) {
    ++writes;
    render_state[state] = reinterpret_cast<std::uintptr_t>(value);
    return true;
}
void Draw(void*) { ++draws; for (int state : States) Set(state,reinterpret_cast<void*>(123)); }
void Fault(void* context) { Draw(context); RaiseException(0xE0434242,0,0,nullptr); }
bool Transform(void*) { std::memset(pool,0xAB,60); return true; }
bool TransformFalse(void* context) { Transform(context); return false; }
bool TransformFault(void* context) { Transform(context); RaiseException(0xE0434242,0,0,nullptr); return false; }
bool End() { ++ends; std::memset(pool,0,60); return true; }

void ScopeTest() {
    for (int i = 0; i < 31; ++i) render_state[i] = i+500;
    std::uint64_t restored = 0;
    assert(DrawStateScoped(Set,Get,Draw,nullptr,restored) && restored == 1 && draws == 1);
    for (int i = 0; i < 31; ++i) assert(render_state[i] == static_cast<unsigned>(i+500));
    bool caught = false;
    __try { DrawStateScoped(Set,Get,Fault,nullptr,restored); }
    __except(GetExceptionCode() == 0xE0434242 ? EXCEPTION_EXECUTE_HANDLER : EXCEPTION_CONTINUE_SEARCH) { caught = true; }
    assert(caught && restored == 2 && draws == 2);
    for (int i = 0; i < 31; ++i) assert(render_state[i] == static_cast<unsigned>(i+500));
    fail_get = 30;
    const int old_writes = writes;
    assert(!DrawStateScoped(Set,Get,Draw,nullptr,restored));
    assert(draws == 2 && writes == old_writes && restored == 2);

    std::memset(pool+8,0xCD,52); // Inactive pool's complete previous stash must survive as well.
    unsigned char saved[60]; std::memcpy(saved,pool,60);
    restored = 0;
    assert(DrawImmediateScoped(pool,Transform,nullptr,End,restored));
    assert(restored == 1 && ends == 1 && !std::memcmp(saved,pool,60));
    assert(!DrawImmediateScoped(pool,TransformFalse,nullptr,End,restored));
    assert(restored == 2 && ends == 2 && !std::memcmp(saved,pool,60));
    caught = false;
    __try { DrawImmediateScoped(pool,TransformFault,nullptr,End,restored); }
    __except(GetExceptionCode() == 0xE0434242 ? EXCEPTION_EXECUTE_HANDLER : EXCEPTION_CONTINUE_SEARCH) { caught = true; }
    assert(caught && restored == 3 && ends == 3 && !std::memcmp(saved,pool,60));
    pool[4] = 1;
    assert(!DrawImmediateScoped(pool,Transform,nullptr,End,restored));
    assert(restored == 3 && ends == 3 && pool[4] == 1);
}

void SkinTest() {
    float palette[4][16]{};
    for (auto& matrix : palette) for (int i = 0; i < 16; ++i) matrix[i] = i%5 == 0 ? 1.f : 0.f;
    palette[0][12] = 10; palette[1][13] = 20;
    const float conversion[16] = {0,.5f,0,0, -.5f,0,0,0, 0,0,.5f,0, 6,5.5f,5.5f,1};
    SourceVertex source{};
    source.vertex = {{1,2,3},{1,0,0},0x80808080,{.125f,.875f}};
    source.bone[0] = 48; source.bone[1] = 49;
    source.weight[0] = .25f; source.weight[1] = .75f;
    Vertex output{};
    assert(SkinVertex(source,palette,conversion,output));
    assert(output.position[0] == -2.5f && output.position[1] == 7.25f && output.position[2] == 7.f);
    assert(output.normal[0] == 0 && output.normal[1] == 1 && output.normal[2] == 0);
    assert(output.color == 0xffffffff && output.uv[0] == .125f && output.uv[1] == .875f);
    const auto before = source;
    palette[0][12] = 18;
    assert(SkinVertex(source,palette,conversion,output) && output.position[1] == 8.25f);
    assert(!std::memcmp(&before,&source,sizeof(source)));
    palette[0][12] = std::numeric_limits<float>::quiet_NaN();
    assert(!SkinVertex(source,palette,conversion,output));
}

void SkinCacheTest() {
    Prefix bundle{};
    for (int i = 0; i < 16; ++i) bundle.header.conversion[i] = i%5 == 0 ? 1.f : 0.f;
    for (auto& vertex : bundle.vertex) {
        vertex.vertex.position[0] = 1;
        vertex.vertex.normal[2] = 1;
        vertex.bone[0] = 48;
        vertex.weight[0] = 1;
    }
    float palette[4][16]{}, cached[4][16]{};
    for (auto& matrix : palette) for (int i = 0; i < 16; ++i) matrix[i] = i%5 == 0 ? 1.f : 0.f;
    Vertex output[558]{};
    bool valid = false;
    assert(PrepareSkin(bundle,palette,cached,valid,output) == SkinResult::Updated && valid);
    assert(output[0].position[0] == 1);
    assert(PrepareSkin(bundle,palette,cached,valid,output) == SkinResult::Cached);
    // A restarted/same transport sequence with new original palette data must re-skin.
    palette[0][12] = 2;
    assert(PrepareSkin(bundle,palette,cached,valid,output) == SkinResult::Updated);
    assert(output[0].position[0] == 3);
    const float accepted = cached[0][12];
    // Reject after earlier vertices were already updated; no partially built cache is accepted.
    bundle.vertex[557].vertex.position[0] = 11;
    palette[0][12] = 3;
    assert(PrepareSkin(bundle,palette,cached,valid,output) == SkinResult::Rejected && !valid);
    assert(cached[0][12] == accepted);
    bundle.vertex[557].vertex.position[0] = 1;
    palette[0][12] = accepted;
    assert(PrepareSkin(bundle,palette,cached,valid,output) == SkinResult::Updated && valid);
    assert(output[0].position[0] == 3 && output[557].position[0] == 3);
    assert(PrepareSkin(bundle,palette,cached,valid,output) == SkinResult::Cached);
}

int main() {
    Vertex holster[8]{};
    for(int i=0;i<8;++i) { holster[i].position[0]=(i&1)?.5f:-.5f;holster[i].position[1]=(i&2)?.12f:-.12f;holster[i].position[2]=(i&4)?.025f:-.025f;holster[i].normal[2]=1; }
    assert(CenterHolster(holster,8));
    for(const auto& v:holster) { assert(std::fabs(v.position[2])==.5f);assert(std::fabs(v.position[0])==.12f);assert(std::fabs(v.position[1])==.025f);assert(v.normal[1]==-1); }
    assert(!CenterHolster(holster,0));
    assert(gta_thaw::rw_raster::ValidTextureFormat(64,64,16,0x504,64,64));
    assert(gta_thaw::rw_raster::ValidTextureFormat(64,64,32,0x604,64,64));
    assert(!gta_thaw::rw_raster::ValidTextureFormat(64,64,8,4,64,64));
    assert(!gta_thaw::rw_raster::ValidTextureFormat(128,64,32,4,64,64));
    assert(!gta_thaw::rw_raster::ValidTextureFormat(64,64,32,1,64,64));
    auto& p = *reinterpret_cast<Prefix*>(bytes);
    std::memcpy(p.header.magic,"GTABRD02",8);
    p.header.version = 2; p.header.byte_size = sizeof(bytes);
    p.header.mesh_count = p.header.texture_count = 5;
    p.header.vertex_count = 558; p.header.index_count = 1656;
    p.header.bind_hash[0] = p.header.rig_hash[0] = 1;
    const unsigned char model_hash[] = {0xc2,0x96,0x2d,0x8d,0xd7,0x8b,0xf3,0x5c,0x8b,0xad,0xc4,0x06,0x27,0x73,0xb3,0xc9,
        0xc9,0xaf,0x2f,0x05,0x99,0x30,0x40,0x0c,0x28,0x53,0x10,0x7f,0xe1,0xa6,0xc2,0xdc};
    std::memcpy(p.header.source_model_hash,model_hash,32);
    for (int i = 0; i < 16; ++i) p.header.conversion[i] = i%5 == 0 ? 1.f : 0.f;
    unsigned vertices = 0, indices = 0;
    for (unsigned i = 0; i < 5; ++i) {
        const unsigned count = i == 4 ? 110 : 112, icount = i == 4 ? 336 : 330;
        p.mesh[i] = {vertices,count,indices,icount,i,1,3,i%2};
        vertices += count; indices += icount;
        p.texture[i] = {1,1,static_cast<unsigned>(sizeof(Prefix))+i*4,4};
    }
    for (auto& v : p.vertex) { v.vertex.normal[2] = 1; v.vertex.color = 0x80808080; v.bone[0] = 48; v.weight[0] = 1; }
    assert(ValidBundle(bytes,sizeof(bytes)));
    assert(!ValidBundle(bytes,sizeof(bytes)-1));
    p.mesh[0].vertices = 0xffffffff; assert(!ValidBundle(bytes,sizeof(bytes))); p.mesh[0].vertices = 112;
    p.index[0] = 112; assert(!ValidBundle(bytes,sizeof(bytes))); p.index[0] = 0;
    p.texture[0].offset = 0xffffffff; assert(!ValidBundle(bytes,sizeof(bytes))); p.texture[0].offset = sizeof(Prefix);
    p.vertex[0].vertex.position[0] = std::numeric_limits<float>::quiet_NaN(); assert(!ValidBundle(bytes,sizeof(bytes)));
    p.vertex[0].vertex.position[0] = 0;
    p.vertex[0].vertex.normal[2] = 2; assert(!ValidBundle(bytes,sizeof(bytes))); p.vertex[0].vertex.normal[2] = 1;
    p.vertex[0].weight[0] = .9f; assert(!ValidBundle(bytes,sizeof(bytes))); p.vertex[0].weight[0] = 1;
    p.vertex[0].bone[0] = 47; assert(!ValidBundle(bytes,sizeof(bytes))); p.vertex[0].bone[0] = 48;
    p.header.conversion[0] = 2; assert(!ValidBundle(bytes,sizeof(bytes))); p.header.conversion[0] = 1;
    assert(ValidBundle(bytes,sizeof(bytes)));
    ScopeTest();
    SkinTest();
    SkinCacheTest();
    std::puts("Board preview: bounded topology/textures, normal/SEH RW state restore and fail-before-mutation passed.");
}
