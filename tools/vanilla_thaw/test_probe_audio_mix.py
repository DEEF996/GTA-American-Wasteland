import struct
from types import SimpleNamespace
import unittest

import probe_audio
import probe_audio_mix as mix


def row(buffer=0x10000, base=22050, desired=33075, active=1, sample=0x20000):
    return struct.pack('<6I', buffer, 0, base, desired, active, sample)


class PitchTests(unittest.TestCase):
    def test_shadow_preserves_authored_pitch_and_reused_voice_uses_new_rate(self):
        first = row()
        self.assertEqual(mix.voice_rates(first, 1, .3, {0x20000}), [(0x10000, 9922)])
        self.assertEqual(mix.voice_rates(first, 1, .3, {0x20000}), [(0x10000, 9922)])
        self.assertEqual(mix.voice_rates(row(desired=11025), 1, .3, {0x20000}), [(0x10000, 3308)])
        self.assertEqual(mix.voice_rates(first, 1, 1, {0x20000}), [(0x10000, 33075)])
        self.assertEqual(first, row())

    def test_complete_snapshot_validation_rejects_bad_second_voice(self):
        for second in (row(buffer=0x10000), row(buffer=3), row(buffer=0x11000, desired=0),
                       row(buffer=0x11000, sample=0xdeadbeef), row(active=2)):
            with self.subTest(second=second), self.assertRaises(RuntimeError):
                mix.voice_rates(row() + second, 2, .3, {0x20000})
        self.assertEqual(mix.voice_rates(row(active=0, desired=0), 1, .3, set()), [])
        self.assertEqual(mix.voice_rates(row(base=100, desired=100), 1, .05, {0x20000}), [(0x10000, 100)])

    def control(self):
        operations = []
        state = {'raw': row()}
        words = {probe_audio.MANAGER: 0x40000, probe_audio.DEVICE: 0x50000,
                 probe_audio.MUTEX: 123, probe_audio.SAMPLES: 113,
                 probe_audio.VOICES: 1, mix.ACTIVE_VOICES: 1}
        system = SimpleNamespace(word=lambda at: words[at],
            read=lambda at, size: state['raw'],
            call=lambda module, name, args: operations.append(name),
            com=lambda name, pointer, slot, args: operations.append((pointer, slot, args)) or 0)
        control = mix.PitchControl.__new__(mix.PitchControl)
        control.frame = SimpleNamespace(sample_count=113,
            owners={key: words[key] for key in (probe_audio.MANAGER, probe_audio.DEVICE, probe_audio.MUTEX)})
        control.system, control.scale, control.descriptors = system, 1., {0x20000}
        return control, operations, state, words

    def test_normal_play_has_zero_remote_work_and_stock_retune_is_reapplied(self):
        control, operations, state, _ = self.control()
        for _ in range(120):
            self.assertEqual(control.apply(1., after_frame=True), 0)
        self.assertFalse(operations)
        control.apply(.3)
        count = len(operations)
        control.apply(.3)
        self.assertEqual(len(operations), count)
        # A new original frame can write the same desired rate into the COM
        # buffer; it still needs reapplication even when metadata is unchanged.
        control.apply(.3, after_frame=True)
        self.assertEqual(len([x for x in operations if isinstance(x, tuple)]), 2)
        state['raw'] = row(desired=11025)
        control.apply(.3, after_frame=True)
        self.assertIn((0x10000, 17, (3308,)), operations)
        control.apply(1.)
        self.assertIn((0x10000, 17, (11025,)), operations)

    def test_owner_failure_unlocks_and_never_calls_buffer(self):
        control, operations, state, words = self.control()
        words[probe_audio.DEVICE] += 4
        with self.assertRaisesRegex(RuntimeError, 'ownership'):
            control.apply(.3)
        self.assertEqual(operations, ['EnterCriticalSection', 'LeaveCriticalSection'])


class TransitionTests(unittest.TestCase):
    def test_session_priming_is_muted_own_buffer_and_stopped_before_restore(self):
        operations = []
        transitions = mix.Transitions.__new__(mix.Transitions)
        transitions.buffers = {1: 0x41000, 2: 0x42000}
        transitions.system = SimpleNamespace(com=lambda label, buffer, slot, args=():
            operations.append((buffer, slot, args)) or 0)
        transitions.ensure_session()
        self.assertEqual(operations, [(0x41000, 15, (0xffffd8f0,)),
            (0x41000, 12, (0, 0, 0)), (0x41000, 18, ()), (0x41000, 15, (0,))])

    def test_failed_priming_never_unmutes_running_buffer_and_preserves_first_error(self):
        for failed_slot in (12, 18):
            with self.subTest(failed_slot=failed_slot):
                operations = []
                transitions = mix.Transitions.__new__(mix.Transitions)
                transitions.buffers = {1: 0x41000}
                def com(label, buffer, slot, args=()):
                    operations.append((slot, args))
                    if slot == failed_slot:
                        raise OSError('original prime failure')
                    if slot == 18:
                        raise RuntimeError('cleanup failed too')
                    return 0
                transitions.system = SimpleNamespace(com=com)
                with self.assertRaisesRegex(OSError, 'original prime failure'):
                    transitions.ensure_session()
                self.assertIn((18, ()), operations)
                self.assertNotIn((15, (0,)), operations)

    def test_failed_pcm_write_preserves_original_error_when_unlock_also_fails(self):
        operations = []
        transitions = mix.Transitions.__new__(mix.Transitions)
        transitions.scratch = 0x30000
        transitions.device = 0x40000
        def write(at, data):
            if at == 0x50000:
                raise OSError('PCM write failed')
        def com(label, buffer, slot, args=()):
            operations.append(slot)
            if slot == 19:
                raise RuntimeError('unlock failed too')
            return 0
        transitions.system = SimpleNamespace(com=com, write=write,
            word=lambda at: 0x41000, read=lambda at, size: struct.pack('<4I', 0x50000, 2, 0, 0))
        with self.assertRaisesRegex(OSError, 'PCM write failed'):
            transitions._create(b'\0\0')
        self.assertEqual(operations, [3, 11, 19, 2])

    def test_synthesis_is_bounded_silent_at_edges_and_has_distinct_directions(self):
        a, b = mix.transition_pcm(True), mix.transition_pcm(False)
        self.assertEqual(len(a), round(22050 * .22) * 2)
        self.assertEqual(a, mix.transition_pcm(True))
        self.assertNotEqual(a, b)
        for pcm in (a, b):
            samples = struct.unpack(f'<{len(pcm)//2}h', pcm)
            self.assertEqual((samples[0], samples[-1]), (0, 0))
            self.assertLess(max(map(abs, samples)), 10000)
            self.assertGreater(max(map(abs, samples)), 1000)
            self.assertLess(abs(sum(samples) / len(samples)), 100)


if __name__ == '__main__':
    unittest.main()
