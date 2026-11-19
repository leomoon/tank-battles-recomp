import unittest

from host_resource_report import interval_usage
from measure_host_resources import HostSampler


class HostResourceTests(unittest.TestCase):
    def test_cpu_tick_conversion_and_process_reuse(self):
        sampler = HostSampler()
        sampler.hz = 100
        fields = ["0"] * 20
        fields[0], fields[11], fields[12], fields[19] = "R", "40", "10", "1"
        first = {}
        sampler._cpu(first, 12, "12 (game worker) " + " ".join(fields), 1_000_000_000, "usage")
        self.assertEqual(first["cpu_seconds"], 0.5)
        fields[11] = "115"
        second = {}
        sampler._cpu(second, 12, "12 (game worker) " + " ".join(fields), 1_500_000_000, "usage")
        self.assertEqual(second["usage"], 150)
        fields[19] = "2"
        reused = {}
        sampler._cpu(reused, 12, "12 (game worker) " + " ".join(fields), 2_000_000_000, "usage")
        self.assertNotIn("usage", reused)
        fields[0] = "Z"
        with self.assertRaises(ProcessLookupError):
            sampler._cpu({}, 12, "12 (game worker) " + " ".join(fields), 2_100_000_000, "usage")

    def test_scene_excludes_counter_deltas_crossing_boundaries(self):
        samples = [
            dict(monotonic_ns=stamp, cpu_seconds=cpu, pid=7, start_ticks=1,
                 threads=[dict(tid=7, start_ticks=1, name="main", cpu_seconds=cpu / 2)])
            for stamp, cpu in ((0, 0), (200_000_000, 0.4), (700_000_000, 1.15), (1_000_000_000, 1.30))
        ]
        result = interval_usage(samples, 100_000_000, 800_000_000)
        self.assertAlmostEqual(result["cores"], 1.5)
        self.assertAlmostEqual(result["threads"][0]["cores"], 0.75)
        with self.assertRaises(ValueError):
            interval_usage(samples, 100_000_000, 3_000_000_000)


if __name__ == "__main__":
    unittest.main()
