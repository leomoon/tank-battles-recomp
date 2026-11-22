#pragma once

#include <cstdint>
#include <future>
#include <string>
#include <vector>

namespace tb::rpcn {

enum class State { offline, connecting, online };
enum class ConnectionError { none, account_in_use, unavailable };

struct Status {
    State state = State::offline;
    std::string npid;
    uint64_t user_id = 0;
    std::string endpoint;
    ConnectionError error = ConnectionError::none;
};

enum class AccountAction { sign_in, create, request_reset, reset_password };

struct Reply {
    // A missing response indicates that the connection ended.
    std::vector<unsigned char> body;
};

enum class Request : uint16_t {
    add_friend = 8, send_message = 29,
    server_list = 12, world_list = 13, create_room = 14, join_room = 15,
    leave_room = 16, search_room = 17, get_room_data = 20, set_room_data = 21,
    ping_room_owner = 25, send_room_message = 26,
    signaling_info = 27,
    record_score = 31, record_score_data = 32, score_data = 33,
    score_range = 34, score_npid = 36,
};

struct Notification {
    uint16_t command;
    std::vector<unsigned char> body;
};

std::future<Reply> submit(Request command, std::vector<unsigned char> body);
std::vector<Notification> take_notifications();
std::vector<Notification> take_basic_notifications();
struct Friend { std::string npid; bool online=false; uint64_t timestamp=0; };
std::vector<Friend> friends();

bool start(const char* profile_path);
void retry_sign_in();
void stop();
bool enabled();
Status status();
int account_command(const char* profile_path, AccountAction action);

}
