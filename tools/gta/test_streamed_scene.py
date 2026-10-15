"""Coordinator lifetime and selection tests; no native engine or game is opened."""
from concurrent.futures import Future
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import geometry
from streamed_scene import StreamedScene
from world_stream import WorldStream, RegionFailed
from probe_world import Rejected


class Observer:
    def __init__(self):
        self.state = dict(pid=123, creation_100ns=456, ped_address=0x123400, epoch=1,
            area=0, ped_area=0, age_ms=1, clock_hour=14, clock_minute=0,
            position=[0., 0., 0.], raw_gta_velocity=[0., 0., 0.], sequence=2, game_timer=20)

    def read(self):
        return dict(self.state)


class Ipls:
    def __init__(self):
        self.activation = dict(active_streams=["barriers1"], generation=1)

    def read(self):
        return dict(self.activation)


class Compiler:
    """Deferred producer behind the real WorldStream cache and identity checks."""
    def __init__(self):
        self.hold, self.hour_changes_geometry = False, False
        self.requests = []

    def submit(self, function, request):
        self.requests.append(request)
        future = Future()
        if not self.hold:
            recipe = request["recipe"]
            payload = json.dumps([recipe["center"], recipe["area"], recipe["active_streams"],
                recipe["hour"] if self.hour_changes_geometry else 0]).encode()
            future.set_result(dict(key=request["key"], inputs=dict(col=payload,
                col_sha256=hashlib.sha256(payload).hexdigest(),
                coordinates=dict(origin_gta=recipe["origin"]),
                selection=dict(center_gta=recipe["center"]))))
        return future

    def shutdown(self, **_):
        pass


class TimedCompiler(Compiler):
    """Two workers; submitted Futures become RUNNING before actual dispatch."""
    def __init__(self, seconds=1.84):
        super().__init__()
        self.now, self.seconds, self.jobs = 0., seconds, []

    def submit(self, function, request):
        self.requests.append(request)
        future = Future()
        future.set_running_or_notify_cancel()  # CPython's process call queue cannot cancel it.
        self.jobs.append([request, future, None])
        self.advance(self.now)
        return future

    def advance(self, until):
        while True:
            running = [row for row in self.jobs if row[2] is not None and not row[1].done()]
            for row in self.jobs:
                if len(running) == 2:
                    break
                if row[2] is None and not row[1].done():
                    row[2] = self.now + self.seconds
                    running.append(row)
            completion = min((row[2] for row in running), default=float('inf'))
            if completion > until:
                self.now = until
                return
            self.now = completion
            for request, future, at in running:
                if at == completion:
                    producer = Compiler()
                    future.set_result(producer.submit(None, request).result())


class Native:
    def __init__(self, origin):
        self.origin, self.stage = origin, None
        self.calls, self.begun, self.discarded = [], [], 0

    def begin(self, inputs):
        assert self.stage is None
        self.begun.append(inputs)
        self.stage = "allocate"

    def advance(self):
        self.calls.append(self.stage)
        stages = dict(allocate="load", load="query", query="commit", commit="retire", retire=None)
        previous = self.stage
        self.stage = stages[self.stage]
        return previous == "retire"

    def discard_pending(self):
        assert self.stage != "retire"
        self.discarded += 1
        self.stage = None


class StreamedSceneTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        database = Path(self.temp.name) / "world.db"
        with closing(sqlite3.connect(database)) as db:
            db.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY,value TEXT)")
            db.executemany("INSERT INTO metadata VALUES (?,?)", [("schema", json.dumps(geometry.SCHEMA)), ("complete", "true")])
            db.commit()
        self.observer, self.ipls, self.compiler = Observer(), Ipls(), Compiler()
        world = WorldStream(database, origin=(0, 0, 0), grid=32, executor=self.compiler)
        self.scene = StreamedScene(self.observer, database, world=world, ipls=self.ipls)
        self.addCleanup(self.scene.close)
        # Most coordinator tests start with only the current region prepared;
        # startup prewarm is exercised separately, with its actual scheduling.
        with patch.object(world, 'prefetch', return_value=[]):
            self.scene.initial()
        self.native = Native(world.origin)
        self.scene.bind(self.native)

    def advance_to_commit(self, *, source_frame=0):
        result = None
        for _ in range(4):
            result = self.scene.update(source_frame=source_frame)
            if result["stage"] == "retire":
                break
        self.assertEqual(result["stage"], "retire")
        return result

    def fail_tile(self, state, center):
        self.compiler.hold = True
        try:
            ticket = self.scene.world.request(state, self.ipls.activation, center=center)
        finally:
            self.compiler.hold = False
        self.scene.world.pending[ticket.key].set_exception(
            ValueError('Selected scene has no supported collision surfaces'))
        return ticket

    def test_empty_forecast_preserves_current_but_not_required_target(self):
        current = self.scene.current_ticket
        failed = self.fail_tile(self.observer.state, (32.,0.,0.))
        self.observer.state['raw_gta_velocity'] = [.1,0.,0.]
        messages = []
        self.scene.log = lambda phase, **data: messages.append((phase,data))
        with patch.object(self.scene.world, 'prefetch', return_value=[failed]):
            for _ in range(3):
                self.assertTrue(self.scene.update(active=True)['ready'])
            self.assertIs(self.scene.current_ticket, current)
            self.assertEqual(self.native.begun, [])
            self.assertEqual(len(messages), 1)
            self.assertEqual(messages[0][0], 'world_forecast_failed')
            self.assertEqual(messages[0][1]['key'], failed.key)
            self.assertTrue(messages[0][1]['current_ready'])
            # The same failed tile becomes the actual desired selection.
            self.observer.state['position'] = [28.,0.,0.]
            with self.assertRaisesRegex(RegionFailed, 'no supported collision'):
                self.scene.update(active=True)

    def test_empty_next_hour_is_optional_only_until_clock_changes(self):
        self.observer.state['clock_minute'] = 58
        future = dict(self.observer.state, clock_hour=15)
        self.fail_tile(future, self.scene.current_ticket.center)
        with patch.object(self.scene.world, 'prefetch', return_value=[]):
            self.assertTrue(self.scene.update(active=True)['ready'])
            self.assertEqual(self.native.begun, [])
            self.observer.state.update(clock_hour=15, clock_minute=0)
            with self.assertRaisesRegex(RegionFailed, 'no supported collision'):
                self.scene.update(active=True)

    def test_recorded_wallride_forecast_below_map_keeps_covered_current_region(self):
        # Final03 frame6582; the real below-map query contained zero primitives.
        self.observer.state.update(position=[1733.4807453125,-1352.44274453125,18.3225522123],
            raw_gta_velocity=[6.42797413468e-5,.0820713935547,-.1138175178223],clock_hour=5)
        world = WorldStream(self.scene.world.database, origin=(0,0,0), executor=self.compiler)
        scene = StreamedScene(self.observer, world.database, world=world, ipls=self.ipls)
        self.addCleanup(scene.close)
        with patch.object(world,'prefetch',return_value=[]):
            scene.initial()
        native=Native(world.origin);scene.bind(native)
        current=scene.current_ticket
        self.assertEqual(current.center,(1728.,-1344.,0.))
        self.compiler.hold=True
        failed=world.request(self.observer.state,self.ipls.activation,center=(1728.,-1344.,-64.))
        self.compiler.hold=False
        world.pending[failed.key].set_exception(ValueError('Selected scene has no supported collision surfaces'))
        messages=[];scene.log=lambda phase,**data:messages.append((phase,data))
        result=scene.update(active=True,source_frame=6582)
        self.assertTrue(result['ready'])
        self.assertIs(scene.current_ticket,current)
        self.assertTrue(current.covers(self.observer.state['position']))
        self.assertEqual([(p,d['center']) for p,d in messages],
                         [('world_forecast_failed',(1728.,-1344.,-64.))])
        self.assertTrue(all(row['selection']['center_gta']!=failed.center for row in native.begun))

    def test_optional_failure_log_is_bounded_without_retrying_compile(self):
        self.scene.world.cache_entries = 2
        self.observer.state['raw_gta_velocity'] = [.1,0.,0.]
        messages = []
        self.scene.log = lambda phase, **data: messages.append(data)
        for x in (32.,64.,96.,128.):
            failed = self.fail_tile(self.observer.state, (x,0.,0.))
            with patch.object(self.scene.world, 'prefetch', return_value=[failed]):
                self.assertTrue(self.scene.update()['ready'])
                count = len(self.compiler.requests)
                self.assertTrue(self.scene.update()['ready'])
                self.assertEqual(len(self.compiler.requests), count)
            self.assertLessEqual(len(self.scene._reported_failures), 2)
        self.assertEqual(len(messages), 4)

    def test_transient_ipl_read_retries_once_without_restarting_native_scene(self):
        for reason in ('ipl_changed_during_read', 'ipl_load_transition'):
            with self.subTest(reason=reason), patch.object(self.ipls, 'read',
                    side_effect=[Rejected(reason), self.ipls.activation, self.ipls.activation]) as read:
                result = self.scene.update(active=True)
                self.assertTrue(result['ready'])
                self.assertEqual(read.call_count, 3)  # Two initial attempts plus final scene check.
                self.assertEqual(self.native.calls, [])

    def test_repeated_ipl_transition_buffers_with_bounded_reads_and_keeps_current_scene(self):
        current = self.scene.current_ticket
        for reason in ('ipl_changed_during_read', 'ipl_load_transition'):
            with self.subTest(reason=reason), patch.object(self.ipls, 'read', side_effect=Rejected(reason)) as read:
                result = self.scene.update(active=True)
                self.assertFalse(result['ready'])
                self.assertEqual(result['reason'], 'scene_transition')
                self.assertEqual(read.call_count, 2)
                self.assertIs(self.scene.current_ticket, current)
                self.assertEqual(self.native.calls, [])

    def test_initial_transient_ipl_transition_retries_within_existing_timeout(self):
        scene = StreamedScene(self.observer, self.scene.world.database,
                             world=self.scene.world, ipls=self.ipls)
        now = [0.]
        scene.clock = lambda: now[0]
        scene.sleeper = lambda seconds: now.__setitem__(0, now[0]+seconds)
        with patch.object(self.ipls, 'read', side_effect=[Rejected('ipl_changed_during_read'),
                Rejected('ipl_load_transition'), self.ipls.activation]):
            self.assertIsNotNone(scene.initial(timeout=.1))
        self.assertEqual(now[0], .01)
        scene.current_inputs = None
        with patch.object(self.ipls, 'read', side_effect=Rejected('ipl_load_transition')) as read:
            with self.assertRaises(TimeoutError): scene.initial(timeout=.025)
            self.assertLessEqual(read.call_count, 6)

    def test_nontransition_ipl_guards_remain_fatal_without_retry(self):
        for reason in ('ipl_layout_guard_mismatch', 'unsupported_ipl_pool_size', 'missing_conditional_ipl'):
            with self.subTest(reason=reason), patch.object(self.ipls, 'read', side_effect=Rejected(reason)) as read:
                with self.assertRaisesRegex(Rejected, reason): self.scene.update(active=True)
                self.assertEqual(read.call_count, 1)

    def test_default_world_origin_is_stable_across_player_positions(self):
        for position in ((0., 0., 0.), (2239., -1261., 24.)):
            self.observer.state['position'] = list(position)
            with patch('streamed_scene.WorldStream', return_value=self.scene.world) as create:
                StreamedScene(self.observer, self.scene.world.database, ipls=self.ipls)
                create.assert_called_once_with(self.scene.world.database, origin=(0., 0., 0.))
        with patch('streamed_scene.WorldStream', return_value=self.scene.world) as create:
            StreamedScene(self.observer, self.scene.world.database, origin=(1., 2., 3.), ipls=self.ipls)
            create.assert_called_once_with(self.scene.world.database, origin=(1., 2., 3.))

    def test_commit_updates_coverage_and_next_update_retires_without_artificial_frame(self):
        self.observer.state["position"] = [28., 0., 0.]
        result = self.advance_to_commit(source_frame=3)
        self.assertTrue(result["ready"])
        self.assertTrue(result["swapped"])
        self.assertFalse(result["needs_source_frame"])
        self.assertEqual(self.scene.current_ticket.center, (32., 0., 0.))
        result = self.scene.update(source_frame=3)
        self.assertEqual(self.native.calls[-1], "retire")
        self.assertFalse(result["needs_source_frame"])
        self.assertTrue(result["ready"])
        self.assertEqual(result["step_timing_ms"][0]["stage"], "retire")

    def test_idle_teleport_and_new_area_keep_origin_and_buffer_until_publication(self):
        self.observer.state.update(position=[-1985., 138., 30.], area=13, ped_area=13)
        first = self.scene.update(active=False)
        self.assertTrue(first["ready"])
        self.assertEqual([row['stage'] for row in first['step_timing_ms']],['allocate','load','query','commit'])
        for _ in range(3):
            last = self.scene.update(active=False)
        self.assertTrue(last["ready"])
        self.assertEqual(last["origin"], (0., 0., 0.))
        self.assertEqual(self.scene.current_ticket.scene[-1], 13)

    def test_proposed_step_outside_xyz_coverage_buffers_without_waiting(self):
        self.compiler.hold = True
        for position in [(49., 0., 0.), (0., 0., 49.)]:
            result = self.scene.update(position=position, active=True)
            self.assertFalse(result["ready"])
            self.assertEqual(result["reason"], "current_region_preparing")
        self.assertEqual(self.native.calls, [])

    def test_ready_neighbor_outside_new_forecast_avoids_waiting_for_compiler(self):
        state,activation = self.observer.read(),self.ipls.read()
        cached = self.scene.world.request(state,activation,center=(32,32,0))
        self.assertIsNotNone(self.scene.world.poll(cached,state,activation))
        self.compiler.hold = True
        self.observer.state.update(position=[49.,0.,0.],raw_gta_velocity=[.4,0.,0.])
        result = self.scene.update(active=True)
        self.assertTrue(result['ready'])
        self.assertTrue(result['swapped'])
        self.assertEqual(self.scene.current_ticket.center,(32.,32.,0.))
        self.assertEqual(result['stage'],'retire')

    def test_buffered_native_catchup_stops_at_time_budget_before_next_stage(self):
        now = [0.]
        self.scene.clock = lambda: now[0]
        original = self.native.advance
        def advance():
            now[0] += .003
            return original()
        self.native.advance = advance
        self.observer.state['position'] = [49.,0.,0.]
        result = self.scene.update(active=True)
        self.assertFalse(result['ready'])
        self.assertEqual([row['stage'] for row in result['step_timing_ms']],['allocate','load'])
        self.assertEqual(result['stage'],'query')
        result = self.scene.update(active=True)
        self.assertTrue(result['ready'])
        self.assertEqual([row['stage'] for row in result['step_timing_ms']],['query','commit'])
        self.assertEqual(result['stage'],'retire')

    def test_unpublished_scene_is_discarded_when_ipl_generation_changes(self):
        self.observer.state["position"] = [28., 0., 0.]
        self.scene.update()
        self.ipls.activation["generation"] = 2
        result = self.scene.update()
        self.assertFalse(result["ready"])
        self.assertEqual(self.native.discarded, 1)
        self.assertIsNone(self.scene.pending_ticket)
        self.assertEqual(result["step_timing_ms"][0]["stage"], "discard")

    def test_published_scene_is_never_discarded_after_another_teleport(self):
        self.observer.state["position"] = [28., 0., 0.]
        self.advance_to_commit()
        self.observer.state["position"] = [2000., 1000., 10.]
        result = self.scene.update()
        self.assertFalse(result["ready"])
        self.assertFalse(result["needs_source_frame"])
        self.assertEqual(self.native.discarded, 0)
        self.assertEqual(self.native.calls[-1], "retire")
        self.assertEqual(self.scene.source_frame, 0)
        self.scene.update()
        self.assertEqual(self.native.calls[-1], "commit")
        self.assertEqual(self.native.discarded, 0)

    def test_new_player_epoch_reuses_verified_identical_geometry(self):
        previous = self.scene.current_ticket
        self.observer.state["epoch"] += 1
        result = self.scene.update()
        self.assertTrue(result["ready"])
        self.assertEqual(previous.key, self.scene.current_ticket.key)
        self.assertNotEqual(previous.scene, self.scene.current_ticket.scene)
        self.assertEqual(self.native.begun, [])

    def test_hour_prewarm_reuses_identical_col_only_after_clock_changes(self):
        self.observer.state["clock_minute"] = 55
        self.scene.update()
        self.assertIn(15, [request["recipe"]["hour"] for request in self.compiler.requests])
        self.assertEqual(self.scene.current_ticket.hour, 14)
        self.assertEqual(self.native.begun, [])
        self.observer.state.update(clock_hour=15, clock_minute=0)
        result = self.scene.update(active=True)
        self.assertTrue(result["ready"])
        self.assertEqual(self.scene.current_ticket.hour, 15)
        self.assertEqual(self.native.begun, [])

    def test_stationary_hour_prewarm_keeps_current_center_across_grid_midpoint(self):
        self.observer.state.update(position=[17., 0., 0.], clock_minute=55)
        self.scene.update()
        self.assertEqual([request["recipe"]["center"] for request in self.compiler.requests[:2]],
                         [(0., 0., 0.), (0., 0., 0.)])
        self.observer.state.update(clock_hour=15, clock_minute=0)
        result = self.scene.update(active=True)
        self.assertTrue(result["ready"])
        self.assertEqual(self.scene.current_ticket.center, (0., 0., 0.))
        self.assertEqual(self.scene.current_ticket.hour, 15)
        self.assertEqual([request['recipe']['center'] for request in self.compiler.requests
                          if request['recipe']['hour'] == 15][0], (0.,0.,0.))
        self.assertEqual(self.native.begun, [])

    def test_hour_prewarm_reserves_queue_before_speculative_spatial_tiles(self):
        self.compiler.hold = True
        self.observer.state.update(clock_minute=55, raw_gta_velocity=[1., 0., 0.])
        self.scene.update()
        submitted = [request["recipe"] for request in self.compiler.requests[1:]]
        self.assertEqual(len(submitted), self.scene.world.max_pending)
        self.assertEqual(submitted[0]["hour"], 15)
        self.assertEqual(submitted[0]["center"], (0., 0., 0.))
        self.assertEqual(submitted[1]["hour"], 14)

    def test_changed_hour_geometry_is_preloaded_but_cannot_publish_early(self):
        self.compiler.hour_changes_geometry = True
        self.observer.state["clock_minute"] = 55
        for _ in range(5):
            self.scene.update(active=True)
        self.assertEqual(self.native.stage, "commit")
        self.assertEqual(self.scene.current_ticket.hour, 14)
        self.assertNotIn("commit", self.native.calls)
        self.observer.state.update(clock_hour=15, clock_minute=0)
        result = self.scene.update(active=True)
        self.assertTrue(result["ready"])
        self.assertTrue(result["swapped"])
        self.assertEqual(self.scene.current_ticket.hour, 15)

    def test_movement_preempts_unpublished_future_hour(self):
        self.compiler.hour_changes_geometry = True
        self.observer.state["clock_minute"] = 55
        self.scene.update()
        self.assertEqual(self.scene.pending_ticket.hour, 15)
        self.observer.state["position"] = [28., 0., 0.]
        self.scene.update(active=True)
        self.assertEqual(self.native.discarded, 1)
        self.scene.update(active=True)
        self.assertEqual(self.scene.pending_ticket.hour, 14)
        self.assertEqual(self.scene.pending_ticket.center, (32., 0., 0.))

    def test_predictive_tile_does_not_ping_pong_without_crossing_threshold(self):
        self.observer.state.update(position=[17., 0., 0.], raw_gta_velocity=[.2, 0., 0.])
        for _ in range(6):
            result = self.scene.update(active=False)
        self.assertEqual(result["stage"], "commit")
        self.assertEqual(self.scene.current_ticket.center, (0., 0., 0.))
        self.assertNotIn("commit", self.native.calls)

    def test_stationary_at_threshold_does_not_cycle_through_far_covering_neighbors(self):
        state, activation = self.observer.read(), self.ipls.read()
        far = self.scene.world.request(state, activation, center=(32., 32., 0.))
        self.assertIsNotNone(self.scene.world.poll(far, state, activation))
        self.compiler.hold = True  # The nearest (32,0,0) tile is still compiling.
        self.observer.state["position"] = [24., 0., 0.]
        # Both loaded neighbors cover this stationary player. The diagonal one
        # is farther away, so it must not trigger a swap and immediate swap back.
        for _ in range(24):
            result = self.scene.update(active=True)
            self.assertTrue(result["ready"])
            self.assertFalse(result["swapped"])
            self.assertEqual(self.scene.current_ticket.center, (0., 0., 0.))
        self.assertEqual(self.native.begun, [])
        self.assertEqual(self.native.calls, [])

    def test_turn_discards_far_staged_forecast_before_slow_boundary_crossing(self):
        self.observer.state.update(position=[17., 17., 0.], raw_gta_velocity=[.2, .2, 0.])
        for _ in range(4):
            self.scene.update(active=True)
        self.assertEqual(self.native.stage, "commit")
        self.assertEqual(self.scene.pending_ticket.center, (32., 32., 0.))
        self.observer.state.update(position=[24., 0., 0.], raw_gta_velocity=[.02, 0., 0.])
        result = self.scene.update(active=True)
        self.assertTrue(result["ready"])
        self.assertFalse(result["swapped"])
        self.assertEqual(self.native.discarded, 1)
        self.assertEqual(result["step_timing_ms"][0]["stage"], "discard")
        centers = []
        # Advance slowly, including several frames on each side of the boundary.
        for x in [24.+i*.125 for i in range(81)]:
            self.observer.state["position"] = [x, 0., 0.]
            result = self.scene.update(active=True)
            self.assertTrue(result["ready"])
            if result["swapped"]:
                centers.append(self.scene.current_ticket.center)
        self.assertEqual(centers, [(32., 0., 0.)])
        self.assertEqual(self.native.calls.count("commit"), 1)

    def test_equally_distant_covering_neighbor_is_not_a_spatial_improvement(self):
        state, activation = self.observer.read(), self.ipls.read()
        tied = self.scene.world.request(state, activation, center=(0., 32., 0.))
        self.assertIsNotNone(self.scene.world.poll(tied, state, activation))
        self.compiler.hold = True
        self.observer.state["position"] = [24., 24., 0.]
        for _ in range(12):
            result = self.scene.update(active=True)
            self.assertTrue(result["ready"])
            self.assertFalse(result["swapped"])
        self.assertEqual(self.native.begun, [])
        self.assertEqual(self.scene.current_ticket.center, (0., 0., 0.))

    def test_changed_hour_can_publish_same_center_at_spatial_threshold(self):
        self.compiler.hour_changes_geometry = True
        self.observer.state.update(position=[17., 0., 0.], clock_minute=55)
        for _ in range(4):
            self.scene.update(active=True)
        self.assertEqual(self.native.stage, "commit")
        self.assertEqual(self.scene.pending_ticket.center, (0., 0., 0.))
        self.assertEqual(self.scene.pending_ticket.hour, 15)
        self.observer.state.update(position=[24., 0., 0.], clock_hour=15, clock_minute=0)
        result = self.scene.update(active=True)
        self.assertTrue(result["ready"])
        self.assertTrue(result["swapped"])
        self.assertEqual(self.scene.current_ticket.center, (0., 0., 0.))
        self.assertEqual(self.scene.current_ticket.hour, 15)

    def test_relevant_far_forecast_cannot_block_ready_covering_tile(self):
        state, activation = self.observer.read(), self.ipls.read()
        ticket = self.scene.world.request(state, activation, center=(160., 0., 0.))
        result = self.scene.world.poll(ticket, state, activation, require_coverage=False)
        self.native.begin(result["inputs"])
        self.scene.pending_ticket, self.scene.pending_result = ticket, result
        for _ in range(3):
            self.native.advance()
        self.assertEqual(self.native.stage, "commit")
        # The player has left the old tile, while x=160 remains the 3-second
        # forecast. It cannot publish here because its safe coverage starts112.
        self.observer.state.update(position=[49., 0., 0.], raw_gta_velocity=[.8, 0., 0.])
        forecasts = self.scene.world.prefetch(self.observer.read(), activation)
        self.assertIn(ticket.key, [value.key for value in forecasts])
        result = self.scene.update(active=True)
        self.assertFalse(result["ready"])
        self.assertEqual(self.native.discarded, 1)
        self.assertEqual(result["step_timing_ms"][0]["stage"], "discard")
        result = self.advance_to_commit()
        self.assertTrue(result["ready"])
        self.assertEqual(self.scene.current_ticket.center, (64., 0., 0.))

    def test_scene_race_buffers_and_frame_rollback_is_rejected(self):
        old = self.observer.read()
        self.observer.state["epoch"] += 1
        result = self.scene.update(old)
        self.assertFalse(result["ready"])
        self.assertEqual(result["reason"], "scene_transition")
        self.scene.update(source_frame=5)
        with self.assertRaisesRegex(ValueError, "monotonic"):
            self.scene.update(source_frame=4)

    def test_malformed_world_identity_is_not_silently_buffered(self):
        self.observer.state["pid"] = 0
        with self.assertRaisesRegex(ValueError, "identity"):
            self.scene.update()

    def test_bind_native_forwards_actor_context_and_original_global_data(self):
        self.scene.native = None
        seen = {}

        def factory(*args, **kwargs):
            seen.update(kwargs)
            return self.native

        with patch.dict("sys.modules", probe_scene_stream=SimpleNamespace(SceneStream=factory)):
            self.scene.bind_native(None, None, None, None, None, {},
                context={"actor": 123}, global_data={"col": b"global"})
        self.assertEqual(seen, dict(context={"actor": 123}, global_data={"col": b"global"}))

    def test_cold_two_worker_compile_is_ready_after_straight_then_right_angle_turn(self):
        # The current tile is already loaded; subsequent tiles take the actual
        # 1.84-second cold compile time. Nothing completes on the caller thread.
        for start in ((0.,0.,0.), (2239.72,-1261.92,23.94)):
            with self.subTest(start=start):
                observer = Observer()
                observer.state['position'] = list(start)
                world = WorldStream(self.scene.world.database, origin=(0,0,0), grid=32, executor=Compiler())
                scene = StreamedScene(observer, world.database, world=world, ipls=Ipls())
                self.addCleanup(scene.close)
                with patch.object(world, 'prefetch', return_value=[]):
                    scene.initial()
                compiler = TimedCompiler()
                world.executor = compiler
                scene.clock = lambda: compiler.now
                scene.bind(Native(world.origin))
                blocked = []
                for frame in range(253):
                    now = frame / 60
                    compiler.advance(now)
                    if now < 1.6:
                        offset, velocity = (20*now, 0., 0.), (.4, 0., 0.)
                    else:
                        offset, velocity = (32., -20*(now-1.6), 0.), (0., -.4, 0.)
                    position = tuple(a+b for a,b in zip(start,offset))
                    observer.state.update(position=list(position), raw_gta_velocity=list(velocity))
                    result = scene.update(active=True, source_frame=frame)
                    if not result['ready']:
                        blocked.append((frame, result['reason']))
                    self.assertLessEqual(len(world.pending), 4)
                    self.assertLessEqual(len(world.cache), 8)
                    self.assertLessEqual(sum(row[2] is not None and not row[1].done()
                                             for row in compiler.jobs), 2)
                self.assertEqual(blocked, [])
                self.assertTrue(scene.current_ticket.covers(position))
                self.assertLess(scene.current_ticket.center[1], world.tile_center(start)[1])
                if start[0]:
                    # The actual cold-stall tile follows the first eastbound
                    # center, not the center behind the player's approach.
                    self.assertIn((2272.,-1280.,32.),
                                  [row['recipe']['center'] for row in compiler.requests[:2]])

    def test_reversal_does_not_wait_behind_dispatched_obsolete_forecasts(self):
        def replay(old_backlog):
            observer = Observer()
            world = WorldStream(self.scene.world.database, origin=(0,0,0), grid=32, executor=Compiler())
            self.assertEqual(world.max_pending, 2)
            if old_backlog:
                world.max_pending = 4  # Historical control, not an accepted constructor setting.
            scene = StreamedScene(observer, world.database, world=world, ipls=Ipls())
            self.addCleanup(scene.close)
            with patch.object(world, 'prefetch', return_value=[]):
                scene.initial()
            compiler = TimedCompiler(seconds=1.965134)  # Actual cold-tile preparation cost.
            world.executor = compiler
            scene.clock = lambda: compiler.now
            scene.bind(Native(world.origin))
            blocked = source_frames = 0
            x = 0.
            for frame in range(481):
                compiler.advance(frame/60)
                velocity = .4 if source_frames < 6 else -.4
                observer.state.update(position=[x,0.,0.], raw_gta_velocity=[velocity,0.,0.])
                result = scene.update(active=True, source_frame=source_frames)
                if result['ready']:
                    self.assertTrue(scene.current_ticket.covers((x,0,0)))
                    source_frames += 1
                    x += velocity*50/60
                else:
                    blocked += 1  # Keep this position/sample until original geometry is ready.
                if not old_backlog:
                    self.assertFalse(any(at is None and not future.done()
                                         for _,future,at in compiler.jobs))
            self.assertLessEqual(len(world.cache), 8)
            # The already published tile is reused; no cold duplicate at origin.
            self.assertNotIn((0.,0.,0.), [r['recipe']['center'] for r in compiler.requests])
            return blocked

        old, bounded = replay(True), replay(False)
        self.assertEqual((old, bounded), (198, 80))
        self.assertGreater((old-bounded)/60, 1.9)

    def test_default_grid_cold_dense_tiles_cover_straight_travel_and_two_turns(self):
        # Measured optimized 79k-face tile cost; identical 64m collision cubes
        # need fewer compilations/publications than the previous 32m spacing.
        for start in ((0.,0.,0.), (31.,31.,31.)):
            observer = Observer()
            observer.state['position'] = list(start)
            world = WorldStream(self.scene.world.database, origin=(0,0,0), executor=Compiler())
            scene = StreamedScene(observer, world.database, world=world, ipls=Ipls())
            self.addCleanup(scene.close)
            with patch.object(world, 'prefetch', return_value=[]):
                scene.initial()
            compiler = TimedCompiler(seconds=2.35)
            world.executor = compiler
            scene.clock = lambda: compiler.now
            native = Native(world.origin)
            scene.bind(native)
            blocked = []
            for frame in range(1201):
                compiler.advance(frame/60)
                if frame < 720:
                    offset, velocity = (0., -frame*16/60, 0.), (0., -16., 0.)
                elif frame < 960:
                    offset, velocity = (-(frame-720)*16/60, -192., 0.), (-16., 0., 0.)
                else:
                    offset, velocity = (-64., -192.+(frame-960)*16/60, 0.), (0., 16., 0.)
                position = [a+b for a,b in zip(start,offset)]
                observer.state.update(position=position, raw_gta_velocity=[v/50 for v in velocity])
                result = scene.update(active=True, source_frame=frame)
                if not result['ready']:
                    blocked.append(frame)
                else:
                    self.assertTrue(scene.current_ticket.covers(position))
                self.assertLessEqual(len(world.pending), 2)
                self.assertLessEqual(len(world.cache), 8)
            self.assertEqual(blocked, [], start)
            self.assertLessEqual(native.calls.count('commit'), 6)

    def test_wrong_forecast_is_replaced_before_safe_coverage_runs_out(self):
        class SeveralQueries(Native):
            def begin(self, inputs):
                super().begin(inputs)
                self.queries_left = 5

            def advance(self):
                if self.stage == 'query' and self.queries_left > 1:
                    self.calls.append(self.stage)
                    self.queries_left -= 1
                    return False
                return super().advance()

        for grid, start, threshold in ((32., 12., 24.), (64., 24., 36.)):
            with self.subTest(grid=grid):
                observer = Observer()
                world = WorldStream(self.scene.world.database, origin=(0,0,0), grid=grid, executor=Compiler())
                scene = StreamedScene(observer, world.database, world=world, ipls=Ipls())
                self.addCleanup(scene.close)
                with patch.object(world, 'prefetch', return_value=[]):
                    scene.initial()
                native = SeveralQueries(world.origin)
                scene.bind(native)
                ticket = world.request(observer.state, scene.ipls.read(), center=(0,-grid,0))
                result = world.poll(ticket, observer.state, scene.ipls.read(), require_coverage=False)
                native.begin(result['inputs'])
                while native.stage != 'commit':
                    native.advance()
                # This covering side tile is no nearer after a diagonal turn.
                scene.pending_ticket, scene.pending_result = ticket, result
                discarded_at = []
                for frame in range(121):
                    position = [start+16*frame/60, 1-start-16*frame/60, 0.]
                    observer.state.update(position=position, raw_gta_velocity=[.32,-.32,0.])
                    status = scene.update(active=True, source_frame=frame)
                    self.assertTrue(status['ready'], (grid, frame, native.stage))
                    self.assertTrue(scene.current_ticket.covers(position))
                    if any(row['stage'] == 'discard' for row in status['step_timing_ms']):
                        discarded_at.append(position[0])
                self.assertEqual(discarded_at, [threshold])
                self.assertEqual(native.calls.count('commit'), 1)
                self.assertEqual(scene.current_ticket.center, (grid,-grid,0.))

    def test_idle_startup_prepares_turns_after_reserving_hour_without_native_swaps(self):
        compiler = TimedCompiler()
        observer = Observer()
        observer.state['clock_minute'] = 55
        world = WorldStream(self.scene.world.database, origin=(0,0,0), grid=32, executor=compiler)
        scene = StreamedScene(observer, world.database, world=world, ipls=Ipls(),
            clock=lambda: compiler.now, sleeper=lambda seconds: compiler.advance(compiler.now+seconds))
        self.addCleanup(scene.close)
        scene.initial()
        native = Native(world.origin)
        scene.bind(native)
        self.assertEqual(compiler.requests[1]['recipe']['hour'], 15)
        self.assertEqual(compiler.requests[1]['recipe']['center'], (0.,0.,0.))
        start = compiler.now
        for frame in range(241):
            compiler.advance(start+frame/60)
            self.assertTrue(scene.update(active=False)['ready'])
            self.assertLessEqual(len(world.pending), 4)
            self.assertLessEqual(len(world.cache), 8)
        self.assertEqual({tuple(row['recipe']['center']) for row in compiler.requests
                          if row['recipe']['hour'] == 14},
                         {(0.,0.,0.), (32.,0.,0.), (-32.,0.,0.), (0.,32.,0.), (0.,-32.,0.)})
        self.assertEqual(len(compiler.requests), 6)
        self.assertEqual(native.calls, [])
        self.assertEqual(native.begun, [])
        observer.state.update(clock_hour=15, clock_minute=0)
        self.assertTrue(scene.update(active=False)['ready'])
        self.assertEqual(scene.current_ticket.hour, 15)
        self.assertEqual(native.calls, [])


if __name__ == "__main__":
    unittest.main()
