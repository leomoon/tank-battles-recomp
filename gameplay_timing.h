#pragma once
#include <array>
#include <cstdint>
#include <vector>

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
