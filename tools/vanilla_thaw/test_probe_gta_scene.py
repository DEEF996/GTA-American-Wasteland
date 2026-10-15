"""Pure COL/ray/result validation only; no native process or installed sources."""
import copy
import hashlib
import math
import random
import struct
import unittest

import probe_gta_scene as scene
from carentan_col import compile_col


def report_for(payload, kind="triangle"):
    parsed = scene.parse_col(payload)
    return {"format": "gta-thaw-static-scene-v1", "col_sha256": hashlib.sha256(payload).hexdigest(),
            "source_region_sha256": "0"*64, "selected_primitives_complete": True,
            "runtime_tested": False, "live_collision_complete": False,
            "surface_policy": scene.surface_flags.surface_policy(parsed['face_flags']),
            "wallride_triangles": parsed['face_flags'].count(4),
            "stats": {"col_triangles": parsed["stats"]["triangles"], "col_objects": parsed["stats"]["objects"],
                      "col_bytes": len(payload)},
            "coordinates": {"thaw_units_per_gta_unit": scene.SCALE, "axes": "(x,z,-y)",
                            "position_storage": "float32", "origin_gta": [0, 0, 0]},
            "selection": {"center_gta": [0, 0, 0], "interior": 0, "hour": 14, "active_streams": []},
            "face_provenance_in_col_order": [[1, kind, i, [0, 0]] for i in range(parsed["stats"]["triangles"])]}


def layers(reverse_upper=False):
    points = []
    for y in (0, 10):
        points.extend([[-128, y, -128], [-128, y, 128], [128, y, 128]])
    faces = [[0, 1, 2], [3, 5, 4] if reverse_upper else [3, 4, 5]]
    return scene._mesh(scene.parse_col(compile_col({"positions": points, "indices": faces}, coordinates="thaw")))


