import ctypes as C
import math
import struct
import unittest
from types import SimpleNamespace
import preview_move as m
from preview_hold import scene
from preview_pose import Publisher
from test_atomic_store import fake_store32


class MoveTests(unittest.TestCase):
    def state(self):
        return dict(protocol="GTATHAW2", pid=17, creation_100ns=123, ped_address=0x20000,
                    epoch=3, area=0, ped_area=0, control_eligible=True, grounded=True,
                    flags=25, age_ms=1, position=[2240., -1261., 23.], heading=0.)

    def test_wire_offsets_and_gta_units(self):
        s = self.state()
        position, heading, velocity = m.trajectory(s, .5, 1., 1., 45.)
        raw = m.packet(s, scene(s), 99, 5, 1000, 1000, position, heading, velocity)
        self.assertEqual(len(raw), 104)
        self.assertEqual(raw[:8], b"GTAMOVE1")
        self.assertEqual(struct.unpack_from("<I", raw, 12)[0], 104)
        self.assertEqual(struct.unpack_from("<3f", raw, 72), (2240., -1260.5, 23.))
        self.assertAlmostEqual(struct.unpack_from("<f", raw, 84)[0], math.pi/8)
        self.assertEqual(struct.unpack_from("<f", raw, 88)[0], 0.)
        self.assertAlmostEqual(struct.unpack_from("<f", raw, 92)[0], .02)
        self.assertEqual(raw[96:], bytes(8))

    def test_heading_sign_and_path_endpoint(self):
        s = self.state(); s["heading"] = math.pi/2
        position, heading, velocity = m.trajectory(s, 1., 1., .25, -90.)
        self.assertEqual(position, [2239.75, -1261., 23.])
        self.assertAlmostEqual(heading, 0.)
        self.assertAlmostEqual(velocity[0]*50, -.25)
        self.assertEqual(velocity[2], 0.)

    def test_malformed_or_changed_scene_rejected(self):
        s = self.state(); identity = scene(s)
        for position, heading, velocity, duration in (([float("nan"), 0, 0], 0, [0, 0, 0], 1000),
                ([0, 0, 0], float("inf"), [0, 0, 0], 1000),
                ([0, 0, 0], 0, [.021, 0, 0], 1000),
                ([0, 0, 0], 0, [0, 0, .001], 1000),
                ([0, 0, 0], 0, [0, 0, 0], 1001)):
            with self.subTest(position=position, velocity=velocity), self.assertRaises(ValueError):
                m.packet(s, identity, 99, 1, 1000, duration, position, heading, velocity)
        for key, value in (("epoch", 4), ("area", 1), ("grounded", False), ("age_ms", 101)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                m.packet(dict(s, **{key: value}), identity, 99, 1, 1000, 1000, s["position"], 0, [0, 0, 0])

    def test_path_bounds(self):
        for elapsed, seconds, speed, turn in ((0, 1.1, 0, 0), (0, 1, 1.1, 0), (0, .5, 0, 46),
                                               (1.1, 1, 0, 0), (0, 1, float("nan"), 0)):
            with self.subTest(seconds=seconds, speed=speed), self.assertRaises(ValueError):
                m.trajectory(self.state(), elapsed, seconds, speed, turn)

    def test_publisher_keeps_mapping_boundary(self):
        memory = C.create_string_buffer(120)
        C.memset(C.addressof(memory)+112, 0xAC, 8)
        writer = Publisher.__new__(Publisher)
        writer.packet_size = 104; writer.view = C.addressof(memory)
        writer.exchange32 = fake_store32
        s = self.state(); raw = m.packet(s, scene(s), 99, 5, 1000, 1000, s["position"], 0, [0, 0, 0])
        writer.publish(raw, 5)
        self.assertEqual(bytes(memory)[4:108], raw)
        self.assertEqual(struct.unpack_from("<I", memory, 108)[0], 10)
        self.assertEqual(bytes(memory)[112:], b"\xAC"*8)


if __name__ == "__main__": unittest.main()
