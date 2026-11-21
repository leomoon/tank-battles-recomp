#include "accounts.h"

#include "platform.h"

#include <algorithm>
#include <charconv>
#include <fstream>
#include <map>
#include <random>
#include <sstream>

namespace tb::launcher {

namespace {

constexpr const char* npid_characters = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-";
constexpr const char* host_characters = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-";

std::map<std::string, std::string> read_settings(const fs::path& path) {
    std::map<std::string, std::string> settings;
    std::ifstream stream(path);
    std::string line;
    while (std::getline(stream, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        const auto equals = line.find('=');
        if (equals != std::string::npos && line[0] != '#') settings[line.substr(0, equals)] = line.substr(equals + 1);
    }
    return settings;
}

std::optional<Account> read_account(const fs::path& path) {
    auto settings = read_settings(path);
    if (settings["endpoint"].empty() || settings["npid"].empty()) return std::nullopt;
    Account account{settings["endpoint"], settings["npid"], settings["token"]};
    if (!settings["ca"].empty()) account.ca = path.parent_path() / from_utf8(settings["ca"]);
    account.remembered = !settings["password"].empty() || load_password(account.id()).has_value();
    return account;
}

bool clean(const std::string& value) {
    // One line each: a newline would add settings to the profile.
    return value.find_first_of(std::string("\r\n\0", 3)) == std::string::npos;
}

std::string result_code(const std::string& output) {
    const std::string marker = "[rpcn-result] ";
    const auto at = output.rfind(marker);
    if (at == std::string::npos) return "failed";
    const auto end = output.find_first_of("\r\n", at);
    return output.substr(at + marker.size(), end == std::string::npos ? std::string::npos : end - at - marker.size());
}

}  // namespace

std::string Account::id() const {
    std::string server = endpoint;
    std::replace(server.begin(), server.end(), ':', '_');
    return npid + "@" + server;
}

std::optional<std::string> validate(const Account& account, const std::string& password, const std::string* email) {
    const auto colon = account.endpoint.rfind(':');
    unsigned port = 0;
    const auto* digits = account.endpoint.data() + (colon == std::string::npos ? 0 : colon + 1);
    const auto* end = account.endpoint.data() + account.endpoint.size();
    const auto parsed = std::from_chars(digits, end, port);
    if (colon == std::string::npos || colon == 0 || parsed.ptr != end || parsed.ec != std::errc() || !port || port > 65535 ||
        account.endpoint.substr(0, colon).find_first_not_of(host_characters) != std::string::npos)
        return account.endpoint.empty() ? "Enter the RPCN server you want to use, as name:port."
                                         : "Enter the server as name:port, for example rpcn.example.org:31313.";
    if (account.npid.size() < 3 || account.npid.size() > 16 || account.npid.find_first_not_of(npid_characters) != std::string::npos)
        return "Usernames have 3 to 16 letters, digits, hyphens or underscores.";
    if (password.empty() || !clean(password)) return "Enter your password (one line).";
    if (!clean(account.token) || account.token.find(' ') != std::string::npos) return "Enter the token exactly as it was sent.";
    if (email && (email->find('@') == std::string::npos || email->size() < 3 || !clean(*email) ||
                  email->find(' ') != std::string::npos))
        return "Enter the email address the server should use for this account.";
    return std::nullopt;
}

std::string explain(const std::string& code, bool token_sent) {
    if (code == "signed_in") return "Signed in.";
    if (code == "created") return "Account created.";
    if (code == "invalid_profile") return "Check the server address (name:port) and the username.";
    if (code == "name_taken") return "That username is already taken on this server. Choose another.";
    if (code == "email_taken") return "That email already has an account on this server. Sign in instead.";
    if (code == "unknown_user" || code == "wrong_password") return "The username or password is not correct for this server.";
    if (code == "token_required")
        return token_sent ? "That token is not valid. Check the newest email from the server and try again."
                          : "This server sent a token to your email. Enter it below to finish signing in.";
    if (code == "already_signed_in") return "This account is signed in on another game right now. Close that game, then try again.";
    if (code == "unreachable") return "The server could not be reached. Check the address and your connection, then retry.";
    if (code == "certificate")
        return "The server's certificate is not trusted. Public servers need nothing extra; for a private server, add its certificate under Advanced.";
    if (code == "protocol") return "This server runs a different RPCN version than this game supports.";
    if (code == "creation_rejected") return "The server did not accept the new account. Try another username or email.";
    if (code == "no_player") return "The player is missing from this folder. Build it again with the builder.";
    return "The server rejected the request. Check the details and retry.";
}

std::vector<Account> Accounts::saved() const {
    std::vector<Account> accounts;
    std::error_code error;
    for (const auto& entry : fs::directory_iterator(folder_.online(), error))
        if (entry.path().extension() == ".account")
            if (auto account = read_account(entry.path())) accounts.push_back(*account);
    std::sort(accounts.begin(), accounts.end(), [](const Account& a, const Account& b) { return a.id() < b.id(); });
    return accounts;
}

std::optional<Account> Accounts::active() const {
    std::ifstream stream(folder_.online() / "active");
    std::string name;
    if (!std::getline(stream, name)) return std::nullopt;
    while (!name.empty() && (name.back() == '\r' || name.back() == ' ')) name.pop_back();
    const auto file = from_utf8(name);
    if (name.empty() || file.has_parent_path()) return std::nullopt;
    return read_account(folder_.online() / file);
}

void Accounts::select(const Account& account) {
    write_private(folder_.online() / "active", account.id() + ".account\n");
}

void Accounts::sign_out() {
    std::error_code error;
    fs::remove(folder_.online() / "active", error);
}

void Accounts::forget(const Account& account) {
    if (const auto current = active(); current && current->id() == account.id()) sign_out();
    forget_password(account.id());
    erase_private(folder_.online() / from_utf8(account.id() + ".account"));
}

std::optional<std::string> Accounts::remembered_password(const Account& account) const {
    if (auto stored = load_password(account.id())) return stored;
    auto settings = read_settings(folder_.online() / from_utf8(account.id() + ".account"));
    if (!settings["password"].empty()) return settings["password"];
    return std::nullopt;
}

fs::path Accounts::import_ca(const fs::path& source, const std::string& endpoint) const {
    std::string name = endpoint;
    std::replace(name.begin(), name.end(), ':', '_');
    const auto target = folder_.online() / from_utf8(name + ".pem");
    std::ifstream input(source, std::ios::binary);
    std::stringstream content;
    content << input.rdbuf();
    if (!input || content.str().find("-----BEGIN CERTIFICATE-----") == std::string::npos)
        throw std::runtime_error("That file is not a PEM certificate.");
    fs::create_directories(folder_.online());
    std::ofstream(target, std::ios::binary) << content.str();
    return target;
}

void Accounts::save(const Account& account, const std::string* password) const {
    std::string content = "endpoint=" + account.endpoint + "\nnpid=" + account.npid + "\n";
    if (!account.ca.empty()) content += "ca=" + utf8(account.ca.filename()) + "\n";
    if (!account.token.empty()) content += "token=" + account.token + "\n";
    if (password) {
        if (!store_password(account.id(), *password)) content += "password=" + *password + "\n";
    } else {
        forget_password(account.id());
    }
    write_private(folder_.online() / from_utf8(account.id() + ".account"), content);
}

fs::path Accounts::write_runtime_profile(const Account& account, const std::string& password, const std::string* email) const {
    std::random_device device;
    const auto directory = private_temporary_directory(folder_.online() / ".session");
    const auto path = directory / ("session-" + std::to_string(device()) + std::to_string(device()) + ".profile");
    std::string content = "endpoint=" + account.endpoint + "\nnpid=" + account.npid + "\npassword=" + password + "\n";
    if (!account.ca.empty()) content += "ca=" + utf8(fs::absolute(account.ca)) + "\n";
    if (!account.token.empty()) content += "token=" + account.token + "\n";
    if (email) content += "email=" + *email + "\n";
    write_private(path, content);
    return path;
}

Result Accounts::run(const Account& account, const std::string& password, const std::string* email) {
    std::error_code error;
    if (!fs::is_regular_file(folder_.player(), error)) return {"no_player", explain("no_player", false)};
    const auto profile = write_runtime_profile(account, password, email);
    auto environment = folder_.environment();
    environment.emplace_back("TB_SESSION_CAPTURED", "1");   // Results on stdout, not in a session log.
    const auto output = run_captured(folder_.player(),
                                     {email ? "--rpcn-create-account" : "--rpcn-check", profile}, environment);
    erase_private(profile);
    const auto code = output.status < 0 ? std::string("no_player") : result_code(output.text);
    return {code, explain(code, !account.token.empty())};
}

Result Accounts::sign_in(Account account, const std::string& password, bool remember) {
    if (auto problem = validate(account, password, nullptr)) return {"invalid_input", *problem};
    auto result = run(account, password, nullptr);
    if (result.ok()) {
        save(account, remember ? &password : nullptr);
        select(account);
    } else if (result.code == "token_required") {
        save(account, nullptr);   // Keep the server and name for the token step; no password.
    }
    return result;
}

Result Accounts::create(Account account, const std::string& email, const std::string& password, bool remember) {
    if (auto problem = validate(account, password, &email)) return {"invalid_input", *problem};
    auto result = run(account, password, &email);
    if (!result.ok()) return result;
    // Sign in straight away; a server with email verification answers with token_required.
    auto signed_in = sign_in(account, password, remember);
    if (signed_in.code == "token_required") signed_in.message = "Account created. " + signed_in.message;
    return signed_in;
}

int Accounts::play(const std::optional<std::pair<Account, std::string>>& online) {
    std::vector<fs::path> arguments{folder_.executable()};
    fs::path profile;
    if (online) {
        profile = write_runtime_profile(online->first, online->second, nullptr);
        arguments.insert(arguments.end(), {"--rpcn-profile", profile});
    }
    const int status = run_game(folder_.player(), arguments, folder_.environment(), folder_.root);
    if (!profile.empty()) erase_private(profile);
    return status;
}

}  // namespace tb::launcher
