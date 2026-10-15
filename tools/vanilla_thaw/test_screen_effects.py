import struct
import unittest

import probe_screen_effects as effects


def fixture():
    raw = bytearray(effects.OBJECT_BYTES)
    struct.pack_into('<I', raw, 0, effects.VTABLE)
    raw[4] = 1
    struct.pack_into('<2I', raw, 0x74, 0x3f000000, 0x3f000000)
    for offset, table in effects.INTERPOLATORS:
        struct.pack_into('<I', raw, offset, table)
    return raw


class ScreenEffectsTests(unittest.TestCase):
    def test_native_identity_and_all_interpolators_required(self):
        effects.validate_object(fixture())
        for offset in (0, 4, 8, 0x70, 0x74, 0x78, *(row[0] for row in effects.INTERPOLATORS)):
            raw = fixture()
            raw[offset] ^= 0x10
            with self.assertRaises(RuntimeError):
                effects.validate_object(raw)

    def test_wrong_extent_rejected(self):
        for size in (0, effects.OBJECT_BYTES - 4, effects.OBJECT_BYTES + 4):
            with self.assertRaises(RuntimeError):
                effects.validate_object(bytes(size))

    def test_stock_half_scale_not_assumed_unit_scale(self):
        raw = fixture()
        struct.pack_into('<2I', raw, 0x74, 0x3f800000, 0x3f800000)
        with self.assertRaises(RuntimeError):
            effects.validate_object(raw)


if __name__ == '__main__':
    unittest.main()
