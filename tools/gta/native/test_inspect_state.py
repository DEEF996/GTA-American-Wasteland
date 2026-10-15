import math
import unittest

from inspect_state import BODY, decode


class SnapshotTests(unittest.TestCase):
    def fields(self):
        return [b"GTATHAW2", 2, 272, 123, 3, 456, 1, 1000,
                0x123400, 1, 0, 0, 20, 2, 0, 17,
                1., 2., 3., 0.1, 0.2, 0.3, 0.5, 100., 1., 0,
                b"I:\\GTA_THAW\\thaw_gta\\profile\0", 18, 34, 56, 0, 0, 1]

    def test_current_scene_and_velocity_are_preserved(self):
        result = decode(BODY.pack(*self.fields()), 123, 456, 1010)
        self.assertEqual(result["position"], [1, 2, 3])
        self.assertAlmostEqual(result["raw_gta_velocity"][1], 0.2)
        self.assertEqual(result["profile_path"], "I:\\GTA_THAW\\thaw_gta\\profile")
        self.assertFalse(result["skating_available"])
        self.assertEqual((result["clock_hour"], result["clock_minute"], result["clock_second"]), (18, 34, 56))
        self.assertTrue(result["grounded"])
        self.assertTrue(result["control_eligible"])

    def test_rejects_wrong_identity_and_stale_or_future_frames(self):
        body = BODY.pack(*self.fields())
        for pid, created, now in [(124, 456, 1010), (123, 457, 1010),
                                  (123, 456, 999), (123, 456, 2001)]:
            with self.subTest(pid=pid, created=created, now=now), self.assertRaises(ValueError):
                decode(body, pid, created, now)
        with self.assertRaises(ValueError):
            decode(body[:248], 123, 456, 1010)

    def test_selected_game_owns_the_profile(self):
        body = BODY.pack(*self.fields())
        decode(body, 123, 456, 1010, "i:\\gta_thaw\\thaw_gta\\profile")
        with self.assertRaisesRegex(ValueError, "outside"):
            decode(body, 123, 456, 1010, "I:\\Another Game\\thaw_gta\\profile")

    def test_rejects_missing_isolation_and_malformed_data(self):
        for index, value in [(4, 1), (9, 0), (16, math.nan), (25, 1), (26, b"x" * 128),
                             (16, 100001.), (19, -10001.), (15, 0),
                             (26, b"relative\\profile\0"), (26, b"I:relative\0"),
                             (26, b"I:\\test\\..\\profile\0"), (26, b"I:\\test\\\xff\0")]:
            values = self.fields()
            values[index] = value
            with self.subTest(index=index), self.assertRaises(ValueError):
                decode(BODY.pack(*values), 123, 456, 1010)

    def test_rejects_legacy_protocol_and_out_of_range_scene_data(self):
        for index, value in [(0, b"GTATHAW1"), (1, 1), (2, 248), (27, 24), (28, 60),
                             (29, 61), (30, 65536), (31, 4), (32, 8)]:
            values = self.fields()
            values[index] = value
            with self.subTest(index=index), self.assertRaises(ValueError):
                decode(BODY.pack(*values), 123, 456, 1010)

    def test_native_sixtieth_second_is_preserved_without_advancing_hour(self):
        values = self.fields()
        values[27:30] = [23, 59, 60]
        result = decode(BODY.pack(*values), 123, 456, 1010)
        self.assertEqual((result["clock_hour"], result["clock_minute"], result["clock_second"]), (23, 59, 60))
        self.assertTrue(result["control_eligible"])
        values[29] = 61
        with self.assertRaisesRegex(ValueError, "Invalid GTA clock"):
            decode(BODY.pack(*values), 123, 456, 1010)

    def test_control_gate_preserves_diagnostics_and_airborne_motion(self):
        for index, value in [(30, 1), (30, 128), (31, 1), (31, 2), (31, 3),
                             (15, 17 | 2), (15, 17 | 32), (15, 5), (23, 0.), (11, 1)]:
            values = self.fields()
            values[index] = value
            with self.subTest(index=index, value=value):
                result = decode(BODY.pack(*values), 123, 456, 1010)
                self.assertFalse(result["control_eligible"])
        values = self.fields()
        values[32] = 2
        result = decode(BODY.pack(*values), 123, 456, 1010)
        self.assertFalse(result["grounded"])
        self.assertTrue(result["control_eligible"])
        self.assertEqual(result["ped_motion_flags"], 2)


if __name__ == "__main__":
    unittest.main()
