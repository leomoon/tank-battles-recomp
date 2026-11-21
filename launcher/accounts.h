#pragma once
// RPCN accounts for the player folder. Accounts belong to one RPCN server and
// are separate from PSN. Each saved account is online/<npid>@<host>_<port>.account
// in the runtime's profile format; online/active names the selected one.
//
// Passwords never appear in command lines or logs. Remembered passwords live in
// Windows Credential Manager, or (where no keyring is universally available) in
// the account file restricted to this user. Every launch and check writes a
// temporary runtime profile readable only by this user and erases it afterwards.
#include "launcher_paths.h"

#include <optional>
#include <string>
#include <vector>

namespace tb::launcher {

inline constexpr const char* default_server = "rpcn.example.org:31313";

struct Account {
    std::string endpoint, npid, token;
    fs::path ca;               // Private CA for a non-public server; empty uses system trust.
    bool remembered = false;   // A password is stored for it.

    std::string id() const;
};

// What happened, as a stable word, plus a sentence for people.
struct Result {
    std::string code;          // signed_in, created, or a failure word from account_command.
    std::string message;
    bool ok() const { return code == "signed_in" || code == "created"; }
};

class Accounts {
public:
    explicit Accounts(Folder folder) : folder_(std::move(folder)) {}

    std::vector<Account> saved() const;
    std::optional<Account> active() const;
    void select(const Account& account);
    void sign_out();                                   // Keeps the saved account.
    void forget(const Account& account);               // Removes it and any remembered password.
    std::optional<std::string> remembered_password(const Account& account) const;

    // Validates locally, then asks the server through the player. On success the
    // account is saved, selected and, if requested, its password remembered.
    Result sign_in(Account account, const std::string& password, bool remember);
    Result create(Account account, const std::string& email, const std::string& password, bool remember);

    // Starts the game; online with `password` for the active account when given.
    int play(const std::optional<std::pair<Account, std::string>>& online);

    // Copies a CA file next to the accounts so the folder stays movable.
    fs::path import_ca(const fs::path& source, const std::string& endpoint) const;

    const Folder& folder() const { return folder_; }

private:
    Result run(const Account& account, const std::string& password, const std::string* email);
    fs::path write_runtime_profile(const Account& account, const std::string& password, const std::string* email) const;
    void save(const Account& account, const std::string* password) const;

    Folder folder_;
};

std::optional<std::string> validate(const Account& account, const std::string& password, const std::string* email);
std::string explain(const std::string& code, bool token_sent);

}  // namespace tb::launcher
