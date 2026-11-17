#!/usr/bin/env python3
"""Run the native offline contract probe with all new sockets denied on Linux."""
import os
from pathlib import Path
import re
import resource
import subprocess
import unittest
from port import ROOT

SOCKET_GUARD = r'''
#include <errno.h>
#include <stddef.h>
#include <stdio.h>
#include <unistd.h>
#include <sys/prctl.h>
#include <sys/socket.h>
#include <sys/syscall.h>
#include <linux/filter.h>
#include <linux/seccomp.h>
int main(int argc,char** argv){
    struct sock_filter instructions[]={
        BPF_STMT(BPF_LD|BPF_W|BPF_ABS,offsetof(struct seccomp_data,nr)),
        BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,SYS_socket,0,1),
        BPF_STMT(BPF_RET|BPF_K,SECCOMP_RET_ERRNO|ENETUNREACH),
        BPF_STMT(BPF_RET|BPF_K,SECCOMP_RET_ALLOW)
    };
    struct sock_fprog program={4,instructions};
    if(argc<2 || prctl(PR_SET_NO_NEW_PRIVS,1,0,0,0) || prctl(PR_SET_SECCOMP,SECCOMP_MODE_FILTER,&program))return 125;
    if(socket(AF_INET,SOCK_STREAM,0)!=-1 || errno!=ENETUNREACH)return 125;
    fputs("[offline-sandbox] new sockets blocked\n",stderr);
    execv(argv[1],argv+1);return 126;
}
'''

class OfflineNetworkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory=ROOT/'build-tests/offline-network'
        cls.directory.mkdir(parents=True,exist_ok=True)
        source=cls.directory/'socket_guard.c'
        source.write_text(SOCKET_GUARD)
        cls.guard=cls.directory/'socket_guard'
        subprocess.run(['cc','-std=gnu17',str(source),'-o',str(cls.guard)],check=True,capture_output=True)
        resource.setrlimit(resource.RLIMIT_CORE,(0,0))

    def run_profile(self,hostile=False):
        env={k:v for k,v in os.environ.items() if not k.startswith(('PS3_','TB_'))}
        image=Path(os.environ.get('TB_TEST_ELF',str(ROOT/'build-smoke/synthetic/smoke.elf')))
        generated=image.parent
        env.update(PS3_PARAM_SFO=os.environ.get('TB_TEST_SFO',str(generated/'PARAM.SFO')),PS3_VFS_ROOT=str(generated),
                   PS3_HDD0_ROOT=str(self.directory/'dev_hdd0'),PS3_HDD1_ROOT=str(self.directory/'dev_hdd1'),
                   PS3_GAMEDATA_ROOT=str(self.directory),PS3_DEV_FLASH=str(self.directory/'dev_flash'),
                   TB_OFFLINE_PROBE='1',TB_OFFLINE_TRACE='1',SDL_AUDIODRIVER='dummy',SDL_VIDEODRIVER='dummy')
        if hostile:
            env.update(PS3_NET_ONLINE='1',PSNR_SERVER='unreachable.invalid:31313')
        command=[str(self.guard),os.environ.get('TB_TEST_RUNNER',str(ROOT/'build-smoke/TankBattlesRecomp')),str(image)]
        result=subprocess.run(command,cwd=ROOT,env=env,capture_output=True,timeout=15)
        log=(result.stdout+result.stderr).decode(errors='replace')
        label='hostile-env' if hostile else 'default'
        (self.directory/(label+'.log')).write_text(log)
        self.assertEqual(result.returncode,0,log[-2500:])
        self.assertIn('[offline-sandbox] new sockets blocked',log)
        self.assertIn('[offline-trace]',log)
        self.assertIn('name=net-dialog-event',log)
        count=re.search(r'\[offline-probe\] ALL PASS checks=(\d+)',log)
        self.assertIsNotNone(count,log[-2500:])
        self.assertGreaterEqual(int(count[1]),500)

    def test_default_offline_contract(self):
        self.run_profile()

    def test_online_environment_cannot_enable_network(self):
        self.run_profile(hostile=True)

if __name__=='__main__':
    unittest.main()
