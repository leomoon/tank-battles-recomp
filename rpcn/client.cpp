#include "client.h"

#include <curl/curl.h>
#include <algorithm>
#include <array>
#include <charconv>
#include <chrono>
#include <cstdio>
#include <deque>
#include <filesystem>
#include <fstream>
#include <memory>
#include <mutex>
#include <optional>
#include <span>
#include <stdexcept>
#include <string_view>
#include <thread>
#include <vector>

namespace tb::rpcn {
namespace {

using namespace std::chrono_literals;
using Clock = std::chrono::steady_clock;
using Bytes = std::vector<unsigned char>;
constexpr unsigned protocol_version = 34;
constexpr size_t header_size = 15;
constexpr size_t max_body = 0x800000 - header_size;
enum class Packet : unsigned char { request, reply, notification, server_info };
enum class Command : uint16_t { login, terminate, create };

struct LoginBusy : std::runtime_error {
    using std::runtime_error::runtime_error;
};

struct Profile {
    std::string endpoint, ca, npid, password, token, email;
};

std::filesystem::path utf8_path(std::string_view value) {
    return std::filesystem::path(std::u8string(value.begin(), value.end()));
}

std::string read_small_file(const std::filesystem::path& path, size_t limit) {
    std::ifstream input(path, std::ios::binary);
    std::string content(limit + 1, '\0');
    input.read(content.data(), static_cast<std::streamsize>(content.size()));
    if (!input.eof() || input.bad() || static_cast<size_t>(input.gcount()) > limit)
        throw std::runtime_error("Cannot read RPCN profile or CA file within its size limit");
    content.resize(static_cast<size_t>(input.gcount()));
    if (content.find('\0') != std::string::npos)
        throw std::runtime_error("Unexpected NUL byte in RPCN profile or CA file");
    return content;
}

Profile read_profile(const char* name, bool create) {
    const auto path = utf8_path(name);
    const auto content = read_small_file(path, 4096);
    Profile profile;
    std::string ca_path;
    size_t start = 0;
    while (start < content.size()) {
        const size_t end = content.find('\n', start);
        auto line = std::string_view(content).substr(start, end - start);
        start = end == std::string::npos ? content.size() : end + 1;
        if (line.ends_with('\r')) line.remove_suffix(1);
        if (line.empty() || line.starts_with('#')) continue;
        const size_t equals = line.find('=');
        if (equals == std::string_view::npos)
            throw std::runtime_error("Expected key=value in RPCN profile");
        const auto key = line.substr(0, equals);
        const auto value = line.substr(equals + 1);
        std::string* target = nullptr;
        if (key == "endpoint") target = &profile.endpoint;
        else if (key == "ca") target = &ca_path;
        else if (key == "npid") target = &profile.npid;
        else if (key == "password") target = &profile.password;
        else if (key == "token") target = &profile.token;
        else if (key == "email") target = &profile.email;
        if (!target || !target->empty() || value.empty())
            throw std::runtime_error("Unknown, repeated or empty RPCN profile setting");
        *target = value;
    }
    const auto colon = profile.endpoint.rfind(':');
    const std::string_view endpoint(profile.endpoint);
    if (colon == std::string::npos || colon == 0 ||
        endpoint.substr(0, colon).find_first_not_of(
            "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-") != std::string_view::npos)
        throw std::runtime_error("RPCN endpoint must use hostname:port or IPv4:port");
    const auto port = endpoint.substr(colon + 1);
    unsigned port_number = 0;
    const auto parsed = std::from_chars(port.data(), port.data() + port.size(), port_number);
    if (parsed.ec != std::errc() || parsed.ptr != port.data() + port.size() ||
        port_number == 0 || port_number > 65535)
        throw std::runtime_error("Invalid RPCN endpoint port");
    if (profile.npid.size() < 3 || profile.npid.size() > 16 ||
        profile.npid.find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-") !=
            std::string::npos)
        throw std::runtime_error("RPCN NP ID must be 3 to 16 letters, digits, hyphens or underscores");
    if (profile.password.empty() || (create && profile.email.empty()))
        throw std::runtime_error("RPCN profile needs a password and account creation also needs an email");
    if (!ca_path.empty())
        profile.ca = read_small_file(path.parent_path() / utf8_path(ca_path), 256 * 1024);
    return profile;
}

uint64_t read_le(std::span<const unsigned char> data) {
    uint64_t value = 0;
    for (size_t i = 0; i < data.size(); ++i) value |= uint64_t(data[i]) << (i * 8);
    return value;
}

void append_le(Bytes& bytes, uint64_t value, unsigned count) {
    for (unsigned i = 0; i < count; ++i) {
        bytes.push_back(static_cast<unsigned char>(value));
        value >>= 8;
    }
}

struct Frame {
    Packet type;
    uint16_t command;
    uint64_t id;
    Bytes body;
};

struct Pending {
    Request command;
    Bytes data;
    std::promise<Reply> completion;
};

std::mutex state_lock;
Status session_status;
std::deque<Pending> requests;
std::vector<Notification> notifications;
bool requested = false;
std::jthread worker;

void require_ok(CURLcode code) {
    if (code != CURLE_OK) throw std::runtime_error(curl_easy_strerror(code));
}

void initialize_curl() {
    static const CURLcode result = curl_global_init(CURL_GLOBAL_DEFAULT);
    require_ok(result);
}

int cancel_transfer(void* context, curl_off_t, curl_off_t, curl_off_t, curl_off_t) {
    return static_cast<const std::stop_token*>(context)->stop_requested() ? 1 : 0;
}

class Connection {
public:
    Connection(const Profile& profile, std::stop_token stop) : stop_(stop) {
        handle_.reset(curl_easy_init());
        if (!handle_) throw std::runtime_error("Could not allocate RPCN TLS connection");
        CURL* easy = handle_.get();
        const auto url = "https://" + profile.endpoint + "/";
        require_ok(curl_easy_setopt(easy, CURLOPT_URL, url.c_str()));
        require_ok(curl_easy_setopt(easy, CURLOPT_CONNECT_ONLY, 1L));
        require_ok(curl_easy_setopt(easy, CURLOPT_PROXY, ""));
        require_ok(curl_easy_setopt(easy, CURLOPT_SSL_VERIFYPEER, 1L));
        require_ok(curl_easy_setopt(easy, CURLOPT_SSL_VERIFYHOST, 2L));
        require_ok(curl_easy_setopt(easy, CURLOPT_SSLVERSION, CURL_SSLVERSION_TLSv1_2));
        require_ok(curl_easy_setopt(easy, CURLOPT_SSL_ENABLE_ALPN, 0L));
        require_ok(curl_easy_setopt(easy, CURLOPT_NOSIGNAL, 1L));
        require_ok(curl_easy_setopt(easy, CURLOPT_TCP_KEEPALIVE, 1L));
        require_ok(curl_easy_setopt(easy, CURLOPT_CONNECTTIMEOUT_MS, 5000L));
        require_ok(curl_easy_setopt(easy, CURLOPT_TIMEOUT_MS, 5000L));
        require_ok(curl_easy_setopt(easy, CURLOPT_NOPROGRESS, 0L));
        require_ok(curl_easy_setopt(easy, CURLOPT_XFERINFOFUNCTION, cancel_transfer));
        require_ok(curl_easy_setopt(easy, CURLOPT_XFERINFODATA, &stop_));
        if (!profile.ca.empty()) {
            curl_blob ca{const_cast<char*>(profile.ca.data()), profile.ca.size(), CURL_BLOB_COPY};
            require_ok(curl_easy_setopt(easy, CURLOPT_CAINFO_BLOB, &ca));
        }
#ifdef _WIN32
        else {
            require_ok(curl_easy_setopt(easy, CURLOPT_SSL_OPTIONS, static_cast<long>(CURLSSLOPT_NATIVE_CA)));
        }
#endif
        require_ok(curl_easy_perform(easy));
        const auto hello = receive(Clock::now() + 5s);
        if (hello.type != Packet::server_info || hello.command || hello.id || hello.body.size() != 4)
            throw std::runtime_error("Invalid RPCN greeting");
        if (read_le(hello.body) != protocol_version)
            throw std::runtime_error("RPCN protocol mismatch (expected version 34)");
    }

