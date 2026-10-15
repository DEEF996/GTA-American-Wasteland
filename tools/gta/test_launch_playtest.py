import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import launch_playtest as launcher


class LauncherTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.research = self.root / 'research'
        self.research.mkdir()
        self.active = self.research / 'active-user-playtest.json'
        for name, value in (('RESEARCH', self.research), ('ACTIVE', self.active)):
            patcher = patch.object(launcher, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.game = dict(pid=100, creation_100ns=1234,
                         executable=str(self.root / 'game/gta_sa.exe'))
        self.supervisor = dict(pid=200, creation_100ns=5678, executable=sys.executable)
        self.directory = self.research / 'session'
        self.directory.mkdir()
        self.metadata = dict(pid=200, creation_100ns=5678, game_pid=100,
            logs=str(self.directory), script=str(launcher.SUPERVISOR),
            world_stream=True, continuous=True)
        self.active.write_text(json.dumps(self.metadata), encoding='utf-8')
        (self.directory / 'status.json').write_text(json.dumps(self.game), encoding='utf-8')

    def test_installation_requires_clone_hash_and_existing_window_mode(self):
        game = self.root / 'game'
        (game / 'scripts').mkdir(parents=True)
        (game / 'thaw_gta').mkdir()
        executable = game / 'gta_sa.exe'
        executable.write_bytes(b'owned test executable fixture')
        manifest = dict(format='gta-thaw-isolated-copy-v1', game=str(game), source=str(self.root / 'original'))
        manifest_path = game / 'thaw_gta/base-copy.json'
        manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        settings = game / 'scripts/III.VC.SA.WindowedMode.ini'
        asi = game / 'scripts/thaw_gta.asi'
        asi.touch()
        with patch.object(launcher, 'EXE_SHA256', hashlib.sha256(executable.read_bytes()).hexdigest()):
            for mode in (1, 2):
                settings.write_text(f'[window]\nmode={mode} ; existing setting\n', encoding='utf-8')
                self.assertEqual(launcher.validate_installation(game), executable)
            settings.write_text('[window]\nmode=3\n', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'windowed'):
                launcher.validate_installation(game)
            settings.write_text('[window]\nmode=1\n', encoding='utf-8')
            manifest_path.write_text(json.dumps(dict(manifest, source=str(game))), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'isolated'):
                launcher.validate_installation(game)
            manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
            executable.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'executable has changed'):
                launcher.validate_installation(game)

    def test_existing_game_selection_rejects_foreign_and_duplicate_games(self):
        executable = Path(self.game['executable'])
        self.assertIsNone(launcher.select_game([], executable))
        self.assertIs(launcher.select_game([self.game], executable), self.game)
        with self.assertRaisesRegex(ValueError, 'Another GTA'):
            launcher.select_game([dict(self.game, executable=str(self.root / 'original/gta_sa.exe'))], executable)
        with self.assertRaisesRegex(ValueError, 'Multiple copies'):
            launcher.select_game([self.game, dict(self.game, pid=101)], executable)

    def test_persisted_first_run_selection_and_incomplete_marker(self):
        game=self.root/'selected'; game.mkdir()
        selection=self.root/'installation.json'
        with patch.object(launcher,'ROOT',self.root):
            selection.write_text(json.dumps(dict(format='gta-thaw-installation-v1',complete=True,game=str(game))))
            self.assertEqual(launcher.selected_game(configure=False),game)
            self.assertEqual(launcher.selected_game(self.root/'explicit'),self.root/'explicit')
            selection.write_text(json.dumps(dict(format='gta-thaw-installation-v1',complete=False,game=str(game))))
            with self.assertRaisesRegex(ValueError,'Invalid local'): launcher.selected_game(configure=False)
        (game/'thaw_gta').mkdir()
        (game/'thaw_gta/installation.json').write_text(json.dumps(dict(format='gta-thaw-installation-v1',complete=False,game=str(game))))
        with self.assertRaisesRegex(ValueError,'incomplete'): launcher.validate_installation(game)

    def test_supervisor_arguments_enable_continuous_whole_world(self):
        command = launcher.supervisor_command(100, self.directory)
        self.assertEqual(command[:3], [sys.executable, '-B', str(launcher.SUPERVISOR)])
        self.assertEqual(command[command.index('--directory') + 1], str(self.directory))
        self.assertIn('--world-stream', command)
        self.assertIn('--continuous', command)
        self.assertNotIn('--scene', command)
        self.assertNotIn('--seconds', command)

    def test_stale_supervisor_pid_does_not_touch_reused_process(self):
        processes = Mock()
        processes.identity.return_value = dict(self.supervisor, creation_100ns=9999)
        self.assertIsNone(launcher.existing_supervisor(processes, self.game, Mock()))
        self.assertFalse((self.directory / 'STOP').exists())
        processes.identity.return_value = None
        self.assertIsNone(launcher.existing_supervisor(processes, self.game, Mock()))

    def test_existing_continuous_supervisor_is_reused(self):
        processes, log = Mock(), Mock()
        processes.identity.return_value = self.supervisor
        self.assertEqual(launcher.existing_supervisor(processes, self.game, log), self.metadata)
        self.assertFalse((self.directory / 'STOP').exists())
        self.assertEqual(log.call_args.args, ('reuse_supervisor',))

    def test_old_verified_supervisor_uses_cooperative_stop(self):
        self.active.write_text(json.dumps({key: self.metadata[key]
            for key in ('pid', 'creation_100ns', 'game_pid', 'logs')}), encoding='utf-8')
        processes = Mock()
        processes.identity.side_effect = [self.supervisor, self.supervisor, self.supervisor, None]
        with patch.object(launcher.time, 'sleep'):
            self.assertIsNone(launcher.existing_supervisor(processes, self.game, Mock()))
        self.assertTrue((self.directory / 'STOP').exists())

    def test_wrong_game_lifetime_prevents_reuse_and_stop(self):
        processes = Mock()
        processes.identity.return_value = self.supervisor
        with self.assertRaisesRegex(ValueError, 'another GTA process lifetime'):
            launcher.existing_supervisor(processes, dict(self.game, creation_100ns=999), Mock())
        self.assertFalse((self.directory / 'STOP').exists())

    def test_foreign_supervisor_image_or_log_path_is_not_touched(self):
        processes = Mock()
        processes.identity.return_value = dict(self.supervisor, executable=str(self.root / 'another.exe'))
        with self.assertRaisesRegex(ValueError, 'executable differs'):
            launcher.existing_supervisor(processes, self.game, Mock())
        processes.identity.return_value = self.supervisor
        self.active.write_text(json.dumps(dict(self.metadata, logs=str(self.root / 'foreign'))), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'outside this project'):
            launcher.existing_supervisor(processes, self.game, Mock())
        self.assertFalse((self.directory / 'STOP').exists())

    def test_startup_metadata_can_precede_status_but_wait_is_finite(self):
        processes = Mock()
        processes.identity.return_value = self.supervisor
        (self.directory / 'status.json').unlink()
        with patch.object(launcher.time, 'monotonic', side_effect=[0, 0, 6]), \
                patch.object(launcher.time, 'sleep'):
            with self.assertRaisesRegex(RuntimeError, 'startup status'):
                launcher.wait_for_supervisor(processes, self.metadata, self.game)

    def test_adapter_wait_closes_mapping_and_checks_game_lifetime(self):
        processes, observer = Mock(), Mock()
        processes.identity.return_value = self.game
        observer.created, observer.exe = 1234, Path(self.game['executable'])
        with patch.object(launcher, 'Observer', side_effect=[OSError('not ready'), observer]), \
                patch.object(launcher.time, 'sleep'):
            launcher.wait_for_adapter(processes, self.game, observer.exe)
        observer.close.assert_called_once_with()
        observer.reset_mock()
        observer.created = 999
        with patch.object(launcher, 'Observer', return_value=observer):
            with self.assertRaisesRegex(ValueError, 'another GTA process lifetime'):
                launcher.wait_for_adapter(processes, self.game, observer.exe)
        observer.close.assert_called_once_with()

    def test_failed_owned_supervisor_startup_requests_cleanup_and_releases_lock(self):
        processes, child = Mock(), Mock()
        processes.api.CreateMutexW.return_value = 123
        processes.api.WaitForSingleObject.return_value = 0
        processes.games.return_value = [self.game]
        processes.identity.return_value = self.supervisor
        child.pid = self.supervisor['pid']
        with patch.object(launcher, 'validate_installation', return_value=Path(self.game['executable'])), \
                patch.object(launcher, 'Processes', return_value=processes), \
                patch.object(launcher, 'existing_supervisor', return_value=None), \
                patch.object(launcher, 'wait_for_adapter'), \
                patch.object(launcher, 'wait_for_supervisor', side_effect=RuntimeError('startup failed')), \
                patch.object(launcher.subprocess, 'Popen', return_value=child) as create:
            with self.assertRaisesRegex(RuntimeError, 'startup failed'):
                launcher.launch(Path(self.game['executable']).parent, Mock())
        metadata = json.loads(self.active.read_text(encoding='utf-8'))
        self.assertTrue((Path(metadata['logs']) / 'STOP').is_file())
        child.wait.assert_called_once_with(timeout=3)
        child.kill.assert_not_called()
        self.assertEqual(create.call_count, 1)
        self.assertEqual(create.call_args.kwargs['creationflags'], launcher.subprocess.CREATE_NO_WINDOW)
        processes.api.ReleaseMutex.assert_called_once_with(123)
        processes.api.CloseHandle.assert_called_once_with(123)


if __name__ == '__main__':
    unittest.main()
