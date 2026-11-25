"""Read-only CPU-time and memory snapshots for diagnostic player runs."""
import ctypes as c
import time
from ctypes import wintypes as w
from sample_windows_cpu import kernel, process_threads, thread_name


class Memory(c.Structure):
    _fields_=[('cb',w.DWORD),('PageFaultCount',w.DWORD)]+[(name,c.c_size_t) for name in (
        'PeakWorkingSetSize','WorkingSetSize','QuotaPeakPagedPoolUsage','QuotaPagedPoolUsage',
        'QuotaPeakNonPagedPoolUsage','QuotaNonPagedPoolUsage','PagefileUsage','PeakPagefileUsage','PrivateUsage')]


class WindowsResources:
    def __init__(self,pid,all_threads=False):
        self.k=kernel();self.pid=pid;self.threads={};self.all_threads=all_threads
        self.process=self.k.OpenProcess(0x0400|0x0010,False,pid)
        if not self.process:raise c.WinError(c.get_last_error())
        self.ps=c.WinDLL('psapi',use_last_error=True)
        self.ps.GetProcessMemoryInfo.argtypes=[w.HANDLE,c.POINTER(Memory),w.DWORD]
        self.k.GetProcessTimes.argtypes=self.k.GetThreadTimes.argtypes=[w.HANDLE]+[c.POINTER(w.FILETIME)]*4

    def times(self,fn,handle):
        times=[w.FILETIME() for _ in range(4)]
        if not fn(handle,*[c.byref(t) for t in times]):raise c.WinError(c.get_last_error())
        return sum(((t.dwHighDateTime<<32)|t.dwLowDateTime)*100 for t in times[2:])

    def sample(self):
        memory=Memory(cb=c.sizeof(Memory))
        if not self.ps.GetProcessMemoryInfo(self.process,c.byref(memory),c.sizeof(memory)):
            raise c.WinError(c.get_last_error())
        for tid in process_threads(self.k,self.pid):
            if tid in self.threads:continue
            handle=self.k.OpenThread(0x0800,False,tid)
            if not handle:continue
            name=thread_name(self.k,handle)
            if self.all_threads or name in ('ppu main','tb-rsx-walker','tb-present'):
                self.threads[tid]=(handle,name or f'unnamed-{tid}')
            else:self.k.CloseHandle(handle)
        threads=[]
        for tid,(handle,name) in self.threads.items():
            try:
                # A thread can be observed before it assigns its description.
                name=thread_name(self.k,handle) or name
                self.threads[tid]=(handle,name)
                threads.append(dict(tid=tid,name=name,cpu_ns=self.times(self.k.GetThreadTimes,handle)))
            except OSError:continue
        return dict(status='available',monotonic_ns=time.perf_counter_ns(),process_cpu_ns=self.times(self.k.GetProcessTimes,self.process),
                    working_set_bytes=memory.WorkingSetSize,private_bytes=memory.PrivateUsage,
                    peak_working_set_bytes=memory.PeakWorkingSetSize,threads=threads,
                    thread_coverage='all_process_threads' if self.all_threads else 'main_walker_present')

    def close(self):
        for handle,_ in self.threads.values():self.k.CloseHandle(handle)
        self.threads.clear()
        if self.process:self.k.CloseHandle(self.process);self.process=None
