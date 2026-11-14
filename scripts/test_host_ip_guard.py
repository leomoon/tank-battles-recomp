"""Verify IP rejection and real local IPC remain active after exec."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

class HostIPGuardTests(unittest.TestCase):
    def test_guard_survives_exec_without_blocking_local_ipc(self):
        with tempfile.TemporaryDirectory() as directory:
            guard=Path(directory)/'guard'
            subprocess.run(['cc','-std=gnu17',str(Path(__file__).with_name('host_ip_guard.c')),'-o',str(guard)],check=True)
            code='''import errno,socket
for family in (socket.AF_INET,socket.AF_INET6):
 try: socket.socket(family,socket.SOCK_STREAM)
 except OSError as e: assert e.errno==errno.ENETUNREACH
 else: raise AssertionError('IP socket allowed')
a,b=socket.socketpair();a.send(b'local');assert b.recv(5)==b'local'
s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);s.close()
'''
            result=subprocess.run([str(guard),sys.executable,'-c',code],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('IPv4/IPv6 denied',result.stderr)

if __name__=='__main__':unittest.main()
