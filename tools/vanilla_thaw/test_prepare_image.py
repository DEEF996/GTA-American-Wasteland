"""Native-input preparation uses synthetic bytes only; nothing is executed."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import prepare_image as image


class PrepareTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="synthetic-stock-image-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.thaw, self.gta = self.root / "thaw", self.root / "gta"
        (self.thaw / "data/pak").mkdir(parents=True)
        self.gta.mkdir()
        names = set(image.FILES) | set(image.qb.setup_sources.THAW_REQUIRED)
        self.inputs = {name: ("synthetic input " + name).encode() for name in names}
        for name, data in self.inputs.items():
            (self.thaw / name).write_bytes(data)
        native_pins = {name: (len(self.inputs[name]), image.qb.sha(self.inputs[name])) for name in image.FILES}
        core_pins = {name: (len(self.inputs[name]), image.qb.sha(self.inputs[name]))
                     for name in image.qb.setup_sources.THAW_REQUIRED}
        for owner, name, value in ((image, "FILES", native_pins), (image.qb, "PINNED_INPUTS", core_pins)):
            mock = patch.object(owner, name, value)
            mock.start()
            self.addCleanup(mock.stop)
        self.config = self.root / "sources.json"
        self.config.write_bytes(image.qb.json_bytes({
            "format": "gta-thaw-local-sources-v1", "gta": {"directory": str(self.gta)},
            "thaw": {"directory": str(self.thaw), "rethawed_present": False,
                     "files": image.qb.setup_sources.files(self.thaw, image.qb.setup_sources.THAW_REQUIRED)},
        }))
        self.output = self.root / "native"

    def test_copies_are_independent_exact_and_exclusive(self):
        result = image.prepare(self.config, self.output)
        self.assertEqual(result, json.loads((self.output / "manifest.json").read_text()))
        self.assertFalse(result["entrypoint_allowed"])
        self.assertFalse(result["backend_ready"])
        for row in result["files"]:
            source, target = self.thaw / row["path"], self.output / row["path"]
            self.assertFalse(source.samefile(target))
            self.assertEqual(target.read_bytes(), self.inputs[row["path"]])
            self.assertEqual(source.read_bytes(), self.inputs[row["path"]])
        with self.assertRaisesRegex(ValueError, "existing native cache"):
            image.prepare(self.config, self.output)

    def test_mismatched_dll_and_original_destination_are_rejected(self):
        for root in (self.thaw, self.gta):
            with self.assertRaisesRegex(ValueError, "outside original"):
                image.prepare(self.config, root / "new-cache")
            self.assertFalse((root / "new-cache").exists())
        (self.thaw / "binkw32.dll").write_bytes(b"different version")
        with self.assertRaisesRegex(ValueError, "Unsupported native input"):
            image.prepare(self.config, self.output)
        self.assertFalse(self.output.exists())

    def test_failed_publication_preserves_unrelated_data(self):
        unrelated = self.root / ".stock-image-unrelated"
        unrelated.mkdir()
        marker = unrelated / "keep"
        marker.write_bytes(b"preserve")
        with patch.object(image.os, "rename", side_effect=OSError("publish refused")):
            with self.assertRaisesRegex(OSError, "publish refused"):
                image.prepare(self.config, self.output)
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.root.glob(".stock-image-*")), [unrelated])
        self.assertEqual(marker.read_bytes(), b"preserve")

    def test_dll_change_before_publication_rejects_and_cleans(self):
        native_inputs = image.native_inputs
        calls = []

        def changed(root):
            calls.append(True)
            if len(calls) == 2:
                (root / "d3dx9_27.dll").write_bytes(b"changed while copying")
            return native_inputs(root)

        with patch.object(image, "native_inputs", side_effect=changed):
            with self.assertRaisesRegex(ValueError, "Unsupported native input"):
                image.prepare(self.config, self.output)
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.root.glob(".stock-image-*")), [])


if __name__ == "__main__":
    unittest.main()