class GtaSceneProbeTests(unittest.TestCase):
    def setUp(self):
        self.payload = scene.floor.floor_fixture()
        self.report = report_for(self.payload)

    def test_preparation_uses_final_col_and_skips_ambiguous_center_seam(self):
        before = copy.deepcopy(self.report)
        prepared = scene.prepare_inputs(self.payload, self.report)
        self.assertEqual(self.report, before)
        self.assertEqual(prepared["seal"], scene._seal(prepared))
        self.assertEqual([case["name"] for case in prepared["cases"]], ["primitive_triangle", "outside_bounds"])
        hit, miss = prepared["cases"]
        self.assertEqual(hit["normal"], (0, 1, 0))
        self.assertEqual(hit["point"][1], 0)
        self.assertFalse(miss["hit"])
        self.assertEqual(prepared["stats"]["triangles"], 2)
        self.assertIn("unverified", prepared["backface_policy"])

    def test_mesh_reference_retains_exact_arithmetic_and_zero_area_guard(self):
        rng = random.Random(7321)
        points = [list(struct.unpack('<3f', struct.pack('<3f',
                  *(rng.uniform(-90000, 90000) for _ in range(3))))) for _ in range(90)]
        expected = []
        for i in range(0, len(points), 3):
            a, b, c = points[i:i+3]
            e1, e2 = scene._sub(b, a), scene._sub(c, a)
            normal = scene._cross(e1, e2)
            length = math.sqrt(scene._dot(normal, normal))
            expected.append((a, e1, e2, tuple(v/length for v in normal),
                tuple(min(p[j] for p in (a, b, c)) for j in range(3)),
                tuple(max(p[j] for p in (a, b, c)) for j in range(3))))
        self.assertEqual(scene._mesh(dict(positions=points, indices=list(range(90)))), expected)
        with self.assertRaisesRegex(RuntimeError, 'nondegenerate'):
            scene._mesh(dict(positions=[[0., 0., 0.]], indices=[0, 0, 0]))

    def test_independently_validated_rail_auxiliary_preserves_only_the_collision_seal(self):
        prepared = scene.prepare_inputs(self.payload, self.report)
        prepared['rails'] = dict(source=b'auxiliary original-format QB bytes', report={})
        self.assertEqual(prepared['seal'], scene._seal(prepared))
        changed_metadata = copy.deepcopy(prepared)
        changed_metadata['bounds']['min'][0] -= 1
        self.assertNotEqual(changed_metadata['seal'], scene._seal(changed_metadata))
        prepared['col'] = prepared['col'][:-1] + bytes([prepared['col'][-1] ^ 1])
        with self.assertRaisesRegex(RuntimeError, 'changed after validation'):
            scene.run(None, None, None, None, None, prepared, None)

    def test_reference_uses_nearest_actual_surface_not_selected_triangle(self):
        result = scene._control(layers(), "stack", (-30, 20, 40), (-30, -20, 40), True)
        self.assertEqual(result["face"], 1)
        self.assertEqual(result["point"], (-30, 10, 40))

    def test_occluded_nearest_facets_do_not_hide_other_source_primitives(self):
        points = []
        for x, y in ((0, 0), (0, 1), (200, 0), (100, 0)):
            points.extend([[x-5, y, -5], [x-5, y, 5], [x+5, y, 5]])
        # The reversed blocker makes all 16 nearest sphere facets ambiguous.
        faces = [[0, 1, 2]]*16 + [[3, 5, 4], [6, 7, 8], [9, 10, 11]]
        payload = compile_col(dict(positions=points, indices=faces), coordinates='thaw')
        report = report_for(payload)
        report['face_provenance_in_col_order'] = ([[1, 'sphere', 0, [0, 0]] for _ in range(16)] +
            [[1, 'triangle', 0, [0, 0]], [2, 'sphere', 0, [0, 0]], [2, 'triangle', 0, [0, 0]]])
        prepared = scene.prepare_inputs(payload, report)
        control = next(row for row in prepared['cases'] if row['name'] == 'primitive_sphere')
        self.assertEqual(control['source_primitive'][:3], [2, 'sphere', 0])
        self.assertGreater(control['point'][0], 190)

    def test_unverified_backface_policy_cannot_choose_a_different_expected_hit(self):
        mesh = layers(reverse_upper=True)
        front, both = scene._cast(mesh, (-30, 20, 40), (-30, -20, 40))
        self.assertEqual(front[0]["point"][1], 0)
        self.assertEqual(both[0]["point"][1], 10)
        with self.assertRaisesRegex(RuntimeError, "ambiguous"):
            scene._control(mesh, "back", (-30, 20, 40), (-30, -20, 40), True)

    def test_edge_nonfinite_and_zero_length_controls_rejected(self):
        mesh = scene._mesh(scene.parse_col(self.payload))
        with self.assertRaisesRegex(RuntimeError, "triangle edge"):
            scene._control(mesh, "seam", (0, 10, 0), (0, -10, 0), True)
        with self.assertRaisesRegex(RuntimeError, "Zero-length"):
            scene._control(mesh, "zero", (0, 1, 0), (0, 1, 0), False)
        with self.assertRaisesRegex(RuntimeError, "Invalid"):
            scene._control(mesh, "nan", (0, float("nan"), 0), (0, -1, 0), True)

    def test_payload_and_report_hash_counts_transform_and_completeness_are_bound(self):
        for mutate in (lambda r: r.update(col_sha256="0"*64),
                       lambda r: r.update(selected_primitives_complete=False),
                       lambda r: r["stats"].update(col_triangles=3),
                       lambda r: r["coordinates"].update(thaw_units_per_gta_unit=1),
                       lambda r: r["face_provenance_in_col_order"].pop()):
            report = copy.deepcopy(self.report)
            mutate(report)
            with self.assertRaises(RuntimeError):
                scene.prepare_inputs(self.payload, report)

    def test_native_profile_rejects_oversized_pools_and_false_header_bounds(self):
        for offset, value in ((12, 200001), (4, 4097), (8, 500001), (40, 41), (16, 1)):
            payload = bytearray(self.payload)
            struct.pack_into("<I", payload, offset, value)
            with self.assertRaisesRegex(RuntimeError, "COL profile"):
                scene.prepare_inputs(bytes(payload), self.report)
        payload = bytearray(self.payload)
        struct.pack_into("<f", payload, 48, -999.)
        report = report_for(payload)
        with self.assertRaisesRegex(RuntimeError, "header bounds"):
            scene.prepare_inputs(bytes(payload), report)

    def test_material_or_packing_cannot_become_a_neutral_contact(self):
        payload = compile_col({"positions": [[-1, 0, -1], [-1, 0, 1], [1, 0, 1], [1, 0, -1]],
                               "indices": [[0, 1, 2], [0, 2, 3]]}, coordinates="thaw", terrain=12)
        with self.assertRaisesRegex(RuntimeError, "material policy"):
            scene.prepare_inputs(payload, report_for(payload))
        payload = bytearray(self.payload)
        struct.pack_into("<I", payload, 80+60, 1)
        with self.assertRaisesRegex(RuntimeError, "packing"):
            scene.prepare_inputs(bytes(payload), report_for(payload))

    def test_wall_flags_follow_geometry_and_survive_native_control_validation(self):
        positions = [[-128,0,-128],[-128,0,128],[128,0,128],[128,0,-128],
                     [200,0,-128],[200,128,-128],[200,128,128],[200,0,128]]
        indices = [[0,1,2],[0,2,3],[4,6,5],[4,7,6]]
        for flags in ([0,0,4,4], [4,4,4,4], [0,0,0,0]):
            payload = compile_col(dict(format='thaw-col-v10', positions=positions,
                indices=indices, face_flags=flags), coordinates='thaw')
            report = report_for(payload)
            for row in report['face_provenance_in_col_order'][2:]:
                row[1] = 'box'
            if flags != [0,0,4,4]:
                # Even a matching payload hash and self-consistent claimed
                # policy/count must not authorize flags on the wrong faces.
                with self.assertRaisesRegex(RuntimeError, 'material policy'):
                    scene.prepare_inputs(payload, report)
                continue
            prepared = scene.prepare_inputs(payload, report)
            case = next(row for row in prepared['cases'] if row['name']=='primitive_box')
            self.assertEqual((case['flags'],case['normal']), (4,(-1.,0.,0.)))
            data = bytearray(0xc0)
            struct.pack_into('<8f',data,0x24,*case['normal'],0.,*case['point'],1.)
            struct.pack_into('<3H',data,0x4c,0x10,0,4)
            self.assertEqual(scene.query_result(data,True,case)['flags'],4)
            struct.pack_into('<H',data,0x50,0)
            with self.assertRaisesRegex(RuntimeError,'material'):
                scene.query_result(data,True,case)

    def test_native_result_must_match_contact_normal_point_material_and_hit(self):
        case = scene.prepare_inputs(self.payload, self.report)["cases"][0]
        data = bytearray(0xc0)
        struct.pack_into("<8f", data, 0x24, *case["normal"], 0., *case["point"], 1.)
        struct.pack_into("<2H", data, 0x4c, 0x10, 0)
        self.assertTrue(scene.query_result(data, True, case)["hit"])
        for offset, value in ((0x28, -1.), (0x38, 1.), (0x30, 1.), (0x40, 0.), (0x24, float("nan"))):
            changed = bytearray(data)
            struct.pack_into("<f", changed, offset, value)
            with self.assertRaises(RuntimeError):
                scene.query_result(changed, True, case)
        changed = bytearray(data)
        struct.pack_into("<H", changed, 0x50, 4)
        with self.assertRaisesRegex(RuntimeError, "material"):
            scene.query_result(changed, True, case)
        with self.assertRaisesRegex(RuntimeError, "hit/miss"):
            scene.query_result(data, False, case)
        miss = scene.prepare_inputs(self.payload, self.report)["cases"][-1]
        self.assertFalse(scene.query_result(data, False, miss)["hit"])
        with self.assertRaises(RuntimeError):
            scene.query_result(bytes(16), False, miss)

    def test_native_input_masks_are_distinct_from_output_flags_and_byte_terrain(self):
        case = scene.prepare_inputs(self.payload, self.report)["cases"][0]
        case.update(flags=4, terrain=9)
        data = bytearray(0xc0)
        struct.pack_into("<8f", data, 0x24, *case["normal"], 0., *case["point"], 1.)
        struct.pack_into("<2H", data, 0x4c, 0x10, 0)
        struct.pack_into("<HBB", data, 0x50, 4, 9, 0xff)  # Terrain is one byte, not a u16.
        result = scene.query_result(data, True, case)
        self.assertEqual((result["flags"], result["terrain"], result["input_masks"]), (4, 9, (16, 0)))
        struct.pack_into("<H", data, 0x4c, 0)
        with self.assertRaisesRegex(RuntimeError, "input masks"):
            scene.query_result(data, True, case)


if __name__ == "__main__":
    unittest.main()
