"""Cached source upkeep never advances or acquires a movement session."""
import copy
import unittest
import live_session as live
from test_preview_gameplay_move import state


class ActiveUpkeepTests(unittest.TestCase):
    def fixture(self):
        current = dict(state(), flags=1 | 8 | 16 | 64 | 128, f7_requests=11, sequence=100)
        command = dict(source_tick=9, source_time_us=150000)
        ack = dict(protocol='GTAMACK1', age_ms=1, active=True, trajectory=True, preparing=False,
                   entry_f7_requests=11, f7_requests=11, request=99, applied_sequence=15,
                   source_tick=9, source_time_us=150000, collision_generation=1,
                   **{key: current[key] for key in ('pid', 'creation_100ns', 'ped_address', 'epoch', 'area', 'ped_area')})
        return current, ack, live.movement.scene(current), command

    def test_exact_accepted_sample_is_refreshed_without_advancing_it(self):
        current, ack, identity, command = self.fixture()
        original = copy.deepcopy((current, ack, command))
        self.assertTrue(live.accepted_upkeep_snapshot(current, ack, identity, 11, 99, 15, command))
        self.assertEqual((current, ack, command), original)

    def test_upkeep_does_not_restart_a_lost_or_changed_session(self):
        for key, value in [('f7_requests', 12), ('flags', 1 | 8 | 16 | 128),
                           ('flags', 1 | 8 | 16 | 64), ('control_eligible', False),
                           ('flags', 1 | 16 | 64 | 128), ('flags', 1 | 8 | 16 | 32 | 64 | 128),
                           ('epoch', 4), ('ped_address', 0x21000), ('age_ms', 101)]:
            current, ack, identity, command = self.fixture()
            current[key] = value
            with self.subTest(key=key, value=value):
                self.assertFalse(live.accepted_upkeep_snapshot(current, ack, identity, 11, 99, 15, command))

    def test_unaccepted_future_or_different_sample_is_not_published(self):
        for key, value in [('active', False), ('preparing', True), ('applied_sequence', 14),
                           ('source_tick', 10), ('source_time_us', 166667), ('age_ms', 101),
                           ('request', 100), ('collision_generation', 2)]:
            current, ack, identity, command = self.fixture()
            ack[key] = value
            with self.subTest(key=key):
                self.assertFalse(live.accepted_upkeep_snapshot(current, ack, identity, 11, 99, 15, command))

    def test_active_status_from_before_release_is_not_current(self):
        current, ack, _, _ = self.fixture()
        current['flags'] &= ~64
        ack['active'] = False
        status = dict(epoch=current['epoch'], f7_requests=11, sequence=current['sequence'],
                      hold_request=99, hold_active=True)
        self.assertFalse(live.adapter_diagnostic_current(status, current, ack, 1000000))
        status['hold_active'] = False
        self.assertTrue(live.adapter_diagnostic_current(status, current, ack, 1000000))
        status['hold_request'] = 98
        self.assertFalse(live.adapter_diagnostic_current(status, current, ack, 1000000))

    def test_diagnostic_from_another_epoch_or_f7_is_not_current(self):
        current, ack, _, _ = self.fixture()
        status = dict(epoch=current['epoch'], f7_requests=11, sequence=current['sequence'],
                      hold_request=99, hold_active=True)
        self.assertTrue(live.adapter_diagnostic_current(status, current, ack, 1000000))
        for update in (dict(epoch=current['epoch']+1), dict(f7_requests=12)):
            self.assertFalse(live.adapter_diagnostic_current(dict(status, **update), current, ack, 1000000))
        self.assertFalse(live.adapter_diagnostic_current(status, current, None, 1000000))
        self.assertFalse(live.adapter_diagnostic_current(status, current, ack, 1500000001))


if __name__ == '__main__':
    unittest.main()
