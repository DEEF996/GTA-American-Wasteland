"""Runtime IPC removes only offline provenance; native inputs stay sealed."""
import copy
import hashlib
import pickle
import unittest

from compiler_payload import runtime_rail_report, runtime_world_result
import probe_gta_scene
import probe_world_rails
import rail_export
import rails
import scene_export
from test_scene_export import fixture


class CompilerPayloadTests(unittest.TestCase):
    def test_full_native_bytes_nodes_and_validation_survive_ipc(self):
        region = fixture(box=True, position=(0., 0., 0.))
        col, scene = scene_export.build_scene(region,
            primitive_policy=scene_export.TESSELLATE_SPHERES, hour=12)
        inputs = probe_gta_scene.prepare_inputs(col, scene)
        qb, report = rail_export.build_rails(rails.generate(region), scene, sort_native=True)
        inputs['rails'] = dict(source=qb, report=report)
        inputs['rails']['validation'] = probe_world_rails.prepare_rail_validation(inputs)
        original = dict(inputs=inputs, key='same', stats=scene['stats'])
        before = copy.deepcopy(original)
        compact = runtime_world_result(original)
        self.assertEqual(original, before)
        self.assertIs(compact['inputs']['col'], col)
        self.assertIs(compact['inputs']['rails']['source'], qb)
        self.assertIs(compact['inputs']['rails']['report']['nodes'], report['nodes'])
        received = pickle.loads(pickle.dumps(compact))
        other = received['inputs']
        self.assertNotIn('candidate_provenance', other['rails']['report'])
        self.assertEqual(other['rails']['report'], {key: value for key, value in report.items()
                                                   if key != 'candidate_provenance'})
        self.assertEqual(other['col'], col)
        self.assertEqual(other['rails']['source'], qb)
        self.assertEqual(other['rails']['report']['nodes'], report['nodes'])
        self.assertEqual(hashlib.sha256(other['col']).hexdigest(), inputs['col_sha256'])
        self.assertEqual(hashlib.sha256(other['rails']['source']).hexdigest(), report['qb_sha256'])
        self.assertEqual(probe_gta_scene._seal(other), inputs['seal'])
        self.assertEqual(probe_world_rails.prepare_rail_validation(other), inputs['rails']['validation'])
        self.assertEqual(probe_world_rails.verified_rail_identity(other), report['qb_sha256'])
        other['rails']['report']['nodes'][0]['position_thaw'][0] += 1
        with self.assertRaises(RuntimeError):
            probe_world_rails.verified_rail_identity(other)

    def test_empty_or_absent_rails_keep_existing_contract(self):
        self.assertIsNone(runtime_rail_report(None))
        without = dict(inputs=dict(col=b'col'))
        self.assertIs(runtime_world_result(without), without)
        empty = dict(inputs=dict(col=b'col', rails=dict(source=None, report=None)))
        self.assertEqual(runtime_world_result(empty), empty)


if __name__ == '__main__':
    unittest.main()
