"""Exact reference-scanner boundaries and ordering; no original process."""
import struct
import unittest

from probe_scene_references import reference_words


class ReferenceWordsTests(unittest.TestCase):
    def test_outer_bounds_gaps_and_duplicate_words_keep_block_order(self):
        values = (99, 100, 119, 120, 199, 200, 219, 220, 100, 0xffffffff)
        blocks = [('first', struct.pack('<10I', *values)),
                  ('second', struct.pack('<2I', 219, 100))]
        self.assertEqual(reference_words(blocks, [(200, 220), (100, 120)]), [
            dict(component='first', offset=4, pointer=100),
            dict(component='first', offset=8, pointer=119),
            dict(component='first', offset=20, pointer=200),
            dict(component='first', offset=24, pointer=219),
            dict(component='first', offset=32, pointer=100),
            dict(component='second', offset=0, pointer=219),
            dict(component='second', offset=4, pointer=100)])

    def test_nested_touching_and_invalid_extents_keep_half_open_union(self):
        values = (0, 99, 100, 109, 110, 129, 130, 149, 150, 0xffffffff)
        extents = [(130, 150), (100, 130), (110, 120), (90, 90), (90, 80)]
        found = reference_words(iter([('source', struct.pack('<10I', *values))]), iter(extents))
        self.assertEqual(found, [dict(component='source', offset=i*4, pointer=value)
                                 for i, value in enumerate(values) if 100 <= value < 150])
        self.assertEqual(reference_words([('edge', struct.pack('<2I', 0, 0xffffffff))],
                                         [(0, 1), (0xffffffff, 0x100000000)]), [
            dict(component='edge', offset=0, pointer=0),
            dict(component='edge', offset=4, pointer=0xffffffff)])

    def test_empty_ranges_still_consume_and_validate_all_source_blocks(self):
        for extents in ([], [(5, 5)], [(10, 1)]):
            with self.subTest(extents=extents):
                self.assertEqual(reference_words([('empty', b''), ('zero', bytes(8))], extents), [])
                with self.assertRaises(struct.error):
                    reference_words([('first', bytes(4)), ('malformed', b'bad')], extents)


if __name__ == '__main__':
    unittest.main()
