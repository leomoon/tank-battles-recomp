#!/usr/bin/env python3
"""Low-overhead Windows CPU sampling of named player threads.

  scripts/sample_windows_cpu.py --pid N --seconds 20 --output samples.json [--map TankBattlesRecomp.map]
  scripts/sample_windows_cpu.py --report samples.json [--map TankBattlesRecomp.map]

Every interval each selected thread is suspended, its instruction pointer is
read and it is resumed (the same technique as a sampling profiler's
"suspend and sample"). Threads are selected by their Windows thread description
(the port names guest PPU threads, the walker and the present thread). Samples
are symbolized against an lld-link /MAP file of the executable. The player
build writes one (TankBattlesRecomp.map); only addresses inside the main module
are attributed.

This is a diagnostic: suspension perturbs timing slightly, so keep sampled runs
separate from clean timing, like the Linux sampler.
"""
import argparse
import bisect
import ctypes
import json
import re
import sys
import time
from collections import Counter
from ctypes import wintypes
from pathlib import Path

THREAD_SUSPEND_RESUME, THREAD_GET_CONTEXT, THREAD_QUERY_LIMITED_INFORMATION = 0x0002, 0x0008, 0x0800
PROCESS_QUERY_INFORMATION, PROCESS_VM_READ = 0x0400, 0x0010
TH32CS_SNAPTHREAD = 0x00000004
CONTEXT_CONTROL = 0x00100001        # CONTEXT_AMD64 | CONTEXT_CONTROL
CONTEXT_SIZE, CONTEXT_FLAGS_OFFSET, CONTEXT_RIP_OFFSET = 1232, 0x30, 0xF8


class THREADENTRY32(ctypes.Structure):
    _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD), ('th32ThreadID', wintypes.DWORD),
                ('th32OwnerProcessID', wintypes.DWORD), ('tpBasePri', wintypes.LONG),
                ('tpDeltaPri', wintypes.LONG), ('dwFlags', wintypes.DWORD)]


def kernel():
    k = ctypes.WinDLL('kernel32', use_last_error=True)
    k.OpenThread.restype = wintypes.HANDLE
    k.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.OpenProcess.restype = wintypes.HANDLE
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.SuspendThread.argtypes = [wintypes.HANDLE]
    k.SuspendThread.restype = wintypes.DWORD
    k.ResumeThread.argtypes = [wintypes.HANDLE]
    k.ResumeThread.restype = wintypes.DWORD
    k.GetThreadContext.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    k.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(THREADENTRY32)]
    k.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(THREADENTRY32)]
    k.GetThreadDescription.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
    k.LocalFree.argtypes = [ctypes.c_void_p]
    return k


def process_threads(k, pid):
    snap = k.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
    entry = THREADENTRY32(dwSize=ctypes.sizeof(THREADENTRY32))
    found = []
    ok = k.Thread32First(snap, ctypes.byref(entry))
    while ok:
        if entry.th32OwnerProcessID == pid:
            found.append(entry.th32ThreadID)
        ok = k.Thread32Next(snap, ctypes.byref(entry))
    k.CloseHandle(snap)
    return found


def thread_name(k, handle):
    text = ctypes.c_void_p()
    if k.GetThreadDescription(handle, ctypes.byref(text)) < 0 or not text.value:
        return ''
    name = ctypes.wstring_at(text.value)
    k.LocalFree(text)
    return name


def main_module_base(pid):
    psapi = ctypes.WinDLL('psapi', use_last_error=True)
    k = kernel()
    process = k.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    modules = (wintypes.HMODULE * 1)()
    needed = wintypes.DWORD()
    psapi.EnumProcessModules.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    ok = psapi.EnumProcessModules(process, modules, ctypes.sizeof(modules), ctypes.byref(needed))
    k.CloseHandle(process)
    return int(modules[0] or 0) if ok else 0


