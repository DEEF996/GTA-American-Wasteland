"""Synthetic native-format geometry proofs; no installed game or native calls."""
import copy
from collections import Counter
import itertools
import math
import struct
import unittest
from unittest.mock import patch

import geometry as g
import scene_export as s


def fixture(area=0, position=(100, 200, 30), quaternion=(0, 0, 0, 1), box=False):
    model = {"name": "test", "flags": 2, "vertices": [[-1, -1, 0], [1, -1, 0], [1, 1, 0], [-1, 1, 0]],
             # Clockwise GTA indices produce a positive-Z native GTA plane.
             "faces": [[0, 2, 1, 37, 93], [0, 3, 2, 37, 93]], "boxes": [], "spheres": []}
    if box:
        model.update(vertices=[], faces=[], boxes=[[-1, -2, -3, 1, 2, 3, 47, 2, 17, 9]])
    instance = {**g.placement(1000, position, quaternion, area, -1, "test"),
                "instance_id": 1, "source": "synthetic.ipl", "stream": "synthetic", "dynamic": False,
                "conditional": False, "definition": {"time_window": None}}
    return {"format": "gta-sa-region-v1", "coordinate_system": "gta-z-up", "center": list(position),
            "radius": 32, "interior": area, "models": {"test": model}, "instances": [instance],
            "omitted": {}, "live_dynamic_transforms_applied": False}


def normal(a, b, c):
    ab, ac = [[p[i]-a[i] for i in range(3)] for p in (b, c)]
    return (ab[1]*ac[2]-ab[2]*ac[1], ab[2]*ac[0]-ab[0]*ac[2], ab[0]*ac[1]-ab[1]*ac[0])


def build(region, **kwargs):
    return s.build_scene(region, primitive_policy=s.REJECT_SPHERES, hour=14, **kwargs)


def triangles(col):
    indices = col["indices"]
    return zip(indices[::3], indices[1::3], indices[2::3])


