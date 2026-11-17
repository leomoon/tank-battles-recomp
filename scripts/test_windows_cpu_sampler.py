"""Check Windows sampler symbolization against a synthetic lld-link map."""
import tempfile
import unittest
from pathlib import Path

from sample_windows_cpu import load_map, report

MAP = """Address  Size     Align Out     In      Symbol
00001000 00002000  4096 .text
00001000 00000400    16         C:\\b\\ppu_recomp_000.obj:(.text)
00001000 00000000     0                 void __cdecl func_00192EB8(struct ppu_context *)
00001000 00000000     0                 void __cdecl func_00192F00(struct ppu_context *)
00001200 00000000     0                 ps3_indirect_call_impl
00001800 00000000     0                 tb_execution_event
"""


class SamplerTests(unittest.TestCase):
    def test_map_symbols_extend_to_the_next_symbol(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'player.map'
            path.write_text(MAP)
            symbols = load_map(path)
            self.assertEqual([s[1] for s in symbols][-2:], ['ps3_indirect_call_impl', 'tb_execution_event'])
            base = 0x7FF600000000
            data = dict(module_base=base, threads={'7': dict(name='ppu main', rips=[
                base + 0x1010, base + 0x1250, base + 0x1250, base + 0x1900, 0x7FFE00001234])})
            top = dict((label, n) for label, n, _ in report(data, path)['7']['top'])
            self.assertEqual(top['ps3_indirect_call_impl'], 2)
            self.assertEqual(top['tb_execution_event'], 1)
            self.assertEqual(top['outside-module'], 1)
            self.assertEqual(sum(top.values()), 5)


if __name__ == '__main__':
    unittest.main()
