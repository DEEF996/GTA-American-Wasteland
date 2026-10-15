"""Lease/epoch and native failure tests without running either game."""
import copy
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from native.inspect_state import SnapshotBusy
from vehicle_runtime import VehicleRuntime


def state(**changes):
    return dict(dict(pid=17, creation_100ns=123, epoch=3, area=0, sequence=50,
                     flags=8 | 64, control_eligible=True, age_ms=0), **changes)


def snapshot(**changes):
    return dict(dict(source_identity=(17, 123, 3, 0), sequence=50, tick_ms=1000,
                     ready=True, vehicles=[dict(id=1, shape_key='car')]), **changes)


class Reader:
    def __init__(self):
        self.sample, self.error, self.closed, self.calls = snapshot(), None, False, []

    def read(self, observed, *, deadline=None):
        self.calls.append((observed, deadline))
        if self.error:
            raise self.error
        return copy.deepcopy(self.sample)

    def close(self):
        self.closed = True


class Bridge:
    def __init__(self):
        self.pending, self.failures, self.closed, self.calls = [], {}, [], []

    def update(self, observed):
        self.calls.append(observed)
        return dict(observed, shapes={} if self.pending else {'car': {'col': b'fixture'}},
                    pending_shapes=self.pending, shape_failures=self.failures)

    def close(self, *, wait=True):
        self.closed.append(wait)


class World:
    def __init__(self):
        self.syncs, self.parks, self.error = [], 0, None
        self.status = dict(active=1, retired=0, pending=0, created=0, ready=True)
        self.rail = self.skitch = None
        self.observations = []
        self.skitch_inputs = []

    def sync(self, prepared, *, allow_create=True, active=True):
        if self.error:
            raise self.error
        self.syncs.append((prepared, allow_create, active))
        return self.status

    def park(self):
        self.parks += 1
        if self.error:
            raise self.error
        return dict(self.status, active=0)

    def observe_skitch(self, sample):
        self.observations.append(('skitch', sample['frame']))
        if self.error:
            raise self.error
        return self.skitch

    def observe_rail(self, sample):
        self.observations.append(('rail', sample['frame']))
        return self.rail

    def set_skitch_input(self, requested):
        if self.error:raise self.error
        self.skitch_inputs.append(requested)


class VehicleRuntimeTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {'GTA_THAW_PROFILE_FRAMES': '0'})
        environment.start()
        self.addCleanup(environment.stop)
        self.now = 1000
        self.reader, self.bridge, self.world = Reader(), Bridge(), World()
        self.logs = []
        self.runtime = VehicleRuntime(SimpleNamespace(), self.world, (0, 0, 0),
            reader=self.reader, bridge=self.bridge, clock=lambda: self.now,
            log=lambda event, **data: self.logs.append((event, data)))

    def test_idle_prewarms_parked_then_activates_without_sync_park_churn(self):
        self.assertTrue(self.runtime.poll(state())['ready'])
        self.assertEqual(self.world.syncs[-1][1:], (True, False))
        self.runtime.poll(state())
        self.assertEqual(self.world.parks, 0)
        self.runtime.poll(state(), active=True)
        self.assertEqual(self.world.syncs[-1][1:], (True, True))
        self.runtime.poll(state(), active=False)
        self.assertEqual(self.world.parks, 1)
        self.assertEqual(self.world.syncs[-1][1:], (True, False))
        self.runtime.poll(state(), active=False)
        self.assertEqual(self.world.parks, 1)
        self.assertTrue(all(deadline is not None for _, deadline in self.reader.calls))

    def test_diagnostic_capture_is_explicit_and_uses_last_committed_vehicle_identity(self):
        from unittest.mock import Mock
        self.world.diagnostic_snapshot=Mock(return_value={'vehicles':[{'vehicle_id':1}]})
        self.runtime.poll(state(),active=True)
        self.world.diagnostic_snapshot.assert_not_called()
        self.assertEqual(self.runtime.diagnostic_snapshot(),dict(sequence=50,tick_ms=1000,
            source_identity=(17,123,3,0),vehicles=[{'vehicle_id':1}]))
        self.world.diagnostic_snapshot.assert_called_once_with()

    def test_skitch_input_only_admitted_in_active_runtime_and_failure_propagates(self):
        self.runtime.prepare_input(True)
        self.runtime.poll(state(),active=True)
        self.runtime.prepare_input(True);self.runtime.prepare_input(False)
        self.runtime.park();self.runtime.prepare_input(True)
        self.assertEqual(self.world.skitch_inputs,[False,True,False,False])
        with self.assertRaises(ValueError):self.runtime.prepare_input(1)
        self.world.error=RuntimeError('original ABI changed')
        with self.assertRaisesRegex(RuntimeError,'ABI'):self.runtime.prepare_input(False)

    def test_busy_keeps_exact_lease_until_250ms_then_retires_once(self):
        self.runtime.poll(state(), active=True)
        self.reader.error = SnapshotBusy('contended')
        self.now = 1249
        status = self.runtime.poll(state(), active=True)
        self.assertEqual(status['reason'], 'observation_pending')
        self.assertTrue(status['active'])
        self.assertEqual(status['observed'], 1)  # Busy is not an empty-world observation.
        self.assertEqual((len(self.world.syncs), self.world.parks), (1, 0))
        self.now = 1250
        status = self.runtime.poll(state(), active=True)
        self.assertEqual(status['reason'], 'observation_expired')
        self.assertFalse(status['active'])
        retired, create, active = self.world.syncs[-1]
        self.assertFalse(create or active or retired['ready'])
        self.assertEqual(retired['vehicles'], [])
        self.runtime.poll(state(), active=True)
        self.assertEqual(len(self.world.syncs), 2)

    def test_idle_edge_parks_even_when_reader_busy_and_fresh_resume_uses_latest_data(self):
        self.runtime.poll(state(), active=True)
        self.reader.error = ValueError('newer packet than observer')
        self.runtime.poll(state(), active=False)
        self.assertEqual(self.world.parks, 1)
        self.assertEqual(len(self.world.syncs), 1)
        self.now = 1400
        self.reader.error = None
        self.reader.sample.update(tick_ms=1400, sequence=61)
        result = self.runtime.poll(state(sequence=61), active=True)
        self.assertTrue(result['active'])
        self.assertTrue(self.world.syncs[-1][0]['ready'])
        self.assertEqual(len(self.world.syncs), 2)  # No unnecessary expiry before a fresh read.

    def test_epoch_and_area_change_retire_before_new_world_and_while_reader_busy(self):
        self.runtime.poll(state(), active=True)
        self.reader.error = SnapshotBusy('old epoch still publishing')
        self.runtime.poll(state(epoch=4), active=True)
        empty = self.world.syncs[-1][0]
        self.assertEqual(empty['source_identity'], (17, 123, 4, 0))
        self.assertFalse(empty['ready'])
        self.reader.error = None
        self.reader.sample.update(source_identity=(17, 123, 4, 1))
        self.runtime.poll(state(epoch=4, area=1), active=True)
        self.assertFalse(self.world.syncs[-2][0]['ready'])
        self.assertEqual(self.world.syncs[-1][0]['source_identity'], (17, 123, 4, 1))
        self.assertTrue(self.world.syncs[-1][0]['ready'])

    def test_pause_focus_loss_stale_state_and_menu_park_without_inventing_observations(self):
        for update in (dict(flags=2 | 8 | 64), dict(flags=64), dict(flags=8 | 32),
                       dict(control_eligible=False), dict(age_ms=250)):
            with self.subTest(update=update):
                self.runtime.poll(state(), active=True)
                before = len(self.reader.calls)
                result = self.runtime.poll(state(**update), active=True)
                self.assertEqual(result['reason'], 'gta_inactive')
                self.assertEqual(len(self.reader.calls), before)
        self.runtime.poll(state(), active=True)
        self.runtime.poll(state(flags=8 | 64 | 256), active=True)
        self.assertEqual(self.world.syncs[-1][1:], (True, False))

    def test_ready_false_is_explicit_retirement_but_pending_shape_and_backpressure_are_not(self):
        self.bridge.pending = ['car']
        self.world.status.update(ready=False, pending=1)
        result = self.runtime.poll(state(), active=True)
        self.assertEqual(result['reason'], 'vehicles_preparing')
        self.assertEqual(self.world.syncs[-1][0]['vehicles'][0]['id'], 1)
        self.assertTrue(self.world.syncs[-1][0]['ready'])
        self.reader.sample.update(ready=False, vehicles=[])
        result = self.runtime.poll(state(), active=True)
        self.assertEqual(result['reason'], 'observation_unavailable')
        self.assertEqual(self.world.syncs[-1][1:], (False, False))

    def test_expired_successful_read_and_foreign_identity_cannot_revive_owners(self):
        self.now = 1250
        self.assertEqual(self.runtime.poll(state(), active=True)['reason'], 'observation_expired')
        self.assertFalse(self.world.syncs[-1][0]['ready'])
        self.reader.sample.update(tick_ms=1250, source_identity=(17, 123, 90, 0))
        with self.assertRaisesRegex(ValueError, 'another world'):
            self.runtime.poll(state(), active=True)
        self.assertEqual(len(self.world.syncs), 1)

    def test_native_poison_or_pin_error_is_not_masked_as_temporary_backpressure(self):
        self.world.error = RuntimeError('native worker poisoned / pin differs')
        with self.assertRaisesRegex(RuntimeError, 'poisoned'):
            self.runtime.poll(state(), active=True)
        self.assertEqual(self.runtime.last_tick, 1000)

    def test_close_parks_once_and_releases_worker_reader_even_if_native_cleanup_fails(self):
        self.runtime.poll(state(), active=True)
        self.world.error = RuntimeError('native worker poisoned')
        with self.assertRaisesRegex(RuntimeError, 'poisoned'): self.runtime.close()
        self.assertTrue(self.reader.closed)
        self.assertEqual(self.bridge.closed, [False])
        self.runtime.close()
        self.assertEqual(self.world.parks, 1)
        with self.assertRaisesRegex(RuntimeError, 'closed'): self.runtime.poll(state())

    def test_diagnostics_coalesce_changes_and_log_stable_heartbeat_not_every_frame(self):
        self.runtime.poll(state(), active=True)
        for tick in range(1001, 1100):
            self.now = tick
            self.reader.sample.update(tick_ms=tick, sequence=tick)
            self.bridge.pending = ['car'] if tick & 1 else []
            self.runtime.poll(state(), active=True)
        events = [data for event, data in self.logs if event == 'vehicle_runtime_state']
        self.assertEqual(len(events), 1)
        self.now = 1100
        self.reader.sample['tick_ms'] = 1100
        self.bridge.pending = ['car']
        self.runtime.poll(state(), active=True)
        self.assertEqual(len([event for event, _ in self.logs if event == 'vehicle_runtime_state']), 2)
        for tick in (1500, 2000, 2099, 2100):
            self.now = tick
            self.reader.sample.update(tick_ms=tick, sequence=tick)
            self.runtime.poll(state(), active=True)
        events = [data for event, data in self.logs if event == 'vehicle_runtime_state']
        self.assertEqual(len(events), 3)
        self.assertEqual(events[-1]['sequence'], 2100)
        self.assertEqual(events[-1]['pending_shapes'], 1)

    def test_contact_evidence_tracks_owned_vehicle_changes_not_pointers_or_each_rail_node(self):
        self.runtime.poll(state(), active=True)
        self.world.rail = dict(contact='rail', vehicle_id=7, rail_name=100, native_node_index=0,
                               vehicle_owner=0x1234, rail_manager=0x4567, dynamic_vehicle=True)
        first = self.runtime.after_step(dict(frame=1))
        self.assertEqual(first, dict(contact='rail', vehicle_id=7, rail_name=100, native_node_index=0))
        self.world.rail.update(rail_name=101, native_node_index=1, vehicle_owner=0x6789)
        self.runtime.after_step(dict(frame=2))
        self.world.rail['vehicle_id'] = 8
        self.runtime.after_step(dict(frame=3))
        self.world.skitch = dict(contact='skitch', vehicle_id=8, vehicle_owner=0x6789,
                                 vehicle_position=[1, 2, 3], vehicle_velocity=[4, 5, 6])
        self.assertEqual(self.runtime.after_step(dict(frame=4)), dict(contact='skitch', vehicle_id=8))
        self.assertNotIn(('rail', 4), self.world.observations)
        self.world.rail = self.world.skitch = None
        self.assertIsNone(self.runtime.after_step(dict(frame=5)))
        self.runtime.after_step(dict(frame=6))
        events = [data for event, data in self.logs if event == 'vehicle_contact_changed']
        self.assertEqual([row['source_frame'] for row in events], [1, 3, 4, 5])
        self.assertIsNone(events[-1]['contact'])

    def test_contact_reads_only_active_world_clears_on_park_and_keeps_native_errors_fatal(self):
        self.assertIsNone(self.runtime.after_step(dict(frame=0)))
        self.assertEqual(self.world.observations, [])
        self.runtime.poll(state(), active=True)
        self.world.skitch = dict(contact='skitch', vehicle_id=7)
        self.runtime.after_step(dict(frame=1))
        self.runtime.park()
        self.assertIsNone(self.runtime.after_step(dict(frame=2)))
        self.assertEqual(self.world.observations, [('skitch', 1)])
        self.runtime.poll(state(), active=True)
        self.runtime.after_step(dict(frame=3))
        self.world.error = RuntimeError('Original skitch references an unowned vehicle')
        with self.assertRaisesRegex(RuntimeError, 'unowned'):
            self.runtime.after_step(dict(frame=4))
        events = [data for event, data in self.logs if event == 'vehicle_contact_changed']
        self.assertEqual([row['source_frame'] for row in events], [1, None, 3])


