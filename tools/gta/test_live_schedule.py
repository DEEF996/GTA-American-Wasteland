import unittest

from live_session import continuous_host_schedule


class LiveScheduleTest(unittest.TestCase):
    def test_hour_of_sustained_overload_has_at_most_one_tick_of_backlog(self):
        period = 1/60
        scheduled, now = -period, 0.0
        for _ in range(60 * 60 * 60):
            previous = scheduled
            scheduled = continuous_host_schedule(previous, now)
            self.assertGreaterEqual(scheduled, now-period-1e-9)
            self.assertGreaterEqual(scheduled, previous + period)
            self.assertLessEqual(scheduled - now, period + 1e-9)
            # A fixed original source step plus host overhead exceeds its ideal slot.
            now = max(scheduled, now) + period + .001

    def test_small_intermittent_overruns_do_not_slow_a_minute_of_simulation(self):
        period = 1/60
        scheduled, now = -period, 0.
        for frame in range(3600):
            scheduled = continuous_host_schedule(scheduled, now)
            # Occasional work beyond one tick, with realistic timer wake jitter.
            if scheduled > now:
                now = scheduled + .0002
            now += .018 if frame % 10 == 0 else .013
        self.assertAlmostEqual(scheduled, 3599/60, places=8)
        self.assertLess(now, 60.001)

    def test_long_wall_stall_rebases_once_without_a_catchup_burst(self):
        now = 1234.5
        scheduled = continuous_host_schedule(1.0, now)
        self.assertEqual(scheduled, now)
        for _ in range(60):
            following = continuous_host_schedule(scheduled, scheduled + .002)
            self.assertAlmostEqual(following - scheduled, 1/60)
            scheduled = following

    def test_fast_work_preserves_one_source_step_per_host_interval(self):
        scheduled = continuous_host_schedule(-1/60, 0.0)
        self.assertEqual(scheduled, 0.0)
        for frame in range(1, 61):
            scheduled = continuous_host_schedule(scheduled, scheduled + .005)
            self.assertAlmostEqual(scheduled, frame / 60)


if __name__ == '__main__':
    unittest.main()
