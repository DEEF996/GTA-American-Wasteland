"""Exact vehicle COL grouping; no game or original native code is executed."""
import struct
import unittest

import scene_export  # Installs the shared, existing tools import path.
from assets_export import parse_col
from carentan_col import align, compile_col, validate_bsp
import vehicle_col as v


def source(count=600, *, coincident=False):
    positions, triangles = [], []
    for i in range(count):
        x, z = ((0., 0.) if coincident else ((i % 30)-15., (i//30)-10.))
        first = len(positions)
        positions.extend(((x-.75, 0., z), (x+.75, 0., z), (x, .5, z+.9)))
        triangles.append((first, first+1, first+2))
    return dict(format='thaw-col-v10', positions=positions, indices=triangles,
                face_flags=[4*(i % 2) for i in range(count)],
                terrain_types=[i % 32 for i in range(count)])


def triangle_bytes(parsed):
    points, indices = parsed['positions'], parsed['indices']
    return [struct.pack('<9fHH', *(p for vertex in indices[i*3:i*3+3] for p in points[vertex]), flag, terrain)
            for i, (flag, terrain) in enumerate(zip(parsed['face_flags'], parsed['terrain_types']))]


def pools(data):
    _, count, total, faces, _, vertices = struct.unpack_from('<6I', data)
    tail = align(align(80+count*64, 16)+vertices*12+total, 4)
    tail = align(tail+faces*11, 4)
    size, = struct.unpack_from('<I', data, tail)
    return tail+4, tail+4+size


def inspect_tree(data, root, node_base, refs_base, query=None):
    pending, leaves, found, count = [(root, 0)], [], [], 0
    while pending:
        node, depth = pending.pop()
        word, second = struct.unpack_from('<II', data, node_base+node)
        axis = word & 3
        if axis == 3:
            size = word >> 16
            ids = struct.unpack_from('<'+str(size)+'H', data, refs_base+second*2)
            leaves.append((depth, size)); found.extend(ids); count += size
        else:
            signed, = struct.unpack('<i', struct.pack('<I', word))
            plane = (signed >> 2)/16
            if query is None or query[0][axis] < plane:
                pending.append((second, depth+1))
            if query is None or query[1][axis] >= plane:
                pending.append((second+8, depth+1))
    return leaves, found, count


class VehicleColTests(unittest.TestCase):
    def test_grouping_preserves_every_ordered_float32_triangle_and_material(self):
        original = compile_col(source(), coordinates='thaw')
        packed = v.repack_col(original)
        before, after = parse_col(original), parse_col(packed)
        self.assertEqual(triangle_bytes(before), triangle_bytes(after))
        self.assertLess(after['stats']['objects'], before['stats']['objects'])
        self.assertEqual(before['bounds'], after['bounds'])
        self.assertEqual(v.repack_col(original), packed)
        validate_bsp(packed)

    def test_each_face_survives_native_split_comparisons_including_plane_boundary(self):
        packed = v.repack_col(compile_col(source(1300), coordinates='thaw'))
        parsed, (node_base, refs_base) = parse_col(packed), pools(packed)
        for number, obj in enumerate(parsed['objects']):
            root = struct.unpack_from('<I', packed, 80+number*64+52)[0]
            leaves, found, total = inspect_tree(packed, root, node_base, refs_base)
            self.assertLessEqual(obj['face_count'], v.MAX_OBJECT_FACES)
            self.assertLessEqual(total, v.MAX_OBJECT_REFERENCES)
            self.assertEqual(set(found), set(range(obj['face_count'])))
            self.assertTrue(all(depth <= v.MAX_DEPTH and n <= v.MAX_LEAF_FACES for depth, n in leaves))
            for local in range(obj['face_count']):
                index = obj['face_start']+local
                vertices = [parsed['positions'][j] for j in parsed['indices'][index*3:index*3+3]]
                # A degenerate point AABB on every corner is particularly strict:
                # split-plane equality must retain the face in the right child.
                for point in vertices:
                    _, candidates, _ = inspect_tree(packed, root, node_base, refs_base, (point, point))
                    self.assertIn(local, candidates)

    def test_coincident_geometry_falls_back_to_more_objects_without_loss(self):
        original = compile_col(source(200, coincident=True), coordinates='thaw')
        packed = v.repack_col(original)
        before, after = parse_col(original), parse_col(packed)
        self.assertEqual(triangle_bytes(before), triangle_bytes(after))
        self.assertEqual(after['stats']['objects'], 4)
        self.assertTrue(all(o['face_count'] <= 50 for o in after['objects']))

    def test_reference_budget_is_for_all_leaves_not_only_unique_faces(self):
        old = v.MAX_OBJECT_REFERENCES
        try:
            v.MAX_OBJECT_REFERENCES = 64
            packed = v.repack_col(compile_col(source(300), coordinates='thaw'))
            node_base, refs_base = pools(packed)
            for i in range(parse_col(packed)['stats']['objects']):
                root = struct.unpack_from('<I', packed, 80+i*64+52)[0]
                self.assertLessEqual(inspect_tree(packed, root, node_base, refs_base)[2], 64)
        finally:
            v.MAX_OBJECT_REFERENCES = old

    def test_invalid_original_col_remains_rejected(self):
        with self.assertRaises(ValueError):
            v.repack_col(bytes(80))


if __name__ == '__main__':
    unittest.main()
