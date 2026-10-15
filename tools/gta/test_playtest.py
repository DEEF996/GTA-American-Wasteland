import unittest
import json
from pathlib import Path
import subprocess
import tempfile
from unittest.mock import Mock, patch

import playtest
from playtest import eligible, child_command


class PlaytestTest(unittest.TestCase):
    def setUp(self):
        self.display = Mock()
        connector = patch.object(playtest.startup_status, 'connect', return_value=self.display)
        connector.start()
        self.addCleanup(connector.stop)
        preparation = patch.object(playtest, 'prepare_world', return_value=True)
        self.preparation = preparation.start()
        self.addCleanup(preparation.stop)

    def test_wait_for_valid_grounded_start_without_an_existing_owner(self):
        state = dict(control_eligible=True, flags=8, grounded=True,
                     ped_motion_flags=1, area=0, position=[10, 10, 20])
        self.assertTrue(eligible(state, [10, 10, 0]))
        for change in ({'control_eligible': False}, {'flags': 0}, {'flags': 8 | 64},
                       {'flags': 8 | 256}, {'grounded': False}, {'ped_motion_flags': 5},
                       {'area': 1}, {'position': [106, 10, 20]}):
            with self.subTest(change=change):
                self.assertFalse(eligible(dict(state, **change), [10, 10, 0]))

    def test_streaming_accepts_far_away_and_interior_but_keeps_ownership_guards(self):
        state = dict(control_eligible=True, flags=8, grounded=True,
                     ped_motion_flags=1, area=13, ped_area=13, position=[-2800, 2700, 1500])
        self.assertTrue(eligible(state, world_stream=True))
        for change in ({'ped_area': 0}, {'control_eligible': False}, {'flags': 8 | 64},
                       {'flags': 8 | 256}, {'grounded': False}, {'ped_motion_flags': 3}):
            with self.subTest(change=change):
                self.assertFalse(eligible(dict(state, **change), world_stream=True))

    def test_continuous_stream_child_has_no_fixed_scene_or_session_limits(self):
        command = child_command(123, None, Path('session.jsonl'), world_stream=True, continuous=True, f7_baseline=23)
        self.assertIn('--world-stream', command)
        self.assertIn('--continuous', command)
        self.assertIn('--camera', command)
        self.assertIn('--hud', command)
        self.assertEqual(command[command.index('--f7-baseline')+1], '23')
        for argument in ('--scene', '--seconds', '--wait', '--sessions'):
            self.assertNotIn(argument, command)
        legacy = child_command(123, Path('scene.col'), Path('session.jsonl'))
        self.assertEqual(legacy[legacy.index('--scene')+1], 'scene.col')
        self.assertNotIn('--world-stream', legacy)

    def test_stop_gracefully_releases_owned_child_and_closes_observer_without_deadline(self):
        class FakeObserver:
            created, closed = 456, False

            def read(self):
                return dict(control_eligible=True, flags=8, grounded=True,
                    ped_motion_flags=1, area=0, ped_area=0, position=[2000, 1000, 10], f7_requests=23)

            def close(self):
                self.closed = True

        class FakeChild:
            killed = False
            exited = False
            waited = []

            def poll(self):
                return 1 if self.killed or self.exited else None

            def kill(self):
                self.killed = True

            def wait(self, timeout):
                self.waited.append(timeout)
                self.exited = True
                return 1

        observer, child = FakeObserver(), FakeChild()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / 'run'

            def sleep(_):
                (directory / 'STOP').touch()

            with patch.object(playtest, 'Observer', return_value=observer), \
                    patch.object(playtest, 'alive', return_value=True), \
                    patch.object(playtest.subprocess, 'Popen', return_value=child) as launch, \
                    patch.object(playtest.time, 'sleep', side_effect=sleep), \
                    patch.object(playtest.time, 'monotonic', side_effect=AssertionError('No supervisor deadline')):
                playtest.supervise(123, None, directory, world_stream=True, continuous=True)
            self.assertFalse(child.killed)
            self.assertEqual(child.waited, [3])
            self.assertTrue(observer.closed)
            self.assertEqual(launch.call_count, 1)
            self.preparation.assert_called_once()
            self.assertIn('--continuous', launch.call_args.args[0])
            command = launch.call_args.args[0]
            self.assertEqual(command[command.index('--stop-file') + 1], str(directory / 'STOP'))
            self.assertEqual(command[command.index('--f7-baseline') + 1], '23')
            status = json.loads((directory / 'status.json').read_bytes())
            self.assertEqual(status['phase'], 'stopped')
            self.assertEqual(status['creation_100ns'], 456)
            self.display.close.assert_called_once()
            self.assertIn('starting_gameplay', [call.args[0] for call in self.display.update.call_args_list])
            self.assertNotIn('ready', [call.args[0] for call in self.display.update.call_args_list])

    def test_map_then_gameplay_startup_remain_distinct_until_engine_ready_marker(self):
        observer=Mock(created=456)
        observer.read.return_value=dict(control_eligible=True, flags=8, grounded=True,
            ped_motion_flags=1, area=0, ped_area=0, position=[2000,1000,10], f7_requests=23)
        child=Mock()
        child.poll.return_value=None
        child.wait.return_value=0
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary)/'run'
            def prepare(_observer, _directory, status):
                status('loading',map_stage='checking_sources')
                return True
            self.preparation.side_effect=prepare
            def sleep(_):
                phase=self.display.update.call_args.args[0]
                if phase=='starting_gameplay':
                    self.assertNotIn('ready',[call.args[0] for call in self.display.update.call_args_list])
                    (directory/'attempt-0001.ready').write_text('original engine ready')
                elif phase=='ready':
                    (directory/'STOP').touch()
            with patch.object(playtest,'Observer',return_value=observer), \
                    patch.object(playtest,'alive',return_value=True), \
                    patch.object(playtest.subprocess,'Popen',return_value=child), \
                    patch.object(playtest.time,'sleep',side_effect=sleep):
                playtest.supervise(123,None,directory,world_stream=True,continuous=True)
            self.assertEqual([call.args[0] for call in self.display.update.call_args_list],
                ['loading','starting_gameplay','starting_gameplay','ready','restarting','stopped'])
            child.kill.assert_not_called()

    def test_only_an_unresponsive_owned_child_is_killed_after_grace(self):
        child = Mock()
        child.wait.side_effect = [subprocess.TimeoutExpired('owned-child', 3), 1]
        self.assertEqual(playtest.wait_for_owned_child(child), 1)
        self.assertEqual([call.kwargs['timeout'] for call in child.wait.call_args_list], [3, 5])
        child.kill.assert_called_once_with()

    def test_f7_intent_survives_unavailable_start_and_child_restart(self):
        ready = dict(control_eligible=True, flags=8, grounded=True, ped_motion_flags=1,
                     area=0, ped_area=0, position=[2000, 1000, 10])
        observer = Mock(created=456)
        observer.read.side_effect = [dict(ready, flags=0, f7_requests=23),
                                    dict(ready, f7_requests=24), dict(ready, f7_requests=25)]
        child = Mock()
        child.poll.return_value = 1
        child.wait.return_value = 1
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / 'run'
            with patch.object(playtest, 'Observer', return_value=observer), \
                    patch.object(playtest, 'alive', return_value=True), \
                    patch.object(playtest.subprocess, 'Popen', return_value=child) as launch:
                def sleep(_):
                    if launch.call_count == 2:
                        (directory / 'STOP').touch()
                with patch.object(playtest.time, 'sleep', side_effect=sleep):
                    playtest.supervise(123, None, directory, world_stream=True, continuous=True)
            commands = [call.args[0] for call in launch.call_args_list]
            self.assertEqual([command[command.index('--f7-baseline')+1] for command in commands], ['23', '23'])
            observer.close.assert_called_once()


