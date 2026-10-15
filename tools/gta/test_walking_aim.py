import math
import unittest

from geometry import gta_to_thaw
from probe_frame_input import packet, validate_packet
from walking_aim import rotate_packet, walking


class WalkingAimTest(unittest.TestCase):
    def test_forward_follows_camera_after_half_turn_and_quarter_turns(self):
        original = gta_to_thaw((0, 1, 0), scale=1)
        value = packet((17, 240, 128, 0), 0x48)
        for aim, expected in (((0, 1, 0), (128, 0)), ((0, -1, 0), (128, 255)),
                              ((1, 0, 0), (255, 128)), ((-1, 0, 0), (0, 128))):
            with self.subTest(aim=aim):
                result = rotate_packet(value, original, aim)
                self.assertEqual(tuple(result[2:4]), expected)
                self.assertEqual(result[:2], value[:2])
                self.assertEqual(result[0x24:], value[0x24:])
                validate_packet(result)

    def test_all_directions_track_world_heading_for_rotated_original_camera(self):
        for source_angle in (0, .34, 2.8):
            source = (math.cos(source_angle), math.sin(source_angle), 0)
            for angle in (-3.14, -2., -.7, 0., .8, 2., 3.14):
                aim = (math.cos(angle), math.sin(angle), .8)
                for x, y in ((128, 0), (255, 128), (128, 255), (0, 128), (0, 0), (255, 255)):
                    result = rotate_packet(packet((128, 128, x, y)), gta_to_thaw(source, scale=1), aim)
                    right, forward = (result[2]-128)/128, (128-result[3])/128
                    world = (source[1]*right+source[0]*forward, -source[0]*right+source[1]*forward)
                    r, f = (x-128)/(128 if x < 128 else 127), (128-y)/(128 if y < 128 else 127)
                    desired = (aim[1]*r+aim[0]*f, -aim[0]*r+aim[1]*f)
                    dot = sum(a*b for a,b in zip(world, desired))/(math.hypot(*world)*math.hypot(*desired))
                    self.assertGreater(dot, .9999)

    def test_stationary_input_preserves_original_actions(self):
        value = packet((0, 255, 128, 128), 0x8000)
        self.assertEqual(rotate_packet(value, (0, 0, -1), (1, 0, 0)), value)

    def test_only_free_walking_uses_camera_relative_controls(self):
        for mode in (0, 1):
            for state in range(6):
                value = dict(mode=mode, walking_gate=0, walk_state=state, bailing=False)
                self.assertEqual(walking(value), mode == 1 and state in (0, 1))
                self.assertFalse(walking(dict(value, bailing=True)))
                self.assertFalse(walking(dict(value, walking_gate=1)))

    def test_invalid_basis_is_not_used_as_input(self):
        for aim in ((0, 0, 1), (float('nan'), 1, 0)):
            with self.assertRaises(ValueError):
                rotate_packet(packet((128, 128, 128, 0)), (0, 0, -1), aim)


if __name__ == '__main__': unittest.main()
