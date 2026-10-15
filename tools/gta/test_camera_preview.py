import copy
import math
import struct
import unittest
from unittest.mock import patch

import camera_preview as m


def state():
    return dict(protocol='GTATHAW2', pid=17, creation_100ns=123, ped_address=0x20000,
                epoch=3, area=0, ped_area=0, control_eligible=True, grounded=False,
                flags=25, age_ms=1, position=[2240., -1261., 23.])


def camera():
    return dict(original_solved_pose=True, current_projection_verified=True,
                position_thaw=[0., 100., 200.], forward_thaw=[0., 0., -1.], up_thaw=[0., 1., 0.],
                effective_horizontal_fov_degrees=72., fov_y_radians=.8449562842, native_viewport_aspect=1280/720,
                original_projection=[1.376381874, 0, 0, 0, 0, 2.224455595, 0, 0, 0, 0, -1.00002, -1, 0, 0, -2.00004, 0])


class CameraTests(unittest.TestCase):
    def packet(self, s=None, c=None, **kw):
        s = state() if s is None else s
        return m.packet(s, m.scene(state()), 99, 7, 1000, kw.get('source_tick', 1),
                        kw.get('source_time_us', 16667), camera() if c is None else c,
                        origin=(2240., -1261., 23.))

    def test_physical_world_axes_and_no_ped_root_offset(self):
        c = camera(); saved = copy.deepcopy(c)
        pose = m.source_pose(c, origin=(2240, -1261, 23))
        self.assertEqual(pose[3:9], (0., 1., 0., 0., 0., 1.))
        self.assertAlmostEqual(pose[1], -1266.08)
        self.assertAlmostEqual(pose[2], 25.54) # No additional one-metre CJ root offset.
        self.assertEqual(pose[9], 72.) # The native horizontal getter, not cached/vertical fields.
        self.assertAlmostEqual(pose[11], 1/2.224455595) # Original vertical scaling, not horizontal / aspect.
        self.assertNotAlmostEqual(pose[11], pose[10]/pose[12])
        self.assertEqual(c, saved)

    def test_wire_layout_and_air_allowed(self):
        raw = self.packet()
        self.assertEqual(len(raw), 140)
        self.assertEqual(struct.unpack_from('<8s4I', raw), (b'GTACAM02', 2, 140, 17, 3))
        self.assertEqual(struct.unpack_from('<4Q4I2Q', raw, 24),
                         (123, 99, 7, 1000, 0x20000, 0, 0, 0, 1, 16667))
        self.assertAlmostEqual(struct.unpack_from('<f', raw, 124)[0], 72.)
        self.assertAlmostEqual(struct.unpack_from('<f', raw, 132)[0], 1/2.224455595)

    def test_source_provenance_basis_and_fov_rejected(self):
        for key, value in [('current_projection_verified', False), ('original_solved_pose', False),
                           ('forward_thaw', [0., 1., 0.]), ('up_thaw', [0., 2., 0.]),
                           ('effective_horizontal_fov_degrees', math.nan),
                           ('effective_horizontal_fov_degrees', 151.), ('position_thaw', [math.inf, 0., 0.]),
                           ('original_projection', [0]*16), ('native_viewport_aspect', 0)]:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.packet(c=dict(camera(), **{key: value}))

    def test_clock_scene_and_camera_distance_rejected(self):
        for key, value in [('epoch', 4), ('area', 1), ('age_ms', 101), ('age_ms', -1),
                           ('control_eligible', False), ('flags', 17), ('position', [0., 0., 0.])]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.packet(s=dict(state(), **{key: value}))
        for tick, micros in [(0, 1), (1, 0), (1, 2**64), (-1, 1)]:
            with self.subTest(tick=tick), self.assertRaises(ValueError):
                self.packet(source_tick=tick, source_time_us=micros)
        self.packet(source_tick=0, source_time_us=0)
        self.packet(source_tick=432000, source_time_us=7200000000)

    def test_publisher_keeps_transport_and_monotonic_sequence_between_sessions(self):
        with patch.object(m, 'Publisher') as transport:
            publisher = m.CameraPublisher(object())
            s = state()
            for request in (99, 100):
                publisher.publish(s, m.scene(s), request, 1000, 0, 0, camera(), origin=(2240., -1261., 23.))
                publisher.clear()
            self.assertEqual([call.args[1] for call in transport.return_value.publish.call_args_list], [1, 2])
            self.assertEqual(transport.return_value.clear.call_count, 2)
            publisher.close()
            transport.return_value.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
