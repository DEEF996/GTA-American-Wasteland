import unittest
from types import SimpleNamespace
from unittest.mock import patch
from game_effects import BODY, decode, matches, SourcePacer, EffectsInput


def packet(**changes):
    value = dict(magic=b'GTAFX001', version=1, size=104, pid=7, flags=3,
                 created=1234, sequence=1, tick=1000, request=44, ped=0x12300, epoch=8,
                 area=0, ped_area=0, entry_toggle=3, toggle=3, event_sequence=1,
                 scale=.3, effect=1., volume=.5, event_kind=1)
    value.update(changes)
    return BODY.pack(*value.values())


class EffectsTests(unittest.TestCase):
    def test_optional_effect_reader_has_a_small_wait_budget_and_busy_does_not_abort_gameplay(self):
        reader = EffectsInput.__new__(EffectsInput)
        reader.view = 123
        reader.observer = SimpleNamespace(pid=7, created=1234, process=45)
        reader.api = SimpleNamespace(GetTickCount64=lambda: 1000)
        state = dict(decode(packet(), 7, 1234, 1000), flags=8|64, control_eligible=True)
        with patch('game_effects.time.monotonic', return_value=42), patch('game_effects.read_mapping_body',
                return_value=packet()) as read:
            self.assertAlmostEqual(reader.current(state, 44)['time_scale'], .3)
            self.assertEqual(read.call_args.kwargs['deadline'], 42.001)
            read.side_effect = ValueError('No complete game effects available')
            self.assertIsNone(reader.current(state, 44))

    def test_exact_protocol_and_slider(self):
        effect = decode(packet(volume=0.), 7, 1234, 1030)
        self.assertEqual(effect['sfx_volume'], 0)
        self.assertAlmostEqual(effect['time_scale'], .3)
        self.assertEqual(effect['age_ms'], 30)
        state = dict(effect, control_eligible=True, flags=8|64, age_ms=0)
        self.assertTrue(matches(effect, state, 44))
        for key,value in [('ped_address', 99), ('epoch', 9), ('f7_requests', 4),
                          ('flags', 8|64|256), ('control_eligible', False), ('age_ms', 101)]:
            self.assertFalse(matches(effect, dict(state, **{key:value}), 44), key)
        self.assertFalse(matches(effect, state, 45))

    def test_invalid_transport_and_controls(self):
        for changes in (dict(magic=b'FOREIGN!'), dict(size=103), dict(pid=8), dict(created=1235),
                        dict(sequence=0), dict(tick=899), dict(tick=1001), dict(flags=8),
                        dict(flags=1), dict(flags=0), dict(flags=5), dict(request=0), dict(ped=0),
                        dict(epoch=0), dict(area=1), dict(toggle=4), dict(scale=float('nan')),
                        dict(scale=.1), dict(effect=.4), dict(volume=1.1), dict(volume=-.1),
                        dict(event_kind=4), dict(event_sequence=0)):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                decode(packet(**changes), 7, 1234, 1000)
        with self.assertRaises(ValueError):
            decode(packet()[:-1], 7, 1234, 1000)

    def test_inactive_restored_event_retains_volume_only(self):
        effect = decode(packet(flags=0, request=0, scale=1., effect=0., volume=.25,
                               event_kind=3, event_sequence=9), 7, 1234, 1000)
        self.assertFalse(effect['active'])
        self.assertEqual(effect['sfx_volume'], .25)

    def test_source_pacing_preserves_fractional_steps(self):
        for scale,expected in [(1., 600), (.3, 180), (.5, 300)]:
            pacer = SourcePacer()
            steps = [i for i in range(600) if pacer.due(i/60, scale)]
            self.assertLessEqual(abs(len(steps)-expected), 1)
            gaps = [b-a for a,b in zip(steps, steps[1:])]
            self.assertLessEqual(max(gaps), 4)

    def test_stalls_pause_and_resume_never_catch_up(self):
        pacer = SourcePacer()
        self.assertTrue(pacer.due(0, .3))
        self.assertTrue(pacer.due(5, .3))
        self.assertFalse(pacer.due(5.001, .3))
        pacer.pause(8)
        self.assertFalse(pacer.due(8.001, .3))
        self.assertTrue(pacer.due(8.002, 1.))
        self.assertTrue(pacer.due(8.018, 1.))
        self.assertFalse(pacer.due(8.019, .3))
        for scale in (0, float('nan'), 2):
            with self.assertRaises(ValueError):
                pacer.due(9, scale)


if __name__ == '__main__':
    unittest.main()
