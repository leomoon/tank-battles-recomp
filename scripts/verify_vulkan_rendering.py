#!/usr/bin/env python3
"""Verify real guest draws, shaders, pixels and window presentation locally."""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import shutil
import signal
import struct
import subprocess
import threading
import tomllib
import uuid
import zlib

from boot_session import run_session
from port import ROOT, launch_environment, positive_seconds, resolve


def inspect_frame(path: Path, png: Path) -> dict:
    header, pixels = path.read_bytes().split(b'\n255\n', 1)
    magic, width, height = header.split()
    width, height = int(width), int(height)
    if magic != b'P6' or len(pixels) != width * height * 3:
        raise ValueError('Incomplete or unsupported framebuffer capture')
    colors = len(set(zip(pixels[::3], pixels[1::3], pixels[2::3])))

    def chunk(kind, data):
        return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data))

    scanlines = b''.join(b'\0' + pixels[y * width * 3:(y + 1) * width * 3] for y in range(height))
    png.write_bytes(b'\x89PNG\r\n\x1a\n'
                    + chunk(b'IHDR', struct.pack('!2I5B', width, height, 8, 2, 0, 0, 0))
                    + chunk(b'IDAT', zlib.compress(scanlines)) + chunk(b'IEND', b''))
    return dict(width=width, height=height, unique_rgb=colors,
                pixel_sha256=hashlib.sha256(pixels).hexdigest(), png=str(png))


def request_close(window):
    # Send WM_DELETE_WINDOW directly. The tested XWayland environment did not
    # deliver xdotool's normal close request to SDL.
    x11 = ctypes.CDLL('libX11.so.6')
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
    x11.XInternAtom.restype = ctypes.c_ulong
    x11.XSendEvent.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_long, ctypes.c_void_p]
    x11.XCloseDisplay.argtypes = [ctypes.c_void_p]

    class Message(ctypes.Structure):
        _fields_ = [('type', ctypes.c_int), ('serial', ctypes.c_ulong),
                    ('send_event', ctypes.c_int), ('display', ctypes.c_void_p),
                    ('window', ctypes.c_ulong), ('message_type', ctypes.c_ulong),
                    ('format', ctypes.c_int), ('data', ctypes.c_long * 5)]

    class Event(ctypes.Union):
        _fields_ = [('message', Message), ('padding', ctypes.c_long * 24)]

    display = x11.XOpenDisplay(None)
    if not display:
        raise RuntimeError('Cannot open X11 display')
    try:
        event = Event()
        event.message.type = 33  # ClientMessage
        event.message.display = display
        event.message.window = int(window)
        event.message.message_type = x11.XInternAtom(display, b'WM_PROTOCOLS', 0)
        event.message.format = 32
        event.message.data[0] = x11.XInternAtom(display, b'WM_DELETE_WINDOW', 0)
        if not x11.XSendEvent(display, int(window), 0, 0, ctypes.byref(event)):
            raise RuntimeError('WM_DELETE_WINDOW delivery failed')
    finally:
        x11.XCloseDisplay(display)  # Flushes the request.


