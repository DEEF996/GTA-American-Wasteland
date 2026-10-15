"""Pure audio ownership/order and COM span tests; executes no original code."""
import struct
import unittest
from unittest.mock import patch

import probe_audio_frame as a
from test_audio_bank import Memory


class FrameFixture(Memory):
    def __init__(self):
        super().__init__()
        self.calls, self.fail, self.change_bank = [], False, False
        self.put(0x84376c, 0x100000, 0x200000)
        self.put(a.probe_audio.MANAGER, 0x101000)
        self.put(a.probe_audio.DEVICE, 0x201000)
        self.put(a.probe_audio.MUTEX, 24)
        self.put(a.probe_audio.REFERENCES, 1)
        self.put(a.probe_audio.SAMPLES, 113)
        self.put(a.probe_audio.VOICES, 48)
        self.put(0x84388c, a.ALLOCATOR)
        self.put(a.ALLOCATOR + 4, a.BOTTOM_UP)
        self.put(a.ALLOCATOR + 0x118, a.BOTTOM_UP)
        self.put(a.ALLOCATOR + 0x110, 0x102000)
        self.put(a.BOTTOM_UP, 0x6a2200)
        self.put(a.BOTTOM_UP + 0x24, 0x102000, 1)
        self.put(a.BOTTOM_UP + 0x34, 0x6a22b4)
        self.write(0x6a22b4, b'BottomUp\0')

    def call(self, module, name, args):
        assert module == 'kernel32.dll' and args == (a.CRITICAL,)
        self.calls.append(name)

    def native(self, name, args=(), this=None):
        self.calls.append(name)
        if name in ('Poll', 'Update'): assert this == 0x101000 and args == ()
        if name == 'Push':
            assert this == a.ALLOCATOR and args == (a.BOTTOM_UP,)
            self.put(a.ALLOCATOR + 0x10c, 1)
            self.put(a.ALLOCATOR + 0xc8, a.ALLOCATOR + 0xcc, a.BOTTOM_UP)
        if name == 'Update':
            if self.change_bank: self.put(0x6e0f90, 1)
            if self.fail: raise RuntimeError('simulated native refusal')
        if name == 'Pop':
            assert this == a.ALLOCATOR and args == ()
            self.put(a.ALLOCATOR + 0x10c, 0)
            self.put(a.ALLOCATOR + 0xc8, 0, 0xffffffff)

    def phase(self): return a.FramePhase(self, self.native, lambda *args, **kwargs: None)


class PCMFixture(Memory):
    def __init__(self):
        super().__init__()
        self.calls, self.spans, self.lock_result, self.read_fail = [], (0x10000, 3, 0x20000, 2), 0, False
        self.write(0x10000, b'abc'); self.write(0x20000, b'de')

    def allocate(self, contents): self.write(0x30000, contents); return 0x30000

    def com(self, label, buffer, slot, args):
        self.calls.append((label, slot, args))
        assert buffer == 0x50000
        if slot == 11:
            assert args == (0, 0, 0x30000, 0x30004, 0x30008, 0x3000c, 2)
            self.put(0x30000, *self.spans)
            return self.lock_result
        assert slot == 19 and args == self.spans
        return 0

    def read(self, address, size):
        if self.read_fail and address == 0x10000: raise RuntimeError('unreadable actual PCM')
        return super().read(address, size)


class StopFixture(FrameFixture):
    def __init__(self):
        super().__init__()
        self.put(a.probe_audio.VOICES, 2)
        self.put(a.PLATFORM_VOICES, 0x301000)
        self.put(a.PLATFORM_VOICES + 16, 1)
        self.put(a.PLATFORM_VOICES + 24, 0x302000)
        self.put(a.PLATFORM_VOICES + 40, 1)
        self.put(a.ACTIVE_VOICES, 2)
        self.statuses, self.keep_playing = {0x301000: 1, 0x302000: 5}, False

    def allocate(self, data):
        self.write(0x303000, data)
        return 0x303000

    def com(self, name, pointer, slot, args):
        assert name == 'StopAllSounds:GetStatus' and slot == 9 and args == (0x303000,)
        self.put(args[0], self.statuses[pointer])
        return 0

    def native(self, name, args=(), this=None):
        if name == 'StopAll':
            self.calls.append(name)
            assert args == (0, 0) and this is None
            if self.fail:
                raise RuntimeError('simulated original StopAll failure')
            if not self.keep_playing:
                self.statuses = {pointer: 0 for pointer in self.statuses}
            return 1
        if name == 'PlatformPoll':
            self.calls.append(name)
            assert args == () and this is None
            if not self.keep_playing:
                self.put(a.PLATFORM_VOICES + 16, 0)
                self.put(a.PLATFORM_VOICES + 40, 0)
                self.put(a.ACTIVE_VOICES, 0)
            return
        return super().native(name, args, this)


