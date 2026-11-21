#pragma once
#include "accounts.h"

#include <optional>
#include <string>
#include <utility>

namespace tb::launcher {

struct Choice {
    enum Kind { quit, offline, online } kind = quit;
    std::optional<std::pair<Account, std::string>> session;   // For online.
};

// Shows the launcher window. Returns nullopt if no window could be opened.
std::optional<Choice> run_ui(Accounts& accounts);

}  // namespace tb::launcher
