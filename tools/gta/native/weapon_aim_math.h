#pragma once
#include "camera_preview_math.h"

namespace gta_thaw::camera_preview {
constexpr float AimPi=3.141592654f;
inline float WrapAim(float angle) noexcept { return std::remainder(angle,2.f*AimPi); }

struct AimController {
    std::uint64_t request=0, updated_us=0;
    float yaw=0,pitch=0,blend=0;
    bool held=false;
    void Clear() noexcept { *this={}; }
    // Pixel deltas are already integrated input. Scaling them by frame time would
    // make mouse sensitivity change with the game's FPS.
    bool Present(const Packet& source,const float* origin,bool pressed,
                 float mouse_x,float mouse_y,float sensitivity_x,float sensitivity_y,
                 std::uint64_t now_us,Packet& output,bool hanging=false,float body_yaw=0,float height=.5f) noexcept {
        output=source;
        for(float value:{mouse_x,mouse_y,sensitivity_x,sensitivity_y,body_yaw,height}) if(!std::isfinite(value)) return false;
        for(unsigned i=0;i<3;++i) if(!std::isfinite(origin[i])) return false;
        if(sensitivity_x<0 || sensitivity_x>1 || sensitivity_y<0 || sensitivity_y>1 ||
           std::fabs(mouse_x)>10000 || std::fabs(mouse_y)>10000) return false;
        if(request!=source.request) { Clear(); request=source.request; updated_us=now_us; }
        const float dt=now_us>=updated_us?std::min(.05f,static_cast<float>(now_us-updated_us)/1000000.f):0.f;
        updated_us=now_us;
        if(pressed && !held) {
            yaw=std::atan2(source.forward[1],source.forward[0]);
            pitch=std::asin(std::clamp(source.forward[2],-1.f,1.f));
        }
        held=pressed;
        if(pressed) {
            yaw=WrapAim(yaw-mouse_x*3.f*sensitivity_x);
            pitch=std::clamp(pitch+mouse_y*4.f*sensitivity_y,-1.15f,1.15f);
            if(hanging) {
                yaw=WrapAim(body_yaw+std::clamp(WrapAim(yaw-body_yaw),-95.f*AimPi/180.f,95.f*AimPi/180.f));
                pitch=std::clamp(pitch,-AimPi/3.f,AimPi/3.f);
            }
        }
        blend=std::clamp(blend+(pressed?1.f:-1.f)*dt/.16f,0.f,1.f);
        if(!blend) return true;
        Packet aim=source;
        const float cp=std::cos(pitch),sp=std::sin(pitch),cy=std::cos(yaw),sy=std::sin(yaw);
        aim.forward[0]=cp*cy; aim.forward[1]=cp*sy; aim.forward[2]=sp;
        aim.up[0]=-sp*cy; aim.up[1]=-sp*sy; aim.up[2]=cp;
        const float right[3]{sy,-cy,0};
        for(unsigned i=0;i<3;++i)
            aim.eye[i]=origin[i]+(i==2?height:0.f)+right[i]*.48f-aim.forward[i]*2.f;
        aim.horizontal_fov_degrees=60.f;
        aim.view_window[0]=std::tan(30.f/57.295779513f);
        aim.view_window[1]=aim.view_window[0]/source.source_aspect;
        output=BlendCamera(source,aim,blend*blend*(3.f-2.f*blend));
        return ValidPose(output);
    }
};
}
