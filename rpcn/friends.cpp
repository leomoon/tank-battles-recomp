#include "friends.h"

#include <algorithm>
#include <string_view>
#include <utility>

namespace tb::rpcn {
namespace {

bool same(std::string_view a,std::string_view b) {
    const auto lower=[](char c){return c>='A' && c<='Z'?char(c-'A'+'a'):c;};
    return a.size()==b.size() && std::equal(a.begin(),a.end(),b.begin(),
        [&](char x,char y){return lower(x)==lower(y);});
}

bool contains(const std::vector<std::string>& list,const std::string& name) {
    return std::any_of(list.begin(),list.end(),[&](const auto& value){return same(value,name);});
}

bool is_friend(const Relationships& state,const std::string& name) {
    return std::any_of(state.friends.begin(),state.friends.end(),[&](const auto& value){return same(value.npid,name);});
}

FriendResult result(FriendAction action,FriendOutcome outcome,const std::string& npid) {
    const char* message="The server did not accept the request. Try again later.";
    switch (outcome) {
    case FriendOutcome::pending: message="Waiting for the server...";break;
    case FriendOutcome::request_sent: message="Friend request sent. The other player needs to accept it.";break;
    case FriendOutcome::accepted: message="You are now friends.";break;
    case FriendOutcome::canceled: message="Friend request canceled.";break;
    case FriendOutcome::declined: message="Friend request declined.";break;
    case FriendOutcome::removed: message="Friend removed.";break;
    case FriendOutcome::already_pending: message="You have already sent this player a friend request.";break;
    case FriendOutcome::already_friends: message="This player is already your friend.";break;
    case FriendOutcome::already_related: message="The server reports an existing request or friendship. Reconnect to refresh its status.";break;
    case FriendOutcome::invalid_name: message="Enter a username with 3 to 16 letters, digits, hyphens or underscores.";break;
    case FriendOutcome::self: message="You cannot add yourself as a friend.";break;
    case FriendOutcome::not_found: message="That player or request was not found on this server. Check the spelling and capitalization.";break;
    case FriendOutcome::blocked: message="This friend request is blocked by the server.";break;
    case FriendOutcome::not_online: message="Connect to online play before managing friends.";break;
    case FriendOutcome::busy: message="Wait for the current friend request to finish.";break;
    case FriendOutcome::confirmation_required: message="Confirm that you want to remove this friend.";break;
    case FriendOutcome::unknown: message="The change could not be confirmed. Reconnect to check its status before retrying.";break;
    case FriendOutcome::session_changed: message="The signed-in account changed. Open Friends again for the current account.";break;
    case FriendOutcome::rejected: break;
    }
    return {action,outcome,npid,message};
}

}

FriendResult FriendsController::begin(FriendAction action,std::string name,bool confirmed) {
    if (pending_) return result(action,FriendOutcome::busy,name);
    if (name.size()<3 || name.size()>16 || name.find_first_not_of(
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")!=std::string::npos)
        return result(action,FriendOutcome::invalid_name,name);
    const auto state=relationships();
    if (status().state!=State::online || state.npid.empty()) return result(action,FriendOutcome::not_online,name);
    if (same(state.npid,name)) return result(action,FriendOutcome::self,name);
    // Use the server's spelling when the user selected an existing relationship.
    for (const auto& f:state.friends) if (same(f.npid,name)) name=f.npid;
    for (const auto* list:{&state.outgoing,&state.incoming})
        for (const auto& entry:*list) if (same(entry,name)) name=entry;
    const bool mutual=is_friend(state,name),incoming=contains(state.incoming,name),outgoing=contains(state.outgoing,name);
    if (action==FriendAction::add && mutual) return result(action,FriendOutcome::already_friends,name);
    if (action==FriendAction::add && outgoing && !incoming) return result(action,FriendOutcome::already_pending,name);
    if ((action==FriendAction::accept && !incoming) || (action==FriendAction::cancel && !outgoing) ||
        (action==FriendAction::decline && !incoming) || (action==FriendAction::remove && !mutual))
        return result(action,FriendOutcome::not_found,name);
    if (action==FriendAction::remove && !confirmed) return result(action,FriendOutcome::confirmation_required,name);
    // Adding somebody who already sent a request is the same server action as Accept.
    if (action==FriendAction::add && incoming) action=FriendAction::accept;
    const auto deadline=Clock::now()+std::chrono::seconds(6);
    std::vector<unsigned char> body(name.begin(),name.end());body.push_back(0);
    auto reply=submit(action==FriendAction::add || action==FriendAction::accept?Request::add_friend:Request::remove_friend,
                      std::move(body),state.generation,deadline);
    if (!reply.valid()) return result(action,FriendOutcome::not_online,name);
    pending_.emplace(Pending{action,name,state.endpoint,state.npid,state.generation,state.user_id,deadline,std::move(reply)});
    return result(action,FriendOutcome::pending,name);
}

std::optional<FriendResult> FriendsController::poll(Clock::time_point now) {
    if (!pending_) return {};
    auto state=relationships();
    auto finish=[&](FriendOutcome outcome) {
        auto value=result(pending_->action,outcome,pending_->npid);
        pending_.reset();return value;
    };
    const auto changed_session=[&] {
        return state.generation!=pending_->generation || (!state.npid.empty() &&
            (state.endpoint!=pending_->endpoint || state.user_id!=pending_->user_id || !same(state.npid,pending_->owner)));
    };
    if (changed_session())
        return finish(FriendOutcome::session_changed);
    if (state.npid.empty()) return finish(FriendOutcome::unknown);
    if (pending_->reply.wait_for(std::chrono::seconds(0))==std::future_status::ready) {
        const auto reply=pending_->reply.get();
        // The worker can apply acceptance or switch sessions between the
        // initial snapshot and completing this future. Classify the fresh state.
        state=relationships();
        if (changed_session()) return finish(FriendOutcome::session_changed);
        if (state.npid.empty()) return finish(FriendOutcome::unknown);
        if (reply.body.size()!=1) return finish(FriendOutcome::unknown);
        switch (reply.body[0]) {
        case 0:
            switch (pending_->action) {
            case FriendAction::add: return finish(is_friend(state,pending_->npid)?FriendOutcome::accepted:FriendOutcome::request_sent);
            case FriendAction::accept: return finish(FriendOutcome::accepted);
            case FriendAction::cancel: return finish(FriendOutcome::canceled);
            case FriendAction::decline: return finish(FriendOutcome::declined);
            case FriendAction::remove: return finish(FriendOutcome::removed);
            }
            break;
        case 26: return finish(FriendOutcome::not_found);
        case 27: return finish(FriendOutcome::blocked);
        case 28:
            return finish(is_friend(state,pending_->npid)?FriendOutcome::already_friends:
                          contains(state.outgoing,pending_->npid)?FriendOutcome::already_pending:FriendOutcome::already_related);
        default: return finish(FriendOutcome::rejected);
        }
    }
    if (now>=pending_->deadline || status().state!=State::online) return finish(FriendOutcome::unknown);
    return {};
}

}
