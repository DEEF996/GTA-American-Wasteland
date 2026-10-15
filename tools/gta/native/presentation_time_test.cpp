#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <cassert>
#include <cstdio>
#include <vector>
#include "presentation_time.h"
#include "camera_preview_math.h"

using gta_thaw::presentation_time::CounterEpoch;

void TestSlowerSource(double hz,bool quantized) {
    using gta_thaw::camera_preview::PresentationClock;
    PresentationClock clock;
    std::vector<std::uint64_t> arrivals;
    double time=1000000.;
    for(unsigned n=0;n<540;++n) {
        arrivals.push_back(static_cast<std::uint64_t>(std::llround(time)));
        time+=1000000./(n<180?hz:60.);
    }
    unsigned next=0,holds=0,rebases=0;std::uint64_t previous=0,max_lag=0,min_step=100000;
    for(unsigned frame=0;frame<1000;++frame) {
        const auto now=1000000+static_cast<std::uint64_t>(std::llround(frame*1000000./120.));
        while(next<arrivals.size() && arrivals[next]<=now) {
            const auto tick=quantized?(arrivals[next]/15625*15625)/1000:arrivals[next]/1000;
            clock.Advance(std::min(now,tick*1000));
            bool shifted=false;
            clock.Push(static_cast<std::uint64_t>(std::llround(next*1000000./60.)),tick,shifted);
            rebases+=clock.rebased;++next;
        }
        clock.Advance(now);
        if(frame>120 && next<520) {
            const auto step=clock.cursor_us-previous;
            holds+=step==0;min_step=std::min(min_step,step);
            max_lag=std::max(max_lag,clock.samples[clock.count-1].source_us-clock.cursor_us);
            assert(step<=8752); // At most 5% visual catch-up; never beyond a published source sample.
        }
        assert(clock.count<=clock.Capacity && clock.cursor_us<=clock.samples[clock.count-1].source_us);
        previous=clock.cursor_us;
    }
    std::printf("source %.0fHz quantized=%d: holds=%u rebases=%u min_step=%llu max_lag=%llu us\n",
        hz,quantized,holds,rebases,static_cast<unsigned long long>(min_step),static_cast<unsigned long long>(max_lag));
    assert(!holds && !rebases && min_step>3000 && max_lag<50000);
    const auto held=clock.cursor_us;
    clock.Advance(30000000);clock.Advance(40000000);
    assert(clock.cursor_us==clock.samples[clock.count-1].source_us && clock.cursor_us>=held);
    // A discontinuous source resets its bounded history rather than interpolating a teleport.
    bool shifted=false;clock.Push(clock.cursor_us+100001,40000,shifted);
    assert(clock.count==1 && !clock.started && clock.rate==1.);
}

void TestSeparatedRenderHooks() {
    using gta_thaw::camera_preview::PresentationClock;
    PresentationClock a,b;
    unsigned next_a=0,next_b=0;std::uint64_t difference=0;
    auto observe=[](PresentationClock& clock,unsigned& next,std::uint64_t now) {
        auto arrival=[](unsigned n) { return 1000000+static_cast<std::uint64_t>(std::llround(n*1000000./55.)); };
        while(arrival(next)<=now) {
            const auto tick=(arrival(next)/15625*15625)/1000;
            clock.Advance(std::min(now,tick*1000));bool shifted=false;
            clock.Push(static_cast<std::uint64_t>(std::llround(next*1000000./60.)),tick,shifted);++next;
        }
        clock.Advance(now);
    };
    for(unsigned frame=0;frame<720;++frame) {
        const auto now=1000000+static_cast<std::uint64_t>(std::llround(frame*1000000./120.));
        observe(a,next_a,now);observe(b,next_b,now+3000);
        if(next_a==next_b && frame>120) {
            auto projected=a;projected.Advance(now+3000);
            difference=std::max(difference,projected.cursor_us>b.cursor_us?
                projected.cursor_us-b.cursor_us:b.cursor_us-projected.cursor_us);
        }
    }
    std::printf("separate hooks: maximum equal-time source difference=%llu us\n",static_cast<unsigned long long>(difference));
    assert(difference<3000);
}

