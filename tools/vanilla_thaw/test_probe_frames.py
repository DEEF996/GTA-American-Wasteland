import struct
import unittest

import probe_frames
from probe_frame_input import packet


class FrameSampleTests(unittest.TestCase):
    def test_motion_context_uses_separate_bail_byte_from_the_same_snapshot(self):
        state, walking = [0] * (0x134 // 4), [0] * 7
        state[6], state[7], walking[6] = 4, 1, 3
        state[0x78 // 4] = 0xaabbcc01
        self.assertEqual(probe_frames.motion_state(state, walking),
                         dict(state=4, mode=1, walk_state=3, walking_gate=0, bailing=True))
        state[0x78 // 4], state[0x130 // 4] = 0xff000000, 1
        result = probe_frames.motion_state(state, walking)
        self.assertFalse(result['bailing'])
        self.assertEqual(result['walking_gate'], 1)
        for index, invalid in ((6, 8), (7, 2), (0x78 // 4, 2)):
            changed = list(state); changed[index] = invalid
            with self.subTest(index=index), self.assertRaises(RuntimeError):
                probe_frames.motion_state(changed, walking)
        with self.assertRaises(RuntimeError): probe_frames.motion_state(state[:-1], walking)
        walking[6] = 8
        with self.assertRaises(RuntimeError): probe_frames.motion_state(state, walking)

    def test_original_board_attack_bool_rejects_corrupt_flag(self):
        self.assertFalse(probe_frames.board_attack_flag(bytes(32)))
        self.assertTrue(probe_frames.board_attack_flag(bytes(31)+b'\x01'))
        for raw in (bytes(31), bytes(33), bytes(31)+b'\x02'):
            with self.assertRaisesRegex(RuntimeError, 'board-smack component flag'):
                probe_frames.board_attack_flag(raw)

    def test_metadata_calls_and_provenance_are_distinct(self):
        value = probe_frames.read_metadata()
        functions = {row[0]: row[1] for row in value["functions"]}
        self.assertEqual(functions["ComponentManagerUpdate"], 0x430e20)
        self.assertEqual(functions["SpawnedScriptsUpdate"], 0x4137b0)
        self.assertNotEqual(functions["DeferredOwnerCleanup"], functions["DeferredListDrain"])
        self.assertEqual(functions["PlayersRegister"], 0x4a03c0)
        self.assertEqual(functions["GameRegisterPlayerCaller"], 0x56fa6b)

    def test_sample_rejects_truncated_and_nonfinite_vectors(self):
        values = tuple(float(index - 8) for index in range(16))
        self.assertEqual(probe_frames.finite_vectors(struct.pack("<16f", *values)), values)
        for raw in (bytes(60), bytes(68), struct.pack("<16f", float("nan"), *values[1:]),
                    struct.pack("<16f", *values[:-1], float("inf"))):
            with self.assertRaises(RuntimeError):
                probe_frames.finite_vectors(raw)

    def driver(self, fail=False, max_frames=600):
        calls = []
        initial = {'frame': 0}
        counters = dict(original_cleanup_calls=0, original_owner_updates=0, original_sound_updates=0,
                        original_spawned_updates=0, frames_with_deferred_owners=0, neutral_input_only=True)

        def advance(frame, submitted):
            calls.append((frame, submitted))
            if fail:
                raise RuntimeError('native phase refused')
            for key in ('original_cleanup_calls', 'original_owner_updates', 'original_sound_updates', 'original_spawned_updates'):
                counters[key] += 1
            return dict(sample={'frame': frame}, palette=bytes(52 * 64), actor_matrix=tuple(range(16)),
                        clock={'native_frame_count': frame, 'frame_open': False}, input={'neutral': submitted is None})

        return probe_frames.FrameDriver(0x10000, initial, advance, counters, max_frames=max_frames), calls

    def test_driver_longer_limit_is_explicit_and_checked_before_initialization(self):
        driver, calls = self.driver(max_frames=700)
        driver.frame_count = 600
        self.assertEqual(driver.step()['sample']['frame'], 601)
        self.assertEqual(calls, [(601, None)])
        self.assertEqual(driver.summary()['max_frames'], 700)
        for limit in (0, 216001, 600., True):
            with self.subTest(limit=limit), self.assertRaises(RuntimeError):
                probe_frames.initialize(None, None, None, None, None, None, None, None, None, max_frames=limit)

    def test_driver_steps_without_reinitializing_and_returns_complete_result(self):
        driver, calls = self.driver()
        first = driver.step()
        requested = packet(digital_mask=0x40)
        second = driver.step(requested)
        self.assertEqual(calls, [(1, None), (2, requested)])
        self.assertEqual(len(first['palette']), 52 * 64)
        self.assertEqual(second['actor_matrix'], tuple(range(16)))
        summary = driver.summary()
        self.assertTrue(summary['started'])
        self.assertFalse(summary['poisoned'])
        self.assertEqual(summary['frame_count'], 2)
        self.assertEqual(summary['initial'], {'frame': 0})
        self.assertEqual(summary['final'], {'frame': 2})
        self.assertEqual(summary['original_sound_updates'], 2)

    def test_failed_step_poisoned_and_never_reenters_native_graph(self):
        driver, calls = self.driver(fail=True)
        with self.assertRaisesRegex(RuntimeError, 'native phase refused'):
            driver.step()
        with self.assertRaisesRegex(RuntimeError, 'poisoned'):
            driver.step()
        self.assertEqual(calls, [(1, None)])
        self.assertEqual(driver.frame_count, 0)
        self.assertTrue(driver.summary()['poisoned'])
        self.assertFalse(driver.summary()['native_heap_stack_restored'])

    def test_invalid_input_refuses_before_opening_a_native_frame(self):
        driver, calls = self.driver()
        with self.assertRaises(RuntimeError): driver.step(bytes(71))
        self.assertEqual(calls, [])
        self.assertTrue(driver.poisoned)

    def test_driver_rejects_incomplete_result_reentrancy_and_clock_overrun(self):
        driver, calls = self.driver()
        original = driver._advance
        def incomplete(frame, submitted):
            result = original(frame, submitted)
            result['clock']['frame_open'] = True
            return result
        driver._advance = incomplete
        with self.assertRaisesRegex(RuntimeError, 'complete'): driver.step()
        self.assertTrue(driver.poisoned)
        self.assertEqual(driver.frame_count, 0)

        driver, calls = self.driver()
        driver._advance = lambda *_: driver.step()
        with self.assertRaisesRegex(RuntimeError, 'busy'): driver.step()
        self.assertTrue(driver.poisoned)

        driver, calls = self.driver()
        driver.frame_count = 600
        with self.assertRaisesRegex(RuntimeError, 'clock limit'): driver.step()
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
