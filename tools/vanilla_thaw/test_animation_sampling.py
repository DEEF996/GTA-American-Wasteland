"""Native evaluator scheduling only; no PE, private inputs or process calls."""
import math
import unittest
from probe_animation import sample_times


class SamplingTests(unittest.TestCase):
    def test_legacy_four_samples_are_unchanged(self):
        self.assertEqual(sample_times(.6333333253860474), ([0., .3, .6333333253860474, .3], 3))

    def test_native_grid_has_exact_end_and_separate_determinism_samples(self):
        times, count = sample_times(.6333333253860474, 120)
        self.assertEqual(count, 77)
        self.assertEqual(times[:76], [i/120 for i in range(76)])
        self.assertEqual(times[76:], [.6333333253860474, .3, .3])
        self.assertTrue(all(a < b for a, b in zip(times[:count], times[1:count])))

    def test_unsupported_or_unbounded_grid_is_rejected(self):
        for duration, rate in ((math.nan, 120), (2, 120), (.633, 0), (.633, 60)):
            with self.subTest(duration=duration, rate=rate), self.assertRaises(RuntimeError):
                sample_times(duration, rate)


if __name__ == "__main__":
    unittest.main()
