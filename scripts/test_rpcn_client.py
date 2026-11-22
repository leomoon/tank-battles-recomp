#!/usr/bin/env python3
"""Exercise the native RPCN account commands against a local TLS fixture."""
import argparse
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
    parser.add_argument("--openssl", default="openssl")
    args = parser.parse_args()
    RpcnClientTests.runner = args.runner.resolve()
    RpcnClientTests.matching_runner = args.matching_runner.resolve() if args.matching_runner else None
    RpcnClientTests.openssl = args.openssl
    unittest.main(argv=[__file__], verbosity=2)
