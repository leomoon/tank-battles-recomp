#pragma once

#include <string>

namespace tb::rpcn {

enum class State { offline, connecting, online };

struct Status {
    State state = State::offline;
    std::string npid;
};

bool start(const char* profile_path);
void stop();
bool enabled();
Status status();
int account_command(const char* profile_path, bool create);

}