class MapPreparationTest(unittest.TestCase):
    def run_preparation(self, directory, future, *, game_alive=True):
        observer, status = Mock(exe=Path(directory) / 'game/gta_sa.exe'), Mock()
        with patch.object(playtest, 'ThreadPoolExecutor') as executor, \
                patch.object(playtest, 'alive', return_value=game_alive), \
                patch.object(playtest.time, 'sleep'):
            submit = executor.return_value.__enter__.return_value.submit
            submit.return_value = future
            result = playtest.prepare_world(observer, Path(directory), status)
        return result, status, submit

    def test_slow_preparation_keeps_loading_heartbeat_without_claiming_engine_ready(self):
        future = Mock()
        future.done.side_effect = [False, False, True]
        future.result.return_value = dict(changed=False, source_count=118)
        with tempfile.TemporaryDirectory() as directory:
            result, status, submit = self.run_preparation(directory, future)
            self.assertTrue(result)
            self.assertEqual([call.args[0] for call in status.call_args_list], ['loading'] * 3)
            self.assertEqual(json.loads((Path(directory) / 'map-preparation.json').read_bytes()),
                             future.result.return_value)
            self.assertFalse(submit.call_args.kwargs['stopped']())

    def test_game_exit_cancels_map_publication_and_does_not_start_an_engine(self):
        future = Mock()
        future.done.return_value = False
        with tempfile.TemporaryDirectory() as directory:
            result, status, submit = self.run_preparation(directory, future, game_alive=False)
            self.assertFalse(result)
            self.assertTrue(submit.call_args.kwargs['stopped']())
            future.result.assert_not_called()
            self.assertEqual(status.call_args.args[0], 'game_closed')
            self.assertFalse((Path(directory) / 'map-preparation.json').exists())

    def test_bad_map_never_reports_ready(self):
        future = Mock()
        future.done.return_value = True
        future.result.side_effect = ValueError('Unsupported map source')
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, 'Unsupported map source'):
                self.run_preparation(directory, future)


if __name__ == '__main__':
    unittest.main()
