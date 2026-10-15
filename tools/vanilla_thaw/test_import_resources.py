"""Synthetic fixtures only; no installed game files are changed."""
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import import_resources as resources
import probe_skater

qb = resources.qb


class ResourceImportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="synthetic-stock-resources-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.thaw, self.gta = self.root / "thaw", self.root / "gta"
        self.gta.mkdir()
        for index, folder in enumerate(resources.DIRECTORIES):
            path = self.thaw / folder / f"part{index}.bin"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"synthetic resource " + bytes([index]))
        (self.thaw / resources.METADATA).parent.mkdir(parents=True)
        (self.thaw / resources.METADATA).write_bytes(b"synthetic metadata")
        extra_pins = {}
        for name in resources.EXTRA_FILES:
            path = self.thaw / name
            path.parent.mkdir(parents=True, exist_ok=True)
            data = b"synthetic pinned global sound bank"
            path.write_bytes(data)
            extra_pins[name] = (len(data), qb.sha(data))
        extras = patch.object(resources, "EXTRA_FILES", extra_pins)
        extras.start()
        self.addCleanup(extras.stop)
        inputs = {name: b"synthetic identity " + bytes([index])
                  for index, name in enumerate(qb.setup_sources.THAW_REQUIRED)}
        for name, content in inputs.items():
            (self.thaw / name).write_bytes(content)
        pins = patch.object(qb, "PINNED_INPUTS", {
            name: (len(data), qb.sha(data)) for name, data in inputs.items()})
        pins.start()
        self.addCleanup(pins.stop)
        self.selection, self.output = self.root / "sources.json", self.root / "cache"
        self.selection.write_bytes(qb.json_bytes(dict(
            format="gta-thaw-local-sources-v1", gta={"directory": str(self.gta)},
            thaw=dict(directory=str(self.thaw), rethawed_present=False,
                      files=qb.setup_sources.files(self.thaw, qb.setup_sources.THAW_REQUIRED)))))
        self.fixture = self.thaw / "data/models/part0.bin"

    def assert_no_cache(self):
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.root.glob(".stock-resources-*")), [])

    def test_complete_private_copy_is_exclusive_and_preserves_inputs(self):
        excluded = self.thaw / "data/movies/unrelated.bin"
        excluded.parent.mkdir()
        excluded.write_bytes(b"excluded original")
        original = {name: (self.thaw / name).read_bytes() for name in resources.inventory(self.thaw)}
        result = resources.prepare(self.selection, self.output)
        self.assertEqual(json.loads((self.output / "manifest.json").read_text()), result)
        self.assertEqual(result["format"], resources.FORMAT)
        self.assertTrue(result["sources_revalidated_before_publish"])
        self.assertFalse(result["backend_ready"])
        self.assertFalse(result["complete_asset_closure"])
        self.assertFalse(result["original_files_modified"])
        self.assertEqual({row["path"] for row in result["files"]}, set(original))
        for name, expected in original.items():
            self.assertEqual((self.output / name).read_bytes(), expected)
            self.assertEqual((self.thaw / name).read_bytes(), expected)
        self.assertFalse((self.output / "data/movies").exists())
        with self.assertRaisesRegex(ValueError, "existing resource cache"):
            resources.prepare(self.selection, self.output)

    def test_changed_source_content_refuses_publication_and_cleans_staging(self):
        real_copy = resources.copy_file

        def mutate(source, destination):
            real_copy(source, destination)
            if source == self.fixture:
                source.write_bytes(b"source edited during copy")

        with patch.object(resources, "copy_file", side_effect=mutate):
            with self.assertRaisesRegex(ValueError, "sources changed"):
                resources.prepare(self.selection, self.output)
        self.assert_no_cache()

    def test_only_the_pinned_sound_bank_is_included(self):
        unrelated = self.thaw / "data/sounds/pak/other.pak.wpc"
        unrelated.write_bytes(b"unrequested audio")
        result = resources.prepare(self.selection, self.output)
        sounds = [row["path"] for row in result["files"] if row["path"].startswith("data/sounds/")]
        self.assertEqual(sounds, list(resources.EXTRA_FILES))
        self.assertFalse((self.output / "data/sounds/pak/other.pak.wpc").exists())

    def test_missing_or_changed_pinned_sound_bank_refuses_before_copy(self):
        sound = self.thaw / next(iter(resources.EXTRA_FILES))
        for change in ("different bytes", "missing"):
            with self.subTest(change=change):
                if change == "missing":
                    sound.unlink()
                else:
                    sound.write_bytes(b"wrong stock sound bank")
                with patch.object(resources, "copy_file") as copy:
                    with self.assertRaisesRegex(ValueError, "resource pin differs|Missing resource path"):
                        resources.prepare(self.selection, self.output)
                    copy.assert_not_called()
                self.assert_no_cache()

    def test_added_source_file_and_configuration_change_refuse_publication(self):
        real_copy = resources.copy_file
        for change in ("file", "configuration"):
            with self.subTest(change=change):
                extra = self.thaw / "data/models/added.bin"

                def mutate(source, destination):
                    real_copy(source, destination)
                    if source == self.fixture:
                        if change == "file":
                            extra.write_bytes(b"late resource")
                        else:
                            self.selection.write_bytes(self.selection.read_bytes() + b"\n")

                with patch.object(resources, "copy_file", side_effect=mutate):
                    with self.assertRaisesRegex(ValueError, "sources changed|configuration changed"):
                        resources.prepare(self.selection, self.output)
                self.assert_no_cache()
                if extra.exists():
                    extra.unlink()

    def test_bad_copy_is_not_published(self):
        real_copy = resources.copy_file

        def corrupt(source, destination):
            real_copy(source, destination)
            if source == self.fixture:
                destination.write_bytes(b"wrong bytes")

        with patch.object(resources, "copy_file", side_effect=corrupt):
            with self.assertRaisesRegex(ValueError, "Copied resource bytes"):
                resources.prepare(self.selection, self.output)
        self.assert_no_cache()

    def test_publish_failure_preserves_other_directories(self):
        unrelated = self.root / ".stock-resources-unrelated"
        unrelated.mkdir()
        marker = unrelated / "keep.txt"
        marker.write_text("keep")
        with patch.object(resources.os, "rename", side_effect=OSError("synthetic rename failure")):
            with self.assertRaisesRegex(OSError, "rename failure"):
                resources.prepare(self.selection, self.output)
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.root.glob(".stock-resources-*")), [unrelated])
        self.assertEqual(marker.read_text(), "keep")

    def test_destination_created_during_publish_is_preserved(self):
        original_rename = resources.os.rename

        def competing_publish(stage, output):
            output.mkdir()
            (output / "owner.txt").write_text("another publisher")
            return original_rename(stage, output)

        with patch.object(resources.os, "rename", side_effect=competing_publish):
            with self.assertRaises(FileExistsError):
                resources.prepare(self.selection, self.output)
        self.assertEqual((self.output / "owner.txt").read_text(), "another publisher")
        self.assertFalse((self.output / "manifest.json").exists())
        self.assertEqual(list(self.root.glob(".stock-resources-*")), [])

    def test_game_directory_and_traversal_outputs_are_rejected(self):
        for output in (self.thaw / "new-cache", self.gta / "new-cache", self.root / "sub/../cache"):
            with self.subTest(output=output), self.assertRaises(ValueError):
                resources.prepare(self.selection, output)
            self.assertFalse(output.exists())

    def test_reparse_points_and_symlinks_are_rejected_before_copy(self):
        actual_lstat = Path.lstat
        for target in (self.fixture, self.root):
            for mode, attributes in ((stat.S_IFLNK, 0), (stat.S_IFDIR, stat.FILE_ATTRIBUTE_REPARSE_POINT)):
                with self.subTest(target=target, mode=mode):
                    def redirected(path, *args, **kwargs):
                        if path == target:
                            return SimpleNamespace(st_mode=mode, st_file_attributes=attributes)
                        return actual_lstat(path, *args, **kwargs)
                    with patch.object(Path, "lstat", redirected):
                        with self.assertRaisesRegex(ValueError, "Symlink or reparse"):
                            resources.prepare(self.selection, self.output)
                    self.assert_no_cache()

    def test_single_pass_matches_existing_full_inventory_and_hashes_each_file(self):
        nested = self.thaw / "data/models/nested/MixedCase.bin"
        nested.parent.mkdir()
        nested.write_bytes(b"nested resource")
        names = resources.inventory(self.thaw)
        expected = qb.setup_sources.files(self.thaw, names)
        with patch.object(qb.setup_sources, "digest", wraps=qb.setup_sources.digest) as digest:
            self.assertEqual(resources.verified_files(self.thaw), expected)
        self.assertEqual(digest.call_count, len(names))

    def test_single_pass_rejects_redirects_at_leaves_folders_and_ancestors(self):
        actual_lstat = Path.lstat
        for target in (self.fixture, self.root, self.thaw / "data/models",
                       self.thaw / "data/streams"):
            for mode, attributes in ((stat.S_IFLNK, 0), (stat.S_IFDIR, stat.FILE_ATTRIBUTE_REPARSE_POINT)):
                with self.subTest(target=target, mode=mode):
                    def redirected(path, *args, **kwargs):
                        if path == target:
                            return SimpleNamespace(st_mode=mode, st_file_attributes=attributes)
                        return actual_lstat(path, *args, **kwargs)
                    with patch.object(Path, "lstat", redirected):
                        with self.assertRaisesRegex(ValueError, "Symlink or reparse"):
                            resources.verified_files(self.thaw)

    def test_single_pass_rejects_ambiguous_names_and_duplicate_entries(self):
        for name in ("trailing.", "trailing ", "alternate:stream", "back\\slash"):
            with self.subTest(name=name), patch.object(resources.os, "scandir") as scan:
                scan.return_value.__enter__.return_value = [SimpleNamespace(name=name)]
                with self.assertRaisesRegex(ValueError, "Ambiguous resource filename"):
                    resources.verified_files(self.thaw)
        entry = SimpleNamespace(name=self.fixture.name, path=str(self.fixture))
        with patch.object(resources.os, "scandir") as scan:
            scan.return_value.__enter__.return_value = [entry, entry]
            with self.assertRaisesRegex(ValueError, "Duplicate resource path"):
                resources.verified_files(self.thaw)

    def test_single_pass_rejects_file_and_inventory_mutation_during_hashing(self):
        digest = qb.setup_sources.digest
        original = self.fixture.read_bytes()
        extra = self.fixture.parent / "late.bin"
        for change in ("file", "inventory"):
            with self.subTest(change=change):
                def mutate(path):
                    result = digest(path)
                    if path == self.fixture:
                        if change == "file":
                            path.write_bytes(original + b"modified while hashing")
                        else:
                            extra.write_bytes(b"new resource")
                    return result
                with patch.object(qb.setup_sources, "digest", side_effect=mutate):
                    with self.assertRaisesRegex(ValueError, "changed during verification"):
                        resources.verified_files(self.thaw)
                self.fixture.write_bytes(original)
                if extra.exists():
                    extra.unlink()

    def test_common_verifier_rechecks_both_trees_and_manifest(self):
        imported = resources.prepare(self.selection, self.output)
        image = self.root / "image.json"
        image.write_text(json.dumps(dict(source=str(self.thaw))))
        manifest_path = self.output / "manifest.json"

        def verify():
            return probe_skater.verify_common_resources(manifest_path, image)

        expected = dict(directory=self.output, files=len(imported["files"]),
                        bytes=sum(row["bytes"] for row in imported["files"]))
        self.assertEqual(verify(), expected)
        for root in (self.thaw, self.output):
            with self.subTest(root=root):
                path = root / self.fixture.relative_to(self.thaw)
                original, stamp = path.read_bytes(), path.stat()
                # Equal size and timestamp must never substitute for full content hashing.
                path.write_bytes(bytes(byte ^ 1 for byte in original))
                os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
                with self.assertRaisesRegex(RuntimeError, "Common resource bytes differ"):
                    verify()
                path.write_bytes(original)
                extra = path.parent / "unexpected.bin"
                extra.write_bytes(b"extra")
                with self.assertRaisesRegex(RuntimeError, "Common resource inventory changed"):
                    verify()
                extra.unlink()
                path.unlink()
                with self.assertRaisesRegex(RuntimeError, "Common resource inventory changed"):
                    verify()
                path.write_bytes(original)
        manifest = dict(imported, files=list(reversed(imported["files"])))
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(RuntimeError, "Common resource inventory changed"):
            verify()


if __name__ == "__main__":
    unittest.main()
