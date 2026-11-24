#!/usr/bin/env python3
"""Summarize native RSX traces as scalar evidence for the render-scale design."""
import argparse
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import struct


@dataclass(frozen=True)
class Surface:
    location: int
    offset: int
    pitch: int
    width: int
    height: int

    def region_origin(self, location, offset, pitch):
        if location != self.location or pitch != self.pitch or not pitch or offset < self.offset:
            return None
        y, x_bytes = divmod(offset - self.offset, pitch)
        if x_bytes % 4 or x_bytes // 4 >= self.width or y >= self.height:
            return None
        return x_bytes // 4, y


def whole_scene_plan(colors, scene_depths, displays, resolve_routes, approved_post=(), excluded=()):
    """Reference policy only. Inputs represent evidence tied to a surface generation.

    Each color/depth pair must be approved and its scene color must resolve to a
    registered display. Excluded or ambiguous dependencies reject the whole route.
    """
    blocked = set(excluded)
    groups = []
    for color, depth in colors.items():
        destinations = {display for source, display in resolve_routes if source == color}
        group = {color, depth} | destinations | set(approved_post)
        if depth not in scene_depths or not destinations or destinations - displays:
            blocked.update(group)
        groups.append(group)
    # A scene depth or post attachment shared by accepted and rejected paths is
    # ambiguous. Propagate the rejection across all connected groups.
    while True:
        rejected = set().union(*(group for group in groups if group & blocked))
        if rejected <= blocked:
            break
        blocked |= rejected
    return set().union(*(group for group in groups if not group & blocked))


def scanouts_from_log(log):
    pattern = r'SetDisplayBuffer\(id=(\d+), offset=(0x[\da-fA-F]+), pitch=(\d+), (\d+)x(\d+)\)'
    # A later registration supersedes the old backing range for this buffer ID.
    return {int(i): Surface(0, int(offset, 16), int(pitch), int(w), int(h))
            for i, offset, pitch, w, h in re.findall(pattern, log)}


def xy(word):
    return word & 0xffff, word >> 16


def dma(word):
    return int(word == 0xfeed0001)


def resolve_routes(surfaces, displays, tiles):
    """Prove full, disjoint 2x2 point-resolve coverage in this captured scope."""
    routes = []
    for entry in surfaces:
        if entry['target'] != 2 or entry['aa'] != [2, 2]:
            continue
        source = Surface(**{**entry['color'], 'width': entry['backing'][0], 'height': entry['backing'][1]})
        for buffer, display in displays.items():
            selected = [tile for tile in tiles if tile['buffer'] == buffer]
            if not selected or (source.width, source.height) != (display.width * 2, display.height * 2):
                continue
            rectangles = []
            for tile in selected:
                x, y = tile['dest_xy']
                origin = source.region_origin(tile['source_location'], int(tile['source'], 16), tile['source_pitch'])
                w, h = map(min, zip(tile['clip'], tile['size']))
                if (origin != (2*x, 2*y) or tile['step'] != [0x200000, 0x200000] or tile['uv'] != '0x100010'
                        or tile['clip_xy'] != (0, 0) or tile['out_xy'] != (0, 0)
                        or not w or not h or x + w > display.width or y + h > display.height
                        or any(x < r and x+w > l and y < b and y+h > t for l,t,r,b in rectangles)):
                    break
                rectangles.append((x, y, x+w, y+h))
            else:
                if sum((r-l)*(b-t) for l,t,r,b in rectangles) == display.width * display.height:
                    routes.append(dict(color=hex(source.offset), zeta=hex(entry['zeta'][1]),
                                       buffer=buffer, destination=hex(display.offset), tiles=len(rectangles)))
    return routes


