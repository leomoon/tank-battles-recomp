"""Deterministic packet schedules and real UDP forwarding, without game data."""
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from online_network_lab import Policy, Scheduler, Relay


class Schedules(unittest.TestCase):
    def packets(self, policy):
        scheduler = Scheduler({'A-B': policy})
        for index in range(1, 101):
            scheduler.enqueue('A-B', index.to_bytes(2, 'big'), index * 10_000_000)
        return scheduler, scheduler.ready(10_000_000_000)

    def test_delay_jitter_loss_duplication_and_reordering_are_reproducible(self):
        policy = Policy(delay_ms=20, jitter_ms=7, drop_every=10,
                        duplicate_every=9, reorder_every=4, reorder_delay_ms=40)
        first, packets = self.packets(policy)
        _, repeated = self.packets(policy)
        self.assertEqual(packets, repeated)
        self.assertEqual(first.stats['A-B']['policy_dropped'], 10)
        self.assertEqual(first.stats['A-B']['duplicates'], 10)
        self.assertEqual(len(packets), 100)
        order = [p[3] for p in packets]
        self.assertNotEqual(order, sorted(order))
        for due, _, _, sequence, payload in packets:
            self.assertEqual(payload, sequence.to_bytes(2, 'big'))
            self.assertGreaterEqual(due, sequence * 10_000_000)
        self.assertTrue(any(n < 20_000_000 for n in policy.deliveries(1)))

    def test_zero_condition_is_identity_and_links_are_independent(self):
        scheduler = Scheduler({'A-B': Policy(), 'B-A': Policy(drop_every=1)})
        for link in ('A-B', 'B-A'):
            scheduler.enqueue(link, b'unchanged\0bytes', 42)
        ready = scheduler.ready(42)
        self.assertEqual([(r[2], r[4]) for r in ready], [('A-B', b'unchanged\0bytes')])
        self.assertEqual(scheduler.stats['B-A']['policy_dropped'], 1)

    def test_bounded_queue_and_profile_transition_do_not_retime_queued_packets(self):
        scheduler = Scheduler({'A-B': Policy(delay_ms=100)}, capacity=1)
        scheduler.enqueue('A-B', b'old', 0)
        scheduler.policies['A-B'] = Policy()
        scheduler.enqueue('A-B', b'overflow', 1)
        self.assertEqual(scheduler.stats['A-B']['overflow_dropped'], 1)
        self.assertEqual(scheduler.ready(99_000_000), [])
        self.assertEqual(scheduler.ready(100_000_000)[0][4], b'old')
        scheduler.enqueue('A-B', b'new', 101_000_000)
        self.assertEqual(scheduler.ready(101_000_000)[0][4], b'new')

    def test_invalid_policies_are_rejected(self):
        for kwargs in ({'delay_ms': -1}, {'jitter_ms': 1001}, {'drop_every': True},
                       {'seed': 2**32}, {'delay_ms': 1.2}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                Policy(**kwargs)

    def test_real_candidate_provenance_survives_reordered_and_duplicate_frames(self):
        compiler = shutil.which('g++') or shutil.which('clang++')
        if not compiler:
            self.skipTest('C++ compiler unavailable')
        root = Path(__file__).resolve().parents[1]
        source = r'''
#include "online_pacing_recovery.h"
#include <iostream>
#include <cassert>
int main() {
 unsigned frame;bool recovered=false;unsigned last=0;
 tb_pacing_recovery::RenderState state;state.role(33,16);
 uint64_t now=100;
 while(std::cin>>frame) {
  const unsigned remote=frame+1;
  state.observe(1,2,remote,frame,now,true);state.request(1,remote,frame,now);
  state.advance(2,remote);
  const bool used=state.consume(remote,now,true,true);
  const bool expected=!recovered || tb_pacing_recovery::newer(remote,last);
  assert(used==expected);
  if(used) {last=remote;recovered=true;}
  assert(!state.consume(remote,now,true,true));
  ++now;
 }
}
'''
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            (directory/'probe.cpp').write_text(source)
            subprocess.run([compiler, '-std=c++20', '-Wall', '-Wextra', '-Werror',
                            '-I'+str(root), str(directory/'probe.cpp'), '-o', str(directory/'probe')], check=True)
            for policy in (Policy(), Policy(delay_ms=100), Policy(jitter_ms=40),
                           Policy(drop_every=3), Policy(duplicate_every=2),
                           Policy(reorder_every=2, reorder_delay_ms=100)):
                _, packets = self.packets(policy)
                subprocess.run([str(directory/'probe')],
                               input=''.join(str(p[3])+'\n' for p in packets), text=True, check=True)


class SocketRelay(unittest.TestCase):
    @unittest.skipUnless(sys.platform.startswith('linux'), 'LD_PRELOAD adapter is Linux test tooling')
    def test_virtual_lan_changes_only_discovery_address_and_restores_peer_identity(self):
        compiler = shutil.which('cc')
        if not compiler:
            self.skipTest('C compiler unavailable')
        with tempfile.TemporaryDirectory() as temp, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as peer, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as discovery:
            peer.bind(('127.0.0.1', 0));peer.settimeout(3)
            discovery.bind(('127.0.0.1', 0));discovery.settimeout(3)
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as reserve:
                reserve.bind(('127.0.0.1', 0));local = reserve.getsockname()[1]
            library = Path(temp)/'ports.so'
            subprocess.run([compiler, '-shared', '-fPIC', '-Wall', '-Wextra', '-Werror',
                            str(Path(__file__).with_name('online_test_ports.c')), '-ldl', '-o', str(library)], check=True)
            env = dict(os.environ, LD_PRELOAD=str(library), TB_LAB_VIRTUAL_IP='127.0.0.101',
                       TB_LAB_HOST_IP='127.0.0.1', TB_LAB_LOCAL_PORT=str(local),
                       TB_LAB_DISCOVERY_PORT=str(discovery.getsockname()[1]),
                       TB_LAB_VIRTUAL_SEND_PORTS=f'0,{peer.getsockname()[1]},0',
                       TB_LAB_VIRTUAL_RECV_PORTS=f'0,{peer.getsockname()[1]},0')
            env.pop('TB_LAB_SEND_PORT', None);env.pop('TB_LAB_RECV_PORT', None)
            probe = """import socket,os
with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as s:
 s.bind(('127.0.0.1',3658));s.settimeout(3)
 s.sendto(bytes([1])+bytes(12),('127.0.0.1',int(os.environ['TB_LAB_DISCOVERY_PORT'])))
 s.sendto(b'opaque\\0packet',('127.0.0.102',3658))
 data,source=s.recvfrom(100)
 assert data==b'opaque\\0packet' and source==('127.0.0.102',3658)
"""
            child = subprocess.Popen([sys.executable, '-c', probe], env=env,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                registration, _ = discovery.recvfrom(100)
                self.assertEqual(registration, bytes([1])+bytes(8)+socket.inet_aton('127.0.0.101'))
                payload, address = peer.recvfrom(100)
                self.assertEqual(payload, b'opaque\0packet');self.assertEqual(address[1], local)
                peer.sendto(payload, address)
                output = child.communicate(timeout=4)
                self.assertEqual(child.returncode, 0, output)
            finally:
                if child.poll() is None:
                    child.kill();child.communicate()

    def test_udp_bytes_and_source_socket_survive_delay_and_duplication(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as target:
            target.bind(('127.0.0.1', 0));target.settimeout(2)
            relay = Relay([dict(name='A-B', listen=['127.0.0.1', 0],
                                target=list(target.getsockname()),
                                policy=dict(delay_ms=20, duplicate_every=1))])
            stop = threading.Event()
            thread = threading.Thread(target=relay.run, args=(stop,))
            thread.start()
            try:
                start = time.monotonic()
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                    sender.sendto(b'opaque\0packet', relay.sockets['A-B'].getsockname())
                for _ in range(2):
                    payload, source = target.recvfrom(100)
                    self.assertEqual(payload, b'opaque\0packet')
                    self.assertEqual(source, relay.sockets['A-B'].getsockname())
                    self.assertGreaterEqual(time.monotonic()-start, .018)
            finally:
                stop.set();thread.join(2);relay.close()
            self.assertFalse(thread.is_alive())
            self.assertEqual(relay.report()['links']['A-B']['delivered'], 2)

    def test_bad_control_preserves_last_policy_and_transition_is_atomic(self):
        with tempfile.TemporaryDirectory() as temp:
            control = Path(temp)/'control.json'
            relay = Relay([dict(name='A-B', listen=['127.0.0.1', 0],
                                target=['127.0.0.1', 9])], control)
            try:
                control.write_text('{"A-B":{"delay_ms":50}}');relay.refresh()
                self.assertEqual(relay.scheduler.policies['A-B'].delay_ms, 50)
                for value in ('{', '{}', '{"A-B":{"delay_ms":-1}}', 'null'):
                    control.write_text(value);relay.refresh()
                    self.assertEqual(relay.scheduler.policies['A-B'].delay_ms, 50)
                control.write_text('{"A-B":{"drop_every":2}}');relay.refresh()
                self.assertEqual(relay.scheduler.policies['A-B'], Policy(drop_every=2))
                self.assertEqual(relay.control_errors, 4)
            finally:
                relay.close()

    @unittest.skipUnless(sys.platform.startswith('linux'), 'LD_PRELOAD adapter is Linux test tooling')
    def test_native_adapter_routes_and_restores_ports_without_editing_payloads(self):
        compiler = shutil.which('cc')
        if not compiler:
            self.skipTest('C compiler unavailable')
        source = Path(__file__).with_name('online_test_ports.c')
        with tempfile.TemporaryDirectory() as temp, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as relay:
            library = Path(temp)/'ports.so'
            subprocess.run([compiler, '-shared', '-fPIC', '-Wall', '-Wextra', '-Werror',
                            str(source), '-ldl', '-o', str(library)], check=True)
            relay.bind(('127.0.0.1', 0));relay.settimeout(3)
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as reserve:
                reserve.bind(('127.0.0.1', 0));local = reserve.getsockname()[1]
            env = dict(os.environ, LD_PRELOAD=str(library), TB_LAB_LOCAL_PORT=str(local),
                       TB_LAB_SEND_PORT=str(relay.getsockname()[1]), TB_LAB_RECV_PORT=str(relay.getsockname()[1]))
            probe = """import socket
with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as s:
 s.bind(('127.0.0.1',3658));s.settimeout(3)
 assert s.sendto(b'opaque\\0packet',('127.0.0.1',3658))==13
 data,source=s.recvfrom(100)
 assert data==b'opaque\\0packet' and source==('127.0.0.1',3658)
"""
            child = subprocess.Popen([sys.executable, '-c', probe], env=env,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                payload, address = relay.recvfrom(100)
                self.assertEqual(address[1], local)
                self.assertEqual(payload, b'opaque\0packet')
                relay.sendto(payload, address)
                output = child.communicate(timeout=4)
                self.assertEqual(child.returncode, 0, output)
            finally:
                if child.poll() is None:
                    child.kill();child.communicate()


if __name__ == '__main__':
    unittest.main()
