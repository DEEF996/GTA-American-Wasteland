"""Synthetic cache publication tests; exporter format tests live with exporters."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import import_assets as assets


class ImportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="synthetic-stock-assets-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.thaw, self.gta = self.root / "thaw", self.root / "gta"
        self.gta.mkdir()
        names = set(assets.INPUTS) | set(assets.qb.setup_sources.THAW_REQUIRED)
        for name in names:
            path = self.thaw / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(("synthetic " + name).encode())
        for owner, key, names in ((assets, "INPUTS", assets.INPUTS),
                                  (assets.qb, "PINNED_INPUTS", assets.qb.setup_sources.THAW_REQUIRED)):
            pins = {name: ((self.thaw / name).stat().st_size, assets.qb.setup_sources.digest(self.thaw / name))
                    for name in names}
            self.patch(owner, key, pins)
        self.config = self.root / "sources.json"
        self.config.write_bytes(assets.qb.json_bytes(dict(
            format="gta-thaw-local-sources-v1", gta=dict(directory=str(self.gta)),
            thaw=dict(directory=str(self.thaw), rethawed_present=False,
                      files=assets.qb.setup_sources.files(self.thaw, assets.qb.setup_sources.THAW_REQUIRED)))))
        self.output = self.root / "cache"
        self.patch(assets.assets_skeleton, "export", self.rig)
        self.patch(assets.assets_models, "export", self.board)
        self.patch(assets.assets_skeleton, "extract_entry", lambda *args: (bytes(5376), "synthetic source"))

    def patch(self, owner, name, value):
        patched = patch.object(owner, name, value)
        patched.start()
        self.addCleanup(patched.stop)

    @staticmethod
    def rig(source, output):
        output.write_bytes(b"synthetic rig")
        return dict(bone_count=52)

    @staticmethod
    def board(model, texture, output):
        output.mkdir()
        (output / "mesh.json").write_bytes(b"synthetic model")
        return dict(vertices=558, triangles=552, textures=5, weighted_vertices=558,
                    unresolved_texture_checksums=[])

    def test_independent_exact_copy_and_exclusive_publication(self):
        result = assets.prepare(self.config, self.output)
        self.assertEqual(result, json.loads((self.output / "manifest.json").read_text()))
        self.assertFalse(result["backend_ready"])
        self.assertFalse(result["complete_asset_closure"])
        for name in assets.COPIES:
            source, copy = self.thaw / name, self.output / name
            self.assertFalse(source.samefile(copy))
            self.assertEqual(source.read_bytes(), copy.read_bytes())
        marker = self.output / "keep"
        marker.write_bytes(b"unrelated")
        with self.assertRaisesRegex(ValueError, "existing asset cache"):
            assets.prepare(self.config, self.output)
        self.assertEqual(marker.read_bytes(), b"unrelated")

    def test_original_destinations_and_modified_inputs_are_rejected(self):
        for root in (self.thaw, self.gta):
            with self.assertRaisesRegex(ValueError, "outside original"):
                assets.prepare(self.config, root / "new-cache")
            self.assertFalse((root / "new-cache").exists())
        (self.thaw / assets.TEXTURE).write_bytes(b"different asset version")
        with self.assertRaisesRegex(ValueError, "Unsupported stock asset"):
            assets.prepare(self.config, self.output)
        self.assertFalse(self.output.exists())

    def test_source_changed_during_export_does_not_publish(self):
        def changed(source, output):
            (source / assets.TEXTURE).write_bytes(b"changed during export")
            return self.rig(source, output)
        with patch.object(assets.assets_skeleton, "export", changed):
            with self.assertRaisesRegex(ValueError, "Unsupported stock asset"):
                assets.prepare(self.config, self.output)
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.root.glob(".stock-assets-*")), [])

    def test_failed_publication_cleans_only_owned_staging(self):
        unrelated = self.root / ".stock-assets-unrelated"
        unrelated.mkdir()
        (unrelated / "keep").write_bytes(b"keep")
        with patch.object(assets.os, "rename", side_effect=OSError("publish refused")):
            with self.assertRaisesRegex(OSError, "publish refused"):
                assets.prepare(self.config, self.output)
        self.assertEqual(list(self.root.glob(".stock-assets-*")), [unrelated])
        self.assertEqual((unrelated / "keep").read_bytes(), b"keep")
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
