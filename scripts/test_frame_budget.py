"""Check the frame-budget timeline analysis on synthetic traces."""
import tempfile
import unittest
from pathlib import Path

from frame_budget import (COUNT, GPU_CMD, GPU_PASS, counters_per_frame, gpu_per_frame, headroom, BATCH_SUBMIT, PUBLISH, CLOCK_SLEEP, DRAIN_CALL, FENCE_WAIT, FLIP_FIFO, HLE, PRESENT, PUT_ADVANCE, SYSCALL,
                          USLEEP, VBLANK, Union, analyze, load)


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
    spans += [span(VBLANK, t, t) for t in range(0, 60, 16)]
    # Ten publications, two batch submissions (one at idle) and two fence waits.
    spans += [span(PUBLISH, 1 + t, 1 + t, a=0x1D70) for t in range(10)]
    spans += [span(BATCH_SUBMIT, 9, 9), span(BATCH_SUBMIT, 40, 40, a=1)]
    return sorted(spans, key=lambda s: s[4])


class FrameBudgetTests(unittest.TestCase):
    def test_materialization_resources_keep_location_and_operation_separate(self):
        from frame_budget import materializations
        trace=two_frames()+[(30,0,0x1234,16,1.,1.),(30,0,0x80001234,32,2.,2.),
                            (32,0,0x1234,64,3.,3.),(31,0,0x1234,99,70.,70.)]
        result=materializations(trace)
        self.assertEqual(sum(r['bytes'] for r in result['resources']),112)
        self.assertEqual(len(result['resources']),3)
        self.assertEqual(result['resources'][1]['location'],1)
        self.assertEqual(materializations([])['status'],'unavailable')

    def test_scale_observations_do_not_infer_activation_from_requested_scale(self):
        from frame_budget import render_scale_observations
        trace = two_frames() + [(27,0,150,100,1.,1.),(27,0,150,150,31.,31.),
                               (28,0,3840,2160,32.,32.),(29,0,1920,1080,32.,32.),
                               (27,0,300,300,100.,100.)]
        observed=render_scale_observations(trace)
        self.assertEqual(observed['requested_effective_percent'],[(150,100),(150,150)])
        self.assertEqual(observed['present_source_extents'],[(1920,1080)])
        self.assertEqual(render_scale_observations(two_frames())['status'],'unavailable')
        self.assertEqual(render_scale_observations([])['status'],'unavailable')

    def test_render_counters_preserve_existing_trace_ids(self):
        from frame_budget import COUNTER_NAMES
        self.assertEqual(COUNTER_NAMES[14],'draw')
        self.assertEqual(COUNTER_NAMES[24],'logical_draw')
        self.assertEqual(COUNTER_NAMES[47],'present_readback_bytes')
        self.assertEqual(COUNTER_NAMES[52],'fxaa_texels')
        trace=[(COUNT,0,25,10,1.,1.),(COUNT,0,25,30,31.,31.),
               (COUNT,0,29,10,1.,1.),(COUNT,0,29,30,31.,31.)]
        counts=counters_per_frame(trace,[(0.,30.),(30.,60.)])
        self.assertEqual(counts['logical_draw'],10)
        self.assertEqual(counts['engine_scaled_draw'],10)

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
        self.assertAlmostEqual(result['vblank_hz'], 4 * 1000 / 60)

    def test_counts_publications_and_submissions_per_frame(self):
        result = analyze(two_frames())
        self.assertAlmostEqual(result['publications_per_frame'], 5)
        self.assertAlmostEqual(result['async_submits_per_frame'], 1)
        self.assertAlmostEqual(result['idle_submits_per_frame'], 0.5)
        self.assertAlmostEqual(result['fence_waits_per_frame'], 1)

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


class FrameWorkTests(unittest.TestCase):
    def test_cumulative_counters_become_per_frame_deltas(self):
        frames = [(0.0, 30.0), (30.0, 60.0)]
        # Counter 1 (queue_submit): totals 5 by t=10, 12 by t=29, 20 by t=50.
        spans = [(COUNT, 0, 1, 5, 10.0, 10.0), (COUNT, 0, 1, 12, 29.0, 29.0), (COUNT, 0, 1, 20, 50.0, 50.0),
                 # Counter 20 (texture_hash_bytes) wraps past 2**32 between frames.
                 (COUNT, 0, 20, 0xFFFFFFF0, 1.0, 1.0), (COUNT, 0, 20, 0x10, 40.0, 40.0)]
        counters = counters_per_frame(spans, frames)
        # The first frame has no earlier total, so only frame 2 contributes (20 - 12 = 8).
        self.assertAlmostEqual(counters['queue_submit'], 4)
        self.assertAlmostEqual(counters['texture_hash_bytes'], 0x20 / 2)

    def test_gpu_time_is_summed_per_frame_and_by_target(self):
        frames = [(0.0, 30.0), (30.0, 60.0)]
        spans = [(GPU_CMD, 0, 0, 8_000_000, 5.0, 5.0), (GPU_CMD, 0, 0, 4_000_000, 35.0, 35.0),
                 (GPU_PASS, 0, 2560 << 16 | 1440, 6_000_000, 5.0, 5.0), (GPU_PASS, 0, 1280 << 16 | 720, 1_000_000, 35.0, 35.0),
                 (GPU_PASS, 0, 0x80000000 | 7 << 16, 2_000_000, 36.0, 36.0),
                 (GPU_PASS, 0, 0x80000000 | 8 << 16, 500_000, 37.0, 37.0),
                 (GPU_CMD, 0, 0, 99_000_000, 70.0, 70.0)]
        gpu = gpu_per_frame(spans, frames)
        self.assertAlmostEqual(gpu['busy_ms'], 6.0)
        self.assertAlmostEqual(gpu['command_buffers'], 1.0)
        self.assertEqual(gpu['passes_by_extent'][0][0], '2560x1440')
        self.assertAlmostEqual(gpu['passes_by_extent'][0][2], 3.0)
        self.assertIn(('dispatch', 0.5, 1.0), gpu['passes_by_extent'])
        self.assertIn(('fxaa', 0.5, 0.25), gpu['passes_by_extent'])

    def test_headroom_names_the_busiest_resource(self):
        result = dict(guest_threads=[dict(running_ms=21.0)], walker_ms_per_frame=dict(drain_cpu=9.0, present=1.0),
                      present_thread_ms_per_frame=2.0, gpu=dict(busy_ms=12.0))
        h = headroom(result)
        self.assertEqual(h['limiter'], 'main_guest_thread')
        self.assertAlmostEqual(h['headroom_ms'], 1000 / 60 - 21.0)
        self.assertNotIn('gpu_busy', headroom(dict(result, gpu=None))['busy_ms'])


if __name__ == '__main__':
    unittest.main()
