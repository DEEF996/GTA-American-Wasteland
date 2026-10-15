from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from audio_mix import AudioMix
import probe_audio


class AudioMixTests(unittest.TestCase):
    def fixture(self):
        gain = Mock();gain.close.return_value = []
        pitch = Mock()
        transitions = Mock();transitions.close.return_value = []
        frame = SimpleNamespace(preflight=Mock(), owners={probe_audio.DEVICE: 0x12340},
                                system=SimpleNamespace(call=Mock(return_value=345)))
        mix = AudioMix(frame, gain_factory=lambda pid: gain,
                       pitch_factory=lambda frame: pitch, transition_factory=lambda system, device: transitions)
        return mix, gain, pitch, transitions

    def test_event_is_once_and_old_event_after_suspend_is_not_replayed(self):
        mix, gain, pitch, transitions = self.fixture()
        mix.update(.5, 1., event_sequence=0)
        mix.update(.5, .3, event_sequence=1, event_kind=1)
        mix.update(.5, .3, event_sequence=1, event_kind=1)
        transitions.play.assert_called_once_with(1)
        mix.after_frame()
        pitch.apply.assert_called_with(.3, after_frame=True)
        mix.update(.5, 1., event_sequence=2, event_kind=2)
        self.assertEqual(transitions.play.call_count, 2)
        mix.suspend()
        gain.set.assert_called_with(0.)
        pitch.apply.assert_called_with(1.)
        self.assertEqual(mix.after_frame(), 0)
        mix.update(.5, .3, event_sequence=4, event_kind=1)
        self.assertEqual(transitions.play.call_count, 2)

    def test_master_mute_never_plays_transition_and_restored_event_is_silent(self):
        mix, gain, pitch, transitions = self.fixture()
        mix.update(0, 1)
        mix.update(0, .3, event_sequence=1, event_kind=1)
        mix.update(1, 1, event_sequence=2, event_kind=3)
        transitions.play.assert_not_called()
        self.assertEqual(gain.set.call_args_list[1].args, (0.,))

    def test_every_control_is_validated_before_any_audio_mutation(self):
        mix, gain, pitch, transitions = self.fixture()
        gain.reset_mock();pitch.reset_mock()
        for kwargs in ({'sfx_volume': float('nan'), 'time_scale': 1},
                       {'sfx_volume': 1, 'time_scale': 0},
                       {'sfx_volume': 1, 'time_scale': 1, 'event_kind': 99},
                       {'sfx_volume': 1, 'time_scale': 1, 'event_sequence': True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                mix.update(**kwargs)
        gain.set.assert_not_called();pitch.apply.assert_not_called()

    def test_native_audio_failure_propagates_and_cleanup_preserves_it(self):
        mix, gain, pitch, transitions = self.fixture()
        pitch.apply.side_effect = RuntimeError('native ownership changed')
        with self.assertRaisesRegex(RuntimeError, 'native ownership'):
            mix.update(.5, .3)
        transitions.close.side_effect = OSError('cleanup error')
        self.assertEqual(mix.close(), ['native ownership changed', 'cleanup error'])
        gain.close.assert_called_once()
        self.assertEqual(mix.close(), [])
        with self.assertRaisesRegex(RuntimeError, 'closed'):
            mix.update(1, 1)

    def test_close_does_not_overwrite_external_session_gain_before_guarded_restore(self):
        mix, gain, _, _ = self.fixture()
        mix.update(.5, 1.)
        gain.reset_mock()
        mix.close()
        gain.set.assert_not_called()
        gain.close.assert_called_once()

    def test_session_is_primed_before_discovery_and_failure_releases_owned_buffers(self):
        trace = []
        frame = SimpleNamespace(preflight=Mock(), owners={probe_audio.DEVICE: 0x12340},
                                system=SimpleNamespace(call=Mock(return_value=345)))
        transitions = Mock()
        transitions.close.return_value = []
        transitions.ensure_session.side_effect = lambda: trace.append('prime')
        gain = Mock();gain.close.return_value = []
        def gain_factory(pid):
            trace.append(('discover', pid))
            return gain
        mix = AudioMix(frame, gain_factory=gain_factory, pitch_factory=lambda frame: Mock(),
                       transition_factory=lambda system, device: transitions)
        self.assertEqual(trace, ['prime', ('discover', 345)])
        mix.close()
        transitions.reset_mock()
        transitions.ensure_session.side_effect = RuntimeError('prime failed')
        factory = Mock()
        with self.assertRaisesRegex(RuntimeError, 'prime failed'):
            AudioMix(frame, gain_factory=factory, pitch_factory=lambda frame: Mock(),
                     transition_factory=lambda system, device: transitions)
        factory.assert_not_called()
        transitions.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
