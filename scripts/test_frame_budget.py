"""Check the frame-budget timeline analysis on synthetic traces."""
import tempfile
import unittest
from pathlib import Path

from frame_budget import (CLOCK_SLEEP, DRAIN_CALL, FENCE_WAIT, FLIP_FIFO, HLE, PRESENT, PUT_ADVANCE, SYSCALL,
                          USLEEP, Union, analyze, load)


def span(kind, t0, t1, who=0, a=0):
    return (kind, who, a, 0, float(t0), float(t1))


def two_frames():
    spans = [span(FLIP_FIFO, 0, 0), span(FLIP_FIFO, 30, 30), span(FLIP_FIFO, 60, 60)]
    for base in (0, 30):
        spans += [span(DRAIN_CALL, base, base + 10, a=0x100), span(FENCE_WAIT, base + 2, base + 6),
                  span(PRESENT, base + 10, base + 11), span(CLOCK_SLEEP, base + 11, base + 15),
                  span(DRAIN_CALL, base + 15, base + 25, a=0x200), span(PRESENT, base + 25, base + 26),
                  span(CLOCK_SLEEP, base + 26, base + 30)]
    # Guest thread 1: a usleep poll overlapping an HLE wait, and a blocking syscall.
    spans += [span(USLEEP, 5, 20, who=1), span(HLE, 18, 25, who=1, a=0xC44D8F34),
              span(SYSCALL, 40, 44, who=1, a=92), span(PUT_ADVANCE, 12, 12, a=0x200),
              span(PUT_ADVANCE, 31, 31, a=0x300)]
    return sorted(spans, key=lambda s: s[4])


class FrameBudgetTests(unittest.TestCase):
    def test_walker_parts_add_up_to_each_frame(self):
        result = analyze(two_frames())
        self.assertEqual(result['frames'], 2)
        self.assertAlmostEqual(result['fps'], 1000 / 30)
        walker = result['walker_ms_per_frame']
        self.assertAlmostEqual(walker['drain'], 20)
        self.assertAlmostEqual(walker['fence_wait'], 4)
        self.assertAlmostEqual(walker['drain_cpu'], 16)
        self.assertAlmostEqual(walker['present'], 2)
        self.assertAlmostEqual(walker['clock_sleep'], 8)
        self.assertAlmostEqual(walker['other'], 0)
        self.assertLess(abs(result['walker_accounted_fraction'] - 1), 0.05)
        self.assertAlmostEqual(result['drains_per_frame'], 2)

    def test_guest_blocking_is_a_union_not_a_sum(self):
        guest = analyze(two_frames(), nids={0xC44D8F34: 'cellGcmSetWaitFlip'})['guest_threads'][0]
        # Frame 1 blocks 5..25 (20 ms of overlapping spans), frame 2 blocks 4 ms.
        self.assertAlmostEqual(guest['blocked_ms'], 12)
        self.assertAlmostEqual(guest['running_ms'], 18)
        self.assertAlmostEqual(guest['usleep_ms'], 7.5)
        labels = dict(guest['top_blocking_ms_per_frame'])
        self.assertAlmostEqual(labels['cellGcmSetWaitFlip'], 3.5)
        self.assertAlmostEqual(labels['sys_semaphore_wait'], 2)

    def test_put_latency_uses_the_next_drain_that_starts_after_the_write(self):
        result = analyze(two_frames())
        # put at 12 waits for the drain at 15; put at 31 waits for the drain at 45.
        self.assertEqual(result['put_advances'], 2)
        self.assertAlmostEqual(result['put_to_drain_ms']['mean'], (3 + 14) / 2)
        self.assertAlmostEqual(result['put_to_drain_ms']['max'], 14)

    def test_spans_crossing_frame_boundaries_are_clipped(self):
        union = Union([(5, 12), (10, 20), (25, 40)])
        self.assertAlmostEqual(union.within(0, 30), 20)
        self.assertAlmostEqual(union.within(15, 30), 10)
        self.assertAlmostEqual(union.within(12, 13), 1)
        self.assertAlmostEqual(union.within(20, 25), 0)
        self.assertAlmostEqual(union.within(41, 50), 0)

    def test_requires_two_walker_flips(self):
        with self.assertRaises(ValueError):
            analyze([span(FLIP_FIFO, 0, 0), span(DRAIN_CALL, 1, 2)])

    def test_loads_the_toolkit_trace_format(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'trace.txt'
            path.write_text('# ps3_ftrace start_us=1.000 end_us=2.000 spans=2 dropped=0\nT 1 main\n'
                            f'{FLIP_FIFO} 0 1 0 1000.000 1000.000\n{DRAIN_CALL} 0 2a 0 1000.500 1003.000\n')
            header, names, spans = load(path)
        self.assertIn('spans=2', header)
        self.assertEqual(names, {1: 'main'})
        self.assertEqual(spans[1], (DRAIN_CALL, 0, 0x2A, 0, 1.0005, 1.003))


if __name__ == '__main__':
    unittest.main()
