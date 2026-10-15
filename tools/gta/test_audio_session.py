from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from audio_session import SessionGain


class Volume:
    def __init__(self, value=.7, muted=False):
        self.current, self.calls, self.closed = (value, muted), [], False
        self.error = None

    def get(self):
        return self.current

    def set(self, value, muted):
        if self.error:
            raise self.error
        self.current = (value, muted)
        self.calls.append(self.current)

    def close(self):
        self.closed = True


class SessionGainTests(unittest.TestCase):
    def gain(self, *volumes):
        seen = []
        backend = SimpleNamespace(sessions=lambda pid: seen.append(pid) or list(volumes),
                                  close=lambda: seen.append('closed'))
        return SessionGain(123, backend=backend), seen

    def test_master_zero_mutes_all_owned_sessions_and_stable_values_are_free(self):
        a, b = Volume(), Volume(.3)
        gain, seen = self.gain(a, b)
        gain.set(0)
        for _ in range(120):
            gain.set(0.)
        self.assertEqual(seen, [123])
        self.assertEqual(a.calls, [(0., True)])
        self.assertEqual(b.calls, [(0., True)])
        gain.set(.5)
        self.assertEqual(a.current, (.5, False))
        self.assertEqual(gain.close(), [])
        self.assertEqual(a.current, (.7, False))
        self.assertEqual(b.current, (.3, False))

    def test_invalid_controls_never_touch_audio(self):
        volume = Volume()
        gain, _ = self.gain(volume)
        for value in (True, -.1, 1.1, float('nan'), float('inf'), '0'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                gain.set(value)
        self.assertFalse(volume.calls)

    def test_user_changed_session_is_not_overwritten_during_close(self):
        volume = Volume()
        gain, _ = self.gain(volume)
        gain.set(.4)
        volume.current = (.2, True)
        gain.close()
        self.assertEqual(volume.current, (.2, True))
        self.assertTrue(volume.closed)

    def test_session_failure_is_reported_and_does_not_mark_gain_applied(self):
        a, b = Volume(), Volume()
        gain, _ = self.gain(a, b)
        b.error = OSError('device removed')
        with self.assertRaisesRegex(OSError, 'removed'):
            gain.set(0)
        self.assertIsNone(gain.applied)
        self.assertEqual(a.current, (0., True))
        b.error = None
        gain.close()
        self.assertEqual(a.current, (.7, False))

    def test_missing_session_is_not_silently_treated_as_muted(self):
        closed = []
        backend = SimpleNamespace(sessions=lambda pid: [], close=lambda: closed.append(True))
        with patch('audio_session.time.monotonic', side_effect=[0, .25]), \
             self.assertRaisesRegex(OSError, 'no CoreAudio session'):
            SessionGain(123, backend=backend)
        self.assertEqual(closed, [True])

    def test_lazy_owned_session_retries_absence_but_never_com_failure(self):
        volume = Volume()
        backend = SimpleNamespace(sessions=Mock(side_effect=[[], [], [volume]]), close=Mock())
        with patch('audio_session.time.monotonic', side_effect=[0, .001, .011, .021]), \
             patch('audio_session.time.sleep') as sleep:
            gain = SessionGain(123, backend=backend)
        self.assertEqual([call.args for call in backend.sessions.call_args_list], [(123,)] * 3)
        self.assertEqual([call.args for call in sleep.call_args_list], [(.01,)] * 2)
        gain.close()
        backend.sessions.side_effect = OSError('COM device removed')
        with patch('audio_session.time.sleep') as sleep, self.assertRaisesRegex(OSError, 'COM device'):
            SessionGain(123, backend=backend)
        sleep.assert_not_called()

    def test_absent_session_wait_is_bounded_to_quarter_second(self):
        now = [0.]
        backend = SimpleNamespace(sessions=Mock(return_value=[]), close=Mock())
        def sleep(seconds):
            now[0] += seconds
        with patch('audio_session.time.monotonic', side_effect=lambda: now[0]), \
             patch('audio_session.time.sleep', side_effect=sleep), \
             self.assertRaisesRegex(OSError, 'no CoreAudio session'):
            SessionGain(123, backend=backend)
        self.assertLessEqual(now[0], .25)
        self.assertLessEqual(backend.sessions.call_count, 26)
        backend.close.assert_called_once()

    def test_initial_read_failure_releases_every_returned_interface(self):
        a, b = Volume(), Volume()
        a.get = lambda: (_ for _ in ()).throw(OSError('original read error'))
        with self.assertRaisesRegex(OSError, 'original read error'):
            self.gain(a, b)
        self.assertTrue(a.closed and b.closed)


if __name__ == '__main__':
    unittest.main()
