#include "rpcn/friends.h"
#include <cassert>
#include <optional>

namespace tb::rpcn {
Relationships current;
State connection=State::online;
std::optional<std::promise<Reply>> response;
Request last_command;
std::vector<unsigned char> last_body;
unsigned submissions=0;
bool reject_submission=false;
Relationships relationships() {return current;}
Status status() {Status s;s.state=connection;return s;}
std::future<Reply> submit(Request command,std::vector<unsigned char> body,uint64_t generation,
                        std::chrono::steady_clock::time_point deadline) {
    assert(generation==current.generation && deadline>std::chrono::steady_clock::now());
    if (reject_submission) return {};
    ++submissions;last_command=command;last_body=std::move(body);
    response.emplace();return response->get_future();
}
}

int main() {
    using namespace tb::rpcn;
    current.endpoint="one.example:31313";current.npid="PlayerA";current.user_id=1;current.generation=1;
    FriendsController controller;
    for (const auto& name:{"", "ab", "bad/name", "bad\nname", "seventeenlettersxx"})
        assert(controller.begin(FriendAction::add,name).outcome==FriendOutcome::invalid_name);
    assert(controller.begin(FriendAction::add,"playera").outcome==FriendOutcome::self);
    assert(!submissions);
    current.friends={{"FriendA",false,0}};current.incoming={"IncomingA"};current.outgoing={"SentA"};
    assert(controller.begin(FriendAction::add,"frienda").outcome==FriendOutcome::already_friends);
    assert(controller.begin(FriendAction::add,"senta").outcome==FriendOutcome::already_pending);
    assert(controller.begin(FriendAction::remove,"FriendA").outcome==FriendOutcome::confirmation_required);
    for (auto action:{FriendAction::accept,FriendAction::cancel,FriendAction::decline,FriendAction::remove})
        assert(controller.begin(action,"MissingUser",true).outcome==FriendOutcome::not_found);
    assert(!submissions);
    auto pending=controller.begin(FriendAction::add,"incominga");
    assert(pending.outcome==FriendOutcome::pending && pending.action==FriendAction::accept);
    assert(last_command==Request::add_friend);
    assert(std::string(last_body.begin(),last_body.end()-1)=="IncomingA" && last_body.back()==0);
    assert(controller.busy() && !controller.poll());
    assert(controller.begin(FriendAction::add,"OtherUser").outcome==FriendOutcome::busy);
    assert(submissions==1);
    response->set_value({{0}});
    assert(controller.poll()->outcome==FriendOutcome::accepted && !controller.busy());
    assert(!controller.poll());
    for (auto action:{FriendAction::cancel,FriendAction::decline,FriendAction::remove}) {
        const auto name=action==FriendAction::cancel?"SentA":action==FriendAction::decline?"IncomingA":"FriendA";
        assert(controller.begin(action,name,true).outcome==FriendOutcome::pending);
        assert(last_command==Request::remove_friend);
        response->set_value({{0}});
        const auto expected=action==FriendAction::cancel?FriendOutcome::canceled:
                            action==FriendAction::decline?FriendOutcome::declined:FriendOutcome::removed;
        assert(controller.poll()->outcome==expected);
    }
    for (auto code:{26,27,24,28}) {
        const auto before=current;
        assert(controller.begin(FriendAction::add,"OtherUser").outcome==FriendOutcome::pending);
        response->set_value({{static_cast<unsigned char>(code)}});
        const auto expected=code==26?FriendOutcome::not_found:code==27?FriendOutcome::blocked:
                            code==28?FriendOutcome::already_related:FriendOutcome::rejected;
        assert(controller.poll()->outcome==expected);
        assert(current.outgoing==before.outgoing && current.incoming==before.incoming && current.revision==before.revision);
    }
    assert(controller.begin(FriendAction::add,"OfflineUser").outcome==FriendOutcome::pending);
    response->set_value({{0}});
    assert(controller.poll()->outcome==FriendOutcome::request_sent);
    assert(controller.begin(FriendAction::add,"LostReply").outcome==FriendOutcome::pending);
    response->set_value({});
    assert(controller.poll()->outcome==FriendOutcome::unknown);
    assert(controller.begin(FriendAction::add,"LateReply").outcome==FriendOutcome::pending);
    assert(controller.poll(FriendsController::Clock::now()+std::chrono::seconds(7))->outcome==FriendOutcome::unknown);
    response->set_value({{0}}); // A late completion cannot reappear as success.
    assert(!controller.poll());
    assert(controller.begin(FriendAction::add,"OldAccount").outcome==FriendOutcome::pending);
    ++current.generation;current.endpoint="two.example:31313";
    response->set_value({{0}});
    assert(controller.poll()->outcome==FriendOutcome::session_changed);
    reject_submission=true;
    assert(controller.begin(FriendAction::add,"OtherUser").outcome==FriendOutcome::not_online);
    reject_submission=false;connection=State::offline;
    assert(controller.begin(FriendAction::add,"OtherUser").outcome==FriendOutcome::not_online);
}