def check_window(title, log, stop, results):
    """Touch only this session's uniquely named X11 window; request a normal close."""
    def xdo(*args):
        return subprocess.run(['xdotool', *map(str, args)], capture_output=True,
                              text=True, timeout=3, check=True).stdout.strip()

    try:
        window = None
        while not stop.wait(0.25):
            text = log.read_text(errors='replace') if log.exists() else ''
            if '[vulkan-event] guest-draw ' not in text:
                continue
            found = xdo('search', '--name', '^' + re.escape(title) + '$').splitlines()
            if len(found) != 1:
                raise ValueError('Expected exactly one session window')
            window = found[0]
            results['pid'] = int(xdo('getwindowpid', window))
            results['geometry_before'] = xdo('getwindowgeometry', '--shell', window)
            # The compositor may retile after this request. Check its actual swapchain below.
            xdo('windowsize', window, 960, 540)
            results['resize_requested'] = True
            break
        if not window or stop.wait(6):
            return
        results['geometry_after'] = xdo('getwindowgeometry', '--shell', window)
        os.kill(results['pid'], signal.SIGUSR1)
        if stop.wait(0.3):
            return
        request_close(window)
        results['close_requested'] = True
    except Exception as error:
        results['error'] = str(error)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timeout', type=positive_seconds, default=15)
    parser.add_argument('--build-dir', default='build-vulkan')
    parser.add_argument('--output-dir', default='analysis/vulkan/verification')
    parser.add_argument('--check-window', action='store_true',
                        help='Use xdotool/X11 to resize and normally close only the test window')
    args = parser.parse_args()
    if args.check_window and not shutil.which('xdotool'):
        parser.error('--check-window requires xdotool')
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    config = tomllib.loads((ROOT / 'config.toml').read_text())
    build = resolve(args.build_dir)
    directory = resolve(args.output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    frame, log = directory / 'frame.ppm', directory / 'startup.log'
    for stale in (frame, directory / 'frame.png', directory / 'verification.json'):
        stale.unlink(missing_ok=True)
    env = launch_environment(config)
    for name in tuple(env):
        if name.startswith(('TB_', 'PS3RECOMP_VK_', 'RSX_ENGINE_', 'VK_DRAW_')):
            env.pop(name)
    title = 'Tank Battles Vulkan verification ' + uuid.uuid4().hex[:12]
    env.update(TB_EXECUTION_MODE='strict', SDL_AUDIODRIVER='dummy', PS3_TITLE=title,
               PS3RECOMP_VK_WINDOW='1', PS3RECOMP_VK_GUEST_PROGRAMS='1',
               PS3RECOMP_VK_DUMP=str(frame), PS3RECOMP_VK_DUMP_EVERY='5')
    if args.check_window:
        env['SDL_VIDEODRIVER'] = 'x11'
    stop, window = threading.Event(), {}
    watcher = None
    # Remove the previous session's log before the watcher can inspect it.
    log.write_bytes(b'')
    if args.check_window:
        watcher = threading.Thread(target=check_window, args=(title, log, stop, window), daemon=True)
        watcher.start()
    try:
        status, session = run_session([str(build / 'TankBattlesRecomp'), str(ROOT / config['game']['elf'])],
            cwd=ROOT, env=env, timeout=args.timeout, log_path=log, request_snapshot=True)
    finally:
        stop.set()
        if watcher:
            watcher.join(timeout=4)
    text = log.read_text(errors='replace')
    counts = {k: int(v, 16) for k, v in re.findall(r'\[vulkan-count\] ([a-z-]+)=0x([0-9A-Fa-f]+)', text)}
    execution = {k: int(v, 16) for k, v in re.findall(r'\[execution-count\] ([a-z-]+)=0x([0-9A-Fa-f]+)', text)}
    sizes = sorted(set(re.findall(r'window swapchain (\d+x\d+)', text)))
    try:
        image = inspect_frame(frame, directory / 'frame.png')
    except (OSError, ValueError) as error:
        image = dict(error=str(error))
    failures = ('ppu-miss', 'hle-missing', 'spu-interpreter', 'spu-untranslated', 'ppu-recovery')
    checks = dict(
        backend_ready='[vulkan-event] backend-ready a=1 b=1 c=1' in text,
        guest_shader_draws=all(counts.get(k, 0) > 0 for k in
            ('shader-module', 'pipeline', 'guest-draw', 'window-present', 'texture-upload', 'surface-flip', 'event-pump')),
        non_clear_pixels=image.get('unique_rgb', 0) > 64,
        strict_execution=all(execution.get(k) == 0 for k in failures),
        session_ended=(status == 0 and window.get('close_requested') and not window.get('error'))
            if args.check_window else status == 124,
        swapchain_resize_observed=(len(sizes) > 1 and window.get('resize_requested', False))
            if args.check_window else True,
        no_renderer_failure=not re.search(r'(?:\[RSX vulkan\]|\[rsx engine\]).*(?:failed|skipped| -> fail)', text, re.I),
    )
    report = dict(passed=all(checks.values()), checks=checks, vulkan=counts, execution=execution,
        device=re.findall(r'using device \d+: (.+)', text), swapchain_sizes=sizes,
        window=window, image=image, session=session,
        scope='Non-clear guest rendering and presentation only. Inspect frame.png visually; this does not verify menu input, audio, saves, or gameplay.',
        source_overlay=json.loads((build / 'instrumented_sdk/manifest.json').read_text()))
    (directory / 'verification.json').write_text(json.dumps(report, indent=2) + '\n')
    for name, passed in checks.items():
        print(f'{"PASS" if passed else "FAIL"}: {name}')
    if not report['passed']:
        raise SystemExit(f'Vulkan verification failed or incomplete; inspect {directory}/')
    print('PASS: real guest rendering and window presentation; inspect local frame.png.')


if __name__ == '__main__':
    main()