    Bytes request(Command command, std::initializer_list<std::string_view> fields) {
        Bytes data;
        for (const auto field : fields) {
            data.insert(data.end(), field.begin(), field.end());
            data.push_back(0);
        }
        auto reply = exchange(static_cast<uint16_t>(command), data);
        if (reply.front()) {
            const auto reason = reply.front();
            if (command == Command::login && reason == 6)
                throw LoginBusy("RPCN account already signed in (code 6). Close the other session and retry.");
            const char* description = "request rejected";
            if (reason == 6) description = "account already signed in";
            if (reason == 7) description = "unknown NP ID";
            if (reason == 8) description = "incorrect password";
            if (reason == 9) description = "invalid token";
            if (reason == 11) description = "NP ID already exists";
            if (reason == 13) description = "email already registered";
            throw std::runtime_error(std::string("RPCN ") + description + " (code " +
                                     std::to_string(reason) + ")");
        }
        return reply;
    }

    void serve() {
        while (!stop_.stop_requested()) {
            std::optional<Pending> pending;
            {
                std::lock_guard guard(state_lock);
                if (!requests.empty()) {
                    pending.emplace(std::move(requests.front()));
                    requests.pop_front();
                }
            }
            if (pending) {
                try {
                    pending->completion.set_value({exchange(static_cast<uint16_t>(pending->command), pending->data)});
                } catch (...) {
                    pending->completion.set_value({});
                    throw;
                }
                continue;
            }
            unsigned char type = 0;
            size_t length = 0;
            const auto code = curl_easy_recv(handle_.get(), &type, 1, &length);
            if (code == CURLE_AGAIN) {
                std::this_thread::sleep_for(10ms);
                continue;
            }
            require_ok(code);
            if (!length) throw std::runtime_error("RPCN server closed the connection");
            auto frame = receive_tail(type, Clock::now() + 5s);
            if (frame.type != Packet::notification)
                throw std::runtime_error("Unexpected RPCN packet while signed in");
            notify(std::move(frame));
        }
        // On orderly shutdown the server can start releasing the login and
        // room before the TLS connection closes. Never hold up game exit.
        stop_ = {};
        try {
            exchange(static_cast<uint16_t>(Command::terminate), {}, 500ms);
        } catch (const std::exception&) {
            // Closing the connection also triggers server cleanup.
        }
    }

private:
    void notify(Frame frame) {
        // Room membership, room-data and room-message notifications.
        if (frame.command>3 && frame.command!=9) return;
        std::lock_guard guard(state_lock);
        if (notifications.size()>=128 || frame.body.size()>256*1024)
            throw std::runtime_error("RPCN room notification limit exceeded");
        notifications.push_back({frame.command,std::move(frame.body)});
    }