class AudioFrameTests(unittest.TestCase):
    def test_original_stop_all_stops_one_shot_and_loop_without_audio_update(self):
        f = StopFixture()
        result = f.phase().stop_all()
        self.assertEqual(f.calls, ['EnterCriticalSection', 'StopAll', 'PlatformPoll', 'LeaveCriticalSection'])
        self.assertEqual([row['status'] for row in result['voices_before']], [1, 5])
        self.assertEqual([row['status'] for row in result['voices_after']], [0, 0])
        self.assertEqual(result['native_gameplay_frames'], 0)
        self.assertEqual(result['audio_frame_updates'], 0)

    def test_stop_all_failure_or_remaining_playback_releases_lock_and_fails(self):
        for field in ('fail', 'keep_playing'):
            f = StopFixture()
            setattr(f, field, True)
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                f.phase().stop_all()
            self.assertEqual(f.calls[-1], 'LeaveCriticalSection')
            self.assertNotIn('Update', f.calls)

    def test_stop_all_refuses_open_clock_and_invalid_voice_before_native_stop(self):
        for address, value in ((0x8459c8, 1), (a.ACTIVE_VOICES, 1), (a.PLATFORM_VOICES, 0)):
            f = StopFixture()
            f.put(address, value)
            with self.subTest(address=address), self.assertRaises(RuntimeError):
                f.phase().stop_all()
            self.assertNotIn('StopAll', f.calls)
            if f.calls:
                self.assertEqual(f.calls[-1], 'LeaveCriticalSection')

    def test_explicit_actor_provenance_uses_shared_idle_guard_and_propagates_refusal(self):
        f = FrameFixture()
        context, sources = {'actor': 0x100500}, {'synthetic': (0x20000, 100)}
        with patch.object(a.probe_vm_idle, 'require_idle', return_value={'admissible': True}) as guard:
            phase = a.FramePhase(f, f.native, lambda *args, **kwargs: None, context=context, vm_sources=sources)
            f.put(0x6dcf84, 0x101500)
            phase.preflight()
            guard.assert_called_with(f, context, sources)
        with patch.object(a.probe_vm_idle, 'require_idle', side_effect=RuntimeError('unowned waiting context')):
            with self.assertRaisesRegex(RuntimeError, 'unowned waiting'):
                phase.update()
        self.assertEqual(f.calls, [])

    def test_original_phase_order_and_heap_restore(self):
        f = FrameFixture(); f.phase().update()
        self.assertEqual(f.calls, ['EnterCriticalSection', 'Poll', 'Push', 'Update', 'Pop', 'LeaveCriticalSection'])
        self.assertEqual(f.word(a.ALLOCATOR + 0xc8), 0)
        self.assertEqual(f.word(a.ALLOCATOR + 0x10c), 0)
        self.assertEqual(f.word(a.ALLOCATOR + 0xcc), 0xffffffff)

    def test_owner_busy_bank_or_heap_change_refuses_before_native_update(self):
        for address, value in ((a.probe_audio.MANAGER, 0x103000), (a.probe_audio.SAMPLES, 112),
                               (a.probe_audio.REFERENCES, 2), (0x6dcf84, 1), (0x6dd4a4, 1),
                               (a.ALLOCATOR + 0x118, 0), (a.ALLOCATOR + 0x10c, 15)):
            f = FrameFixture(); phase = f.phase(); f.put(address, value)
            with self.subTest(address=address), self.assertRaises(RuntimeError): phase.update()
            self.assertEqual(f.calls, [])

    def test_refusal_restores_heap_and_releases_critical_section(self):
        f = FrameFixture(); f.fail = True
        with self.assertRaisesRegex(RuntimeError, 'simulated native refusal'): f.phase().update()
        self.assertEqual(f.calls[-2:], ['Pop', 'LeaveCriticalSection'])
        self.assertEqual(f.word(a.ALLOCATOR + 0x10c), 0)

    def test_bank_mutation_detected_after_native_phase_cleanup(self):
        f = FrameFixture(); f.change_bank = True
        with self.assertRaisesRegex(RuntimeError, 'immutable sound'): f.phase().update()
        self.assertEqual(f.calls[-2:], ['Pop', 'LeaveCriticalSection'])

    def test_real_com_split_pcm_is_read_then_unlocked(self):
        f = PCMFixture()
        self.assertEqual(a.read_pcm(f, 0x50000, 5), b'abcde')
        self.assertEqual([row[1] for row in f.calls], [11, 19])

    def test_invalid_com_span_or_read_error_still_unlocks(self):
        for spans in ((0x10000, 3, 0, 2), (0x10000, 3, 0x20000, 99), (0xfffffffe, 3, 0x20000, 2)):
            f = PCMFixture(); f.spans = spans
            with self.subTest(spans=spans), self.assertRaises(RuntimeError): a.read_pcm(f, 0x50000, 5)
            self.assertEqual(f.calls[-1][1], 19)
        f = PCMFixture(); f.read_fail = True
        with self.assertRaisesRegex(RuntimeError, 'unreadable actual PCM'): a.read_pcm(f, 0x50000, 5)
        self.assertEqual(f.calls[-1][1], 19)
        f = PCMFixture(); f.lock_result = 0x80004005
        with self.assertRaisesRegex(RuntimeError, 'Cannot lock'): a.read_pcm(f, 0x50000, 5)
        self.assertEqual([row[1] for row in f.calls], [11])


if __name__ == '__main__': unittest.main()
