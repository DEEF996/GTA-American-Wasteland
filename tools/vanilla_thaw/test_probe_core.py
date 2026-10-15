import struct
import unittest

import probe_core


class CoreSourceTests(unittest.TestCase):
    def source(self, words=(), total=48):
        data = bytearray(total)
        struct.pack_into("<I", data, 4, total)
        if words:
            struct.pack_into("<5I", data, 28, *words)
        return bytes(data)

    def test_scalar_and_empty_source(self):
        self.assertEqual(probe_core.sections(self.source((0x10400, 123, 456, 789, 0))), [(28, 0x10400, 123, 789)])
        self.assertEqual(probe_core.sections(self.source(total=28)), [])

    def test_pointer_extent_is_validated(self):
        self.assertEqual(probe_core.sections(self.source((0xa0400, 123, 456, 48, 0), 52)), [(28, 0xa0400, 123, 48)])
        for value in (0, 28, 47, 52):
            with self.assertRaises(RuntimeError):
                probe_core.sections(self.source((0xa0400, 123, 456, value, 0), 52))

    def test_reject_truncation_and_invalid_first_section(self):
        valid = self.source((0x10400, 123, 456, 789, 0))
        for data in (valid[:-4], valid[:27], self.source((0x10400, 123, 456, 789, 4)), self.source(total=44)):
            with self.assertRaises(RuntimeError):
                probe_core.sections(data)


if __name__ == "__main__":
    unittest.main()
