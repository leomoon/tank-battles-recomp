#pragma once
#include <string>
#include <string_view>

namespace tb::rpcn {
// Shared by physical typing and the controller keyboard. Preserve case: the
// stock server looks up usernames using their stored spelling.
struct FriendNameEntry {
    std::string value, original, error;
    bool editing=false, upper=false;
    unsigned key=0;
    void open() {original=value;error.clear();editing=true;key=0;}
    void append(std::string_view text) {
        if (text.find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")!=std::string_view::npos) {
            error="USE LETTERS, DIGITS, - OR _";return;
        }
        if (value.size()+text.size()>16) {error="MAXIMUM 16 CHARACTERS";return;}
        value+=text;error.clear();
    }
    void erase() {if (!value.empty()) value.pop_back();error.clear();}
    bool done() {
        if (value.size()<3) {error="ENTER AT LEAST 3 CHARACTERS";return false;}
        editing=false;error.clear();return true;
    }
    void cancel() {value=original;editing=false;error.clear();}
    void move(int horizontal,int vertical) {
        const int row=static_cast<int>(key)/8,column=static_cast<int>(key)%8;
        key=static_cast<unsigned>(((row+vertical+5)%5)*8+(column+horizontal+8)%8);
    }
    char character() const {
        constexpr std::string_view keys="abcdefghijklmnopqrstuvwxyz0123456789_-";
        if (key>=keys.size()) return 0;
        char c=keys[key];
        return upper && c>='a' && c<='z'?char(c-'a'+'A'):c;
    }
    void choose() {
        if (key==38) erase();
        else if (key==39) done();
        else append(std::string(1,character()));
    }
};
}
