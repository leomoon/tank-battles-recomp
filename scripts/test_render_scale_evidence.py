"""Game-free negative controls for the render-scale evidence and eligibility rules."""
import unittest
from render_scale_evidence import Surface, resolve_routes, whole_scene_plan


class RenderScaleEvidenceTests(unittest.TestCase):
    def test_equal_dimensions_do_not_imply_equal_roles(self):
        scene = Surface(0, 0, 5120, 1280, 720)
        shadow = Surface(0, 0x1000000, 5120, 1280, 720)
        asset = Surface(1, 0, 5120, 1280, 720)
        orphan = Surface(0, 0x2000000, 5120, 1280, 720)
        scanout = Surface(0, 0x3000000, 5120, 1280, 720)
        unrelated_scanout = Surface(0, 0x4000000, 5120, 1280, 720)
        depth = Surface(0, 0x5000000, 5120, 1280, 720)
        plan = whole_scene_plan({scene: depth, orphan: shadow}, {depth},
                                {scanout, unrelated_scanout}, [(scene, scanout)],
                                excluded={shadow, asset})
        self.assertEqual(plan, {scene, depth, scanout})

    def test_mixed_use_rejects_shared_attachments(self):
        colors = {'scene-a': 'scene-depth', 'scene-b': 'scene-depth'}
        routes = [('scene-a', 'display-a'), ('scene-b', 'display-b')]
        self.assertFalse(whole_scene_plan(colors, {'scene-depth'}, {'display-a', 'display-b'},
                                          routes, excluded={'display-b'}))
        self.assertFalse(whole_scene_plan(colors, {'scene-depth'}, {'display-a', 'display-b'},
                                          routes + [('scene-a', 'unknown')]))

    def test_resolve_needs_complete_disjoint_regions_from_the_same_source(self):
        dst = Surface(0, 0x4000, 16, 4, 2)
        src = Surface(0, 0x8000, 32, 4, 2)
        entries = [dict(color=vars(src), backing=[8, 4], aa=[2, 2], target=2, zeta=[0, 0x9000, 32])]
        def tile(x, source):
            return dict(buffer=0, dest_xy=(x, 0), source=hex(source), source_location=0,
                        source_pitch=32, step=[0x200000, 0x200000], uv='0x100010',
                        clip=(2, 2), size=(2, 2), clip_xy=(0, 0), out_xy=(0, 0))
        a, b = tile(0, src.offset), tile(2, src.offset + 16)
        self.assertEqual(len(resolve_routes(entries, {0: dst}, [a, b])), 1)
        for bad in ([a], [a, a], [a, tile(2, src.offset)], [a, {**b, 'source_location': 1}]):
            self.assertEqual(resolve_routes(entries, {0: dst}, bad), [])


if __name__ == '__main__':
    unittest.main()