    Bytes exchange(uint16_t command, const Bytes& data, Clock::duration timeout = 5s) {
        Bytes packet{static_cast<unsigned char>(Packet::request)};
        append_le(packet, command, 2);
        append_le(packet, header_size + data.size(), 4);
        const auto id = next_id_++;
        append_le(packet, id, 8);
        packet.insert(packet.end(), data.begin(), data.end());
        const auto deadline = Clock::now() + timeout;
        write(packet, deadline);
        for (;;) {
            auto reply = receive(deadline);
            if (reply.type == Packet::notification) {
                notify(std::move(reply));
                continue;
            }
            if (reply.type != Packet::reply || reply.id != id || reply.command != command || reply.body.empty())
                throw std::runtime_error("RPCN replied with an unexpected command or request ID");
            return std::move(reply.body);
        }
    }

    void check_deadline(Clock::time_point deadline) {
        if (stop_.stop_requested()) throw std::runtime_error("RPCN operation cancelled");
        if (Clock::now() >= deadline) throw std::runtime_error("RPCN transfer timed out");
    }

    void read(std::span<unsigned char> bytes, Clock::time_point deadline) {
        while (!bytes.empty()) {
            check_deadline(deadline);
            size_t n = 0;
            const auto code = curl_easy_recv(handle_.get(), bytes.data(), bytes.size(), &n);
            if (code == CURLE_AGAIN) {
                std::this_thread::sleep_for(15ms);
                continue;
            }
            require_ok(code);
            if (n == 0) throw std::runtime_error("RPCN server closed the connection");
            bytes = bytes.subspan(n);
        }
    }

    void write(std::span<const unsigned char> bytes, Clock::time_point deadline) {
        while (!bytes.empty()) {
            check_deadline(deadline);
            size_t n = 0;
            const auto code = curl_easy_send(handle_.get(), bytes.data(), bytes.size(), &n);
            if (code == CURLE_AGAIN) {
                std::this_thread::sleep_for(15ms);
                continue;
            }
            require_ok(code);
            if (n == 0) throw std::runtime_error("RPCN connection stopped accepting bytes");
            bytes = bytes.subspan(n);
        }
    }

    Frame receive(Clock::time_point deadline) {
        unsigned char type = 0;
        read({&type, 1}, deadline);
        return receive_tail(type, deadline);
    }

    Frame receive_tail(unsigned char type, Clock::time_point deadline) {
        std::array<unsigned char, header_size> header{};
        header[0] = type;
        auto bytes = std::span(header);
        deadline = std::min(deadline, Clock::now() + 5s);
        read(bytes.subspan(1), deadline);
        const auto length = read_le(bytes.subspan(3, 4));
        if (header[0] > static_cast<unsigned char>(Packet::server_info) ||
            length < header_size || length - header_size > max_body)
            throw std::runtime_error("Invalid RPCN frame header");
        Frame frame{static_cast<Packet>(type), static_cast<uint16_t>(read_le(bytes.subspan(1, 2))),
                    read_le(bytes.subspan(7, 8)), Bytes(static_cast<size_t>(length - header_size))};
        read(frame.body, deadline);
        return frame;
    }

