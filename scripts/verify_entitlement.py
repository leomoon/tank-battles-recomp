#!/usr/bin/env python3
"""Compare purchased/missing entitlement decisions and live guest flags on Linux."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
import resource
import signal
import struct
import subprocess
import sys
import time
import tomllib
from port import ROOT, resolve, launch_environment, positive_seconds


def layout(elf_path):
    sys.path.insert(0, str(ROOT / 'external/ps3recomp/tools'))
    from elf_parser import ELFFile, vaddr_to_offset
    from capstone import Cs, CS_ARCH_PPC, CS_MODE_64, CS_MODE_BIG_ENDIAN
    elf = ELFFile(str(elf_path))
    elf.load()
    def read(address, count):
        offset = vaddr_to_offset(elf.program_headers, address)
        if offset is None:
            raise ValueError('Unsupported local executable layout')
        return elf.raw_data[offset:offset+count]
    toc = struct.unpack('>II', read(elf.elf_header.e_entry, 8))[1]
    disassembler = Cs(CS_ARCH_PPC, CS_MODE_64 | CS_MODE_BIG_ENDIAN)
    # These locations were traced in this title's local dump. Validate their
    # instruction shapes before inspecting only its entitlement-state bytes.
    getter = list(disassembler.disasm(read(0x1efec, 4), 0x1efec))[0]
    store = list(disassembler.disasm(read(0x19a164, 4), 0x19a164))[0]
    if getter.mnemonic != 'lwz' or getter.op_str != 'r29, -0x7ef4(r2)':
        raise ValueError('Unsupported title singleton getter')
    if store.mnemonic != 'stb' or store.op_str != 'r7, 0x4ce(r3)':
        raise ValueError('Unsupported title entitlement-state layout')
    return toc - 0x7ef4


def guest_flags(process, toc_slot, executable, symbol, pie):
    # Resolve the runner's vm_base symbol instead of assuming mmap boundaries;
    # Linux may merge the guest mapping with adjacent anonymous regions.
    mappings = Path(f'/proc/{process.pid}/maps').read_text().splitlines()
    module_base = None
    for line in mappings:
        fields = line.split(maxsplit=5)
        if len(fields) == 6 and fields[5] == str(executable) and int(fields[2], 16) == 0:
            module_base = int(fields[0].split('-')[0], 16)
            break
    if module_base is None:
        return None
    with open(f'/proc/{process.pid}/mem', 'rb', buffering=0) as memory:
        memory.seek((module_base if pie else 0)+symbol)
        base = int.from_bytes(memory.read(8), sys.byteorder)
        if not base:
            return None
        def read(ea, count):
            if not 0x10000 <= ea < 0xe0000000-count:
                return None
            memory.seek(base+ea)
            return memory.read(count)
        pointer = read(toc_slot, 4)
        pointer = read(int.from_bytes(pointer, 'big'), 4) if pointer else None
        flags = read(int.from_bytes(pointer, 'big')+0x4ce, 3) if pointer else None
        if flags and flags[1] == 1:
            return dict(trial=flags[0], verification_complete=flags[1], thread_complete=flags[2])
    return None


def boot(profile, command, env, toc_slot, timeout, directory, symbol, pie):
    log = directory / (profile + '-state-boot.log')
    flags = None
    timed_out = False
    with log.open('wb') as output:
        process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=output,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        try:
            deadline = time.monotonic()+timeout
            while process.poll() is None and time.monotonic() < deadline:
                try:
                    flags = guest_flags(process, toc_slot, Path(command[0]), symbol, pie) or flags
                except (FileNotFoundError, ProcessLookupError):
                    break
                time.sleep(0.02)
        finally:
            timed_out = process.poll() is None
            if timed_out:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
    text = log.read_text(errors='replace')
    values = re.findall(r'\[port-hle\] return nid=0x2ECD48ED r3=(0x[0-9a-fA-F]+)', text)
    return dict(profile=profile, flags=flags, verify_returns=values,
                child_returncode=process.returncode, timed_out=timed_out, timeout_seconds=timeout, log=str(log.relative_to(ROOT)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timeout', type=positive_seconds, default=3)
    args = parser.parse_args()
    if not sys.platform.startswith('linux'):
        parser.error('Guest-state observation currently requires Linux /proc access')
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    config = tomllib.loads((ROOT / 'config.toml').read_text())
    elf = resolve(config['game']['elf'])
    slot = layout(elf)
    env = launch_environment(config)
    for name in ('TB_ENTITLEMENT_PROBE', 'TB_ASSET_PROBE', 'TB_HLE_AUDIT_ONLY', 'TB_DIAGNOSTIC_STALL'):
        env.pop(name, None)
    env.update(TB_TRACE_NID='0x2ECD48ED', TB_SESSION_CAPTURED='1', SDL_AUDIODRIVER='dummy', SDL_VIDEODRIVER='dummy')
    directory = ROOT / 'analysis/step6'
    directory.mkdir(parents=True, exist_ok=True)
    missing = ROOT / 'game/step6-missing'
    (missing / 'dev_hdd0/game').mkdir(parents=True, exist_ok=True)
    link = missing / 'dev_hdd0/game' / config['game']['title_id']
    title = resolve(config['game']['paramsfo']).parent
    if not link.exists():
        link.symlink_to(title, target_is_directory=True)
    if link.resolve() != title:
        raise SystemExit('Missing-profile title link points to unexpected content')
    if (missing / 'dev_hdd0/home/00000001/exdata').exists():
        raise SystemExit('Missing-profile exdata must be absent; use a clean local fixture')
    executable = resolve(config['paths']['build']) / 'TankBattlesRecomp'
    symbols = subprocess.check_output(['nm', '-g', '--defined-only', str(executable)], text=True)
    match = re.search(r'^([0-9a-fA-F]+) \w vm_base$', symbols, re.M)
    if not match:
        raise SystemExit('Runner vm_base symbol unavailable; use an unstripped diagnostic build')
    symbol = int(match[1], 16)
    with executable.open('rb') as stream:
        header = stream.read(18)
    pie = int.from_bytes(header[16:18], 'little' if header[5] == 1 else 'big') == 3
    command = [str(executable), str(elf)]
    results = []
    for profile in ('purchased', 'missing'):
        active = dict(env)
        if profile == 'missing':
            active.update(PS3_HDD0_ROOT=str(missing / 'dev_hdd0'),
                          PS3_HDD1_ROOT=str(missing / 'dev_hdd1'), PS3_GAMEDATA_ROOT=str(missing))
        results.append(boot(profile, command, active, slot, args.timeout, directory, symbol, pie))
    report = dict(profiles=results, reference='analysis/reference/session.json',
                  limitation='Guest entitlement gating only; title-screen/full-content checks await later boot fixes.')
    (directory / 'entitlement-verification.json').write_text(json.dumps(report, indent=2)+'\n')
    for result, expected_trial, expected_return in zip(results, (0, 1), (0, 0x80029521)):
        if not result['flags'] or result['flags']['trial'] != expected_trial:
            raise SystemExit('Unexpected or missing guest trial flag; inspect the verification report')
        if not result['verify_returns'] or int(result['verify_returns'][0], 16) != expected_return:
            raise SystemExit('Unexpected entitlement HLE result')
    print('PASS: purchased unlock clears the guest trial flag; missing unlock sets it.')
    print('Full-content gameplay comparison remains dependent on later boot fixes.')

if __name__ == '__main__':
    main()
