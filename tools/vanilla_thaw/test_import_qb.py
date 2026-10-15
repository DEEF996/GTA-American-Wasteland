"""Synthetic-only import fixtures: no installed game files are modified."""
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import import_qb as qb


def archive(names=(r"scripts\z_last.qb.wpc", r"scripts\a_first.qb.wpc")):
    """Small header-compatible fixture, deliberately not semantically valid QB."""
    pak_size = len(names) * 192 + 64
    pak, pab, payloads = bytearray(pak_size), bytearray(), []
    for index, name in enumerate(names):
        payload = b"opaque synthetic QB " + bytes([index])
        offset = index * 192
        fields = (qb.qbkey(".qb"), pak_size + len(pab) - offset, len(payload),
                  qb.qbkey(name), 0, index + 123, 0, 0x20 if index == 0 else 0x22)
        struct.pack_into("<8I", pak, offset, *fields)
        raw = name.encode("ascii")
        pak[offset+32:offset+32+len(raw)] = raw
        pab.extend(payload)
        payloads.append(payload)
    terminal = len(names) * 192
    struct.pack_into("<8I", pak, terminal, 0xB524565F, pak_size + len(pab) - terminal,
                     4, 0, 0, 0, 0, 0)
    pak[terminal+32:] = b"\xcd" * 32
    pab.extend(b"\0" * 4)
    return bytes(pak), bytes(pab), payloads


