"""Exercise two native friend controllers against a disposable RPCN TLS fixture."""
import argparse
import json
from pathlib import Path
import queue
import socket
import ssl
import struct
import subprocess
import tempfile
import threading
import time
import unittest

from test_rpcn_client import packet, request, relationship_login


class FriendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="tb-friends-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        subprocess.run([self.openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-sha256", "-days", "1",
                        "-subj", "/CN=localhost", "-addext", "subjectAltName=IP:127.0.0.1",
                        "-keyout", str(self.root / "key.pem"), "-out", str(self.root / "cert.pem")],
                       capture_output=True, check=True, timeout=20)
        self.tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.tls.load_cert_chain(self.root / "cert.pem", self.root / "key.pem")
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen()
        self.listener.settimeout(.1)
        self.port = self.listener.getsockname()[1]
        self.lock = threading.RLock()
        self.clients = {}
        self.pending = set()
        self.users = {"PlayerA", "PlayerB", "OfflineUser", "LostUser", "BlockedUser", "ErrorUser"}
        self.connections = []
        self.friendships = set()
        self.errors = []
        self.closed = threading.Event()
        self.slow_barrier = threading.Event()
        self.threads = []
        self.processes = []
        self.addCleanup(self.cleanup)
        self.acceptor = threading.Thread(target=self.accept, daemon=True)
        self.acceptor.start()

    def cleanup(self):
        for process in self.processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=3)
            process.stdin.close()
            process.stdout.close()
            process.stderr.close()
        self.closed.set()
        self.acceptor.join(timeout=2)
        self.listener.close()
        for stream in self.connections:
            stream.close()
        for thread in self.threads:
            thread.join(timeout=2)

    def accept(self):
        while not self.closed.is_set():
            try:
                tcp, _ = self.listener.accept()
            except socket.timeout:
                continue
            thread = threading.Thread(target=self.serve, args=(tcp,), daemon=True)
            self.threads.append(thread)
            thread.start()

    def notify(self, name, command, body):
        if name in self.clients:
            self.clients[name].sendall(packet(2, command, 0, body))

    def serve(self, tcp):
        user = None
        try:
            tcp.settimeout(12)
            with tcp, self.tls.wrap_socket(tcp, server_side=True) as stream, stream.makefile("rb") as reader:
                self.connections.append(stream)
                stream.sendall(packet(3, 0, 0, struct.pack("<I", 34)))
                while not self.closed.is_set():
                    command, serial, data = request(reader)
                    with self.lock:
                        code = 0
                        if command == 0:
                            fields = data.split(b"\0")
                            user = fields[0].decode()
                            self.assertIn(user, self.users)
                            self.assertEqual(fields[1], b"fixture-secret")
                            self.assertNotIn(user, self.clients)
                            friends = [(next(iter(pair - {user})), next(iter(pair - {user})) in self.clients)
                                       for pair in self.friendships if user in pair]
                            outgoing = [b for a, b in self.pending if a == user]
                            incoming = [a for a, b in self.pending if b == user]
                            self.clients[user] = stream
                            stream.sendall(packet(1, command, serial, relationship_login(friends, outgoing, incoming)))
                            continue
                        if command == 1:
                            stream.sendall(packet(1, command, serial, b"\0"))
                            return
                        if command == 12:
                            if self.slow_barrier.is_set():
                                self.slow_barrier.clear()
                                time.sleep(.15)
                            stream.sendall(packet(1, command, serial, b"\0"))
                            continue
                        self.assertIn(command, (8, 9))
                        self.assertTrue(data.endswith(b"\0") and data.count(b"\0") == 1)
                        peer = data[:-1].decode()
                        pair = frozenset((user, peer))
                        if peer not in self.users:
                            code = 26
                        elif peer == "BlockedUser":
                            code = 27
                        elif peer == "ErrorUser":
                            code = 24
                        elif command == 8:
                            if pair in self.friendships or (user, peer) in self.pending:
                                code = 28
                            elif (peer, user) in self.pending:
                                self.pending.discard((peer, user))
                                self.friendships.add(pair)
                            else:
                                self.pending.add((user, peer))
                        elif pair not in self.friendships and (user, peer) not in self.pending and (peer, user) not in self.pending:
                            code = 26
                        else:
                            self.friendships.discard(pair)
                            self.pending.discard((user, peer))
                            self.pending.discard((peer, user))
                        if peer == "LostUser":
                            return  # Applied, but acknowledgement is deliberately lost.
                        stream.sendall(packet(1, command, serial, bytes([code])))
                        if not code:
                            if command == 9:
                                self.notify(user, 7, peer.encode() + b"\0")
                                self.notify(peer, 7, user.encode() + b"\0")
                            elif pair in self.friendships:
                                self.notify(user, 6, bytes([peer in self.clients]) + peer.encode() + b"\0")
                                self.notify(peer, 6, b"\1" + user.encode() + b"\0")
                            else:
                                self.notify(peer, 5, user.encode() + b"\0")
        except (ConnectionError, ssl.SSLError, struct.error):
            pass  # A fixture process can close after a deliberate transport failure.
        except Exception as error:
            self.errors.append(error)
        finally:
            with self.lock:
                if user:
                    self.clients.pop(user, None)

    def start(self, user):
        profile = self.root / f"{user}.ini"
        profile.write_text(f"endpoint=127.0.0.1:{self.port}\nnpid={user}\npassword=fixture-secret\nca=cert.pem\n")
        process = subprocess.Popen([str(self.runner), str(profile)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, bufsize=1)
        self.processes.append(process)
        output = queue.Queue()
        threading.Thread(target=lambda: [output.put(line) for line in process.stdout], daemon=True).start()
        process.fixture_output = output
        self.assertEqual(self.read(process)["result"], "ready")
        return process

    def read(self, process):
        try:
            return json.loads(process.fixture_output.get(timeout=8))
        except queue.Empty:
            raise AssertionError("Timed out waiting for the native friend controller")

    def command(self, process, action, expected=None):
        process.stdin.write(action + "\n")
        process.stdin.flush()
        result = self.read(process)
        if expected:
            self.assertEqual(result["result"], expected)
        return result

    def snapshot(self, process):
        return self.command(process, "snapshot", "snapshot")

    def test_request_accept_decline_cancel_remove_and_failures(self):
        a, b = self.start("PlayerA"), self.start("PlayerB")
        sent = self.command(a, "add PlayerB", "sent")
        self.assertEqual(sent["outgoing"], ["PlayerB"])
        self.assertEqual(self.snapshot(b)["incoming"], ["PlayerA"])
        self.command(a, "add playerb", "already_pending")
        self.command(b, "accept playera", "accepted")
        self.assertEqual(self.snapshot(a)["friends"], ["PlayerB"])
        state = self.snapshot(b)
        self.assertEqual(state["friends"], ["PlayerA"])
        self.assertEqual(state["online"], [True])
        self.command(b, "remove PlayerA", "confirmation_required")
        self.command(b, "remove-confirmed PlayerA", "removed")
        self.assertEqual(self.snapshot(a)["friends"], [])
        self.command(a, "add PlayerB", "sent")
        self.assertEqual(self.snapshot(b)["incoming"], ["PlayerA"])
        self.command(b, "decline PlayerA", "declined")
        self.assertEqual(self.snapshot(a)["outgoing"], [])
        self.command(b, "add PlayerA", "sent")
        self.command(b, "cancel PlayerA", "canceled")
        self.assertEqual(self.snapshot(a)["incoming"], [])
        self.command(a, "add OfflineUser", "sent")
        self.assertEqual(self.snapshot(a)["outgoing"], ["OfflineUser"])
        self.command(a, "cancel OfflineUser", "canceled")
        self.slow_barrier.set()
        expired = self.command(a, "expire-queued PlayerB", "expired")
        self.assertEqual(expired["outgoing"], [])
        self.assertNotIn(("PlayerA", "PlayerB"), self.pending)
        for peer, expected in [("MissingUser", "not_found"), ("BlockedUser", "blocked"), ("ErrorUser", "rejected")]:
            rejected = self.command(a, f"add {peer}", expected)
            self.assertEqual(rejected["friends"], [])
            self.assertEqual(rejected["outgoing"], [])
        lost = self.command(a, "add LostUser", "unknown")
        self.assertEqual(lost["outgoing"], [])
        self.assertIn(("PlayerA", "LostUser"), self.pending)
        recovered = self.command(a, "reconnect", "reconnected")
        self.assertEqual(recovered["generation"], lost["generation"] + 1)
        self.assertEqual(recovered["outgoing"], ["LostUser"])
        self.command(a, "add LostUser", "already_pending")
        self.assertEqual(self.errors, [])

    def test_reciprocal_requests_become_mutual_without_restart(self):
        a, b = self.start("PlayerA"), self.start("PlayerB")
        for process, peer in ((a, "PlayerB"), (b, "PlayerA")):
            process.stdin.write(f"add {peer}\n")
            process.stdin.flush()
        results = [self.read(a), self.read(b)]
        self.assertTrue(all(result["result"] in ("sent", "accepted") for result in results))
        self.assertEqual(self.snapshot(a)["friends"], ["PlayerB"])
        self.assertEqual(self.snapshot(b)["friends"], ["PlayerA"])
        self.assertEqual(self.snapshot(a)["outgoing"], [])
        self.assertEqual(self.snapshot(b)["incoming"], [])
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runner", required=True, type=Path)
    parser.add_argument("--openssl", default="openssl")
    args = parser.parse_args()
    FriendTests.runner = args.runner.resolve()
    FriendTests.openssl = args.openssl
    unittest.main(argv=[__file__], verbosity=2)
