import struct
import unittest

from probe_camera import decode_pose


class CameraTests(unittest.TestCase):
    def raw(self):
        return struct.pack('<26f', 10, 20, 30, 1, 1, 0, 0, 0, 0, 1, 0, 0,
                           0, 0, 1, 0, 0, 0, 0, 1, 72, 82, 1, 0, 0, 1.3333)

    def test_original_backward_row_becomes_forward(self):
        pose = decode_pose(self.raw())
        self.assertEqual(pose['position_thaw'], [10, 20, 30])
        self.assertEqual(pose['forward_thaw'], [0, 0, -1])
        self.assertEqual(pose['up_thaw'], [0, 1, 0])
        self.assertEqual(pose['base_fov_degrees'], 72)
        self.assertIsNone(pose['fov_y_radians'])
        self.assertFalse(pose['current_projection_verified'])

    def test_stale_or_unpublished_projection_is_not_promoted(self):
        raw = bytearray(self.raw())
        struct.pack_into('<f', raw, 84, 0)
        pose = decode_pose(raw)
        self.assertEqual(pose['cached_horizontal_fov_degrees'], 0)
        self.assertIsNone(pose['fov_y_radians'])

    def test_bad_source_axes_position_and_fov_rejected(self):
        for offset, value in ((0, float('nan')), (16, 0), (36, 0), (48, 1), (80, 0)):
            raw = bytearray(self.raw())
            struct.pack_into('<f', raw, offset, value)
            with self.subTest(offset=offset), self.assertRaises(RuntimeError):
                decode_pose(raw)
        with self.assertRaises(RuntimeError):
            decode_pose(self.raw()[:-4])


if __name__ == '__main__':
    unittest.main()
