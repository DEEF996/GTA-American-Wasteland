"""Synthetic source integrity and exclusive publication checks."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import import_profile as profile


class ProfileImportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="synthetic-profile-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.thaw, self.gta = self.root / "thaw", self.root / "gta"
        self.gta.mkdir()
        for name in (*profile.qb.setup_sources.THAW_REQUIRED, profile.CAS):
            target = self.thaw / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(("synthetic " + name).encode())
        pins = {name: ((self.thaw / name).stat().st_size, profile.qb.setup_sources.digest(self.thaw / name))
                for name in profile.qb.setup_sources.THAW_REQUIRED}
        self.patcher(profile.qb, "PINNED_INPUTS", pins)
        archive = (self.thaw / profile.CAS).read_bytes()
        self.patcher(profile, "CAS_PIN", (len(archive), profile.qb.sha(archive)))
        self.payloads = {row[0]: ("original synthetic " + row[0]).encode() for row in profile.SOURCES}
        self.patcher(profile, "SOURCES", tuple((name, filename, len(self.payloads[name]), profile.qb.sha(self.payloads[name]))
                                               for name, filename, *_ in profile.SOURCES))
        self.patcher(profile.qb, "read_member", self.read_member)
        self.config = self.root / "sources.json"
        self.config.write_bytes(profile.qb.json_bytes(dict(
            format="gta-thaw-local-sources-v1", gta=dict(directory=str(self.gta)),
            thaw=dict(directory=str(self.thaw), rethawed_present=False,
                      files=profile.qb.setup_sources.files(self.thaw, profile.qb.setup_sources.THAW_REQUIRED)))))
        self.output = self.root / "profile"

    def patcher(self, owner, name, value):
        item = patch.object(owner, name, value)
        item.start()
        self.addCleanup(item.stop)

    def read_member(self, archive, name, kind):
        self.assertEqual(kind, 0x5d796624 if name.startswith("pak/cas/") else profile.qb.qbkey(".qb"))
        canonical = name + ".wpc" if name.startswith("pak/cas/") else name
        return self.payloads[canonical]

    def test_exact_payloads_and_exclusive_destination(self):
        result = profile.prepare(self.config, self.output)
        self.assertEqual(result, json.loads((self.output / "manifest.json").read_text()))
        self.assertTrue(result["sources_revalidated_before_publish"])
        self.assertFalse(result["backend_ready"])
        for row in result["sources"]:
            self.assertEqual(Path(row["path"]).read_bytes(), self.payloads[row["canonical_name"]])
        with self.assertRaisesRegex(ValueError, "existing profile cache"):
            profile.prepare(self.config, self.output)

    def test_original_destinations_and_wrong_archive_rejected(self):
        for root in (self.gta, self.thaw):
            with self.assertRaisesRegex(ValueError, "outside original"):
                profile.prepare(self.config, root / "new-cache")
        (self.thaw / profile.CAS).write_bytes(b"another version")
        with self.assertRaisesRegex(ValueError, "profile archive"):
            profile.prepare(self.config, self.output)
        self.assertFalse(self.output.exists())

    def test_source_mutation_during_read_prevents_publication(self):
        def changed(*args, **kwargs):
            (self.thaw / profile.CAS).write_bytes(b"modified while importing")
            return self.read_member(*args, **kwargs)
        with patch.object(profile.qb, "read_member", changed):
            with self.assertRaisesRegex(ValueError, "source changed"):
                profile.prepare(self.config, self.output)
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.root.glob(".stock-profile-*")), [])

    def test_failed_publish_preserves_unrelated_staging(self):
        unrelated = self.root / ".stock-profile-unrelated"
        unrelated.mkdir()
        with patch.object(profile.os, "rename", side_effect=OSError("publication refused")):
            with self.assertRaisesRegex(OSError, "publication refused"):
                profile.prepare(self.config, self.output)
        self.assertEqual(list(self.root.glob(".stock-profile-*")), [unrelated])
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