def frame_summary(prefix, displays, transfer_state):
    raw = prefix.with_suffix('.methods').read_bytes()
    if not prefix.with_suffix('.done').exists() or len(raw) % 12:
        raise ValueError(f'Incomplete trace: {prefix}')
    targets = Counter()
    shadow_samples = Counter()
    copies = []
    draws = 0
    for kind, method, value in struct.iter_unpack('<III', raw):
        if kind:
            channel = kind - 1
            if channel in (2, 3):
                transfer_state['dst', 0x184 if method == 0x188 else method] = value
            if channel in (6, 7):
                transfer_state['src', method] = value
                if method == 0x40c:
                    copies.append(transfer_state.copy())
            continue
        if method != 0x1808 or value:
            continue
        path = prefix.parent / f'{prefix.name}-draw-{draws:04d}.state'
        regs = struct.unpack_from('<16384I', path.read_bytes())
        def reg(addr):
            return regs[addr // 4]
        target = reg(0x220)
        # Report the active primary color slot, not inactive COLOR0 for TARGET_1.
        index = target == 2
        color = Surface(dma(reg(0x18c if index else 0x194)),
                        reg(0x218 if index else 0x210), reg(0x21c if index else 0x20c),
                        reg(0x200) >> 16, reg(0x204) >> 16)
        zeta = (dma(reg(0x198)), reg(0x214), reg(0x22c))
        fmt = reg(0x208)
        targets[color, zeta, fmt, target] += 1
        for unit in range(16):
            base = 0x1a00 + unit * 32
            rect = reg(base + 24)
            if reg(base + 12) & 0x80000000 and rect == 0x08000800:
                shadow_samples[(reg(base + 4) & 3) - 1, reg(base)] += 1
        draws += 1
    surfaces = []
    for (color, zeta, fmt, target), count in targets.most_common():
        aa = (fmt >> 12) & 15
        sx, sy = (2 if 3 <= aa <= 5 else 1), (2 if aa in (4, 5) else 1)
        surfaces.append(dict(color=vars(color), zeta=zeta, format=hex(fmt), target=target, draws=count,
                             backing=[color.width * sx, color.height * sy], aa=[sx, sy]))
    resolve_tiles = []
    for state in copies:
        try:
            source, destination = state['src', 0x408], state['dst', 0x30c]
            sp, dp = state['src', 0x404] & 0xffff, state['dst', 0x304] >> 16
            sl = dma(state['src', 0x184])
            dl = dma(state['dst', 0x184])
            step = [state['src', 0x318], state['src', 0x31c]]
            clip, size = xy(state['src', 0x30c]), xy(state['src', 0x314])
            clip_xy, out_xy = xy(state['src', 0x308]), xy(state['src', 0x310])
        except KeyError:
            # The trace can begin mid-frame with already-programmed registers.
            continue
        for buffer, display in displays.items():
            origin = display.region_origin(dl, destination, dp)
            if origin is not None:
                resolve_tiles.append(dict(buffer=buffer, destination=hex(destination), dest_xy=origin,
                    source=hex(source), source_location=sl, source_pitch=sp, step=step, clip=clip,
                    size=size, uv=hex(state['src', 0x40c]),
                    clip_xy=clip_xy, out_xy=out_xy))
                break
    return dict(scope=prefix.with_suffix('.scope').read_text().strip(), draws=draws,
                surfaces=surfaces, declared_2048_texture_units=[[*key, count] for key,count in shadow_samples.most_common()],
                nv3089_triggers=len(copies), resolve_tiles=resolve_tiles,
                qualified_resolves=resolve_routes(surfaces, displays, resolve_tiles))


def reference_frames(capture):
    frames = {}
    for path in sorted(capture.glob('*.ppm')):
        if re.match(r'frame-\d+|latest', path.stem):
            continue
        data = path.read_bytes()
        header, separator, pixels = data.partition(b'\n255\n')
        fields = header.split()
        if not separator or len(fields) != 3 or fields[0] != b'P6':
            continue
        width, height = map(int, fields[1:])
        if width > 0 and height > 0 and len(pixels) == width * height * 3:
            frames[path.stem] = dict(width=width, height=height, pixel_sha256=hashlib.sha256(pixels).hexdigest())
    return frames


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    displays = scanouts_from_log((args.capture/'startup.log').read_text(errors='replace'))
    if not displays:
        parser.error('Missing display registrations in startup.log')
    frames = {}
    state = {}
    for path in sorted((args.capture/'rsx').glob('*.done')):
        if path.stem.endswith('-000'):
            state.clear()
        frames[path.stem] = frame_summary(path.with_suffix(''), displays, state)
    if not frames:
        parser.error('No completed trace frames')
    report = dict(displays={number: vars(surface) for number,surface in displays.items()},
                  frames=frames, reference_frames=reference_frames(args.capture))
    args.output.write_text(json.dumps(report, indent=2)+'\n')


if __name__ == '__main__':
    main()
