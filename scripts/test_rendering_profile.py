#!/usr/bin/env python3
"""Exercise C early-return scopes and profiling modes without a game or GPU."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from port import ROOT
from capture_rendering import summarize_profile


class RenderingProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = ROOT / 'build-tests/rendering-profile'
        cls.directory.mkdir(parents=True, exist_ok=True)
        fixture = cls.directory / 'fixture.c'
        fixture.write_text('''#include "rendering_profile.h"
#include <unistd.h>
static int early(void) { TB_RENDER_SCOPE(0); usleep(2000); return 7; }
int main(void) {
    tb_render_frame();
    if (early() != 7) return 1;
    usleep(1100000);
    tb_render_frame();
    if (early() != 7) return 2;
    usleep(1100000);
    tb_render_frame();
    return 0;
}
''')
        obj = cls.directory / 'fixture.o'
        subprocess.run(['cc', '-I', str(ROOT), '-c', str(fixture), '-o', str(obj)], check=True)
        cls.binary = cls.directory / 'fixture'
        subprocess.run(['c++', '-std=c++20', '-pthread', str(obj), str(ROOT / 'rendering_profile.cpp'), '-o', str(cls.binary)], check=True)

    def run_mode(self, mode):
        target = self.directory / (mode + '.jsonl')
        target.unlink(missing_ok=True)
        env = {k: v for k, v in os.environ.items() if not k.startswith('TB_RENDER_PROFILE')}
        if mode != 'off':
            env['TB_RENDER_PROFILE'] = str(target)
            env['TB_RENDER_PROFILE_MODE'] = mode
        subprocess.run([str(self.binary)], env=env, check=True, timeout=5)
        return target

    def test_disabled_creates_no_output(self):
        self.assertFalse(self.run_mode('off').exists())

    def test_early_return_and_histogram(self):
        path = self.run_mode('scopes')
        rows = [json.loads(s) for s in path.read_text().splitlines()]
        draw = rows[-1]['timings']['draw']
        self.assertEqual(draw['n'], 2)
        self.assertGreater(draw['ns'], 2000000)
        self.assertEqual(sum(draw['log2_ns_bins']), 2)
        summary = summarize_profile(path, .01)
        self.assertEqual(summary['timings']['draw']['n'], 1)
        self.assertGreater(summary['presents_per_second'], 0)

    def test_comparison_rejects_failed_guest_execution(self):
        run = self.directory / 'failed-run'
        run.mkdir(exist_ok=True)
        (run / 'session.json').write_text(json.dumps(dict(strict_passed=False,
            session=dict(launcher_exit_status=86))))
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/compare_rendering_runs.py'),
            str(run), '--output', str(self.directory / 'invalid-comparison.json')], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Exclude failed or interrupted scenarios', result.stderr)

    def test_cadence_leaves_scope_timers_disabled(self):
        rows = [json.loads(s) for s in self.run_mode('cadence').read_text().splitlines()]
        self.assertEqual(rows[-1]['timings']['draw']['n'], 0)
        self.assertEqual(rows[-1]['timings']['frame-interval']['n'], 2)


if __name__ == '__main__':
    unittest.main()
