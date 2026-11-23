#!/usr/bin/env python3
"""Drive two production Friends panels and an invitation into one private RPCN room.

Uses existing, distinct test profiles with no prior friendship. The renderer and
physical input are replaced by the fixture; the client, UI controller, guest
callback adapter and Matching2 room operations are the player's implementations.
Both profiles must target a test server where friend/room mutations are allowed.
"""
import argparse
import json
from pathlib import Path
import queue
import subprocess
import threading
import time


class Player:
    def __init__(self, runner, profile, log):
        self.log = log.open("w")
        self.process = subprocess.Popen([str(runner), str(profile)], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=self.log, text=True, bufsize=1)
        self.output = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()
        self.read("ready")

    def _read(self):
        for line in self.process.stdout:
            self.output.put(line)
        self.output.put(None)

    def read(self, expected):
        line = self.output.get(timeout=9)
        assert line is not None, f"Native UI fixture exited with {self.process.poll()}"
        result = json.loads(line)
        assert result["result"] == expected, result
        return result

    def command(self, command):
        self.process.stdin.write(command + "\n")
        self.process.stdin.flush()
        return self.read(command.split()[0])

    def press(self, mask):
        return self.command(f"press {mask:#x}")

    def wait(self, predicate):
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            state = self.command("snapshot")
            if predicate(state):
                return state
            time.sleep(.02)
        raise AssertionError(f"UI state did not converge: {state}")

    def close(self):
        if self.process.poll() is None:
            self.process.stdin.write("quit\n")
            self.process.stdin.flush()
            try:
                self.process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.process.stdin.close()
        self.process.stdout.close()
        self.log.close()


def npid(profile):
    return next(line.split("=", 1)[1].strip() for line in profile.read_text().splitlines()
                if line.startswith("npid="))


def check(runner, host_profile, peer_profile, output):
    output.mkdir(parents=True, exist_ok=True)
    host_name, peer_name = npid(host_profile), npid(peer_profile)
    assert host_name != peer_name
    a = b = None
    try:
        a = Player(runner, host_profile, output / "host.log")
        b = Player(runner, peer_profile, output / "peer.log")
        assert peer_name not in a.command("snapshot")["friends"], "Use previously unrelated test accounts"
        assert host_name not in b.command("snapshot")["friends"]
        room = a.command("create")["room"]
        assert room
        state = a.command("invite")
        assert state["choices"] == ["MANAGE FRIENDS"]
        a.press(0x4000)  # Manage without abandoning the original private-lobby invitation.
        for _ in range(3):
            state = a.press(0x20)
        assert state["section"] == 3
        a.press(0x4000)
        a.command(f"text {peer_name}")
        a.command("done")
        a.press(0x40)  # Send, separately from finishing the username.
        a.press(0x4000)
        state = a.command("finish")
        assert peer_name in state["outgoing"] and "needs to accept" in state["status"]
        state = b.wait(lambda s: host_name in s["incoming"])
        assert state["request_toasts"] == 1
        state = b.command("toggle")  # Toast shortcut opens Received Requests.
        assert state["section"] == 1
        b.press(0x4000)
        state = b.press(0x4000)
        assert state["actions"] == ["ACCEPT REQUEST", "DECLINE REQUEST"]
        assert "now friends" in b.command("finish")["status"]
        state = a.wait(lambda s: peer_name in s["friends"])
        assert state["friends_open"] and state["room"] == room
        a.press(0x2000)  # Dismiss request result.
        state = a.press(0x2000)  # Return to the original invitation picker.
        assert not state["friends_open"] and state["picker_open"] and peer_name in state["choices"]
        a.press(0x10)  # Newly accepted online friend, immediately actionable.
        a.press(0x4000)
        b.press(0x2000)  # Dismiss acceptance result.
        b.press(0x2000)  # Close management.
        b.wait(lambda s: s["invite_toast"] == host_name)
        state = b.command("receive")
        assert state["choices"] == [host_name]
        b.press(0x4000)
        state = b.command("join")
        assert state["room"] == room
        a.wait(lambda s: s["sent_callbacks"] == 1)
        report = {"passed": True, "room": room, "host": host_name, "peer": peer_name,
                  "checks": ["in-session request and acceptance", "request toast route", "live invite choices",
                             "preserved 24-byte room attachment", "single invitation completion callback",
                             "exact hidden room joined with two members"],
                  "scope": "Production HLE/UI and real RPCN; excludes rendered title and physical input acceptance"}
        (output / "result.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))
    finally:
        for player in (b, a):
            if player:
                player.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runner", type=Path, required=True)
    parser.add_argument("--host-profile", type=Path, required=True)
    parser.add_argument("--peer-profile", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("build-tests/friends-ui-live"))
    args = parser.parse_args()
    check(args.runner.resolve(), args.host_profile.resolve(), args.peer_profile.resolve(), args.output)
