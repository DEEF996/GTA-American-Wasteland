"""Material propagation and cache guards; no installed game or native calls."""
from copy import deepcopy
import hashlib
import math
import struct
import unittest
from unittest.mock import patch

import terrain_mapping as m
import surface_flags as collision
import rail_export
import rails
import scene_export
import world_tile_cache
from test_scene_export import fixture
import probe_gta_scene
import probe_world_rails


def transition(angles):
    positions, triangles = [], []
    x = y = 0.
    for angle in angles:
        end_x, end_y = x+math.cos(math.radians(angle)), y+math.sin(math.radians(angle))
        start = len(positions)
        positions.extend(tuple(struct.unpack('<3f', struct.pack('<3f', *point))) for point in
                         ((x, y, -1.), (x, y, 1.), (end_x, end_y, -1.), (end_x, end_y, 1.)))
        triangles.extend(((start, start+1, start+3), (start, start+3, start+2)))
        x, y = end_x, end_y
    return positions, triangles


class TransferSurfaceTests(unittest.TestCase):
    def test_concave_quarterpipe_keeps_floor_and_wallride_and_marks_transition(self):
        positions, triangles = transition((0, 10, 30, 50, 70, 90))
        flags = collision.collision_flags(positions, triangles)
        self.assertEqual(flags[:2], [0, 0])
        self.assertTrue(all(flag & collision.VERT for flag in flags[2:]))
        self.assertEqual(flags[-2:], [m.WALL_RIDABLE | collision.VERT] * 2)
        self.assertEqual(flags[4:10], [collision.VERT | collision.BANK] * 6)

    def test_planar_banks_are_targets_without_turning_walls_roads_or_ceiling_into_ramps(self):
        for angle, expected in ((0, 0), (20, 0), (24.9, 0), (25.1, collision.BANK),
                                (45, collision.BANK), (84.9, collision.BANK), (85.1, m.WALL_RIDABLE),
                                (90, m.WALL_RIDABLE), (120, 0), (180, 0)):
            with self.subTest(angle=angle):
                self.assertEqual(collision.collision_flags(*transition((angle,))), [expected] * 2)

    def test_convex_roofs_sharp_corners_and_straight_banks_do_not_become_vert(self):
        for angles in ((90, 70, 50, 30, 10, 0), (0, 90), (30, 30, 30), (10, 70), (70, 90)):
            with self.subTest(angles=angles):
                self.assertFalse(any(flag & collision.VERT for flag in collision.collision_flags(*transition(angles))))

    def test_exact_coordinate_welding_survives_duplicate_vertices_and_face_order(self):
        positions, triangles = transition((10, 30, 50, 70, 90))
        expected = collision.collision_flags(positions, triangles)
        duplicated, disconnected = [], []
        for triangle in reversed(triangles):
            disconnected.append(tuple(range(len(duplicated), len(duplicated)+3)))
            duplicated.extend(positions[index] for index in triangle)
        self.assertEqual(collision.collision_flags(duplicated, disconnected), list(reversed(expected)))
        # Merely similar coordinates on detached surfaces must not close a gap.
        separated = list(positions)
        for i in range(8, len(separated)):
            x, y, z = separated[i]
            separated[i] = (x+.01, y, z)
        self.assertFalse(any(flag & collision.VERT for flag in collision.collision_flags(separated, triangles)))

    def test_float32_translated_and_rotated_quad_diagonals_keep_the_whole_ramp(self):
        positions, triangles = transition((0, 10, 30, 50, 70, 90))
        expected = collision.collision_flags(positions, triangles)
        angle = math.radians(37)
        cosine, sine = math.cos(angle), math.sin(angle)
        for scale, offset in ((40, 10000), (80, 90000)):
            transformed = [struct.unpack('<3f', struct.pack('<3f',
                (x*cosine+z*sine)*scale+offset, y*scale+offset/2, (-x*sine+z*cosine)*scale-offset))
                for x, y, z in positions]
            with self.subTest(scale=scale, offset=offset):
                self.assertEqual(collision.collision_flags(transformed, triangles), expected)

    def test_compiled_partition_and_native_validator_preserve_and_recheck_flags(self):
        positions, triangles = transition((0, 10, 30, 50, 70, 90))
        region = fixture(position=(0, 0, 0))
        model = region['models']['test']
        model['vertices'] = [[x*.0254, -z*.0254, y*.0254] for x, y, z in positions]
        model['faces'] = [[a, c, b, 34, 0] for a, b, c in triangles]
        instance = region['instances'][0]
        region['instances'] = [dict(instance, instance_id=i+1, position=[0., i*.3, 0.]) for i in range(6)]
        payload, report = scene_export.build_scene(region, primitive_policy=scene_export.REJECT_SPHERES, hour=14)
        parsed = probe_gta_scene._validate_col(payload, report)
        self.assertGreater(parsed['stats']['objects'], 1)
        self.assertGreater(report['vert_triangles'], 0)
        self.assertGreater(report['bank_triangles'], 0)
        self.assertEqual(parsed['terrain_types'], [1] * len(triangles) * 6)
        changed = deepcopy(report)
        changed['vert_triangles'] -= 1
        with self.assertRaisesRegex(RuntimeError, 'material policy'):
            probe_gta_scene._validate_col(payload, changed)


