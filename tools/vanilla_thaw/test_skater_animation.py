import struct
import unittest

import probe_skater_animation as animation


class AnimationSystemTests(unittest.TestCase):
    def test_typed_arguments_and_native_source_site(self):
        pattern = animation.source_call()
        self.assertEqual(len(pattern), 81)
        self.assertEqual(animation.source_parameters(b"prefix" + pattern + b"tail"), 6)
        for body in (pattern[:-1], pattern * 2, pattern.replace(struct.pack("<I", 0x20000), struct.pack("<I", 0x30000))):
            with self.assertRaisesRegex(RuntimeError, "exactly one"):
                animation.source_parameters(body)

    def test_owned_structure_links_and_original_fields(self):
        base = 0x20000000
        raw = animation.parameter_storage(base)
        self.assertEqual(len(raw), 112)
        self.assertEqual(raw[:16], bytes(16))
        for index, (key, kind, value) in enumerate(animation.FIELDS):
            flags, actual_key, actual_value, following = struct.unpack_from("<4I", raw, 16 + index * 16)
            self.assertEqual((flags, actual_key, actual_value), (kind << 9, key, value))
            self.assertEqual(following, base + 32 + index * 16 if index < 5 else 0)
        for invalid in (0, 0x20000001, 0x100000000):
            with self.assertRaisesRegex(RuntimeError, "parameter allocation"):
                animation.parameter_storage(invalid)


if __name__ == "__main__":
    unittest.main()
