import copy
import math
from types import SimpleNamespace
import unittest

from source_adjustment import SourceAdjustment, adjustment, f32


def completed(frame, root, **changes):
    sample = dict(frame=frame, position=list(root), velocity=[0., 0., 0.],
                  mode=0, state=0, walk_state=0, walking_gate=0, bailing=False)
    sample.update(changes)
    clock = dict(native_frame_count=frame, native_vblank_count=frame,
                 native_timer_frames=frame, frame_open=False, native_seconds_per_frame=1/60,
                 native_time_scale=1., native_next_time_scale=1.,
                 native_gameplay_seconds_per_frame=f32(1/60))
    return dict(sample=sample, clock=clock)


class SourceAdjustmentTests(unittest.TestCase):
    def tracker(self, **kwargs):
        driver = SimpleNamespace(actor=0x100000, frame_count=100, started=True,
                                 poisoned=False, _stepping=False, last=None)
        return SourceAdjustment(driver, {'actor': driver.actor}, **kwargs)

    def submit(self, tracker, result):
        tracker.driver.frame_count = result['sample']['frame']
        tracker.driver.last = result
        return tracker.update(result)

    def test_first_completed_frame_never_relaxes_entry_or_uses_previous_session(self):
        tracker = self.tracker()
        tracker.driver.last = completed(100, [0., 0., 0.])
        self.assertFalse(self.submit(tracker, completed(101, [10000., 0., 0.])))
        self.assertTrue(self.submit(tracker, completed(102, [10060., 0., 0.])))
        renewed = SourceAdjustment(tracker.driver, tracker.context)
        self.assertFalse(self.submit(renewed, completed(103, [20000., 0., 0.])))

    def test_recorded_original_body_adjustments_keep_exact_completed_values(self):
        # Original airrail04 trace: onset Air->Ground, then three existing bails.
        pairs = (
            ([87992.796875, 998.136962890625, 50798.703125],
             [88032.5390625, 990.6658325195312, 50785.984375], False),
            ([87451.6484375, 901.511474609375, 49501.2109375],
             [87440.1484375, 973.9509887695312, 49464.05078125], True),
            ([87439.9765625, 901.4590454101562, 49500.76953125],
             [87443.515625, 956.6080322265625, 49435.51953125], True),
            ([87440.1015625, 901.4600830078125, 49501.18359375],
             [87437.4609375, 948.9403076171875, 49432.2890625], True),
        )
        for before, after, existing_bail in pairs:
            with self.subTest(position=after):
                tracker = self.tracker(offset=1.)
                first = completed(101, before, state=0 if existing_bail else 1, bailing=existing_bail)
                current = completed(102, after, bailing=True)
                original = copy.deepcopy((first, current))
                self.assertFalse(self.submit(tracker, first))
                self.assertTrue(self.submit(tracker, current))
                self.assertEqual((first, current), original)

    def test_bounds_do_not_clamp_or_change_ordinary_motion(self):
        for metres, expected in ((0., False), (.5, False), (1.01, False),
                                 (1.03, True), (3.01, True), (3.03, False), (100., False)):
            with self.subTest(metres=metres):
                tracker = self.tracker()
                self.submit(tracker, completed(101, [0., 0., 0.]))
                self.assertEqual(self.submit(tracker, completed(102, [metres/.0254, 0., 0.])), expected)

    def test_actual_wire_rounding_at_lower_and_upper_bound(self):
        # The mathematical 1.02m difference rounds above the one-step native
        # bound here; classifying pre-wire coordinates would miss this marker.
        origin = (2235.01709, -1290.28711, 26.35265)
        tracker = self.tracker(origin=origin, offset=.1234)
        self.submit(tracker, completed(101, [0., 0., 0.]))
        old = tracker.previous
        result = completed(102, [1.02/.0254, 0., 0.])
        self.assertTrue(self.submit(tracker, result))
        self.assertEqual(tracker.previous[0], f32(origin[0]+1.02))
        self.assertEqual(tracker.previous[2], f32(origin[2]+.1234))
        self.assertEqual(adjustment(old, tracker.previous, 16666), True)
        for micros in (16666, 16667):
            self.assertFalse(adjustment((0.,)*3, (f32(3.0202), 0., 0.), micros))
            self.assertTrue(adjustment((0.,)*3, (f32(3.0198), 0., 0.), micros))

    def test_relative_source_time_not_absolute_native_frame_phase(self):
        tracker = self.tracker()
        self.submit(tracker, completed(101, [0., 0., 0.]))
        # Relative tick2 has16666us; native frame102 would have16667us.
        self.assertTrue(self.submit(tracker, completed(102, [1.01998/.0254, 0., 0.])))
        self.assertFalse(self.submit(tracker, completed(103, [2*1.01998/.0254, 0., 0.])))

    def test_only_the_exact_completed_result_from_the_owned_idle_driver(self):
        for change in ('copy', 'actor', 'context', 'busy', 'poisoned', 'stopped', 'frame'):
            with self.subTest(change=change):
                tracker = self.tracker()
                result = completed(101, [0., 0., 0.])
                tracker.driver.last = result
                tracker.driver.frame_count = 101
                if change == 'copy': result = copy.deepcopy(result)
                elif change == 'actor': tracker.driver.actor += 4
                elif change == 'context': tracker.context['actor'] += 4
                elif change == 'busy': tracker.driver._stepping = True
                elif change == 'poisoned': tracker.driver.poisoned = True
                elif change == 'stopped': tracker.driver.started = False
                else: tracker.driver.frame_count += 1
                with self.assertRaises(ValueError): tracker.update(result)
                self.assertEqual(tracker.count, 0)

    def test_duplicate_pending_or_skipped_original_tick_never_marks(self):
        tracker = self.tracker()
        first = completed(101, [0., 0., 0.])
        self.submit(tracker, first)
        with self.assertRaises(ValueError): tracker.update(first)
        with self.assertRaises(ValueError): self.submit(tracker, completed(103, [60., 0., 0.]))
        self.assertEqual(tracker.count, 1)

    def test_open_divergent_or_retimed_clock_is_fatal(self):
        for key, value in (('native_frame_count', 100), ('native_vblank_count', 100),
                           ('native_timer_frames', 100), ('frame_open', True),
                           ('native_seconds_per_frame', 1/30), ('native_seconds_per_frame', math.nan),
                           ('native_time_scale', math.nan), ('native_next_time_scale', -1.),
                           ('native_gameplay_seconds_per_frame', 1/30)):
            with self.subTest(key=key, value=value):
                tracker = self.tracker()
                result = completed(101, [0., 0., 0.])
                result['clock'][key] = value
                with self.assertRaises(ValueError): self.submit(tracker, result)

    def test_existing_original_focus_is_valid_but_gets_no_added_allowance(self):
        tracker = self.tracker()
        self.submit(tracker, completed(101, [0., 0., 0.]))
        result = completed(102, [60., 0., 0.])
        result['clock'].update(native_time_scale=.5, native_gameplay_seconds_per_frame=.5*f32(1/60))
        self.assertFalse(self.submit(tracker, result))

    def test_malformed_motion_or_nonfinite_overspeed_source_is_fatal(self):
        changes = (dict(position=[math.nan, 0., 0.]), dict(position=[math.inf, 0., 0.]),
                   dict(position=[1.e9, 0., 0.]), dict(position=[0., 0.]),
                   dict(velocity=[60.001/.0254, 0., 0.]), dict(velocity=[math.nan, 0., 0.]),
                   dict(mode=2), dict(state=8), dict(walk_state=8), dict(walking_gate=1), dict(bailing=1))
        for change in changes:
            with self.subTest(change=change):
                tracker = self.tracker()
                with self.assertRaises(ValueError): self.submit(tracker, completed(101, [0., 0., 0.], **change))
        tracker = self.tracker()
        self.assertFalse(self.submit(tracker, completed(101, [0., 0., 0.], velocity=[60/.0254, 0., 0.])))

    def test_supported_contexts_do_not_imply_a_specific_collision_cause(self):
        for mode in (0, 1):
            for state in range(8):
                tracker = self.tracker()
                self.submit(tracker, completed(101, [0., 0., 0.], mode=mode, state=state, walk_state=state))
                self.assertTrue(self.submit(tracker, completed(102, [60., 0., 0.],
                    mode=mode, state=state, walk_state=state)))

    def test_retains_immutable_previous_position_without_native_queries(self):
        tracker = self.tracker()
        first = completed(101, [0., 0., 0.])
        self.submit(tracker, first)
        first['sample']['position'][0] = 60.
        self.assertTrue(self.submit(tracker, completed(102, [60., 0., 0.])))


if __name__ == '__main__':
    unittest.main()