class SceneExportTests(unittest.TestCase):
    def test_native_gta_winding_becomes_upward_thaw_floor_and_one_metre_scale(self):
        region = fixture()
        before = copy.deepcopy(region)
        payload, report = build(region)
        col = s.parse_col(payload)
        self.assertEqual(region, before)
        self.assertEqual(report["coordinates"]["origin_gta"], [100, 200, 30])
        self.assertTrue(report["selected_primitives_complete"])
        self.assertFalse(report["physics_ready"])
        for indices in triangles(col):
            self.assertGreater(normal(*(col["positions"][i] for i in indices))[1], 0)
        self.assertAlmostEqual(col["bounds"]["max"][0], 1/.0254, places=5)
        self.assertEqual(col["face_flags"], [0, 0])
        self.assertEqual(col["terrain_types"], [17, 17])  # GTA rock cliff -> stock THAW rock.
        self.assertEqual([row[3] for row in report["face_provenance_in_col_order"]], [[37, 93], [37, 93]])

    def test_rotated_box_has_twelve_outward_faces_and_keeps_source_surface(self):
        region = fixture(area=13, quaternion=(.5, 0, 0, math.sqrt(.75)), box=True)
        payload, report = build(region)
        col = s.parse_col(payload)
        self.assertEqual(report["stats"]["col_triangles"], 12)
        self.assertEqual(report["selection"]["interior"], 13)
        for indices in triangles(col):
            points = [col["positions"][i] for i in indices]
            center = [sum(p[a] for p in points)/3 for a in range(3)]
            self.assertGreater(sum(a*b for a, b in zip(normal(*points), center)), 0)
        self.assertTrue(all(row[3] == [47, 2, 17, 9] for row in report["face_provenance_in_col_order"]))

    def test_vertical_walls_receive_native_flag_without_changing_floors_or_material(self):
        region = fixture(box=True)
        payload, report = build(region)
        col = s.parse_col(payload)
        expected = []
        for indices in triangles(col):
            n = normal(*(col['positions'][i] for i in indices))
            expected.append(4 if n[1] == 0 else 0)
        self.assertEqual(col['face_flags'], expected)
        self.assertEqual(Counter(expected), {4: 8, 0: 4})
        self.assertEqual(report['wallride_triangles'], 8)
        self.assertEqual(col['terrain_types'], [0] * 12)
        self.assertEqual(report['surface_policy']['thaw_face_flags'], [0, 4])

    def test_wall_metadata_uses_transformed_normals_and_conservative_five_degree_limit(self):
        for angle, expected in ((0, 4), (4.9, 4), (5.1, 0), (45, 0), (90, 0)):
            region = fixture()
            model = region['models']['test']
            model['vertices'].extend([[-1, 0, -1], [1, 0, -1], [1, 0, 1], [-1, 0, 1]])
            model['faces'].extend([[4, 6, 5, 37, 93], [4, 7, 6, 37, 93]])
            half = math.radians(angle) / 2
            # Force GTA's full quaternion branch; its normal IPL fast path
            # treats rotations with small X/Y components as heading only.
            component = math.sin(half) / math.sqrt(1.000001)
            region['instances'][0]['quaternion'] = [component, component*.001, 0., math.cos(half)]
            region['instances'][0]['instance_flags'] |= 0x200
            payload, report = build(region)
            col = s.parse_col(payload)
            flags = [flag for flag, evidence in zip(col['face_flags'], report['face_provenance_in_col_order'])
                     if evidence[2] >= 2]
            bank = 256 if angle in (5.1, 45) else 0
            self.assertEqual(flags, [expected | bank, expected | bank], angle)
            self.assertEqual(col['terrain_types'], [17] * 4)

    def test_all_area_ids_and_origin_change_are_explicit(self):
        for area in (0, 1, 13, 255):
            region = fixture(area)
            first, _ = build(region)
            second, report = build(region, origin=(99, 200, 30))
            self.assertEqual(report["selection"]["interior"], area)
            self.assertNotEqual(s.parse_col(first)["objects"][0]["checksum"],
                                s.parse_col(second)["objects"][0]["checksum"])
            self.assertAlmostEqual(s.parse_col(second)["bounds"]["min"][0], 0.)

    def test_static_selection_has_identical_native_bytes_across_hours(self):
        region = fixture()
        first, before = s.build_scene(region, primitive_policy=s.REJECT_SPHERES, hour=14)
        second, after = s.build_scene(region, primitive_policy=s.REJECT_SPHERES, hour=15)
        self.assertEqual(first, second)
        self.assertEqual(before["source_region_sha256"], after["source_region_sha256"])
        self.assertEqual(before["col_sha256"], after["col_sha256"])
        self.assertEqual(before["selection"]["hour"], 14)
        self.assertEqual(after["selection"]["hour"], 15)

    def test_changed_time_object_selection_changes_native_bytes(self):
        daytime, nighttime = fixture(), fixture()
        timed = copy.deepcopy(nighttime["instances"][0])
        timed.update(instance_id=2, position=[100., 200., 34.])
        timed["definition"]["time_window"] = [20, 6]
        nighttime["instances"].append(timed)
        first, before = s.build_scene(daytime, primitive_policy=s.REJECT_SPHERES, hour=14)
        second, after = s.build_scene(nighttime, primitive_policy=s.REJECT_SPHERES, hour=21)
        self.assertNotEqual(first, second)
        self.assertNotEqual(before["source_region_sha256"], after["source_region_sha256"])
        self.assertEqual(after["stats"]["source_triangles"], 4)
        with self.assertRaisesRegex(ValueError, "Inactive time model"):
            s.build_scene(nighttime, primitive_policy=s.REJECT_SPHERES, hour=14)

    def test_exact_and_research_sphere_policies_remain_explicit(self):
        region = fixture()
        region["models"]["test"]["spheres"] = [[0, 0, 0, 2, 51, 0, 0, 9]]
        with self.assertRaisesRegex(ValueError, "no exact sphere"):
            build(region)
        payload, report = s.build_scene(region, primitive_policy=s.RESEARCH_SUBSET, hour=14)
        self.assertEqual(s.parse_col(payload)["stats"]["triangles"], 2)
        self.assertEqual(report["omitted"]["spheres"], 1)
        self.assertFalse(report["selected_primitives_complete"])
        with self.assertRaisesRegex(ValueError, "Explicit primitive policy"):
            s.build_scene(region, primitive_policy="approximate", hour=14)

    def test_global_area_geometry_is_exported_without_accepting_other_interiors(self):
        for area in (0, 1, 13, 255):
            region = fixture(area=13)
            region['interior'] = area
            payload, report = build(region)
            self.assertEqual(report['selection']['interior'], area)
            self.assertEqual(s.parse_col(payload)['stats']['triangles'], 2)
        region['instances'][0]['instance_flags'] = 1
        with self.assertRaisesRegex(ValueError, 'different area'):
            build(region)

    def test_exact_zero_source_face_is_reported_and_tiny_nonzero_face_preserved(self):
        region = fixture(position=(0, 0, 0))
        model = region['models']['test']
        first = len(model['vertices'])
        model['vertices'].extend([[0, 0, 0], [1e-9, 0, 0], [0, 1e-9, 0]])
        model['faces'].extend([[0, 0, 0, 47, 9], [first, first+1, first+2, 47, 9]])
        payload, report = build(region, origin=(0, 0, 0))
        self.assertEqual(s.parse_col(payload)['stats']['triangles'], 3)
        self.assertEqual(report['stats']['zero_area_source_faces_removed'], 1)
        self.assertEqual(report['stats']['zero_area_float32_faces_removed'], 0)
        removed = report['zero_area_faces']['removed']
        self.assertEqual([(row['primitive_index'], row['reason']) for row in removed], [(2, 'source_zero_area')])
        self.assertEqual([row[2] for row in report['face_provenance_in_col_order']], [0, 1, 3])

    def test_float32_collapsed_box_sides_are_reported_and_both_surfaces_remain(self):
        region = fixture(position=(-1985, 138, 30))
        region['models']['test']['boxes'] = [[-1., 0., -3., 1., 0.00000286, 3., 47, 2, 17, 9]]
        payload, report = build(region, origin=(2240, -1262, 24))
        parsed = s.parse_col(payload)
        self.assertEqual(parsed['stats']['triangles'], 6)
        self.assertEqual(parsed['stats']['degenerate_triangles_preserved'], 0)
        self.assertEqual(report['stats']['zero_area_float32_faces_removed'], 8)
        self.assertEqual(report['stats']['zero_area_source_faces_removed'], 0)
        self.assertEqual(report['omitted']['zero_area_faces'], 8)
        self.assertFalse(report['geometry_exact'])
        normals = [normal(*(parsed['positions'][i] for i in tri)) for tri in triangles(parsed)]
        self.assertTrue(any(v[2] > 0 for v in normals))
        self.assertTrue(any(v[2] < 0 for v in normals))

    def test_selection_refuses_dynamic_other_area_and_inactive_conditions(self):
        for mutate in (lambda i: i.update(dynamic=True), lambda i: i.update(interior=1),
                       lambda i: i.update(conditional=True),
                       lambda i: i.update(definition={"time_window": [20, 6]})):
            region = fixture()
            mutate(region["instances"][0])
            with self.assertRaises(ValueError):
                build(region)
        region = fixture()
        region["instances"][0]["conditional"] = True
        _, report = build(region, active_streams=["synthetic"])
        self.assertEqual(report["selection"]["active_streams"], ["synthetic"])

    def test_missing_arrays_duplicate_ids_and_budgets_do_not_silently_truncate(self):
        region = fixture(box=True)
        with self.assertRaisesRegex(ValueError, "triangle budget"):
            build(region, max_triangles=11)
        with self.assertRaisesRegex(ValueError, "COL byte budget"):
            build(region, max_col_bytes=236)
        region["instances"].append(copy.deepcopy(region["instances"][0]))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            build(region)
        region = fixture()
        del region["models"]["test"]["spheres"]
        with self.assertRaisesRegex(ValueError, "primitive arrays"):
            build(region)
        for origin in ((0, 0), (0, float("nan"), 0), (0, float("inf"), 0)):
            with self.assertRaises(ValueError):
                build(fixture(), origin=origin)

    def test_partition_preserves_compiled_face_provenance(self):
        region = fixture(position=(0, 0, 0), box=True)
        template = region["instances"][0]
        region["instances"] = [{**copy.deepcopy(template), "instance_id": i+1, "position": [x, 0, 0]}
                               for i, x in enumerate((15, -15, 9, -9, 3, -3))]
        payload, report = build(region)
        col = s.parse_col(payload)
        self.assertGreater(report["stats"]["col_objects"], 1)
        positions = {i["instance_id"]: i["position"][0] for i in region["instances"]}
        for triangle, provenance in zip(triangles(col), report["face_provenance_in_col_order"]):
            x = positions[provenance[0]]
            for index in triangle:
                self.assertLessEqual(abs(col["positions"][index][0] * .0254 - x), 1.000001)
        self.assertEqual(report["gta_material_triangle_counts"], {47: 72})

    def test_planar_boxes_keep_both_exact_surfaces_for_all_axes_and_rotations(self):
        for axis in range(3):
            for quaternion in ((0, 0, 0, 1), (.5, 0, 0, math.sqrt(.75))):
                with self.subTest(axis=axis, quaternion=quaternion):
                    region = fixture(quaternion=quaternion)
                    box = [-1, -2, -3, 1, 2, 3, 47, 2, 17, 9]
                    box[axis+3] = box[axis]
                    region['models']['test']['boxes'] = [box]
                    payload, report = build(region, max_triangles=6)
                    parsed = s.parse_col(payload)
                    exported = [tri for tri, source in zip(triangles(parsed), report['face_provenance_in_col_order'])
                                if source[1] == 'box']
                    self.assertEqual(len(exported), 4)
                    self.assertEqual(report['stats']['planar_boxes'], 1)
                    self.assertEqual(report['omitted']['zero_volume_boxes'], 0)
                    self.assertEqual(report['stats']['zero_area_faces_removed'], 0)
                    self.assertTrue(report['selected_primitives_complete'])
                    self.assertTrue(report['geometry_exact'])
                    self.assertEqual(report['gta_material_triangle_counts'][47], 4)
                    instance = region['instances'][0]
                    rotation = s.g.rotation_matrix(instance['quaternion'], instance['instance_flags'])
                    expected = {tuple(struct.unpack('<3f', struct.pack('<3f', *s.g.gta_to_thaw(
                        s.g.transform(point, rotation, instance['position']), origin=report['coordinates']['origin_gta']))))
                        for point in itertools.product(*zip(box[:3], box[3:6]))}
                    actual = {tuple(parsed['positions'][i]) for tri in exported for i in tri}
                    self.assertEqual(actual, expected)
                    normals = [normal(*(parsed['positions'][i] for i in tri)) for tri in exported]
                    self.assertTrue(all(any(s.dot(a, b) < -.9999 for b in normals) for a in normals))
                    self.assertTrue(all(row[2:] == [0, [47, 2, 17, 9]]
                                        for row in report['face_provenance_in_col_order'] if row[1] == 'box'))

    def test_line_or_point_boxes_remain_explicitly_unsupported(self):
        region = fixture()
        for box in ([0, 0, -1, 0, 0, 1, 7, 0, 0, 0], [0, 0, 0, 0, 0, 0, 7, 0, 0, 0]):
            region["models"]["test"]["boxes"] = [box]
            with self.assertRaisesRegex(ValueError, "Line/point"):
                build(region)
        _, report = s.build_scene(region, primitive_policy=s.RESEARCH_SUBSET, hour=14)
        self.assertEqual(report["omitted"]["zero_volume_boxes"], 1)
        self.assertFalse(report["selected_primitives_complete"])
        region["models"]["test"]["faces"] = []
        with self.assertRaisesRegex(ValueError, "no supported"):
            s.build_scene(region, primitive_policy=s.RESEARCH_SUBSET, hour=14)

    def test_sphere_subdivision_closed_outward_and_within_two_centimetres(self):
        # Different radii require different subdivision levels. Tilted placement
        # transforms the center; native GTA leaves the sphere radius unchanged.
        for radius in (.15, .28, .608, 1.158, 6.65):
            region = fixture(area=255, quaternion=(.5, 0, 0, math.sqrt(.75)))
            region["models"]["test"].update(vertices=[], faces=[],
                spheres=[[1, 2, 3, radius, 53, 7, 11, 91]])
            payload, report = s.build_scene(region, primitive_policy=s.TESSELLATE_SPHERES, hour=14)
            col = s.parse_col(payload)
            matrix = g.rotation_matrix(region["instances"][0]["quaternion"], 255)
            center = g.gta_to_thaw(g.transform((1, 2, 3), matrix, region["center"]), origin=region["center"])
            # The writer duplicates vertices between COL objects: weld exactly
            # equal stored triples only when checking the closed mesh topology.
            edges = Counter()
            for ids in triangles(col):
                vertices = [tuple(col["positions"][i]) for i in ids]
                a, b, c = vertices
                n = normal(a, b, c)
                length = math.sqrt(sum(v*v for v in n))
                plane_radius = sum(n[i]*(a[i]-center[i]) for i in range(3))/length*.0254
                self.assertGreater(plane_radius, 0)
                self.assertLessEqual(radius-plane_radius, .02)
                for i in range(3):
                    edge = tuple(sorted((vertices[i], vertices[(i+1) % 3])))
                    edges[edge] += 1
                for vertex in vertices:
                    self.assertLessEqual(abs(math.dist(vertex, center)*.0254-radius), .02)
            self.assertTrue(edges and all(count == 2 for count in edges.values()))
            self.assertTrue(report["selected_primitives_complete"])
            self.assertFalse(report["geometry_exact"])
            self.assertFalse(report["runtime_tested"])
            self.assertEqual(report["omitted"]["spheres"], 0)
            self.assertLessEqual(report["sphere_approximation"]["maximum_certified_error_metres"], .02)
            self.assertTrue(all(row[1:] == ["sphere", 0, [53, 7, 11, 91]]
                                for row in report["face_provenance_in_col_order"]))

    def test_sphere_policy_is_deterministic_and_budget_fails_before_compile(self):
        region = fixture()
        region["models"]["test"]["spheres"] = [[0, 0, 0, 6.65, 53, 0, 0, 0]]
        kwargs = dict(primitive_policy=s.TESSELLATE_SPHERES, hour=14)
        before = copy.deepcopy(region)
        self.assertEqual(s.build_scene(region, **kwargs), s.build_scene(region, **kwargs))
        self.assertEqual(region, before)
        with patch.object(s, "compile_col") as compiler:
            with self.assertRaisesRegex(ValueError, "triangle budget after sphere"):
                s.build_scene(region, max_triangles=5120, **kwargs)
            compiler.assert_not_called()
        for radius in (0, -1, float("nan")):
            region["models"]["test"]["spheres"][0][3] = radius
            with self.assertRaises(ValueError):
                s.build_scene(region, **kwargs)

    def test_sphere_float_storage_origin_error_is_part_of_bound(self):
        region = fixture()
        region["models"]["test"].update(vertices=[], faces=[], spheres=[[0, 0, 0, .28, 0, 0, 0, 0]])
        kwargs = dict(primitive_policy=s.TESSELLATE_SPHERES, hour=14)
        _, local = s.build_scene(region, **kwargs)
        _, shifted = s.build_scene(region, origin=(101, 202, 33), **kwargs)
        self.assertGreater(shifted["coordinates"]["maximum_rounding_error_metres"],
                           local["coordinates"]["maximum_rounding_error_metres"])
        with self.assertRaisesRegex(ValueError, "origin cannot preserve"):
            s.build_scene(region, origin=(250000, 250000, 250000), **kwargs)


if __name__ == "__main__":
    unittest.main()
