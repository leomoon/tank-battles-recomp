import copy
import unittest

from measure_reference import summarize_reference
from script_gameplay import invalid_ending_scene


class ReferenceTimingTests(unittest.TestCase):
    def test_results_rejected_when_ocr_misses_debrief_heading(self):
        self.assertTrue(invalid_ending_scene('KILLS:\nRANK\nPLAYER 2 [CPU]\nX CONTINUE'))
        self.assertTrue(invalid_ending_scene('PLAYER 4\nWINS!'))
        self.assertFalse(invalid_ending_scene('FINAL BATTLE!\nPLAYER 1\n43'))

    def fixture(self):
        return dict(
            phase='gameplay',
            scene_evidence=dict(live_gameplay_verified=True),
            interval_seconds=[5, 65],
            fps_source='synthetic once-per-second reported FPS',
            rows=[dict(seconds=t, visible=True, fps=60.0, window_size=[1280, 720]) for t in range(66)],
        )

    def test_full_reported_cadence(self):
        result = summarize_reference(self.fixture())
        self.assertEqual(result['mean_reported_fps'], 60)
        self.assertEqual(result['samples'], 61)

    def test_rejects_bad_scene_and_incomplete_or_hidden_intervals(self):
        base = self.fixture()
        with self.assertRaises(ValueError):
            summarize_reference(base, invalid_scene=True)
        for field, value in [('phase', 'briefing'), ('scene_evidence', {}), ('interval_seconds', [5, 30])]:
            changed = copy.deepcopy(base)
            changed[field] = value
            with self.assertRaises(ValueError):
                summarize_reference(changed)
        for field, value in [('visible', False), ('fps', None), ('window_size', [640, 480])]:
            changed = copy.deepcopy(base)
            changed['rows'][30][field] = value
            with self.assertRaises(ValueError):
                summarize_reference(changed)
        for rows in [base['rows'][:40], base['rows'][40:]]:
            with self.assertRaises(ValueError):
                summarize_reference(dict(base, rows=rows))


if __name__ == '__main__':
    unittest.main()
