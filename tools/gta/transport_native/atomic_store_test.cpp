#include "atomic_store.cpp"
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>

// Optional reader for a separate-process stress test of the actual wire layout.
int Reader(const char* name, unsigned bytes) {
    constexpr char prefix[]="Local\\THAW_GTA_ATOMIC_TEST_";
    if (std::strncmp(name,prefix,sizeof(prefix)-1) || !bytes ||
        bytes>73860 || bytes%4) return 2;
    HANDLE mapping=OpenFileMappingA(FILE_MAP_ALL_ACCESS,FALSE,name);
    if (!mapping) return 3;
    auto memory=static_cast<unsigned char*>(MapViewOfFile(mapping,FILE_MAP_ALL_ACCESS,0,0,bytes+16));
    if (!memory) { CloseHandle(mapping); return 4; }
    auto begin=reinterpret_cast<volatile LONG*>(memory);
    auto end=reinterpret_cast<volatile LONG*>(memory+4+bytes);
    auto ready=reinterpret_cast<volatile LONG*>(memory+8+bytes);
    auto done=reinterpret_cast<volatile LONG*>(memory+12+bytes);
    std::vector<std::uint32_t> body(bytes/4);
    unsigned accepted=0, changed=0, rejected=0, torn=0;
    LONG previous=0;
    InterlockedExchange(ready,1);
    const ULONGLONG deadline=GetTickCount64()+10000;
    while (!InterlockedCompareExchange(done,0,0) && GetTickCount64()<deadline) {
        const LONG first=InterlockedCompareExchange(begin,0,0);
        if (!first || (first&1)) { ++rejected; continue; }
        std::memcpy(body.data(),memory+4,bytes);
        MemoryBarrier();
        const LONG last=InterlockedCompareExchange(end,0,0);
        const LONG after=InterlockedCompareExchange(begin,0,0);
        if (first!=last || first!=after || (after&1)) { ++rejected; continue; }
        ++accepted;
        if (first!=previous) { ++changed; previous=first; }
        for (auto value:body) if (value!=static_cast<std::uint32_t>(first/2)) { ++torn; break; }
    }
    const bool completed=InterlockedCompareExchange(done,0,0)!=0;
    UnmapViewOfFile(memory); CloseHandle(mapping);
    std::printf("{\"accepted\":%u,\"distinct\":%u,\"rejected\":%u,\"torn\":%u,\"completed\":%s}\n",
                accepted,changed,rejected,torn,completed?"true":"false");
    return completed && accepted && changed>1 && !torn ? 0 : 5;
}

int main(int argc,char** argv) {
    static_assert(sizeof(void*)==8 && sizeof(LONG)==4,"The Python transport is AMD64/LONG32");
    if (argc==4 && !std::strcmp(argv[1],"--reader"))
        return Reader(argv[2],static_cast<unsigned>(std::strtoul(argv[3],nullptr,10)));
    assert(argc==1);
    alignas(4) LONG words[3]={0x12345678,0,0x76543210};
    for (std::uint32_t value:{0u,1u,0x7ffffffcu,0x80000000u,0xffffffffu}) {
        assert(thaw_atomic_store32(&words[1],value)==1);
        assert(static_cast<std::uint32_t>(words[1])==value);
        assert(words[0]==0x12345678 && words[2]==0x76543210);
    }
    const LONG before=words[1];
    assert(thaw_atomic_store32(nullptr,123)==0);
    assert(thaw_atomic_store32(reinterpret_cast<LONG*>(reinterpret_cast<unsigned char*>(&words[1])+1),123)==0);
    assert(words[1]==before && words[0]==0x12345678 && words[2]==0x76543210);
    std::puts("atomic_store x64 tests PASS");
}