class MaterialTests(unittest.TestCase):
    def test_known_ground_rail_wood_concrete_metal_fence_and_track(self):
        for material, ground, pair in (
            (165, 1, ('Concrete', 'TERRAIN_GRINDCONC')),
            (7, 2, ('Concrete', 'TERRAIN_GRINDCONC')),
            (43, 8, ('Wood', 'TERRAIN_GRINDWOOD')),
            (172, 8, ('Wood', 'TERRAIN_GRINDWOODRAILING')),
            (51, 3, ('Metal', 'TERRAIN_GRINDMETAL')),
            (52, 3, ('Metal', 'TERRAIN_GRINDROUNDMETALPOLE')),
            (55, 32, ('Metal', 'TERRAIN_GRINDCHAINLINK')),
            (178, 3, ('Metal', 'TERRAIN_GRINDMETAL'))):
            with self.subTest(material=material):
                self.assertEqual((m.ground(material), m.rail(material)), (ground, pair))
                region = fixture(box=True)
                region['models']['test']['boxes'][0][6] = material
                payload, scene = scene_export.build_scene(region, primitive_policy=scene_export.REJECT_SPHERES, hour=14)
                self.assertEqual(set(scene_export.parse_col(payload)['terrain_types']), {ground})
                prepared = probe_gta_scene.prepare_inputs(payload, scene)
                for case in prepared['cases']:
                    if not case['hit']:
                        continue
                    self.assertEqual(case['terrain'], ground)
                    result = bytearray(0xc0)
                    struct.pack_into('<8f', result, 0x24, *case['normal'], 0., *case['point'], 1.)
                    struct.pack_into('<2H', result, 0x4c, 0x10, 0)
                    struct.pack_into('<H', result, 0x50, case['flags'])
                    result[0x52] = ground
                    self.assertEqual(probe_gta_scene.query_result(result, True, case)['terrain'], ground)
                graph = rails.generate(region)
                qb, report = rail_export.build_rails(graph, scene)
                actual = rail_export.read_nodes(qb)
                self.assertEqual({(row['thaw_type'], row['thaw_terrain']) for row in actual}, {pair})
                prepared['rails'] = dict(source=qb, report=report)
                probe_world_rails.prepare_rail_validation(prepared)
                report['nodes'][0]['gta_surface_material'] = 255
                with self.assertRaisesRegex(RuntimeError, 'material policy'):
                    probe_world_rails.prepare_rail_validation(prepared)

    def test_unknown_ambiguous_and_invalid_materials(self):
        for value in (0, 48, 69, 94, 179, 255):
            self.assertEqual(m.ground(value), 0)
            self.assertEqual(m.rail(value), ('Concrete', 'TERRAIN_DEFAULT'))
        for value in (-1, 256, True, 1.0, None):
            with self.assertRaises(ValueError):
                m.ground(value)
        self.assertEqual(len(m.MATERIALS), sum(len(row[0]) for row in m.GROUPS))

    def test_face_partition_preserves_material_and_rejects_forged_provenance(self):
        region = fixture()
        region['models']['test']['faces'][0][3] = 51
        region['models']['test']['faces'][1][3] = 43
        payload, report = scene_export.build_scene(region, primitive_policy=scene_export.REJECT_SPHERES, hour=14)
        parsed = probe_gta_scene._validate_col(payload, report)
        self.assertEqual(parsed['terrain_types'], [3, 8])
        changed = deepcopy(report)
        changed['face_provenance_in_col_order'][0][3][0] = 43
        with self.assertRaisesRegex(RuntimeError, 'material policy'):
            probe_gta_scene._validate_col(payload, changed)

    def test_ambiguous_overlapping_materials_do_not_authorize_arbitrary_native_result(self):
        mesh = [((-1.,0.,-1.), (0.,0.,2.), (2.,0.,2.), (0.,1.,0.), (-1.,0.,-1.), (1.,0.,1.))]*2
        with self.assertRaisesRegex(RuntimeError, 'competing contact materials'):
            probe_gta_scene._control(mesh, 'overlap', (-.5,1.,0.), (-.5,-1.,0.), True, [3,8])

    def test_mapping_source_is_part_of_tile_fingerprint(self):
        self.assertIn('gta/terrain_mapping.py', world_tile_cache.DEPENDENCIES)
        baseline = world_tile_cache.compiler_fingerprint()
        original = m.Path.read_bytes
        def changed(path):
            raw = original(path)
            return raw+b'\n# changed policy' if path.name == 'terrain_mapping.py' else raw
        with patch.object(m.Path, 'read_bytes', changed):
            self.assertNotEqual(world_tile_cache.compiler_fingerprint(), baseline)

    def test_collision_policy_invalidates_tiles_without_invalidating_global_rail_materials(self):
        self.assertIn('gta/surface_flags.py', world_tile_cache.DEPENDENCIES)
        tiles, materials = world_tile_cache.compiler_fingerprint(), m.fingerprint()
        original = collision.Path.read_bytes
        def changed(path):
            raw = original(path)
            return raw+b'\n# changed collision policy' if path.name == 'surface_flags.py' else raw
        with patch.object(collision.Path, 'read_bytes', changed):
            self.assertNotEqual(world_tile_cache.compiler_fingerprint(), tiles)
            self.assertEqual(m.fingerprint(), materials)


if __name__ == '__main__':
    unittest.main()
