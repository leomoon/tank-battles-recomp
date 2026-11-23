#!/usr/bin/env python3
"""Exercise the native RPCN account commands against a local TLS fixture."""
import argparse
import json
from pathlib import Path
import socket
import ssl
import struct
import subprocess
import tempfile
import threading
import time
import unittest


HEADER = struct.Struct("<BHIQ")


def packet(kind, command, request_id, data):
    return HEADER.pack(kind, command, HEADER.size + len(data), request_id) + data


def request(reader):
    kind, command, size, request_id = HEADER.unpack(reader.read(HEADER.size))
    if kind != 0 or not HEADER.size <= size <= 4096:
        raise AssertionError("Unexpected request header")
    return command, request_id, reader.read(size - HEADER.size)


def relationship_login(friends=(), outgoing=(), incoming=(), blocked=()):
    body = b"\0FixturePlayer\0\0" + struct.pack("<QI", 42, len(friends))
    for name, online in friends:
        body += name.encode() + b"\0" + bytes([online]) + bytes(12) + bytes(3) + bytes(4)
    for names in (outgoing, incoming, blocked):
        body += struct.pack("<I", len(names))
        body += b"".join(name.encode() + b"\0" for name in names)
    return body


def relationship_snapshots(result):
    return [json.loads(line[len("[relationships] "):]) for line in result.stdout.splitlines()
            if line.startswith("[relationships] ")]


class RpcnClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not hasattr(cls, "runner"):
            raise unittest.SkipTest("Run with --runner to select the native executable")
        cls.tmp = tempfile.TemporaryDirectory(prefix="tb-rpcn-test-")
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.directory = Path(cls.tmp.name)
        subprocess.run(
            [cls.openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-sha256",
             "-days", "1", "-subj", "/CN=localhost", "-addext",
             "subjectAltName=DNS:localhost,IP:127.0.0.1",
             "-keyout", str(cls.directory / "key.pem"), "-out", str(cls.directory / "cert.pem")],
            check=True, capture_output=True, timeout=20,
        )
        cls.tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        cls.tls.load_cert_chain(cls.directory / "cert.pem", cls.directory / "key.pem")

    def run_client(self, action, exchange, *, expected_error=None, trust_fixture=True, version=34,
                   stale_login_attempts=0, busy_seconds=0, runner=None, expected_result=None,
                   fields="password=fixture-secret\nemail=fixture@example.invalid\n"):
        errors = []
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            listener.settimeout(6)
            port = listener.getsockname()[1]

            def serve():
                try:
                    first_login = None
                    attempt = 0
                    while True:
                        with listener.accept()[0] as tcp:
                            tcp.settimeout(6)
                            with self.tls.wrap_socket(tcp, server_side=True) as stream:
                                for byte in packet(3, 0, 0, struct.pack("<I", version)):
                                    stream.sendall(bytes([byte]))
                                if exchange:
                                    with stream.makefile("rb") as reader:
                                        now = time.monotonic()
                                        if first_login is None:
                                            first_login = now
                                        if attempt < stale_login_attempts or now < first_login + busy_seconds:
                                            command, request_id, _ = request(reader)
                                            self.assertEqual(command, 0)
                                            stream.sendall(packet(1, command, request_id, b"\x06"))
                                            attempt += 1
                                            continue
                                        else:
                                            exchange(stream, reader)
                        break
                except (ssl.SSLError, ConnectionError) as error:
                    if trust_fixture:
                        errors.append(error)
                except Exception as error:
                    errors.append(error)

            profile = self.directory / "profile.ini"
            profile.write_text(
                f"endpoint=127.0.0.1:{port}\nnpid=FixturePlayer\n" + fields +
                ("ca=cert.pem\n" if trust_fixture else ""),
                encoding="utf-8",
            )
            server = threading.Thread(target=serve, daemon=True)
            server.start()
            result = subprocess.run([str(runner or self.runner), action, str(profile)], text=True,
                                    capture_output=True, timeout=17)
            server.join(timeout=7)
            self.assertFalse(server.is_alive())
            self.assertEqual(errors, [])
            for secret in ("fixture-secret", "fixture-reset-code"):
                self.assertNotIn(secret, result.stdout + result.stderr)
            if expected_result:
                self.assertIn(f"[rpcn-result] {expected_result}", result.stdout.splitlines())
            if expected_error:
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn(expected_error, result.stderr)
            else:
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return result

    def relationship_client(self, login, stages=(), early=()):
        if self.relationships_runner is None:
            self.skipTest("Run with --relationships-runner for friendship state checks")

        def exchange(stream, reader):
            command, request_id, _ = request(reader)
            self.assertEqual(command, 0)
            for kind, data in early:
                stream.sendall(packet(2, kind, 0, data))
            stream.sendall(packet(1, command, request_id, login))
            for index, notices in enumerate([(), *stages]):
                command, request_id, _ = request(reader)
                # Malformed login/notifications cause disconnect before another barrier.
                self.assertEqual(command, 12)
                for kind, data in notices:
                    stream.sendall(packet(2, kind, 0, data))
                stream.sendall(packet(1, command, request_id, bytes([0, index < len(stages)])))
            command, request_id, _ = request(reader)
            self.assertEqual(command, 1)
            stream.sendall(packet(1, command, request_id, b"\0"))

        return relationship_snapshots(self.run_client("--relationships", exchange, runner=self.relationships_runner))

    def test_relationship_login_pending_and_early_requests(self):
        snapshots = self.relationship_client(
            relationship_login([("OnlineFriend", True), ("OfflineFriend", False)], ["SentUser"], ["ReceivedUser"], ["BlockedUser"]),
            early=[(5, b"EarlyUser\0")])
        initial, cleared = snapshots
        self.assertEqual(initial["friends"], [["OnlineFriend", True, 0], ["OfflineFriend", False, 0]])
        self.assertEqual(initial["outgoing"], ["SentUser"])
        self.assertEqual(initial["incoming"], ["ReceivedUser", "EarlyUser"])
        self.assertTrue(initial["endpoint"].startswith("127.0.0.1:"))
        self.assertEqual(initial["npid"], "FixturePlayer")
        self.assertGreater(initial["generation"], 0)
        self.assertEqual(cleared["friends"], [])
        self.assertEqual(cleared["incoming"], [])
        self.assertEqual(cleared["outgoing"], [])
        self.assertEqual(cleared["npid"], "")
        self.assertGreater(cleared["revision"], initial["revision"])

    def test_relationship_notifications_and_interleaved_other_traffic(self):
        presence = lambda timestamp, online: bytes([online]) + struct.pack("<Q", timestamp) + b"SentUser\0"
        snapshots = self.relationship_client(relationship_login([], ["SentUser"], ["ReceivedUser"]), [
            [(5, b"NewUser\0"), (5, b"NewUser\0")],
            [(6, b"\1SentUser\0"), (6, b"\1ReceivedUser\0")],
            [(8, presence(200, False))],
            [(8, presence(100, True)), (8, presence(200, True)), (6, b"\1SentUser\0")],
            [(0, b"room data"), (5, b"Interleaved\0"), (9, b"room message"),
             (10, b"invitation data"), (5, b"Interleaved\0")],
            [(7, b"SentUser\0"), (7, b"NewUser\0")],
            [(7, b"SentUser\0"), (7, b"NewUser\0")],
        ])
        self.assertEqual(snapshots[1]["incoming"], ["ReceivedUser", "NewUser"])
        self.assertEqual(snapshots[2]["outgoing"], [])
        self.assertEqual(snapshots[2]["incoming"], ["NewUser"])
        self.assertEqual(snapshots[3]["friends"][0], ["SentUser", False, 200])
        self.assertEqual(snapshots[4]["friends"][0], ["SentUser", False, 200])
        self.assertEqual(snapshots[4]["revision"], snapshots[3]["revision"])
        self.assertEqual(snapshots[5]["room_notices"], 2)
        self.assertEqual(snapshots[5]["invite_notices"], 1)
        self.assertEqual(snapshots[5]["incoming"], ["NewUser", "Interleaved"])
        self.assertGreater(snapshots[5]["revision"], snapshots[4]["revision"])
        self.assertEqual(snapshots[6]["friends"], [["ReceivedUser", True, 0]])
        self.assertEqual(snapshots[6]["incoming"], ["Interleaved"])
        self.assertEqual(snapshots[7]["revision"], snapshots[6]["revision"])

    def test_malformed_relationship_login_is_rejected_without_partial_state(self):
        if self.relationships_runner is None:
            self.skipTest("Run with --relationships-runner")
        valid = relationship_login([("GoodFriend", True)], ["SentUser"], ["ReceivedUser"])
        invalid = [valid[:-1], valid + b"trailing", relationship_login([("bad/name", True)]),
                   relationship_login([("FriendA", 2)]), relationship_login([], ["Bad@Name"]),
                   relationship_login([("FriendA", True), ("frienda", False)]),
                   relationship_login([], [], [], ["Bad@Name"]),
                   relationship_login([], ["SameUser", "sameuser"]),
                   valid[:len(b"\0FixturePlayer\0\0") + 8] + struct.pack("<I", 1025)]
        for login in invalid:
            with self.subTest(login_size=len(login)):
                def exchange(stream, reader):
                    command, request_id, _ = request(reader)
                    stream.sendall(packet(1, command, request_id, login))
                    self.assertEqual(stream.recv(1), b"")
                result = self.run_client("--relationships", exchange, runner=self.relationships_runner)
                snapshot, = relationship_snapshots(result)
                self.assertEqual(snapshot["friends"], [])
                self.assertEqual(snapshot["incoming"], [])
                self.assertEqual(snapshot["npid"], "")

    def test_malformed_friend_notification_disconnects_and_clears_state(self):
        if self.relationships_runner is None:
            self.skipTest("Run with --relationships-runner")
        for kind, body in [(5, b"MissingTerminator"), (6, b"\2FriendA\0"),
                           (7, b"FriendA\0trailing"), (8, b"\1short")]:
            with self.subTest(kind=kind):
                def exchange(stream, reader):
                    command, request_id, _ = request(reader)
                    stream.sendall(packet(1, command, request_id, relationship_login([("FriendA", True)])))
                    command, request_id, _ = request(reader)
                    stream.sendall(packet(1, command, request_id, b"\0\1"))
                    request(reader)
                    stream.sendall(packet(2, kind, 0, body))
                    self.assertEqual(stream.recv(1), b"")
                result = self.run_client("--relationships", exchange, runner=self.relationships_runner)
                first, cleared = relationship_snapshots(result)
                self.assertEqual(first["friends"], [["FriendA", True, 0]])
                self.assertEqual(cleared["friends"], [])
                self.assertEqual(cleared["npid"], "")

    def test_relationship_state_isolated_between_servers_with_same_username(self):
        if self.relationships_runner is None:
            self.skipTest("Run with --relationships-runner")
        errors = []
        listeners = [socket.socket(), socket.socket()]
        for listener in listeners:
            self.addCleanup(listener.close)
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            listener.settimeout(6)
        profiles = []
        for index, listener in enumerate(listeners):
            profile = self.directory / f"server-{index}.ini"
            profile.write_text(f"endpoint=127.0.0.1:{listener.getsockname()[1]}\nnpid=FixturePlayer\n"
                               "password=fixture-secret\nca=cert.pem\n", encoding="utf-8")
            profiles.append(profile)

        def serve():
            try:
                for index, listener in enumerate(listeners):
                    with listener.accept()[0] as tcp:
                        tcp.settimeout(6)
                        with self.tls.wrap_socket(tcp, server_side=True) as stream, stream.makefile("rb") as reader:
                            stream.sendall(packet(3, 0, 0, struct.pack("<I", 34)))
                            command, request_id, _ = request(reader)
                            login = relationship_login([("FriendA" if index == 0 else "FriendB", True)],
                                                       ["SentA"] if index == 0 else [], ["IncomingB"] if index else [])
                            stream.sendall(packet(1, command, request_id, login))
                            command, request_id, _ = request(reader)
                            stream.sendall(packet(1, command, request_id, b"\0\0"))
                            command, request_id, _ = request(reader)
                            if index == 0:
                                # Queue these during logout, after the last snapshot.
                                # Disconnect must clear them before the next login.
                                stream.sendall(packet(2, 0, 0, b"old room"))
                                stream.sendall(packet(2, 10, 0, b"old invitation"))
                            stream.sendall(packet(1, command, request_id, b"\0"))
            except Exception as error:
                errors.append(error)

        server = threading.Thread(target=serve, daemon=True)
        server.start()
        result = subprocess.run([str(self.relationships_runner), "--relationships", *map(str, profiles)],
                                capture_output=True, text=True, timeout=17)
        server.join(timeout=7)
        self.assertFalse(server.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        first, cleared, second, end = relationship_snapshots(result)
        self.assertNotEqual(first["endpoint"], second["endpoint"])
        self.assertEqual(first["npid"], second["npid"])
        self.assertGreater(second["generation"], first["generation"])
        self.assertGreater(second["revision"], cleared["revision"])
        self.assertEqual(second["friends"], [["FriendB", True, 0]])
        self.assertEqual(second["outgoing"], [])
        self.assertEqual(second["incoming"], ["IncomingB"])
        self.assertEqual(cleared["invite_notices"], 0)
        self.assertEqual(cleared["room_notices"], 0)
        self.assertEqual(second["invite_notices"], 0)
        self.assertEqual(second["room_notices"], 0)
        self.assertEqual(end["invite_notices"], 0)
        self.assertEqual(end["room_notices"], 0)
        self.assertEqual(end["endpoint"], "")

    def login_notification_and_logout(self, stale_login_attempts=0, *, retry_runner=None):
        def exchange(stream, reader):
            self.assertEqual(request(reader), (0, 1, b"FixturePlayer\0fixture-secret\0\0"))
            stream.sendall(packet(2, 0, 0, b"ignored notification"))
            login = b"\0FixturePlayer\0\0" + struct.pack("<QIIII", 42, 0, 0, 0, 0)
            stream.sendall(packet(1, 0, 1, login))
            self.assertEqual(request(reader), (1, 2, b""))
            stream.sendall(packet(1, 1, 2, b"\0"))
        self.run_client("--login-retry" if retry_runner else "--rpcn-check", exchange,
                        stale_login_attempts=stale_login_attempts,
                        busy_seconds=6 if retry_runner else 0, runner=retry_runner)

    def test_login_notification_and_logout(self):
        self.login_notification_and_logout()

    def test_quick_relaunch_retries_login_on_a_fresh_connection(self):
        self.login_notification_and_logout(stale_login_attempts=2)

    def test_in_game_retry_after_initial_login_expires(self):
        if self.matching_runner is None:
            self.skipTest("Run with --matching-runner to exercise the in-game worker")
        self.login_notification_and_logout(retry_runner=self.matching_runner)

    def test_account_create(self):
        def exchange(stream, reader):
            command, request_id, payload = request(reader)
            self.assertEqual((command, request_id), (2, 1))
            fields = payload.split(b"\0")
            self.assertEqual(fields[:3], [b"FixturePlayer", b"fixture-secret", b"FixturePlayer"])
            self.assertEqual(fields[-2:], [b"fixture@example.invalid", b""])
            stream.sendall(packet(1, 2, 1, b"\0"))
        self.run_client("--rpcn-create-account", exchange)

    def test_request_reset_code_without_a_password(self):
        def exchange(stream, reader):
            self.assertEqual(request(reader), (5, 1, b"FixturePlayer\0fixture@example.invalid\0"))
            stream.sendall(packet(1, 5, 1, b"\0"))
        self.run_client("--rpcn-request-reset", exchange, expected_result="reset_sent",
                        fields="email=fixture@example.invalid\n")

    def test_reset_password_and_reject_invalid_code(self):
        for status, result in [(0, "password_reset"), (5, "reset_code_rejected")]:
            with self.subTest(status=status):
                def exchange(stream, reader):
                    self.assertEqual(request(reader), (6, 1, b"FixturePlayer\0fixture-reset-code\0fixture-secret\0"))
                    stream.sendall(packet(1, 6, 1, bytes([status])))
                self.run_client("--rpcn-reset-password", exchange, expected_result=result,
                                expected_error="request rejected" if status else None,
                                fields="reset_code=fixture-reset-code\npassword=fixture-secret\n")

    def test_incorrect_password(self):
        def exchange(stream, reader):
            command, request_id, _ = request(reader)
            stream.sendall(packet(1, command, request_id, b"\x08"))
        self.run_client("--rpcn-check", exchange, expected_error="incorrect password")

    def test_wrong_request_id(self):
        def exchange(stream, reader):
            command, request_id, _ = request(reader)
            stream.sendall(packet(1, command, request_id + 1, b"\0"))
        self.run_client("--rpcn-check", exchange, expected_error="unexpected command or request ID")

    def test_wrong_protocol_version(self):
        self.run_client("--rpcn-check", None, version=33, expected_error="protocol mismatch")

    def test_untrusted_server_certificate(self):
        self.run_client("--rpcn-check", None, trust_fixture=False, expected_error="certificate")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runner", required=True, type=Path)
    parser.add_argument("--matching-runner", type=Path)
    parser.add_argument("--relationships-runner", type=Path)
    parser.add_argument("--openssl", default="openssl")
    args = parser.parse_args()
    RpcnClientTests.runner = args.runner.resolve()
    RpcnClientTests.matching_runner = args.matching_runner.resolve() if args.matching_runner else None
    RpcnClientTests.relationships_runner = args.relationships_runner.resolve() if args.relationships_runner else None
    RpcnClientTests.openssl = args.openssl
    unittest.main(argv=[__file__], verbosity=2)