void TestSlowMotion() {
    using gta_thaw::camera_preview::PresentationClock;
    auto scale=[](double t) {
        double amount=0.;
        if(t>=1. && t<1.2) amount=(t-1.)/.2;
        else if(t>=1.2 && t<5.7) amount=1.;
        else if(t>=5.7 && t<6.) amount=(6.-t)/.3;
        amount=std::clamp(amount,0.,1.);
        return 1.-.7*amount*amount*(3.-2.*amount);
    };
    for(bool quantized:{false,true}) {
        PresentationClock clock;
        unsigned source=0,holds=0,rebases=0;
        double credit=0.;std::uint64_t previous=0,max_step=0;
        for(unsigned frame=0;frame<960;++frame) {
            const double t=frame/120.;
            const auto now=1000000+static_cast<std::uint64_t>(std::llround(t*1000000.));
            clock.SetScale(scale(t));
            if(!(frame%2)) {
                credit+=scale(t)/60.;
                if(credit+1e-9>=1./60.) {
                    credit=std::max(0.,credit-1./60.);
                    const auto tick=quantized?(now/15625*15625)/1000:now/1000;
                    clock.Advance(std::min(now,tick*1000));bool shifted=false;
                    clock.Push(static_cast<std::uint64_t>(std::llround(source++*1000000./60.)),tick,shifted);
                    rebases+=clock.rebased;
                }
            }
            clock.Advance(now);
            assert(clock.count && clock.cursor_us>=previous &&
                   clock.cursor_us<=clock.samples[clock.count-1].source_us);
            const auto step=clock.cursor_us-previous;
            if(t>2. && t<5.5) holds+=step==0;
            if(t>.3) max_step=std::max(max_step,step);
            previous=clock.cursor_us;
        }
        std::printf("slow-motion quantized=%d: holds=%u rebases=%u max_step=%llu us\n",
            quantized,holds,rebases,static_cast<unsigned long long>(max_step));
        assert(!holds && !rebases && max_step<=8752);
    }
}

