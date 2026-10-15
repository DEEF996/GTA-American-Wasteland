import json
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import launcher


class DesktopLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base/'package'
        self.root.mkdir()
        self.gta = self.base/'original GTA'
        self.thaw = self.base/'original THAW'
        self.game = self.base/'GTA_American_Wasteland'
        self.gta.mkdir(); self.thaw.mkdir()
        (self.gta/'gta_sa.exe').write_bytes(b'folder-selection fixture, never executed')
        (self.thaw/'THAW.exe').write_bytes(b'folder-selection fixture, never executed')
        self.settings = dict(format=launcher.FORMAT, language='pl', gta=str(self.gta), thaw=str(self.thaw), game=str(self.game))
        self.adapter = b'our native adapter fixture, never loaded'
        self.adapter_sha256 = hashlib.sha256(self.adapter).hexdigest()
        self.build = dict(asi_sha256=self.adapter_sha256)
        adapter = patch.object(launcher.install, 'adapter_build', return_value=(self.root/'adapter.asi', self.build))
        adapter.start()
        self.addCleanup(adapter.stop)

    def fake_install(self, gta, thaw, game, **options):
        self.assertEqual((gta, thaw, game), (self.gta, self.thaw, self.game))
        self.assertEqual(options['root'], self.root)
        (game/'thaw_gta').mkdir(parents=True)
        (game/'scripts').mkdir()
        (game/'scripts/thaw_gta.asi').write_bytes(self.adapter)
        launcher.install.write_json(game/'thaw_gta/installation.json', dict(format=launcher.install.FORMAT,
            complete=True, source=str(gta), thaw_source=str(thaw), game=str(game), adapter_sha256=self.adapter_sha256))

    def test_first_play_installs_then_launches_and_remembers_both_sources(self):
        events = []
        def install(*args, **kwargs):
            events.append('install')
            self.fake_install(*args, **kwargs)
        def launch(game, log):
            events.append('launch')
            self.assertEqual(game, self.game)
            log('fixture_started', harmless=True)
        result = launcher.prepare_and_play(self.settings, root=self.root, install_game=install, launch_game=launch)
        self.assertEqual(events, ['install', 'launch'])
        self.assertEqual(launcher.load_settings(self.root), self.settings)
        self.assertEqual(result, self.settings)
        self.assertEqual(launcher.read_json(self.root/'installation.json')['game'], str(self.game))
        self.assertEqual(len(list((self.root/'research').glob('desktop-launcher-*.jsonl'))), 1)

    def test_second_play_reuses_only_the_matching_completed_copy(self):
        self.fake_install(self.gta, self.thaw, self.game, root=self.root)
        install, launch = Mock(), Mock()
        launcher.prepare_and_play(self.settings, root=self.root, install_game=install, launch_game=launch)
        install.assert_not_called()
        self.assertEqual(launch.call_count, 1)
        path = self.game/'thaw_gta/installation.json'
        state = launcher.read_json(path)
        for changed in (dict(state, complete=False), dict(state, source=str(self.thaw)),
                        dict(state, thaw_source=str(self.gta)), dict(state, game=str(self.root))):
            launcher.install.write_json(path, changed)
            launch.reset_mock()
            with self.assertRaisesRegex(ValueError, 'NEW modded-copy'):
                launcher.prepare_and_play(self.settings, root=self.root, install_game=install, launch_game=launch)
            launch.assert_not_called()
            install.assert_not_called()

    def test_cancel_after_import_prevents_game_launch_without_deleting_partial_files(self):
        launch = Mock()
        checks = iter((False, True))
        with self.assertRaises(launcher.install.Cancelled):
            launcher.prepare_and_play(self.settings, root=self.root, install_game=self.fake_install,
                                      launch_game=launch, stopped=lambda: next(checks))
        launch.assert_not_called()
        self.assertTrue((self.game/'thaw_gta/installation.json').exists())
        self.assertFalse((self.root/'installation.json').exists())

    def test_new_release_or_changed_installed_adapter_cannot_reuse_an_old_copy(self):
        self.fake_install(self.gta, self.thaw, self.game, root=self.root)
        install, launch = Mock(), Mock()
        self.build['asi_sha256'] = '0'*64
        with self.assertRaisesRegex(ValueError, 'different mod version'):
            launcher.prepare_and_play(self.settings, root=self.root, install_game=install, launch_game=launch)
        self.build['asi_sha256'] = self.adapter_sha256
        path = self.game/'scripts/thaw_gta.asi'
        path.write_bytes(b'changed adapter fixture')
        with self.assertRaisesRegex(ValueError, 'changed adapter'):
            launcher.prepare_and_play(self.settings, root=self.root, install_game=install, launch_game=launch)
        install.assert_not_called(); launch.assert_not_called()
        self.assertEqual(path.read_bytes(), b'changed adapter fixture')

    def test_failed_import_does_not_launch_or_claim_completed_installation(self):
        launch = Mock()
        with self.assertRaisesRegex(ValueError, 'unsupported fixture'):
            launcher.prepare_and_play(self.settings, root=self.root,
                install_game=Mock(side_effect=ValueError('unsupported fixture')), launch_game=launch)
        launch.assert_not_called()
        self.assertFalse((self.root/'installation.json').exists())

    def test_missing_source_and_foreign_output_are_preserved(self):
        self.game.mkdir()
        kept = self.game/'keep.txt'
        kept.write_text('user data')
        install, launch = Mock(), Mock()
        with self.assertRaises(ValueError):
            launcher.prepare_and_play(self.settings, root=self.root, install_game=install, launch_game=launch)
        self.assertEqual(kept.read_text(), 'user data')
        (self.thaw/'THAW.exe').unlink()
        with self.assertRaises(ValueError):
            launcher.prepare_and_play(self.settings, root=self.root, install_game=install, launch_game=launch)
        install.assert_not_called(); launch.assert_not_called()

    def test_settings_reject_corruption_and_oversized_metadata(self):
        path = self.root/launcher.SETTINGS
        for invalid in ([], {'format': 'other'}, dict(self.settings, language='bad'), dict(self.settings, gta=['path'])):
            path.write_text(json.dumps(invalid))
            with self.assertRaises(ValueError):
                launcher.load_settings(self.root)
        path.write_bytes(b' '*16385)
        with self.assertRaisesRegex(ValueError, 'oversized'):
            launcher.load_settings(self.root)

    def test_adopts_completed_installation_but_never_developer_drive_fallback(self):
        empty = launcher.load_settings(self.root)
        self.assertEqual([empty[key] for key in ('gta', 'thaw', 'game')], ['', '', ''])
        self.fake_install(self.gta, self.thaw, self.game, root=self.root)
        launcher.install.write_json(self.root/'installation.json',
            dict(format=launcher.install.FORMAT, complete=True, game=str(self.game)))
        loaded = launcher.load_settings(self.root)
        self.assertEqual([loaded[key] for key in ('gta', 'thaw', 'game')], [str(self.gta), str(self.thaw), str(self.game)])

    def test_both_languages_cover_all_labels_and_statuses(self):
        self.assertEqual(set(launcher.TEXT['en']), set(launcher.TEXT['pl']))
        self.assertTrue(all(value.strip() for values in launcher.TEXT.values() for value in values.values()))
        self.assertEqual(launcher.suggested_destination(self.gta), self.game)

    def test_gui_launch_uses_hidden_console_sibling_and_reports_cli_failure(self):
        runtime = self.root/'runtime/python'
        runtime.mkdir(parents=True)
        (runtime/'python.exe').write_bytes(b'fixture, never executed')
        log = Mock()
        result = Mock(returncode=0, stdout='started')
        with patch.object(launcher.sys, 'executable', str(runtime/'pythonw.exe')), \
                patch.object(launcher.subprocess, 'run', return_value=result) as run:
            launcher.launch_hidden(self.game, log, root=self.root)
            command = run.call_args.args[0]
            self.assertEqual(command, [str(runtime/'python.exe'), '-E', '-s', '-B',
                str(self.root/'tools/gta/launch_playtest.py'), '--game', str(self.game)])
            self.assertEqual(run.call_args.kwargs['creationflags'], launcher.subprocess.CREATE_NO_WINDOW)
            self.assertNotIn('shell', run.call_args.kwargs)
            self.assertTrue(launcher.sys.executable.endswith('pythonw.exe'))
            result.returncode = 1
            result.stdout = 'unsupported installation fixture'
            with self.assertRaisesRegex(RuntimeError, 'unsupported installation'):
                launcher.launch_hidden(self.game, log, root=self.root)

    def test_enter_cannot_restart_setup_before_the_completed_worker_event_is_displayed(self):
        import tkinter as tk
        window = tk.Tk(); window.withdraw()
        self.addCleanup(window.destroy)
        app = launcher.Launcher(window, root=self.root)
        for key, variable in app.values.items():
            variable.set(self.settings[key])
        with patch.object(launcher, 'prepare_and_play', return_value=self.settings) as prepare:
            app.start()
            app.worker.join(timeout=2)
            self.assertFalse(app.worker.is_alive())
            self.assertTrue(app.busy)
            app.start()
            self.assertEqual(prepare.call_count, 1)
            app.tick()
            self.assertFalse(app.busy)
            self.assertEqual(app.status_key, 'started')


if __name__ == '__main__':
    unittest.main()