    std::stop_token stop_;
    std::unique_ptr<CURL, decltype(&curl_easy_cleanup)> handle_{nullptr, curl_easy_cleanup};
    uint64_t next_id_ = 1;
};

uint64_t login(Connection& connection, const Profile& profile) {
    const auto reply = connection.request(Command::login, {profile.npid, profile.password, profile.token});
    auto name = std::find(reply.begin() + 1, reply.end(), 0);
    if (name == reply.end() || name == reply.begin() + 1)
        throw std::runtime_error("Incomplete RPCN login identity");
    const auto avatar = std::find(name + 1, reply.end(), 0);
    if (avatar == reply.end() || reply.end() - avatar < 25 || !read_le(std::span(avatar + 1, 8)))
        throw std::runtime_error("Incomplete RPCN login identity");
    return read_le(std::span(avatar + 1, 8));
}

struct Login {
    std::unique_ptr<Connection> connection;
    uint64_t user_id;
};

Login connect_and_login(const Profile& profile, const std::stop_token& stop) {
    const auto retry_until = Clock::now() + 6s;
    bool retrying = false;
    for (;;) {
        if (stop.stop_requested()) throw std::runtime_error("RPCN operation cancelled");
        try {
            auto connection = std::make_unique<Connection>(profile, stop);
            const auto user_id = login(*connection, profile);
            return {std::move(connection), user_id};
        } catch (const LoginBusy&) {
            // RPCN closes a rejected login connection. A quick restart can
            // see this reply while the old connection's room cleanup runs.
            if (Clock::now() + 500ms >= retry_until) throw;
            if (!retrying) {
                fprintf(stderr, "[rpcn] Waiting for the previous sign-in to clear\n");
                retrying = true;
            }
            std::this_thread::sleep_for(500ms);
        }
    }
}

void set_status(State state, std::string npid = {}, uint64_t user_id = 0, std::string endpoint = {}) {
    std::lock_guard guard(state_lock);
    session_status = {state, std::move(npid), user_id, std::move(endpoint)};
    if (state == State::offline) {
        for (auto& pending : requests) pending.completion.set_value({});
        requests.clear();
        notifications.clear();
    }
}

}

bool start(const char* profile_path) {
    try {
        auto profile = read_profile(profile_path, false);
        initialize_curl();
        requested = true; // Set once before guest threads start.
        set_status(State::connecting);
        worker = std::jthread([profile = std::move(profile)](std::stop_token stop) {
            try {
                auto [connection, user_id] = connect_and_login(profile, stop);
                set_status(State::online, profile.npid, user_id, profile.endpoint);
                fprintf(stderr, "[rpcn] Signed in to %s as %s\n", profile.endpoint.c_str(), profile.npid.c_str());
                connection->serve();
            } catch (const std::exception& error) {
                if (!stop.stop_requested()) fprintf(stderr, "[rpcn] %s\n", error.what());
            }
            set_status(State::offline);
        });
        return true;
    } catch (const std::exception& error) {
        fprintf(stderr, "[rpcn] %s\n", error.what());
        return false;
    }
}

void stop() {
    if (worker.joinable()) {
        worker.request_stop();
        worker.join();
    }
}

bool enabled() { return requested; }

Status status() {
    std::lock_guard guard(state_lock);
    return session_status;
}

std::future<Reply> submit(Request command, Bytes body) {
    std::lock_guard guard(state_lock);
    if (session_status.state != State::online || body.size() > max_body || requests.size() >= 32)
        return {};
    Pending pending{command, std::move(body), {}};
    auto future = pending.completion.get_future();
    requests.push_back(std::move(pending));
    return future;
}

std::vector<Notification> take_notifications() {
    std::lock_guard guard(state_lock);
    std::vector<Notification> result;
    result.swap(notifications);
    return result;
}

int account_command(const char* profile_path, bool create) {
    try {
        const auto profile = read_profile(profile_path, create);
        initialize_curl();
        if (create) {
            Connection connection(profile, {});
            connection.request(Command::create, {profile.npid, profile.password, profile.npid,
                                                  "https://example.invalid/avatar", profile.email});
            printf("[rpcn] Created account %s\n", profile.npid.c_str());
        } else {
            auto session = connect_and_login(profile, {});
            printf("[rpcn] Signed in as %s\n", profile.npid.c_str());
            session.connection->request(Command::terminate, {});
        }
        return 0;
    } catch (const std::exception& error) {
        fprintf(stderr, "[rpcn] %s\n", error.what());
        return 1;
    }
}

}
