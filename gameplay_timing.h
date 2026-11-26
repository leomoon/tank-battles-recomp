#pragma once
#include <array>
#include <cstdint>
#include <vector>
#include <cstdlib>
#include <cerrno>

// Keep diagnostic duration explicit so a finished recording is not mistaken
// for a stopped game during longer session/lifecycle checks.
struct GameplayRecordingWindow {
    uint64_t limit_ns=600000000000ull;
    bool ended=false;
    explicit GameplayRecordingWindow(const char* seconds=nullptr) {
        if(!seconds || !*seconds)return;
        char* end=nullptr;errno=0;
        const auto value=std::strtoul(seconds,&end,10);
        if(!errno && end!=seconds && !*end && value>=1 && value<=3600)
            limit_ns=uint64_t(value)*1000000000ull;
    }
    bool finish(uint64_t elapsed_ns) {
        if(ended || elapsed_ns<=limit_ns)return false;
        ended=true;return true;
    }
};

// A completed guest frame can be refreshed many times by the window.
struct GameplayCadence {
    uint64_t guest=0,completed=0,presents=0,last_completed_serial=0;
    std::array<std::vector<uint64_t>,3> events;
    void flip(uint64_t ns) {++guest;events[0].push_back(ns);}
    void present(uint64_t ns,uint64_t serial) {
        ++presents;events[2].push_back(ns);
        if(serial && serial>last_completed_serial) {
            last_completed_serial=serial;++completed;events[1].push_back(ns);
        }
    }
};
