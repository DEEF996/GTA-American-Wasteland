import struct
import unittest

import probe_career as career


def source_row():
    data = bytearray(160)
    struct.pack_into('<2I', data, 0, 0x10000, 8)
    struct.pack_into('<4I', data, 8, 0x1900, 0x0dccb6bf, 56, 24)
    struct.pack_into('<4I', data, 24, 0x300, 0x97a3a64b, 1, 40)
    struct.pack_into('<4I', data, 40, 0x300, 0xa6479767, 0, 0)
    struct.pack_into('<3I', data, 56, 0x30100, 1, 68)
    data[68:75] = b'level1\0'
    return data


class CareerTests(unittest.TestCase):
    def test_single_name_original_record_and_index(self):
        row = career.level_record(source_row(), 0, 3)
        self.assertEqual(row['words'], (career.probe_native.qbkey('level_level1'), 3, 0, 0))
        self.assertEqual(row['authored_names'], 1)

    def test_malformed_names_or_field_chain_rejected(self):
        for offset, value in ((60, 0), (60, 16), (64, 160), (20, 8), (48, 2)):
            data = source_row()
            struct.pack_into('<I', data, offset, value)
            with self.assertRaises((RuntimeError, ValueError)):
                career.level_record(data, 0, 0)

    def test_changed_or_reconstructed_whole_sources_rejected(self):
        for row in career.SOURCES:
            for data in (bytes(row[2]), bytes(row[2] - 1)):
                with self.assertRaises(RuntimeError):
                    career.validate_source(data, row)


if __name__ == '__main__':
    unittest.main()
