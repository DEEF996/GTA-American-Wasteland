import unittest
import struct
from types import SimpleNamespace

import probe_skater


class SkaterMetadataTests(unittest.TestCase):
    def window_system(self, *, owner=17, visible=1, rectangle=(0, 0, 1280, 720)):
        return SimpleNamespace(window=123, allocate=lambda data: 4096, word=lambda addr: owner,
            read=lambda addr, size: struct.pack('<4i', *rectangle),
            call=lambda dll, name, args: {'GetWindowThreadProcessId': 1, 'IsWindowVisible': visible,
                                        'GetClientRect': 1}[name])

    def test_viewport_uses_external_dimensions_and_retains_hidden_window(self):
        system = self.window_system()
        window, aspect = probe_skater.viewport_aspect(system, display_window=456, display_pid=17)
        self.assertEqual((window, aspect), (456, 1280/720))
        self.assertEqual(system.window, 123)
        self.assertEqual(probe_skater.viewport_aspect(system), (123, aspect))

    def test_foreign_hidden_and_invalid_viewports_are_refused(self):
        for change in ({'owner': 18}, {'visible': 0}, {'rectangle': (0, 0, 0, 720)},
                       {'rectangle': (0, 0, 1280, 0)}, {'rectangle': (0, 0, 1280, 1)}):
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                probe_skater.viewport_aspect(self.window_system(**change), display_window=456, display_pid=17)
        with self.assertRaises(RuntimeError):
            probe_skater.viewport_aspect(self.window_system(), display_window=456)

    def test_whole_original_entries_and_source_heaps(self):
        metadata = probe_skater.read_metadata()
        functions = {row[0]: row for row in metadata["functions"]}
        self.assertEqual(functions["skater_ctor"][1:3], [0x593120, 1726])
        self.assertEqual(functions["skater_original_profile_init"][1:3], [0x590720, 2499])
        self.assertEqual(functions["ObjectLookup"][1:3], [0x433310, 11])
        self.assertEqual(metadata["caller_evidence"]["skater_heaps"]["capacities"], [0x24000, 0x118000])


if __name__ == "__main__":
    unittest.main()
