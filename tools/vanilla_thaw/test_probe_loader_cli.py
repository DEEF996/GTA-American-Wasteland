import contextlib
import io
from pathlib import Path
import unittest
from unittest.mock import patch

import probe_loader


class LoaderSelectionTests(unittest.TestCase):
    def selected(self, *arguments):
        with patch.object(probe_loader.sys, "argv", ["probe_loader.py", "image", "report", *arguments]), patch.object(probe_loader, "probe") as probe, contextlib.redirect_stdout(io.StringIO()):
            probe_loader.main()
            return probe.call_args.kwargs

    def test_skater_has_four_separate_input_roles(self):
        value = self.selected("--skater", "qb", "common", "profile", "assets")
        self.assertTrue(value["core"] and value["scene"])
        self.assertEqual(value["qb_manifest"], Path("qb"))
        self.assertEqual(value["resources_manifest"], Path("common"))
        self.assertEqual(value["game_profile_manifest"], Path("profile"))
        self.assertEqual(value["skater_assets_manifest"], Path("assets"))
        self.assertIsNone(value["animation_manifest"])
        self.assertFalse(value["frames"])

    def test_frames_requires_same_four_inputs_and_complete_actor(self):
        value = self.selected("--frames", "qb", "common", "profile", "assets")
        self.assertTrue(value["core"] and value["scene"] and value["frames"])
        self.assertEqual(value["skater_assets_manifest"], Path("assets"))
        self.assertEqual(value["qb_manifest"], Path("qb"))
        self.assertEqual(value["resources_manifest"], Path("common"))
        self.assertEqual(value["game_profile_manifest"], Path("profile"))
        with self.assertRaises(RuntimeError):
            self.selected("--frames", "qb", "common", "profile")

    def test_previous_modes_do_not_construct_skater(self):
        for mode in ("--game", "--scene", "--core"):
            value = self.selected(mode, "qb", "common", "profile")
            self.assertIsNone(value["skater_assets_manifest"])
            self.assertFalse(value["frames"])
            self.assertEqual(value["core"], mode == "--core")
            self.assertEqual(value["scene"], mode != "--game")
        value = self.selected("--animation-motion", "assets")
        self.assertTrue(value["animation_motion"])
        self.assertIsNone(value["qb_manifest"])
        self.assertIsNone(value["game_profile_manifest"])

    def test_missing_skater_assets_rejected(self):
        with self.assertRaises(RuntimeError):
            self.selected("--skater", "qb", "common", "profile")


if __name__ == "__main__":
    unittest.main()
