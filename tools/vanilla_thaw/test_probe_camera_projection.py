import math
import struct
import unittest

import probe_camera_projection as camera


class ProjectionTests(unittest.TestCase):
    def matrix(self, horizontal=90, y_scale=2):
        return struct.pack('<16f', 1 / math.tan(math.radians(horizontal) / 2), 0, 0, 0,
                           0, y_scale, 0, 0, 0, 0, -1.0002, -1, 0, 0, -2.0004, 0)

    def test_vertical_is_decoded_from_original_matrix_not_assumed_aspect(self):
        for yscale in (1, 2, 3):
            values, vertical = camera.decode_projection(self.matrix(y_scale=yscale), 90)
            self.assertAlmostEqual(vertical, 2 * math.atan(1 / yscale))
            self.assertEqual(values[5], yscale)

    def test_reject_different_camera_or_invalid_matrix(self):
        with self.assertRaises(RuntimeError):
            camera.decode_projection(self.matrix(), 72)
        for offset, value in ((0, float('nan')), (20, 0), (44, 1), (60, 1)):
            raw = bytearray(self.matrix())
            struct.pack_into('<f', raw, offset, value)
            with self.subTest(offset=offset), self.assertRaises(RuntimeError):
                camera.decode_projection(raw, 90)

    def test_glue_captures_both_x87_results_and_passes_all_six_original_arguments(self):
        output = 0x20001010
        code = camera.projection_stub(0x21000000, 0x22000000, 0x23000000, output)
        self.assertEqual(code.count(b'\xd9\x1d'), 2)
        self.assertIn(b'\xd9\x1d' + struct.pack('<I', output), code)
        self.assertIn(b'\xd9\x1d' + struct.pack('<I', output + 4), code)
        arguments = b'\x68\0\0\0\0\x68\0\0\x80\x3f'
        arguments += b''.join(b'\xff\x35' + struct.pack('<I', output + offset) for offset in (0, 4, 8, 12))
        self.assertIn(arguments + b'\xb8\x50\x6e\x53\0\xff\xd0\x83\xc4\x18', code)
        targets = [code.index(b'\xb8' + struct.pack('<I', va))
                   for va in (0x4ee020, 0x4c23a0, 0x4c22f0, 0x4c2310, 0x536e50)]
        self.assertEqual(targets, sorted(targets))
        self.assertEqual(len(camera.CANARY), 16)
        with self.assertRaises(RuntimeError):
            camera.projection_stub(0, 0x22000000, 0x23000000, output)


if __name__ == '__main__':
    unittest.main()