void TestBoundedRenderGaps() {
    using gta_thaw::camera_preview::PresentationClock;
    // Recorded baseline cursor hashes: the four-endpoint implementation on
    // these exact ordinary publication traces. Retention must not change them.
    constexpr std::uint64_t golden[3][2]={{0xd7f285038c646305ull,0x515bf219455b817bull},
        {0x968334e107735df5ull,0x3b2160d1f011941dull},{0x158d0d2bca78d512ull,0x66bea2883de17d1aull}};
    unsigned index=0;
    for(unsigned hz:{50u,55u,60u}) {
        for(unsigned jitter:{0u,1u}) for(unsigned gap:{0u,58000u,62000u,75000u,90000u,100000u})
        for(unsigned phase=0;phase<=8000;phase+=1000) {
            if(!gap && phase)continue;
            PresentationClock c;
            std::vector<std::uint64_t> arrivals;
            for(unsigned n=0;n<hz*65;++n) {
                const int perturb=jitter?static_cast<int>(n%3)*3000-3000:0;
                arrivals.push_back(1010000+static_cast<std::uint64_t>(std::llround(n*1000000./hz))+perturb);
            }
            unsigned next=0;std::uint64_t previous=0,previous_wall=0,hash=1469598103934665603ull;
            for(unsigned frame=0;frame<7200;++frame) {
                const auto now=1010000+static_cast<std::uint64_t>(std::llround(frame*1000000./120.));
                const auto modulo=(now-1000000)%5000000;
                if(gap && modulo>=3000000+phase && modulo<3000000+phase+gap)continue;
                while(next+1<arrivals.size() && arrivals[next+1]<=now)++next;
                const auto source=(next+1)*1000000ull/60;
                const auto tick=(arrivals[next]/15625*15625)/1000;
                const bool fresh=!c.count || source>c.samples[c.count-1].source_us;
                const bool reset=fresh && c.count && source-c.samples[c.count-1].source_us>100000;
                c.Advance(fresh?std::min(now,tick*1000):now);
                if(fresh) {bool shifted;c.Push(source,tick,shifted);}
                c.Advance(now);
                assert(c.cursor_us<=source && c.cursor_us>=previous && c.count<=c.Capacity);
                if(frame>240) {
                    assert(!c.rebased && c.rebase_us==0);
                    // The existing >100 ms discontinuity starts a new history;
                    // its first two publications deliberately refill the delay.
                    if(c.count>=3)assert(c.cursor_us>previous);
                    // Missing draws themselves lose motion; they must not also
                    // produce a forced cursor jump on later, ordinary draws.
                    if(!reset)assert(c.cursor_us-previous<=static_cast<std::uint64_t>((now-previous_wall)*1.05)+2);
                    assert(source-c.cursor_us<=100000);
                    hash=(hash^c.cursor_us)*1099511628211ull;
                }
                previous=c.cursor_us;previous_wall=now;
            }
            if(!gap)assert(hash==golden[index][jitter]);
        }
        ++index;
    }
    PresentationClock c;bool shifted;
    for(unsigned n=0;n<c.Capacity;++n)c.Push(n*16667,1000+n*17,shifted);
    c.Push(c.Capacity*16667,1000+c.Capacity*17,shifted);
    assert(c.rebased && c.rebase_us==16667 && c.cursor_us==16667);
    c.Advance(2000000);c.Push((c.Capacity+1)*16667,2000,shifted);
    assert(!c.rebased && !c.rebase_us);
    c.rebase_us=1;c.Clear();assert(!c.rebased && !c.rebase_us);
    std::puts("Presentation history: unchanged steady cursors; bounded render gaps have no extra jump; rebase magnitude reset passed.");
}

int main() {
    std::setvbuf(stdout,nullptr,_IONBF,0);
    std::uint64_t result=42;
    const CounterEpoch epoch{48000000000ull,300000000000ull,10000000};
    assert(epoch.Read(epoch.counter,result) && result==epoch.tick_us);
    assert(epoch.Read(epoch.counter+83333,result) && result==epoch.tick_us+8333);
    assert(epoch.Read(epoch.counter+166667,result) && result==epoch.tick_us+16666);
    assert(!epoch.Read(epoch.counter-1,result));
    assert(!CounterEpoch{}.Read(1,result));
    const CounterEpoch unusual{0,0,3};
    assert(unusual.Read(1,result) && result==333333);
    assert(unusual.Read(2,result) && result==666666);
    constexpr auto maximum=std::numeric_limits<std::uint64_t>::max();
    const CounterEpoch long_uptime{1,0,10000000};
    assert(long_uptime.Read(maximum,result) && result==maximum/10+1);
    const CounterEpoch overflow{maximum-500000,0,10000000};
    assert(!overflow.Read(10000000,result));
    assert(!overflow.Read(5000010,result));
    // Cursor addition stays bounded even near the integer limit.
    gta_thaw::camera_preview::PresentationClock bounded;
    bounded.count=1;bounded.started=true;bounded.cursor_us=maximum-10;
    bounded.samples[0].source_us=maximum-5;
    bounded.Advance(100);
    assert(bounded.cursor_us==maximum-5);

    const auto start=gta_thaw::presentation_time::NowUs();
    Sleep(2);
    assert(gta_thaw::presentation_time::NowUs()>start);
    for(const auto hz:{50.,55.,60.})for(const auto quantized:{false,true})TestSlowerSource(hz,quantized);
    TestSeparatedRenderHooks();
    TestSlowMotion();
    TestBoundedRenderGaps();
    std::puts("presentation_time_test PASS: QPC epoch, sub-frame progress, overflow bounds");
}
