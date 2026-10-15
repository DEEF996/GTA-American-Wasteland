import struct
import unittest

import probe_camera_recenter as camera


class RecenterTests(unittest.TestCase):
    def snapshots(self):
        before = dict(walk_camera=bytes(0xb8), look=bytes(0x48), walk_flag=bytes(4),
                      actor=b'unchanged native actor', pose='unchanged solved source pose')
        after = {key: bytearray(value) if isinstance(value, bytes) else value for key, value in before.items()}
        after['walk_camera'][0x1c] = after['walk_flag'][0] = 1
        struct.pack_into('<3I', after['look'], 0x1c, 0x3f800000, 0, 0)
        return before, after

    def test_exact_original_camera_request_preserves_motion_height_and_solved_pose(self):
        before, after = self.snapshots()
        camera.validate_changes(before, after)

    def test_actor_pose_camera_height_or_unrelated_flags_cannot_be_changed(self):
        for key, offset in (('actor', 0), ('walk_camera', 0x34), ('walk_flag', 1), ('look', 0x19)):
            before, after = self.snapshots()
            after[key][offset] ^= 1
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                camera.validate_changes(before, after)
        before, after = self.snapshots()
        after['pose'] = 'host-rotated camera'
        with self.assertRaises(RuntimeError):
            camera.validate_changes(before, after)

    def test_missing_native_zoom_reset_and_wrong_extent_fail(self):
        before, after = self.snapshots()
        struct.pack_into('<f', after['look'], 0x1c, 0)
        with self.assertRaises(RuntimeError):
            camera.validate_changes(before, after)
        before, after = self.snapshots()
        before['look'] = before['look'][:-4]
        with self.assertRaises(RuntimeError):
            camera.validate_changes(before, after)


if __name__ == '__main__':
    unittest.main()
