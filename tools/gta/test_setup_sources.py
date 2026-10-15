import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import setup_sources as setup


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_nested_install_and_ambiguity(self):
        nested = self.root / 'GTA San Andreas'
        nested.mkdir()
        (nested / 'gta_sa.exe').write_bytes(b'fixture')
        self.assertEqual(setup.locate(self.root, 'gta_sa.exe', 'GTA San Andreas'), nested)
        (self.root / 'gta_sa.exe').write_bytes(b'other')
        with self.assertRaises(ValueError):
            setup.locate(self.root, 'gta_sa.exe', 'GTA San Andreas')

    def test_configuration_cannot_modify_source_or_replace_choice(self):
        game, thaw = self.root / 'game', self.root / 'thaw'
        game.mkdir(); thaw.mkdir()
        report = {'gta': {'directory': str(game)}, 'thaw': {'directory': str(thaw)}}
        with self.assertRaises(ValueError):
            setup.save_configuration(report, game / 'settings.json')
        output = self.root / 'settings.json'
        setup.save_configuration(report, output)
        with self.assertRaises(FileExistsError):
            setup.save_configuration({'gta': report['gta'], 'thaw': report['thaw'], 'changed': True}, output)
        self.assertEqual(json.loads(output.read_text()), report)

    def test_pe_reader_rejects_bad_headers_and_accepts_no_relocations(self):
        image = bytearray(64 + 24 + 224)
        image[:2] = b'MZ'; struct.pack_into('<I', image, 60, 64)
        image[64:68] = b'PE\0\0'
        struct.pack_into('<HH', image, 68, 0x14c, 3)
        struct.pack_into('<H', image, 84, 224)
        struct.pack_into('<H', image, 88, 0x10b)
        struct.pack_into('<I', image, 116, 0x400000)
        path = self.root / 'image.exe'; path.write_bytes(image)
        self.assertEqual(setup.pe_info(path)['relocation_bytes'], 0)
        struct.pack_into('<H', image, 68, 0x8664); path.write_bytes(image)
        with self.assertRaises(ValueError):
            setup.pe_info(path)

    def test_unrecognized_game_is_rejected_before_thaw_import(self):
        for name in setup.GTA_REQUIRED:
            target = self.root / name; target.parent.mkdir(exist_ok=True)
            target.write_bytes(b'unsupported')
        (self.root / 'THAW.exe').write_bytes(b'stock')
        with self.assertRaisesRegex(ValueError, 'Unsupported GTA'):
            setup.inspect_sources(self.root, self.root)


if __name__ == '__main__':
    unittest.main()
