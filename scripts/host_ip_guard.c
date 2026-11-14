/* Deny new IPv4/IPv6 sockets while retaining local display/audio IPC. */
#include <errno.h>
#include <stddef.h>
#include <stdio.h>
#include <unistd.h>
#include <sys/prctl.h>
#include <sys/socket.h>
#include <sys/syscall.h>
#include <linux/filter.h>
#include <linux/seccomp.h>
#include <linux/audit.h>
#if defined(__x86_64__)
#define HOST_ARCH AUDIT_ARCH_X86_64
#elif defined(__aarch64__)
#define HOST_ARCH AUDIT_ARCH_AARCH64
#else
#error Unsupported seccomp host architecture
#endif
int main(int argc,char** argv) {
    struct sock_filter instructions[]={
        BPF_STMT(BPF_LD|BPF_W|BPF_ABS,offsetof(struct seccomp_data,arch)),
        BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,HOST_ARCH,1,0),
        BPF_STMT(BPF_RET|BPF_K,SECCOMP_RET_KILL_PROCESS),
        BPF_STMT(BPF_LD|BPF_W|BPF_ABS,offsetof(struct seccomp_data,nr)),
#if defined(__x86_64__)
        BPF_JUMP(BPF_JMP|BPF_JSET|BPF_K,0x40000000,0,1),
        BPF_STMT(BPF_RET|BPF_K,SECCOMP_RET_ERRNO|ENETUNREACH),
#endif
        BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,SYS_socket,0,4),
        BPF_STMT(BPF_LD|BPF_W|BPF_ABS,offsetof(struct seccomp_data,args[0])),
        BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,AF_INET,1,0),
        BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,AF_INET6,0,1),
        BPF_STMT(BPF_RET|BPF_K,SECCOMP_RET_ERRNO|ENETUNREACH),
        BPF_STMT(BPF_RET|BPF_K,SECCOMP_RET_ALLOW)
    };
    struct sock_fprog program={sizeof(instructions)/sizeof(instructions[0]),instructions};
    if(argc<2 || prctl(PR_SET_NO_NEW_PRIVS,1,0,0,0) || prctl(PR_SET_SECCOMP,SECCOMP_MODE_FILTER,&program))return 125;
    for(int family=AF_INET;family<=AF_INET6;family+=AF_INET6-AF_INET) {
        if(socket(family,SOCK_STREAM,0)!=-1 || errno!=ENETUNREACH)return 125;
    }
    int local=socket(AF_UNIX,SOCK_STREAM,0);if(local<0)return 125;close(local);
    fputs("[offline-sandbox] IPv4/IPv6 denied; Unix IPC available\n",stderr);
    execv(argv[1],argv+1);perror("execv");return 126;
}
