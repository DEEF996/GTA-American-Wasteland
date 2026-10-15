#pragma once
#include <cmath>
#include <cstdint>

namespace gta_thaw::game_effects {
constexpr float TargetScale=.3f;
constexpr std::uint64_t EnterUs=200000,ExitUs=300000,MaximumUs=5000000;
enum class Event : std::uint32_t { None=0,Enter=1,Exit=2,Restored=3 };

// Pure real-time controller. No physics delta or native input state is modified.
// The caller commits a returned write with compare/exchange against `expected`.
struct Decision { bool write=false,conflict=false;float expected=1,value=1; };
class Controller {
    bool owned_=false,exiting_=false,shift_seen_=false,shift_down_=false;
    std::uint64_t start_=0,exit_start_=0,last_time_=0,event_sequence_=0;
    float amount_=0,exit_amount_=0,scale_=1;
    Event event_=Event::None;
    static float Smooth(float p) noexcept {
        p=p<0?0:(p>1?1:p);return p*p*(3-2*p);
    }
    void Emit(Event event) noexcept { event_=event;++event_sequence_; }
    void BeginExit(std::uint64_t at) noexcept {
        exiting_=true;exit_start_=at;exit_amount_=amount_;Emit(Event::Exit);
    }
public:
    bool Owned() const noexcept { return owned_; }
    bool Exiting() const noexcept { return exiting_; }
    float Amount() const noexcept { return amount_; }
    float Scale() const noexcept { return scale_; }
    Event LastEvent() const noexcept { return event_; }
    std::uint64_t EventSequence() const noexcept { return event_sequence_; }
    void Abandon() noexcept {
        if(owned_) Emit(Event::Restored);
        owned_=false;exiting_=false;amount_=0;scale_=1;
    }
    Decision Release(float observed) noexcept {
        Decision result{};result.expected=observed;
        if(owned_) {
            result.conflict=observed!=scale_;
            result.write=!result.conflict && observed!=1.f;
            Abandon();
        }
        return result;
    }
    Decision Step(std::uint64_t now,bool lease,bool aiming,bool shift,float observed) noexcept {
        // Never activate from a key already held when the ASI/session first sees it.
        const bool fresh=shift_seen_ && shift && !shift_down_;
        shift_seen_=true;shift_down_=shift;
        const bool backwards=last_time_ && now<last_time_;
        last_time_=now;
        if(!lease || backwards || !std::isfinite(observed)) return Release(observed);
        if(owned_ && observed!=scale_) {
            Abandon();Decision result{};result.conflict=true;return result;
        }
        if(!owned_) {
            // An existing foreign slow-motion/debug/mission scale remains its owner's.
            if(!fresh || !aiming || observed!=1.f) return {};
            owned_=true;exiting_=false;start_=now;amount_=0;scale_=1;Emit(Event::Enter);
        } else if(!exiting_ && (fresh || !aiming)) {
            BeginExit(now);
        }
        const auto elapsed=now-start_;
        if(elapsed>=MaximumUs) return Release(observed);
        if(!exiting_ && elapsed>=MaximumUs-ExitUs) BeginExit(start_+MaximumUs-ExitUs);
        if(exiting_) {
            const auto elapsed_exit=now-exit_start_;
            if(elapsed_exit>=ExitUs) return Release(observed);
            amount_=exit_amount_*(1-Smooth(static_cast<float>(elapsed_exit)/static_cast<float>(ExitUs)));
        } else amount_=Smooth(static_cast<float>(elapsed)/static_cast<float>(EnterUs));
        scale_=1.f-(1.f-TargetScale)*amount_;
        return {scale_!=observed,false,observed,scale_};
    }
};
}
