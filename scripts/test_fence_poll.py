"""Check bounded fence polling, fallback and error propagation without game data."""
import subprocess
from port import ROOT
out=ROOT/'build-tests/fence-poll';out.mkdir(parents=True,exist_ok=True)
source=r'''
#include <assert.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
typedef int VkResult;typedef int VkFence;
#define VK_NOT_READY 1
#define VK_TRUE 1
static struct {int device;} s_vk;
static unsigned probes,waits,ready_after;static int error;static uint64_t clock_step,clock_value;
static uint64_t tb_depth_cost_now(void){uint64_t n=clock_value;clock_value+=clock_step;return n;}
static int pvkGetFenceStatus(int device,int fence){assert(fence==7);probes++;return error?error:probes>ready_after?0:1;}
static int pvkWaitForFences(int d,int n,int* f,int all,uint64_t timeout){assert(n==1&&*f==7&&all&&timeout==UINT64_MAX);waits++;return error;}
static int budget_on;static unsigned budget_spans,budget_kind;
__thread const char* tb_wait_label; uint32_t tb_frame_budget_site(const char* l){return 0;}
int tb_frame_budget_on(void){return budget_on;}
uint64_t tb_frame_budget_now(void){return 0;}
void tb_frame_budget_span(unsigned kind,uint32_t a,uint64_t t0,uint64_t t1){budget_spans++;budget_kind=kind;}
#include "sdk_overrides/vulkan_fence_poll.inc"
static void reset(unsigned after,uint64_t step){probes=waits=0;ready_after=after;error=0;clock_step=step;clock_value=0;}
int main(void){
 setenv("TB_VK_FENCE_POLL","0",1);reset(0,0);assert(tb_fence_wait(7)==0&&probes==0&&waits==1);
 setenv("TB_VK_FENCE_POLL","1",1);reset(0,0);assert(tb_fence_wait(7)==0&&probes==1&&!waits);
 reset(3,0);assert(tb_fence_wait(7)==0&&probes==4&&!waits);
 reset(999,0);assert(tb_fence_wait(7)==0&&probes==256&&waits==1);
 reset(999,50000);assert(tb_fence_wait(7)==0&&probes==8&&waits==1);
 reset(0,0);error=-4;assert(tb_fence_wait(7)==-4&&probes==1&&!waits);
 setenv("TB_VK_FENCE_POLL","0",1);reset(0,0);error=-4;assert(tb_fence_wait(7)==-4&&waits==1);
 assert(budget_spans==0);budget_on=1;reset(0,0);assert(tb_fence_wait(7)==0&&waits==1&&budget_spans==1&&budget_kind==TB_FT_FENCE_WAIT);
}
'''
(out/'test.c').write_text(source)
subprocess.run(['cc','-O2','-I'+str(ROOT),str(out/'test.c'),'-o',str(out/'test')],check=True)
subprocess.run([str(out/'test')],check=True)
print('PASS: immediate/delayed completion, probe/time bounds, blocking fallback, device-loss propagation and frame-budget wait spans')
