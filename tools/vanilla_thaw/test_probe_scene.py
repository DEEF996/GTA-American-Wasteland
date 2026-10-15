"""Scene fixture/input/result checks only; these tests execute no native code."""
import hashlib
import struct
import unittest

import probe_scene as scene
from assets_export import parse_col


class SceneProbeTests(unittest.TestCase):
    def test_floor_is_bounded_native_units_with_upward_faces(self):
        raw = scene.floor_fixture()
        parsed = parse_col(raw)
        self.assertEqual(parsed["bounds"], {"min": [-128., 0., -128.], "max": [128., 0., 128.]})
        self.assertEqual(parsed["stats"]["triangles"], 2)
        self.assertEqual(parsed["stats"]["objects"], 1)
        for offset in (0, 3):
            a, b, c = [parsed["positions"][index] for index in parsed["indices"][offset:offset + 3]]
            self.assertGreater((b[2] - a[2]) * (c[0] - a[0]) - (b[0] - a[0]) * (c[2] - a[2]), 0)
        self.assertEqual(parsed["face_flags"], [0, 0])
        self.assertEqual(hashlib.sha256(raw).hexdigest(), scene.FLOOR_SHA)

    def test_whole_engine_source_selection_is_exact_and_unique(self):
        row = {"path": scene.ENGINE_PATH, "bytes": scene.ENGINE_BYTES, "sha256": scene.ENGINE_SHA}
        self.assertEqual(scene.select_engine([{"path": "unrelated"}, row]), row)
        for invalid in ([], [row, row], [dict(row, bytes=1499)], [dict(row, sha256="0" * 64)],
                        [dict(row, path=scene.ENGINE_PATH.upper())],
                        [dict(row, path="../" + scene.ENGINE_PATH)]):
            with self.subTest(invalid=invalid), self.assertRaises(RuntimeError):
                scene.select_engine(invalid)

    def contact(self):
        data = bytearray(0xc0)
        struct.pack_into("<8f", data, 0x24, 0., 1., 0., 0., 31., 0., -17., 1.)
        return data

    def test_original_hit_requires_authored_contact_point_and_normal(self):
        result = scene.query_result(self.contact(), True, (31., 64., -17.), (31., -64., -17.), True)
        self.assertTrue(result["hit"])
        for offset, value in ((0x28, -1.), (0x38, 1.), (0x34, 0.), (0x40, 0.)):
            with self.subTest(offset=offset), self.assertRaises(RuntimeError):
                data = self.contact()
                struct.pack_into("<f", data, offset, value)
                scene.query_result(data, True, (31., 64., -17.), (31., -64., -17.), True)

    def test_misses_and_nonfinite_results_are_not_false_successes(self):
        self.assertFalse(scene.query_result(bytes(0xc0), False, (256., 64., 0.), (256., -64., 0.), False)["hit"])
        with self.assertRaises(RuntimeError):
            scene.query_result(self.contact(), False, (31., 64., -17.), (31., -64., -17.), True)
        for value in (float("nan"), float("inf")):
            data = bytearray(0xc0)
            struct.pack_into("<f", data, 0x24, value)
            with self.assertRaises(RuntimeError):
                scene.query_result(data, False, (256., 64., 0.), (256., -64., 0.), False)
        with self.assertRaises(RuntimeError):
            scene.query_result(bytes(16), False, (0., 1., 0.), (0., 1., 1.), False)

    def test_output_metadata_is_distinct_from_ctor_masks_and_padding(self):
        data = self.contact()
        struct.pack_into("<2H", data, 0x4c, 0x10, 0)
        struct.pack_into("<HBB", data, 0x50, 4, 9, 0xff)
        result = scene.query_result(data, True, (31., 64., -17.), (31., -64., -17.), True)
        self.assertEqual((result["flags"], result["terrain"], result["input_masks"]), (4, 9, (16, 0)))


if __name__ == "__main__":
    unittest.main()
