#pragma once

namespace tb::rpcn {

void register_matching();
void reset_matching();
// Returns true once when the current lobby is being left or was closed.
bool pump_matching();

}
