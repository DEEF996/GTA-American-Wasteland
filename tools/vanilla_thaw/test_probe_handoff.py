import math
import struct
import unittest

import probe_handoff as handoff


class HandoffTests(unittest.TestCase):
    def pose(self, yaw=90, position=(100, -32, 17), velocity=(10, 200, -30), state=1):
        angle = math.radians(yaw)
        matrix = struct.pack('<16f', math.cos(angle), 0, -math.sin(angle), 0,
                             0, 1, 0, 0, math.sin(angle), 0, math.cos(angle), 0, 0, 0, 0, 1)
        vector = struct.pack('<4f', *position, 1)
        return dict(position=vector, previous=vector, core_previous=vector,
                    velocity=struct.pack('<4f', *velocity, 1), matrix=matrix, display=matrix,
                    core_matrix=matrix, state=state, sound_state=state, control_mode=0)

    def test_ground_and_air_inputs_keep_all_velocity_axes(self):
        for grounded, state in ((True, 0), (False, 1)):
            position, velocity, yaw, actual_state = handoff.validate_request([1, 2, 3], [4, 200, -30], -90, grounded)
            self.assertEqual((position, velocity, yaw, actual_state), ((1, 2, 3), (4, 200, -30), -90, state))

    def test_reject_bad_host_inputs_before_native_calls(self):
        for position, velocity, yaw, grounded in (
            ((1, 2), (0, 0, 0), 0, True), ((0, float('nan'), 0), (0, 0, 0), 0, True),
            ((0, 0, 0), (0, float('inf'), 0), 0, False), ((0, 0, 0), (0, 0, 0), 361, True),
            ((0, 0, 0), (0, 0, 0), 0, 1), ((False, 0, 0), (0, 0, 0), 0, True),
            ((0, 0, 10000001), (0, 0, 0), 0, True), ((0, 0, 0), (100001, 0, 0), 0, False)):
            with self.subTest(position=position, velocity=velocity, yaw=yaw, grounded=grounded):
                with self.assertRaises(RuntimeError):
                    handoff.validate_request(position, velocity, yaw, grounded)

    def test_complete_declared_pose_passes_both_states_and_heading_wrap(self):
        for state in (0, 1):
            for yaw in (-360, -90, 0, 90, 360):
                handoff.validate_pose(self.pose(yaw=yaw, state=state), (100, -32, 17), (10, 200, -30), yaw, state, 0)

    def test_old_history_and_lost_vertical_momentum_are_rejected(self):
        for key, value in (('previous', struct.pack('<4f', 0, 0, 0, 1)),
                           ('core_previous', struct.pack('<4f', 0, 0, 0, 1)),
                           ('velocity', struct.pack('<4f', 10, 0, -30, 1)),
                           ('position', struct.pack('<4f', 100, -32, 17, 0))):
            pose = self.pose()
            pose[key] = value
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                handoff.validate_pose(pose, (100, -32, 17), (10, 200, -30), 90, 1, 0)

    def test_wrong_axis_orientation_or_state_is_rejected(self):
        for key, value in (('matrix', self.pose(yaw=0)['matrix']), ('state', 0),
                           ('sound_state', 0), ('control_mode', 1)):
            pose = self.pose()
            pose[key] = value
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                handoff.validate_pose(pose, (100, -32, 17), (10, 200, -30), 90, 1, 0)
        pose = self.pose(yaw=-90)
        with self.assertRaises(RuntimeError):
            handoff.validate_pose(pose, (100, -32, 17), (10, 200, -30), 90, 1, 0)


if __name__ == '__main__':
    unittest.main()
