#define _GNU_SOURCE
#include <linux/perf_event.h>
#include <sys/syscall.h>
#include <sys/mman.h>
#include <dirent.h>
#include <unistd.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <errno.h>
struct event {int fd,tid;struct perf_event_mmap_page* map;};
static double clock_s(){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec+t.tv_nsec/1e9;}
int main(int argc,char**argv){if(argc!=3)return 2;char path[128];snprintf(path,sizeof(path),"/proc/%s/task",argv[1]);DIR*d=opendir(path);if(!d){perror("task");return 1;}struct event events[128];int n=0;struct dirent*e;
while((e=readdir(d))&&n<128){int tid=atoi(e->d_name);if(!tid)continue;struct perf_event_attr a={0};a.size=sizeof(a);a.type=PERF_TYPE_SOFTWARE;a.config=PERF_COUNT_SW_CPU_CLOCK;a.freq=1;a.sample_freq=99;a.sample_type=PERF_SAMPLE_IP|PERF_SAMPLE_TID|PERF_SAMPLE_TIME;a.use_clockid=1;a.clockid=CLOCK_MONOTONIC;a.exclude_kernel=1;a.exclude_hv=1;int fd=syscall(__NR_perf_event_open,&a,tid,-1,-1,0);if(fd<0){fprintf(stderr,"tid %d: %s\n",tid,strerror(errno));continue;}void*m=mmap(0,9*4096,PROT_READ|PROT_WRITE,MAP_SHARED,fd,0);if(m==MAP_FAILED){close(fd);continue;}events[n++]=(struct event){fd,tid,m};}closedir(d);fprintf(stderr,"sampling %d threads at 99 Hz\n",n);double end=clock_s()+atof(argv[2]);
while(clock_s()<end){for(int k=0;k<n;k++){struct perf_event_mmap_page*m=events[k].map;uint64_t head=__atomic_load_n(&m->data_head,__ATOMIC_ACQUIRE),tail=m->data_tail;unsigned char*data=(unsigned char*)m+m->data_offset;uint64_t size=m->data_size;while(tail<head){unsigned char record[4096];unsigned pos=tail%size;struct perf_event_header h;unsigned char hb[8];for(int i=0;i<8;i++)hb[i]=data[(pos+i)%size];memcpy(&h,hb,8);if(h.size<8||h.size>sizeof(record)){tail=head;break;}for(int i=0;i<h.size;i++)record[i]=data[(pos+i)%size];if(h.type==PERF_RECORD_SAMPLE&&h.size>=32){uint64_t ip;uint32_t pid,tid;memcpy(&ip,record+8,8);memcpy(&pid,record+16,4);memcpy(&tid,record+20,4);uint64_t stamp;memcpy(&stamp,record+24,8);printf("%llu,%u,0x%llx\n",(unsigned long long)stamp,tid,(unsigned long long)ip);}tail+=h.size;}__atomic_store_n(&m->data_tail,tail,__ATOMIC_RELEASE);}usleep(20000);}for(int k=0;k<n;k++){munmap(events[k].map,9*4096);close(events[k].fd);}return n?0:1;}