class ArchiveTests(unittest.TestCase):
    def test_order_names_raw_words_and_opaque_payload_are_preserved(self):
        pak, pab, payloads = archive()
        members, terminator = qb.parse_archive(pak, pab)
        self.assertEqual([m["canonical_name"] for m in members],
                         ["scripts/z_last.qb.wpc", "scripts/a_first.qb.wpc"])
        self.assertEqual([m["payload"] for m in members], payloads)
        self.assertEqual([m["cache_file"] for m in members], ["members/0000.qb", "members/0001.qb"])
        for index, member in enumerate(members):
            self.assertEqual(member["header_words"], list(struct.unpack_from("<8I", pak, index * 192)))
            self.assertEqual(member["sha256"], qb.sha(payloads[index]))
        self.assertEqual(terminator["header_padding_sha256"], qb.sha(b"\xcd" * 32))

    def test_invalid_logical_paths_are_rejected(self):
        for name in (r"scripts\..\escape.qb.wpc", r"C:\scripts\bad.qb.wpc",
                     r"\scripts\bad.qb.wpc", "scripts//bad.qb.wpc", "scripts/./bad.qb.wpc",
                     "scripts/bad.exe", "scripts/bad:ads.qb.wpc", "other/test.qb.wpc"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                pak, pab, _ = archive((name,))
                qb.parse_archive(pak, pab)

    def test_case_and_separator_duplicate_is_rejected(self):
        pak, pab, _ = archive((r"scripts\one.qb.wpc", "SCRIPTS/ONE.QB.WPC"))
        with self.assertRaises(ValueError):
            qb.parse_archive(pak, pab)

    def test_wrong_checksum_flags_and_out_of_bounds_are_rejected(self):
        original, pab, _ = archive()
        for word, value in ((3, 123), (7, 0), (1, 0), (2, 0xFFFFFFFF)):
            with self.subTest(word=word), self.assertRaises(ValueError):
                pak = bytearray(original)
                struct.pack_into("<I", pak, word*4, value)
                qb.parse_archive(bytes(pak), pab)

    def test_overlapping_payloads_and_unexpected_tail_are_rejected(self):
        original, pab, _ = archive()
        pak = bytearray(original)
        struct.pack_into("<I", pak, 192+4, len(pak)-192)
        with self.assertRaisesRegex(ValueError, "Overlapping"):
            qb.parse_archive(bytes(pak), pab)
        pak = bytearray(original)
        pak[-1] = 7
        with self.assertRaisesRegex(ValueError, "after core PAK terminator"):
            qb.parse_archive(bytes(pak), pab)


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="synthetic-stock-qb-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.thaw, self.gta = self.root / "thaw", self.root / "gta"
        (self.thaw / "data/pak").mkdir(parents=True)
        self.gta.mkdir()
        pak, pab, self.payloads = archive()
        self.inputs = {"THAW.exe": b"synthetic executable identity, never run",
                       "data/pak/qb.pak.wpc": pak, "data/pak/qb.pab.wpc": pab}
        for name, data in self.inputs.items():
            (self.thaw / name).write_bytes(data)
        self.config = {"format": "gta-thaw-local-sources-v1",
                       "gta": {"directory": str(self.gta)},
                       "thaw": {"directory": str(self.thaw), "rethawed_present": False,
                                "files": qb.setup_sources.files(self.thaw, qb.setup_sources.THAW_REQUIRED)}}
        self.selection, self.output = self.root / "sources.json", self.root / "cache"
        self.save_selection()
        pins = {name: (len(data), qb.sha(data)) for name, data in self.inputs.items()}
        self.pins = patch.object(qb, "PINNED_INPUTS", pins)
        self.pins.start()
        self.addCleanup(self.pins.stop)
        self.count = patch.object(qb, "EXPECTED_MEMBERS", 2)
        self.count.start()
        self.addCleanup(self.count.stop)

    def save_selection(self):
        self.selection.write_bytes(qb.json_bytes(self.config))

    def assert_not_published(self):
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.root.glob(".stock-qb-*")), [])

    def test_exclusive_complete_cache_and_sources_unchanged(self):
        result = qb.import_cache(self.selection, self.output)
        manifest = json.loads((self.output / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest, result)
        self.assertEqual(manifest["member_count"], 2)
        self.assertFalse(manifest["backend_ready"])
        self.assertFalse(manifest["complete_asset_closure"])
        self.assertTrue(manifest["sources_revalidated_before_publish"])
        self.assertEqual(manifest["source_files"], self.config["thaw"]["files"])
        for row, expected in zip(manifest["members"], self.payloads):
            self.assertNotIn("payload", row)
            self.assertEqual((self.output / row["cache_file"]).read_bytes(), expected)
        for name, expected in self.inputs.items():
            self.assertEqual((self.thaw / name).read_bytes(), expected)
        with self.assertRaisesRegex(ValueError, "existing QB cache"):
            qb.import_cache(self.selection, self.output)
        self.assertEqual(json.loads((self.output / "manifest.json").read_text()), manifest)

    def test_changed_source_and_forged_fingerprint_are_rejected(self):
        path = self.thaw / "THAW.exe"
        path.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "changed since"):
            qb.import_cache(self.selection, self.output)
        self.assert_not_published()
        self.config["thaw"]["files"] = qb.setup_sources.files(self.thaw, qb.setup_sources.THAW_REQUIRED)
        self.save_selection()
        with self.assertRaisesRegex(ValueError, "Unsupported observed stock fingerprint"):
            qb.import_cache(self.selection, self.output)
        self.assert_not_published()

    def test_input_path_changes_duplicates_and_duplicate_json_key_are_rejected(self):
        for bad in ("../outside.exe", "data/pak/qb.pak.wpc"):
            self.config["thaw"]["files"][0]["path"] = bad
            self.save_selection()
            with self.assertRaisesRegex(ValueError, "input paths"):
                qb.import_cache(self.selection, self.output)
            self.assert_not_published()
        self.selection.write_text('{"format":"a","format":"b"}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Duplicate configuration key"):
            qb.import_cache(self.selection, self.output)

    def test_original_directories_and_new_rethawed_component_are_rejected(self):
        for root in (self.thaw, self.gta):
            with self.subTest(root=root), self.assertRaisesRegex(ValueError, "outside original"):
                qb.import_cache(self.selection, root / "new-cache")
            self.assertFalse((root / "new-cache").exists())
        (self.thaw / "rthw.dll").write_bytes(b"synthetic")
        with self.assertRaisesRegex(ValueError, "reTHAWed components"):
            qb.import_cache(self.selection, self.output)
        self.assert_not_published()

    def test_publish_failure_removes_only_owned_staging(self):
        unrelated = self.root / ".stock-qb-unrelated"
        unrelated.mkdir()
        marker = unrelated / "keep.txt"
        marker.write_text("keep")
        with patch.object(qb.os, "rename", side_effect=OSError("synthetic publication failure")):
            with self.assertRaisesRegex(OSError, "publication failure"):
                qb.import_cache(self.selection, self.output)
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.root.glob(".stock-qb-*")), [unrelated])
        self.assertEqual(marker.read_text(), "keep")

    def test_source_mutation_during_import_refuses_publication_and_cleans_up(self):
        real_validation = qb.fresh_sources
        calls = []

        def validate(root, declared):
            calls.append(True)
            if len(calls) == 2:
                (self.thaw / "THAW.exe").write_bytes(b"synthetic mutation during import")
            return real_validation(root, declared)

        with patch.object(qb, "fresh_sources", side_effect=validate):
            with self.assertRaisesRegex(ValueError, "changed since"):
                qb.import_cache(self.selection, self.output)
        self.assertEqual(len(calls), 2)
        self.assert_not_published()

    def test_configuration_mutation_during_import_refuses_publication(self):
        real_validation = qb.fresh_sources
        calls = []

        def validate(root, declared):
            calls.append(True)
            if len(calls) == 2:
                self.selection.write_bytes(self.selection.read_bytes() + b"\n")
            return real_validation(root, declared)

        with patch.object(qb, "fresh_sources", side_effect=validate):
            with self.assertRaisesRegex(ValueError, "configuration changed"):
                qb.import_cache(self.selection, self.output)
        self.assert_not_published()


if __name__ == "__main__":
    unittest.main()
