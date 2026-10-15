"""Synthetic native-board boundary/math tests without proprietary fixtures."""
import math
import struct
import unittest

import board_preview as b
from retarget_pose import IDENTITY


def bundle():
    model, rig = b"model-pose", b"r"*32
    meshes, vertices, indices, pixels = [], bytearray(), [], bytes(20)
    for i, count in enumerate((112, 112, 112, 112, 110)):
        index_count = 336 if i == 4 else 330
        meshes.append(b.MESH.pack(len(vertices)//b.VERTEX.size, count, len(indices), index_count, i, 1, 3, i % 2))
        vertices.extend(b.VERTEX.pack(0, 0, 0, 0, 0, 1, 0x80808080, 0, 0,48,0,0,0,1,0,0,0)*count)
        indices.extend(j % count for j in range(index_count))
    textures = [b.TEXTURE.pack(1, 1, b.PIXEL_OFFSET+4*i, 4) for i in range(5)]
    size = b.PIXEL_OFFSET+len(pixels)
    data = (b.HEADER.pack(b"GTABRD02", 2, size, 5, 558, 1656, 5, b.sha(model), rig, bytes.fromhex(b.MODEL_SHA),*IDENTITY)
            + b"".join(meshes+textures)+vertices+struct.pack("<1656H", *indices)+pixels)
    return data, model, rig


class BoardTests(unittest.TestCase):
    def test_exported_triangle_triplets_preserve_winding_and_local_indices(self):
        self.assertEqual(b.triangle_indices([[2, 0, 1], [1, 0, 3]], 4), [2, 0, 1, 1, 0, 3])
        for triangles in ([0, 1, 2], [[0, 1]], [[0, 1, 4]], [[0, -1, 2]], [[True, 1, 2]]):
            with self.subTest(triangles=triangles), self.assertRaises(ValueError):
                b.triangle_indices(triangles, 4)

    def test_exact_layout_and_linkage(self):
        data, model, rig = bundle()
        self.assertEqual(b.PIXEL_OFFSET, 34992)
        self.assertEqual(b.validate_bundle(data, model, rig), b.sha(data))
        for changed_model, changed_rig in ((b"other", rig), (model, b"q"*32)):
            with self.assertRaisesRegex(ValueError, "different bind"):
                b.validate_bundle(data, changed_model, changed_rig)

    def test_bad_ranges_indices_and_nonfinite_vertices(self):
        data, model, rig = bundle()
        edits = [(b.HEADER.size, "<I", 1), (b.HEADER.size+4, "<I", 0xffffffff),
                 (b.HEADER.size+5*b.MESH.size+8, "<I", 0xffffffff),
                 (b.PREFIX_SIZE+558*b.VERTEX.size, "<H", 112), (b.PREFIX_SIZE, "<f", math.nan),
                 (b.PREFIX_SIZE+20, "<f", 2), (b.PREFIX_SIZE+36,"<B",47),
                 (b.PREFIX_SIZE+40,"<f",.9), (128,"<f",math.nan)]
        for offset, fmt, value in edits:
            changed = bytearray(data)
            struct.pack_into(fmt, changed, offset, value)
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                b.validate_bundle(changed, model, rig)
        for changed in (data[:-1], data+b"x"):
            with self.assertRaises(ValueError):
                b.validate_bundle(changed, model, rig)

    def test_skin_uses_original_weights_then_model_calibration(self):
        palettes = [list(IDENTITY) for _ in range(52)]
        palettes[48][12:15] = [10, 0, 0]
        palettes[49][12:15] = [0, 20, 0]
        basis = [0.,1.,0.,0., -1.,0.,0.,0., 0.,0.,1.,0., 0.,0.,0.,1.]
        point, normal = b.skin_vertex([1, 2, 3], [1, 0, 0], [48, 49, 0, 0], [.25, .75, 0, 0],
                                     palettes, basis, .5, [1, 2, 3], [5, 6, 7])
        self.assertEqual(point, [-2.5, 7.25, 7.])
        self.assertEqual(normal, [0., 1., 0.])

    def test_invalid_bone_or_weights_fail_without_normalization(self):
        for bones, weights in (([47, 0, 0, 0], [1, 0, 0, 0]),
                               ([48, 0, 0, 0], [.9, 0, 0, 0]),
                               ([48, 0, 0, 0], [math.nan, 0, 0, 0])):
            with self.assertRaises(ValueError):
                b.skin_vertex([0]*3, [0, 1, 0], bones, weights, [IDENTITY]*52, IDENTITY, 1, [0]*3, [0]*3)


if __name__ == "__main__":
    unittest.main()
