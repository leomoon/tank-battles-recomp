#!/usr/bin/env python3
"""Explicit test-only UDP relay with deterministic per-link impairments.

Not imported by the game or builders. It changes only datagrams explicitly sent
to its sockets. Packet bytes are forwarded unchanged and never written to logs.
"""
import argparse
import collections
import dataclasses
import heapq
import ipaddress
import json
from pathlib import Path
import selectors
import signal
import socket
import threading
import time


@dataclasses.dataclass(frozen=True)
class Policy:
    delay_ms: int = 0
    jitter_ms: int = 0
    drop_every: int = 0
    duplicate_every: int = 0
    reorder_every: int = 0
    reorder_delay_ms: int = 25
    seed: int = 1

    def __post_init__(self):
        for field in dataclasses.fields(self):
            value = getattr(self, field.name)
            maximum = 1000 if field.name.endswith('_ms') else 0xffffffff
            if type(value) is not int or not 0 <= value <= maximum:
                raise ValueError('Invalid network policy field: ' + field.name)

    def deliveries(self, sequence):
        if self.drop_every and sequence % self.drop_every == 0:
            return []
        # Stateless integer mixing makes runs independent of thread scheduling.
        x = (sequence ^ self.seed) & 0xffffffff
        x = ((x ^ (x >> 16)) * 0x45d9f3b) & 0xffffffff
        jitter = x % (2 * self.jitter_ms + 1) - self.jitter_ms
        delay = max(0, self.delay_ms + jitter)
        if self.reorder_every and sequence % self.reorder_every == 0:
            delay += self.reorder_delay_ms
        result = [delay * 1_000_000]
        if self.duplicate_every and sequence % self.duplicate_every == 0:
            result.append(result[0] + 2_000_000)
        return result


class Scheduler:
    def __init__(self, policies, capacity=4096):
        self.policies = dict(policies)
        self.capacity = capacity
        self.queue = []
        self.sequence = collections.Counter()
        self.stats = {name: collections.Counter() for name in policies}
        self.serial = 0

    def enqueue(self, link, payload, now_ns):
        self.sequence[link] += 1
        sequence = self.sequence[link]
        stats = self.stats[link]
        stats['received'] += 1
        delays = self.policies[link].deliveries(sequence)
        if not delays:
            stats['policy_dropped'] += 1
            return
        if len(self.queue) + len(delays) > self.capacity:
            stats['overflow_dropped'] += 1
            return
        stats['duplicates'] += len(delays) - 1
        for delay in delays:
            self.serial += 1
            heapq.heappush(self.queue, (now_ns + delay, self.serial, link,
                                        sequence, bytes(payload)))

    def ready(self, now_ns):
        result = []
        while self.queue and self.queue[0][0] <= now_ns:
            result.append(heapq.heappop(self.queue))
        return result


def endpoint(value):
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError('Endpoint must be [IPv4 address, port]')
    ipaddress.IPv4Address(value[0])
    if type(value[1]) is not int or not 0 <= value[1] <= 65535:
        raise ValueError('Invalid UDP port')
    return tuple(value)


class Relay:
    def __init__(self, links, control=None):
        self.control = Path(control) if control else None
        self.selector = selectors.DefaultSelector()
        self.sockets = {}
        self.targets = {}
        self.control_errors = 0
        self.control_version = None
        self.policies = {}
        try:
            for link in links:
                name = link['name']
                if not isinstance(name, str) or not name or name in self.sockets:
                    raise ValueError('Unique nonempty link names required')
                self.policies[name] = Policy(**link.get('policy', {}))
                self.targets[name] = endpoint(link['target'])
                if not self.targets[name][1]:
                    raise ValueError('Target port must be nonzero')
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self.sockets[name] = sock
                sock.bind(endpoint(link['listen']))
                sock.setblocking(False)
                self.selector.register(sock, selectors.EVENT_READ, name)
        except Exception:
            self.close()
            raise
        self.scheduler = Scheduler(self.policies)

    def close(self):
        self.selector.close()
        for sock in self.sockets.values():
            sock.close()

    def refresh(self):
        if not self.control:
            return
        try:
            data = self.control.read_bytes()
            if data == self.control_version:
                return
            self.control_version = data
            values = json.loads(data)
            if not isinstance(values, dict) or set(values) != set(self.policies):
                raise ValueError('Control must define every link')
            updated = {name: Policy(**value) for name, value in values.items()}
            self.scheduler.policies = updated
        except (OSError, ValueError, TypeError):
            self.control_errors += 1  # Preserve the last complete valid profile.

    def run(self, stop):
        refresh_at = 0
        while not stop.is_set():
            now = time.monotonic_ns()
            if now >= refresh_at:
                self.refresh()
                refresh_at = now + 100_000_000
            for due, _, name, _, payload in self.scheduler.ready(now):
                stats = self.scheduler.stats[name]
                try:
                    sent = self.sockets[name].sendto(payload, self.targets[name])
                    if sent != len(payload):
                        raise OSError('Short UDP send')
                    stats['delivered'] += 1
                    stats['max_late_ns'] = max(stats['max_late_ns'], time.monotonic_ns() - due)
                except OSError:
                    stats['send_failed'] += 1
            wait = .02
            if self.scheduler.queue:
                wait = min(wait, max(0, (self.scheduler.queue[0][0] - time.monotonic_ns()) / 1e9))
            for key, _ in self.selector.select(wait):
                try:
                    payload, _ = key.fileobj.recvfrom(65535)
                    self.scheduler.enqueue(key.data, payload, time.monotonic_ns())
                except BlockingIOError:
                    pass

    def report(self):
        return dict(links={name: dict(stats) for name, stats in self.scheduler.stats.items()},
                    queued_at_stop=len(self.scheduler.queue), control_errors=self.control_errors)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--control', type=Path)
    parser.add_argument('--stats', type=Path, required=True)
    args = parser.parse_args()
    relay = Relay(json.loads(args.config.read_text())['links'], args.control)
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    print('Ready: ' + ', '.join(relay.sockets), flush=True)
    try:
        relay.run(stop)
    finally:
        args.stats.write_text(json.dumps(relay.report(), indent=2) + '\n')
        relay.close()


if __name__ == '__main__':
    main()