def sample(pid, seconds, interval_ms, pattern):
    k = kernel()
    selected = {}
    for tid in process_threads(k, pid):
        handle = k.OpenThread(THREAD_SUSPEND_RESUME | THREAD_GET_CONTEXT | THREAD_QUERY_LIMITED_INFORMATION, False, tid)
        if not handle:
            continue
        name = thread_name(k, handle)
        if re.search(pattern, name):
            selected[tid] = (handle, name)
        else:
            k.CloseHandle(handle)
    raw = ctypes.create_string_buffer(CONTEXT_SIZE + 16)
    context = (ctypes.addressof(raw) + 15) & ~15
    samples = {tid: [] for tid in selected}
    deadline = time.perf_counter() + seconds
    try:
        while time.perf_counter() < deadline:
            for tid, (handle, _) in selected.items():
                if k.SuspendThread(handle) == 0xFFFFFFFF:
                    continue
                ctypes.memmove(context + CONTEXT_FLAGS_OFFSET, ctypes.byref(wintypes.DWORD(CONTEXT_CONTROL)), 4)
                if k.GetThreadContext(handle, context):
                    samples[tid].append(ctypes.c_uint64.from_address(context + CONTEXT_RIP_OFFSET).value)
                k.ResumeThread(handle)
            time.sleep(interval_ms / 1000.0)
    finally:
        for handle, _ in selected.values():
            k.CloseHandle(handle)
    return dict(pid=pid, seconds=seconds, interval_ms=interval_ms, module_base=main_module_base(pid),
                threads={str(tid): dict(name=name, rips=samples[tid]) for tid, (_, name) in selected.items()})


def load_map(path):
    """lld-link /lldmap: 'Address Size Align Out In Symbol' rows. Addresses are
    image-relative (RVA). Symbol rows are the third indentation level and carry
    size 0, so each symbol extends to the next one."""
    symbols = []
    for line in Path(path).read_text(errors='replace').splitlines():
        m = re.match(r'([0-9A-Fa-f]{8,16}) ([0-9A-Fa-f]+) +\d+ (.*)$', line)
        if not m or not m.group(3).startswith(' ' * 16):
            continue
        name = m.group(3).strip()
        short = re.search(r'(func_[0-9A-Fa-f]{8})', name) or re.search(r'([A-Za-z_][\w:~<>]*)\s*\(', name)
        symbols.append((int(m.group(1), 16), short.group(1) if short else name))
    symbols.sort()
    return symbols


def report(data, map_path=None, top=25):
    symbols = load_map(map_path) if map_path and Path(map_path).is_file() else []
    starts = [s[0] for s in symbols]
    base = data.get('module_base') or 0
    out = {}
    for tid, thread in data['threads'].items():
        counts = Counter()
        for rip in thread['rips']:
            rva = rip - base
            if not base or not 0 <= rva < 1 << 31:
                label = 'outside-module'
            elif symbols:
                i = bisect.bisect_right(starts, rva) - 1
                label = symbols[i][1] if i >= 0 else 'module+0x%X' % rva
            else:
                label = 'module+0x%X' % rva
            counts[label] += 1
        total = sum(counts.values()) or 1
        out[tid] = dict(name=thread['name'], samples=sum(counts.values()),
                        top=[(label, n, 100.0 * n / total) for label, n in counts.most_common(top)])
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--pid', type=int)
    parser.add_argument('--seconds', type=float, default=20)
    parser.add_argument('--interval-ms', type=float, default=1.0)
    parser.add_argument('--delay', type=float, default=0, help='Seconds to wait before sampling')
    parser.add_argument('--threads', default=r'^(ppu main|tb-rsx-walker|tb-present)$', help='Regex over thread descriptions')
    parser.add_argument('--output')
    parser.add_argument('--report')
    parser.add_argument('--map')
    args = parser.parse_args()
    if sys.platform != 'win32':
        parser.error('Windows only; use sample_gameplay_cpu.c on Linux')
    if args.report:
        data = json.loads(Path(args.report).read_text())
    else:
        if not args.pid or not args.output:
            parser.error('--pid and --output are required when sampling')
        time.sleep(args.delay)
        data = sample(args.pid, args.seconds, args.interval_ms, args.threads)
        Path(args.output).write_text(json.dumps(data) + '\n')
    for tid, result in report(data, args.map).items():
        print(f"thread {tid} {result['name']!r}: {result['samples']} samples")
        for label, n, share in result['top']:
            print(f"  {share:5.1f}% {n:6d} {label}")


if __name__ == '__main__':
    main()
