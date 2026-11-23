#pragma once

#include "client.h"
#include <optional>

namespace tb::rpcn {

enum class FriendAction { add, accept, cancel, decline, remove };
enum class FriendOutcome {
    pending, request_sent, accepted, canceled, declined, removed,
    already_pending, already_friends, already_related, invalid_name, self,
    not_found, blocked, rejected, not_online, busy, confirmation_required,
    unknown, session_changed
};

struct FriendResult {
    FriendAction action;
    FriendOutcome outcome;
    std::string npid, message;
};

// One controller belongs to one UI owner. begin/poll never wait for network I/O.
// Destruction does not undo a submitted operation; only server acknowledgements
// and notifications update relationships. poll reports an unknown result after
// a deadline or interrupted connection, since the server may have applied it.
class FriendsController {
public:
    using Clock=std::chrono::steady_clock;
    FriendResult begin(FriendAction action,std::string npid,bool confirmed=false,uint64_t expected_generation=0);
    std::optional<FriendResult> poll(Clock::time_point now=Clock::now());
    bool busy() const { return pending_.has_value(); }

private:
    struct Pending {
        FriendAction action;
        std::string npid, endpoint, owner;
        uint64_t generation, user_id;
        Clock::time_point deadline;
        std::future<Reply> reply;
    };
    std::optional<Pending> pending_;
};

}
