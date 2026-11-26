/* Linux test-only two-instance port adapter for online_network_lab.py.
 * No delays, queues or packet edits here. Real send/receive return codes pass
 * through. Build separately with cc -shared -fPIC ... -ldl. Never packaged.
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <netinet/in.h>
#include <stdlib.h>
#include <sys/socket.h>
#include <string.h>
#include <arpa/inet.h>
#include <stdio.h>

/* Optional three-peer virtual LAN: advertise a distinct 127.0.0.101..103
 * address in the RPCN discovery registration and map it to a real local port.
 * Only address metadata is translated. Gameplay datagram bytes stay intact.
 */
static int virtual_self(struct in_addr* address) {
    const char* value=getenv("TB_LAB_VIRTUAL_IP");
    if(!value || inet_pton(AF_INET,value,address)!=1)return 0;
    unsigned n=ntohl(address->s_addr);
    return n>=0x7f000065u && n<=0x7f000067u;
}
static unsigned virtual_port(const char* name,unsigned index) {
    const char* value=getenv(name);unsigned a=0,b=0,c=0;
    if(!value || sscanf(value,"%u,%u,%u",&a,&b,&c)!=3)return 0;
    unsigned ports[3]={a,b,c};return ports[index]<=65535 ? ports[index] : 0;
}

static unsigned port(const char* name) {
    const char* value=getenv(name);
    if(!value || !*value)return 0;
    char* end;unsigned long parsed=strtoul(value,&end,10);
    return !*end && parsed>0 && parsed<=65535 ? (unsigned)parsed : 0;
}
int bind(int fd,const struct sockaddr* addr,socklen_t length) {
    int (*native)(int,const struct sockaddr*,socklen_t)=dlsym(RTLD_NEXT,"bind");
    const unsigned local=port("TB_LAB_LOCAL_PORT");
    if(local && addr && length==sizeof(struct sockaddr_in) && addr->sa_family==AF_INET &&
       ((const struct sockaddr_in*)addr)->sin_port==htons(3658)) {
        struct sockaddr_in copy=*(const struct sockaddr_in*)addr;
        copy.sin_port=htons(local);
        return native(fd,(const struct sockaddr*)&copy,length);
    }
    return native(fd,addr,length);
}
ssize_t sendto(int fd,const void* bytes,size_t size,int flags,const struct sockaddr* addr,socklen_t length) {
    ssize_t (*native)(int,const void*,size_t,int,const struct sockaddr*,socklen_t)=dlsym(RTLD_NEXT,"sendto");
    const unsigned relay=port("TB_LAB_SEND_PORT");
    struct in_addr self;
    if(virtual_self(&self) && addr && length==sizeof(struct sockaddr_in) && addr->sa_family==AF_INET) {
        struct sockaddr_in copy=*(const struct sockaddr_in*)addr;
        unsigned discovery=port("TB_LAB_DISCOVERY_PORT");if(!discovery)discovery=3657;
        if(ntohs(copy.sin_port)==discovery && size==13 && bytes && ((const unsigned char*)bytes)[0]==1) {
            unsigned char registration[13];memcpy(registration,bytes,13);
            memcpy(registration+9,&self.s_addr,4);
            return native(fd,registration,size,flags,addr,length);
        }
        unsigned target=ntohl(copy.sin_addr.s_addr);
        if(ntohs(copy.sin_port)==3658 && target>=0x7f000065u && target<=0x7f000067u) {
            const unsigned index=target-0x7f000065u;
            unsigned destination=virtual_port("TB_LAB_VIRTUAL_SEND_PORTS",index);
            if(!destination)destination=3658+index;
            const char* host=getenv("TB_LAB_HOST_IP");
            if(host && inet_pton(AF_INET,host,&copy.sin_addr)==1) {
                copy.sin_port=htons(destination);
                return native(fd,bytes,size,flags,(const struct sockaddr*)&copy,length);
            }
        }
    }
    if(relay && addr && length==sizeof(struct sockaddr_in) && addr->sa_family==AF_INET &&
       ((const struct sockaddr_in*)addr)->sin_port==htons(3658)) {
        struct sockaddr_in copy=*(const struct sockaddr_in*)addr;
        copy.sin_port=htons(relay);
        return native(fd,bytes,size,flags,(const struct sockaddr*)&copy,length);
    }
    return native(fd,bytes,size,flags,addr,length);
}
ssize_t recvfrom(int fd,void* bytes,size_t size,int flags,struct sockaddr* addr,socklen_t* length) {
    ssize_t (*native)(int,void*,size_t,int,struct sockaddr*,socklen_t*)=dlsym(RTLD_NEXT,"recvfrom");
    const socklen_t capacity=length ? *length : 0;
    ssize_t result=native(fd,bytes,size,flags,addr,length);
    const unsigned relay=port("TB_LAB_RECV_PORT");
    struct in_addr self;
    if(result>=0 && virtual_self(&self) && addr && length && capacity>=sizeof(struct sockaddr_in) &&
       *length>=sizeof(struct sockaddr_in) && addr->sa_family==AF_INET) {
        struct sockaddr_in* source=(struct sockaddr_in*)addr;
        const char* host=getenv("TB_LAB_HOST_IP");struct in_addr expected;
        if(host && inet_pton(AF_INET,host,&expected)==1 && source->sin_addr.s_addr==expected.s_addr)
            for(unsigned i=0;i<3;++i) {
                unsigned incoming=virtual_port("TB_LAB_VIRTUAL_RECV_PORTS",i);
                if(!incoming)incoming=3658+i;
                if(ntohs(source->sin_port)==incoming) {
                    source->sin_port=htons(3658);source->sin_addr.s_addr=htonl(0x7f000065u+i);
                    break;
                }
            }
    }
    if(result>=0 && relay && addr && length && capacity>=sizeof(struct sockaddr_in) &&
       *length>=sizeof(struct sockaddr_in) && addr->sa_family==AF_INET &&
       ((struct sockaddr_in*)addr)->sin_port==htons(relay))
        ((struct sockaddr_in*)addr)->sin_port=htons(3658);
    return result;
}
