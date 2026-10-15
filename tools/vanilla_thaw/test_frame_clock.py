import unittest
import struct
from unittest.mock import patch

from probe_frame_clock import FrameClock, expected_milliseconds, milliseconds_stub, validate_max_frames, validate_snapshot


class OriginalClockChecks(unittest.TestCase):
    def test_bail_resource_precision_change_preserves_original_3999_result(self):
        for control_word, expected in ((0x007f, 3999), (0x027f, 4000), (0x037f, 3999)):
            with self.subTest(control_word=control_word):
                result = validate_snapshot(240, 240, expected, 1/60, False,
                                           timer_frames=240, control_word=control_word)
                self.assertEqual(result['native_x87_control_word'], control_word)
                self.assertEqual(result['native_timer_frames'], 240)
                for wrong in (expected-1, expected+1):
                    with self.assertRaises(RuntimeError):
                        validate_snapshot(240, 240, wrong, 1/60, False, control_word=control_word)
        with self.assertRaisesRegex(RuntimeError, 'counter diverged'):
            validate_snapshot(240, 240, 3999, 1/60, False, timer_frames=239, control_word=0x037f)

    def test_long_single_precision_error_is_exact_not_a_millisecond_tolerance(self):
        # Eight hours: actual float32 division/multiply gives 28,799,998ms.
        f32 = lambda value: struct.unpack('<f', struct.pack('<f', value))[0]
        self.assertEqual(int(f32(f32(1000/60) * 1728000)), 28799998)
        for frames in (216000, 1728000, 43200000):
            expected = int(f32(f32(1000/60) * frames))
            self.assertEqual(expected_milliseconds(frames, 0x007f), expected)
            self.assertEqual(validate_snapshot(frames, frames, expected, 1/60, False, None,
                control_word=0x007f)['native_milliseconds'], expected)
        with self.assertRaises(RuntimeError):
            validate_snapshot(1728000, 1728000, 28800000, 1/60, False, None, control_word=0x007f)

    def test_rounding_modes_unsigned_timer_and_eax_wrap(self):
        # Extended precision under round-up crosses exact seconds, down/zero do not.
        for control_word, expected in ((0x037f, 3999), (0x077f, 3999),
                                       (0x0b7f, 4000), (0x0f7f, 3999)):
            self.assertEqual(expected_milliseconds(240, control_word), expected)
        self.assertEqual(expected_milliseconds(0x80000001, 0x027f), 1431655782)
        self.assertEqual(expected_milliseconds(0x80000001, 0x007f), 1431654400)
        self.assertEqual(expected_milliseconds(0xffffffff, 0x027f), 2863311514)
        self.assertEqual(expected_milliseconds(0x100000001, 0x027f), 16)
        validate_snapshot(0x100000001, 0x100000001, 16, 1/60, False, None, control_word=0x027f)
        for timer, control_word in ((-1, 0x027f), (2**64, 0x027f), (True, 0x027f),
                                     (240, 0x017f), (240, -1), (240, 0x10000), (240, True)):
            with self.subTest(timer=timer, control_word=control_word), self.assertRaises(RuntimeError):
                expected_milliseconds(timer, control_word)

    def test_owned_glue_reads_control_word_around_original_getter_without_writing_it(self):
        record = 0x20000000
        code = milliseconds_stub(record)
        self.assertIn(b'\xd9\x3d'+struct.pack('<I', record+72), code)
        self.assertIn(b'\xd9\x3d'+struct.pack('<I', record+74), code)
        self.assertEqual(code.count(b'\xd9\x3d'), 2)
        self.assertNotIn(b'\xd9\x2d', code)  # FLDCW would modify the game's precision.
        self.assertIn(b'\xb8'+struct.pack('<I', 0x62c4c0)+b'\xff\xd0', code)
        for address in (0x8459c0, 0x8459c4):
            self.assertIn(b'\xa1'+struct.pack('<I', address), code)
        for invalid in (0, record+1, 0x100000000):
            with self.assertRaises(RuntimeError): milliseconds_stub(invalid)

    def test_explicit_longer_limit_keeps_native_rate_and_one_hour_bound(self):
        self.assertEqual(validate_snapshot(601, 601, 10016, 1 / 60, False, 700)['native_frame_count'], 601)
        self.assertEqual(validate_snapshot(216000, 216000, 3600000, 1 / 60, False, 216000)['native_milliseconds'], 3600000)
        for limit in (0, -1, 216001, 600., True):
            with self.subTest(limit=limit), self.assertRaises(RuntimeError): validate_max_frames(limit)
        with self.assertRaises(RuntimeError): validate_snapshot(701, 701, 11683, 1 / 60, False, 700)
        with self.assertRaises(RuntimeError): validate_snapshot(601, 601, 10017, 1 / 60, False, 700)

    def test_continuous_clock_has_no_probe_limit_but_keeps_rate_and_tick_checks(self):
        self.assertIsNone(validate_max_frames(None))
        self.assertEqual(validate_snapshot(432000,432000,7200000,1/60,False,None)['native_frame_count'],432000)
        with self.assertRaises(RuntimeError): validate_snapshot(432000,431999,7200000,1/60,False,None)
        with self.assertRaises(RuntimeError): validate_snapshot(432000,432000,7200001,1/60,False,None)
        clock = FrameClock.__new__(FrameClock)
        clock.open, clock.max_frames = False, None
        with patch.object(clock, 'value', return_value=432000), patch.object(clock, 'call') as call:
            clock.begin_frame()
            self.assertEqual([row.args[0] for row in call.call_args_list], ['vblank','begin_frame'])

    def test_clock_refuses_extra_tick_before_original_calls(self):
        clock = FrameClock.__new__(FrameClock)
        clock.open, clock.max_frames = False, 700
        with patch.object(clock, 'value', return_value=700), patch.object(clock, 'call') as call:
            with self.assertRaisesRegex(RuntimeError, 'frame limit'): clock.begin_frame()
            call.assert_not_called()
        with patch.object(clock, 'value', return_value=699), patch.object(clock, 'call') as call:
            clock.begin_frame()
            self.assertEqual([row.args[0] for row in call.call_args_list], ['vblank', 'begin_frame'])
            with self.assertRaisesRegex(RuntimeError, 'already open'): clock.begin_frame()

    def test_native_sixty_tick_second_and_bounded_extent(self):
        self.assertEqual(validate_snapshot(60, 60, 1000, 1 / 60, False)["native_milliseconds"], 1000)
        for args in ((601, 601, 10016, 1 / 60, False), (60, 59, 1000, 1 / 60, False),
                     (60, 60, 999, 1 / 60, False), (60, 60, 1000, 1 / 120, False),
                     (60, 60, 1000, float("nan"), False)):
            with self.subTest(args=args), self.assertRaises(RuntimeError):
                validate_snapshot(*args)


if __name__ == "__main__":
    unittest.main()
