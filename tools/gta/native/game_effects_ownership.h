#pragma once
#include <cstdint>

namespace gta_thaw::game_effects {
enum class Scalar { Time,Pitch };
// Two original scalars, one bounded owner. A failed second CAS rolls back only
// the first value we actually committed; no foreign value is ever overwritten.
class ScaleOwnership {
    bool time_owned_=false,pitch_owned_=false;
    float time_=1,pitch_=.5f;
    std::uint64_t writes_=0,restores_=0,conflicts_=0;
public:
    bool Held() const noexcept { return time_owned_ && pitch_owned_; }
    float Time() const noexcept { return time_; }
    float Pitch() const noexcept { return pitch_; }
    std::uint64_t Writes() const noexcept { return writes_; }
    std::uint64_t Restores() const noexcept { return restores_; }
    std::uint64_t Conflicts() const noexcept { return conflicts_; }
    template<class Memory> bool Matches(Memory& memory) const noexcept {
        return Held() && memory.Read(Scalar::Time)==time_ && memory.Read(Scalar::Pitch)==pitch_;
    }
    template<class Memory> void Release(Memory& memory) noexcept {
        // Normal completion first stops native slow motion, then restores the
        // original (inactive) pitch factor. On foreign changes each CAS is separate.
        if(time_owned_) {
            if(memory.Exchange(Scalar::Time,time_,1.f)) {
                if(time_!=1.f) { ++writes_;++restores_; }
            } else ++conflicts_;
            time_owned_=false;time_=1;
        }
        if(pitch_owned_) {
            if(memory.Exchange(Scalar::Pitch,pitch_,.5f)) { if(pitch_!=.5f) ++writes_; }
            else ++conflicts_;
            pitch_owned_=false;pitch_=.5f;
        }
    }
    template<class Memory> bool Set(float value,Memory& memory) noexcept {
        if(Held()) {
            if(!Matches(memory)) { ++conflicts_;Release(memory);return false; }
        } else if(time_owned_ || pitch_owned_ || memory.Read(Scalar::Time)!=1.f || memory.Read(Scalar::Pitch)!=.5f) {
            Release(memory);return false;
        }
        // Set the factor while Time==1 on first entry: native sound Service
        // cannot hear the old abrupt0.5 factor when Time next becomes<1.
        if(!memory.Exchange(Scalar::Pitch,pitch_,value)) { ++conflicts_;Release(memory);return false; }
        if(pitch_!=value) ++writes_;
        pitch_=value;pitch_owned_=true;
        if(!memory.Exchange(Scalar::Time,time_,value)) { ++conflicts_;Release(memory);return false; }
        if(time_!=value) ++writes_;
        time_=value;time_owned_=true;return true;
    }
};
}