class VehicleStageTimingTests(unittest.TestCase):
    setUp = VehicleRuntimeTests.setUp

    def make_profiled(self):
        clock = iter(value/1000 for value in range(10000))
        with patch.dict(os.environ, {'GTA_THAW_PROFILE_FRAMES': '1'}), \
                patch('vehicle_runtime.time.perf_counter', side_effect=lambda: next(clock)):
            self.runtime = VehicleRuntime(SimpleNamespace(), self.world, (0, 0, 0),
                reader=self.reader, bridge=self.bridge, clock=lambda: self.now,
                log=lambda event, **data: self.logs.append((event, data)))

    def timing_rows(self):
        return [data for event, data in self.logs if event == 'vehicle_stage_timings']

    def test_disabled_path_has_no_profiling_clock_or_timing_events(self):
        with patch('vehicle_runtime.time.perf_counter', side_effect=AssertionError('unexpected clock')):
            self.runtime.poll(state(), active=True)
            self.runtime.prepare_input(True)
            self.runtime.close()
        self.assertEqual(self.timing_rows(), [])

    def test_sixty_polls_include_following_input_then_flush_before_next_poll(self):
        self.make_profiled()
        for _ in range(60):
            self.runtime.poll(state(), active=True)
            self.runtime.prepare_input(False)
        self.assertEqual(self.timing_rows(), [])
        self.runtime.poll(state(), active=True)
        rows = self.timing_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['polls'], rows[0]['active']), (60, True))
        self.assertEqual(set(rows[0]['stages']),
                         {'snapshot_read', 'bridge_update', 'world_sync', 'prepare_input'})
        for row in rows[0]['stages'].values():
            self.assertEqual(row['samples'], 60)
            self.assertAlmostEqual(row['total_ms'], 60)
            self.assertAlmostEqual(row['mean_ms'], 1)
            self.assertAlmostEqual(row['max_ms'], 1)
        self.runtime.close()
        self.assertEqual(self.timing_rows()[-1]['polls'], 1)
        self.assertNotIn('prepare_input', self.timing_rows()[-1]['stages'])

    def test_busy_and_native_failure_keep_semantics_and_count_only_called_stages(self):
        self.make_profiled()
        self.reader.error = SnapshotBusy('contended')
        self.assertEqual(self.runtime.poll(state(), active=True)['reason'], 'observation_pending')
        self.reader.error = None
        self.world.error = RuntimeError('native worker poisoned')
        with self.assertRaisesRegex(RuntimeError, 'poisoned'):
            self.runtime.poll(state(), active=True)
        self.runtime.close()
        stages = self.timing_rows()[0]['stages']
        self.assertEqual(stages['snapshot_read']['samples'], 2)
        self.assertEqual(stages['bridge_update']['samples'], 1)
        self.assertEqual(stages['world_sync']['samples'], 1)
        self.assertNotIn('prepare_input', stages)
        self.assertTrue(self.reader.closed)
        self.assertEqual(self.bridge.closed, [False])

    def test_prewarm_and_active_batches_are_separate(self):
        self.make_profiled()
        self.runtime.poll(state())
        self.runtime.poll(state(), active=True)
        self.runtime.prepare_input(True)
        self.runtime.poll(state(), active=False)
        self.runtime.close()
        self.assertEqual([row['active'] for row in self.timing_rows()], [False, True, False])
        self.assertEqual([row['polls'] for row in self.timing_rows()], [1, 1, 1])

    def test_slow_sync_uses_existing_timer_and_local_owner_counts_only(self):
        self.make_profiled()
        # Exactly two clock reads for each of read/bridge/sync, no extra timer.
        ticks = iter((0., .001, .001, .002, .002, .012))
        self.runtime._timings.clock = lambda: next(ticks)
        self.world.vehicles = {1: dict(retired=True, parked=True)}
        self.world.shapes = {'old': {}}
        original_sync = self.world.sync
        def create(prepared, **kwargs):
            self.world.vehicles[2] = dict(retired=False, parked=False)
            self.world.shapes['new'] = {}
            return original_sync(prepared, **kwargs)
        self.world.sync = create
        self.runtime.poll(state(position=[1.,2.,3.]), active=True)
        rows = [data for event,data in self.logs if event == 'vehicle_world_sync_slow']
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertAlmostEqual(row['milliseconds'], 10)
        self.assertEqual((row['pid'],row['state_sequence'],row['observation_sequence'],row['runtime_phase']),
                         (17,50,50,'active'))
        self.assertEqual(row['position'], [1.,2.,3.])
        self.assertEqual(row['before'], dict(owners=1,shapes=1,retired=1,parked=1))
        self.assertEqual(row['after'], dict(owners=2,shapes=2,retired=1,parked=1))
        self.assertEqual(row['native'], self.world.status)
        self.assertEqual(self.world.observations, [])
        self.assertEqual(next(ticks, None), None)

    def test_fast_sync_has_no_per_call_log(self):
        self.make_profiled()
        for _ in range(3):
            self.runtime.poll(state(), active=True)
        self.assertFalse(any(event=='vehicle_world_sync_slow' for event,_ in self.logs))


if __name__ == '__main__':
    unittest.main()
