"""Synthetic cache/scheduling/scene-identity tests; no game is opened or changed."""
from concurrent.futures import Future
from contextlib import closing
import json
import itertools
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

import geometry
from test_scene_export import fixture
import world_stream as w


def state(position=(0., 0., 0.)):
    return dict(pid=123, creation_100ns=456, ped_address=0x123400, epoch=1,
                area=0, ped_area=0, age_ms=1, clock_hour=14,
                position=list(position), raw_gta_velocity=[.2, 0., 0.])


def activation():
    return dict(active_streams=["barriers1"], generation=1)


class Deferred:
    def __init__(self):
        self.jobs = []
        self.closed = False

    def submit(self, function, request):
        future = Future()
        self.jobs.append((function, request, future))
        return future

    def finish(self, index):
        _, request, future = self.jobs[index]
        recipe = request["recipe"]
        future.set_result(dict(key=request["key"], inputs=dict(col=b"payload",
            coordinates=dict(origin_gta=recipe["origin"]),
            selection=dict(center_gta=recipe["center"]))))

    def shutdown(self, **_):
        self.closed = True


class WorldStreamTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "world.db"
        with closing(sqlite3.connect(self.db)) as db:
            db.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY,value TEXT)")
            db.executemany("INSERT INTO metadata VALUES (?,?)", [("schema", json.dumps(geometry.SCHEMA)), ("complete", "true")])
            db.commit()
        self.executor = Deferred()
        self.stream = w.WorldStream(self.db, origin=(1, 2, 3), grid=32, max_pending=2, executor=self.executor)
        self.addCleanup(self.stream.close)

    def test_default_grid_keeps_safe_xyz_overlap_without_larger_collision_regions(self):
        stream = w.WorldStream(self.db, origin=(0,0,0), executor=Deferred())
        self.addCleanup(stream.close)
        self.assertEqual((stream.grid, stream.radius, stream.margin, stream.max_pending), (64.,64.,16.,2))
        for point in itertools.product((-32., -31.999, 0., 31.999, 32.), repeat=3):
            center = stream.tile_center(point)
            self.assertLessEqual(max(abs(a-b) for a,b in zip(point,center)), 32.)
            ticket = w.Ticket('test', (), 14, (), '', center, stream.origin, stream.radius, stream.margin)
            self.assertTrue(ticket.covers(point))
            for axis in range(3):
                edge = list(center)
                edge[axis] += 48.
                self.assertTrue(ticket.covers(edge))
                edge[axis] += .001
                self.assertFalse(ticket.covers(edge))

    def test_pending_work_never_waits_and_queue_is_bounded(self):
        first = self.stream.request(state(), activation())
        self.assertIsNone(self.stream.poll(first, state(), activation()))
        self.assertIsNotNone(self.stream.request(state((32, 0, 0)), activation()))
        self.assertIsNone(self.stream.request(state((64, 0, 0)), activation()))
        self.assertEqual(len(self.executor.jobs), 2)
        self.executor.finish(0)
        self.assertEqual(self.stream.poll(first, state(), activation())["inputs"]["col"], b"payload")

    def test_constructor_cannot_queue_more_dispatched_work_than_two_workers(self):
        with self.assertRaises(ValueError):
            w.WorldStream(self.db, origin=(0,0,0), max_pending=3, executor=self.executor)

    def test_real_pool_registers_worker_initializer_and_closes_lifetime_job_after_shutdown(self):
        import compiler_job
        calls = []
        job = Mock(name="owned_job")
        job.name, job.owner_pid = "parent-job", 111
        job.close.side_effect = lambda: calls.append("job_close")
        executor = Mock()
        executor.shutdown.side_effect = lambda **_: calls.append("shutdown")
        with patch.object(compiler_job, "OwnedCompilerJob", return_value=job), \
                patch.object(w, "ProcessPoolExecutor", return_value=executor) as pool:
            stream = w.WorldStream(self.db, origin=(1, 2, 3))
            try:
                self.assertIs(pool.call_args.kwargs["initializer"], compiler_job.join_compiler_job)
                self.assertEqual(pool.call_args.kwargs["initargs"], ("parent-job", 111))
                self.assertEqual(pool.call_args.kwargs["max_workers"], 2)
            finally:
                stream.close()
        self.assertEqual(calls, ["shutdown", "job_close"])

    def test_failed_pool_constructor_closes_job_without_assigning_a_process(self):
        import compiler_job
        job = Mock()
        with patch.object(compiler_job, "OwnedCompilerJob", return_value=job), \
                patch.object(w, "ProcessPoolExecutor", side_effect=RuntimeError("pool failed")):
            with self.assertRaisesRegex(RuntimeError, "pool failed"):
                w.WorldStream(self.db, origin=(1, 2, 3))
        job.close.assert_called_once_with()

    def test_injected_executor_does_not_create_native_job(self):
        import compiler_job
        with patch.object(compiler_job, "OwnedCompilerJob") as job:
            stream = w.WorldStream(self.db, origin=(1, 2, 3), executor=Deferred())
            stream.close()
        job.assert_not_called()

    def test_rejects_changed_process_player_area_hour_and_conditional_state(self):
        first = self.stream.request(state(), activation())
        self.executor.finish(0)
        for field in ("pid", "creation_100ns", "ped_address", "epoch", "clock_hour"):
            changed = state()
            changed[field] += 1
            with self.subTest(field=field), self.assertRaises(w.StaleRegion):
                self.stream.poll(first, changed, activation())
        changed = state()
        changed.update(area=13, ped_area=13)
        with self.assertRaises(w.StaleRegion):
            self.stream.poll(first, changed, activation())
        for active in [dict(active_streams=[]), dict(active_streams=["barriers1"], generation=2)]:
            with self.assertRaises(w.StaleRegion):
                self.stream.poll(first, state(), active)

    def test_new_ticket_can_reuse_geometry_but_old_ticket_cannot_claim_new_scene(self):
        first = self.stream.request(state(), activation())
        self.executor.finish(0)
        changed = state()
        changed["epoch"] += 1
        second = self.stream.request(changed, activation())
        self.assertEqual(first.key, second.key)
        self.assertEqual(len(self.executor.jobs), 1)
        self.assertIsNotNone(self.stream.poll(second, changed, activation()))
        with self.assertRaises(w.StaleRegion):
            self.stream.poll(first, changed, activation())

    def test_prefetch_next_boundary_without_changing_origin(self):
        tickets = self.stream.prefetch(state(), activation())
        self.assertEqual([ticket.center for ticket in tickets], [(0., 0., 0.), (32., 0., 0.)])
        self.assertTrue(all(ticket.origin == (1., 2., 3.) for ticket in tickets))
        self.assertEqual(self.stream.tile_center((-17, 79, 310)), (-32., 64., 320.))

    def test_safe_coverage_includes_height_and_rejects_teleport(self):
        ticket = self.stream.request(state(), activation())
        self.assertTrue(ticket.covers((48, 48, 48)))
        for xyz in ((48.01, 0, 0), (0, 0, -49), (-2000, 0, 0)):
            with self.subTest(position=xyz), self.assertRaises(w.StaleRegion):
                self.stream.poll(ticket, state(xyz), activation())

    def test_prefetch_prioritizes_the_first_boundary_for_diagonal_travel(self):
        tickets = self.stream.prefetch(state((0, 15, 0)), activation(), velocity=(10, 8, 0))
        self.assertEqual([ticket.center for ticket in tickets], [(0., 0., 0.), (0., 32., 0.)])

    def test_fast_forecast_prepares_first_boundary_then_turns_before_far_travel(self):
        self.stream.max_pending = 4
        tickets = self.stream.prefetch(state(), activation(), velocity=(50, 0, 0))
        self.assertEqual([ticket.center for ticket in tickets],
                         [(0., 0., 0.), (32., 0., 0.), (32., 32., 0.), (32., -32., 0.)])

    def test_grind_forecast_keeps_near_path_first_and_fills_turns_before_far_path(self):
        self.stream.max_pending = 4
        tickets = self.stream.prefetch(state(),activation(),velocity=(0,-19.2,0))
        self.assertEqual([ticket.center for ticket in tickets],
                         [(0.,0.,0.),(0.,-32.,0.),(32.,-32.,0.),(-32.,-32.,0.)])
        self.assertEqual(len(self.executor.jobs),4)
        for index in range(4):
            self.executor.finish(index)
        tickets = self.stream.prefetch(state(),activation(),velocity=(0,-19.2,0))
        self.assertEqual([ticket.center for ticket in tickets][-2:], [(0.,-64.,0.),(0.,-96.,0.)])

    def test_idle_and_vertical_forecasts_are_bounded_without_recompiling_evicted_neighbors(self):
        self.stream.max_pending = 4
        idle = dict(state(), raw_gta_velocity=[0., 0., 0.])
        for _ in range(16):
            self.stream.prefetch(idle, activation())
            for index, (_, _, future) in enumerate(self.executor.jobs):
                if not future.done():
                    self.executor.finish(index)
        self.assertEqual({tuple(request['recipe']['center']) for _, request, _ in self.executor.jobs},
                         {(0.,0.,0.), (32.,0.,0.), (-32.,0.,0.), (0.,32.,0.), (0.,-32.,0.)})
        self.assertEqual(len(self.executor.jobs), 5)
        tickets = self.stream.prefetch(idle, activation(), velocity=(0.,0.,-30.))
        self.assertEqual([ticket.center for ticket in tickets][:2], [(0.,0.,0.), (0.,0.,-32.)])
        self.assertLessEqual(len(tickets), self.stream.cache_entries)

    def test_small_cache_prefetch_never_cycles_more_targets_than_it_can_retain(self):
        self.stream.cache_entries = 2
        for _ in range(12):
            tickets = self.stream.prefetch(state(), activation(), velocity=(50.,0.,0.))
            self.assertLessEqual(len(tickets), 2)
            for index, (_, _, future) in enumerate(self.executor.jobs):
                if not future.done():
                    self.executor.finish(index)
        self.assertEqual(len(self.executor.jobs), 1)

    def test_covering_uses_ready_neighbor_without_submitting_or_reusing_stale_scene(self):
        near = self.stream.request(state(),activation(),center=(32,32,0))
        self.executor.finish(0)
        current = state((49,0,0))
        choices = self.stream.covering(current,activation())
        self.assertEqual([ticket.key for ticket in choices],[near.key])
        self.assertIsNotNone(self.stream.poll(choices[0],current,activation()))
        for changes in (dict(clock_hour=15),dict(area=2,ped_area=2),dict(position=(1000,0,0))):
            self.assertEqual(self.stream.covering(dict(current,**changes),activation()),[])
        self.assertEqual(self.stream.covering(current,dict(active_streams=[])),[])
        self.assertEqual(len(self.executor.jobs),1)

    def test_priority_current_tile_cancels_only_unstarted_distant_forecast(self):
        near = self.stream.request(state(), activation(), center=(32, 0, 0))
        distant = self.stream.request(state(), activation(), center=(128, 0, 0))
        current = self.stream.request(state(), activation(), priority=True)
        self.assertIsNotNone(current)
        self.assertFalse(self.executor.jobs[0][2].cancelled())
        self.assertTrue(self.executor.jobs[1][2].cancelled())
        self.assertIn(near.key, self.stream.pending)
        self.assertNotIn(distant.key, self.stream.pending)
        self.assertNotIn(distant.key, self.stream.failures)
        self.assertEqual(len(self.stream.pending), 2)

    def test_priority_never_cancels_running_or_nearby_covering_work(self):
        self.stream.request(state(), activation(), center=(32, 0, 0))
        self.stream.request(state(), activation(), center=(128, 0, 0))
        self.executor.jobs[1][2].set_running_or_notify_cancel()
        self.assertIsNone(self.stream.request(state(), activation(), priority=True))
        self.assertFalse(any(future.cancelled() for _, _, future in self.executor.jobs))
        self.assertEqual(len(self.stream.pending), 2)

    def test_failure_is_visible_without_retrying_the_same_broken_region_forever(self):
        ticket = self.stream.request(state(), activation())
        self.executor.jobs[0][2].set_exception(ValueError("too many triangles"))
        with self.assertRaisesRegex(w.RegionFailed, "too many triangles"):
            self.stream.poll(ticket, state(), activation())
        self.stream.request(state(), activation())
        self.assertEqual(len(self.executor.jobs), 1)

    def test_optional_failure_keeps_required_and_shared_guards(self):
        ticket = self.stream.request(state(), activation())
        self.executor.jobs[0][2].set_exception(ValueError('Selected scene has no supported collision surfaces'))
        self.assertIsNone(self.stream.poll(ticket, state(), activation(), optional=True))
        self.stream.request(state(), activation())
        self.assertEqual(len(self.executor.jobs), 1)
        with self.assertRaisesRegex(w.RegionFailed, 'no supported collision'):
            self.stream.poll(ticket, state(), activation())
        with self.assertRaises(w.StaleRegion):
            self.stream.poll(ticket, dict(state(), epoch=2), activation(), optional=True)
        with self.db.open('ab') as output:
            output.write(b'changed')
        with self.assertRaisesRegex(w.RegionFailed, 'database changed'):
            self.stream.poll(ticket, state(), activation(), optional=True)

    def test_optional_forecast_cannot_hide_malformed_ready_coordinates(self):
        ticket = self.stream.request(state(), activation())
        self.executor.finish(0)
        self.stream._collect()
        self.stream.cache[ticket.key]['inputs']['coordinates']['origin_gta'] = [999.,0.,0.]
        with self.assertRaisesRegex(w.RegionFailed, 'coordinate frame'):
            self.stream.poll(ticket, state(), activation(), optional=True)

    def test_optional_forecast_keeps_other_background_failures_and_wrong_identity_fatal(self):
        message = 'Selected scene has no supported collision surfaces'
        errors = (ValueError('Invalid source geometry'), w.RegionFailed(message),
                  RuntimeError(message), w.RegionFailed('Geometry database changed'))
        for index, error in enumerate(errors):
            current = state((index*32.,0.,0.))
            ticket = self.stream.request(current, activation())
            self.executor.jobs[-1][2].set_exception(error)
            with self.subTest(error=error), self.assertRaises(w.RegionFailed):
                self.stream.poll(ticket, current, activation(), optional=True)
        current = state((128.,0.,0.))
        ticket = self.stream.request(current, activation())
        self.executor.finish(len(self.executor.jobs)-1)
        self.executor.jobs[-1][2].result()['key'] = 'wrong'
        with self.assertRaisesRegex(w.RegionFailed, 'different identity'):
            self.stream.poll(ticket, current, activation(), optional=True)

    def test_cache_evicts_old_content_and_rejects_database_changes(self):
        self.stream.cache_entries = 1
        first = self.stream.request(state(), activation())
        self.executor.finish(0)
        self.stream.poll(first, state(), activation())
        second = self.stream.request(state((32, 0, 0)), activation())
        self.executor.finish(1)
        self.stream.poll(second, state((32, 0, 0)), activation())
        self.assertNotIn(first.key, self.stream.cache)
        with self.db.open("ab") as output:
            output.write(b"changed")
        with self.assertRaisesRegex(w.RegionFailed, "database changed"):
            self.stream.poll(second, state((32, 0, 0)), activation())

    def test_background_compile_keeps_the_same_world_coordinates_across_tiles(self):
        prepared = []
        for center in ((0, 0, 0), (32, 0, 0)):
            region = fixture(position=(0, 0, 0))
            region["center"], region["radius"] = list(center), 64
            ticket = self.stream.request(state(center), activation())
            request = self.executor.jobs[-1][1]
            with patch.object(geometry, "query_world", return_value=region):
                result = w._compile(request)
            self.assertEqual(result["key"], ticket.key)
            self.assertEqual(result["inputs"]["coordinates"]["origin_gta"], [1., 2., 3.])
            self.assertGreater(len(result["inputs"]["cases"]), 0)
            prepared.append(result["inputs"])
        self.assertEqual(prepared[0]["bounds"], prepared[1]["bounds"])

    def test_background_compiler_returns_sealed_fully_validated_rails(self):
        import global_rails
        import rail_export
        import rails
        self.stream.request(state(), activation())
        request = self.executor.jobs[-1][1]
        request.update(rail_database='owned-synthetic-rails', rail_database_stamp=(1, 2))
        request['recipe']['rail_cache_sha256'] = '0'*64
        region = fixture(box=True, position=(0, 0, 0))
        region['center'], region['radius'] = [0, 0, 0], 64

        def authored(_path, _world_sha, scene, **_):
            payload, report = rail_export.build_rails(rails.generate(region), scene, sort_native=True)
            return dict(source=payload, report=report)

        with patch.object(geometry, 'query_world', return_value=region), \
                patch.object(global_rails, 'prepare_rails', side_effect=authored):
            result = w._compile(request)
        from probe_world_rails import verified_rail_identity
        self.assertIn('validation', result['inputs']['rails'])
        with patch.object(rail_export, 'read_nodes', side_effect=AssertionError('control-thread reparse')):
            self.assertEqual(verified_rail_identity(result['inputs']),
                             result['inputs']['rails']['report']['qb_sha256'])

    def test_changed_hour_and_active_geometry_change_content_key(self):
        first = self.stream.request(state(), activation())
        current = state()
        current["clock_hour"] = 15
        second = self.stream.request(current, activation())
        self.assertNotEqual(first.key, second.key)
        self.assertEqual(first.origin, second.origin)

    def test_hour_cache_reuse_requires_same_visibility_for_every_original_definition(self):
        with closing(sqlite3.connect(self.db)) as db:
            db.execute('CREATE TABLE definitions (payload TEXT)')
            db.executemany('INSERT INTO definitions VALUES (?)',
                [(json.dumps(dict(time_window=window)),) for window in ([18, 6], [7, 17], None)])
            db.commit()
        stream = w.WorldStream(self.db, origin=(1, 2, 3), executor=Deferred())
        self.addCleanup(stream.close)
        first = stream.request(state(), activation())
        stream.executor.finish(0)
        midday = dict(state(), clock_hour=15)
        same_geometry = stream.request(midday, activation())
        self.assertEqual(first.key, same_geometry.key)
        self.assertNotEqual(first.hour, same_geometry.hour)
        self.assertIsNotNone(stream.poll(same_geometry, midday, activation()))
        evening = dict(state(), clock_hour=18)
        self.assertNotEqual(first.key, stream.request(evening, activation()).key)
        with self.assertRaises(w.StaleRegion):
            stream.poll(first, midday, activation())

    def test_clock_change_reuses_only_locally_identical_collision(self):
        with closing(sqlite3.connect(self.db)) as db:
            db.execute('CREATE TABLE instances (area,stream,conditional,minx,miny,minz,maxx,maxy,maxz,dynamic,has_collision,payload)')
            rows = [
                (0, '', 0, 1000, 1000, 0, 1010, 1010, 10, 0, 1),  # Remote clock change.
                (2, '', 0, 0, 0, 0, 10, 10, 10, 0, 1),  # Other interior.
                (0, 'inactive', 1, 0, 0, 0, 10, 10, 10, 0, 1),
                (0, '', 0, 0, 0, 0, 10, 10, 10, 1, 1),  # Live dynamic excluded.
                (0, '', 0, 0, 0, 0, 10, 10, 10, 0, 0),  # Visual only.
                (13, '', 0, 200, 0, 0, 210, 10, 10, 0, 1),  # Area13 is global.
            ]
            payload = json.dumps(dict(definition=dict(time_window=[22, 8])))
            db.executemany('INSERT INTO instances VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                           [(*row, payload) for row in rows])
            db.commit()
        stream = w.WorldStream(self.db, origin=(1, 2, 3), executor=Deferred())
        self.addCleanup(stream.close)
        before, after = dict(state(), clock_hour=7), dict(state(), clock_hour=8)
        first = stream.request(before, activation())
        stream.executor.finish(0)
        second = stream.request(after, activation())
        self.assertEqual(first.key, second.key)
        self.assertIsNotNone(stream.poll(second, after, activation()))
        with self.assertRaises(w.StaleRegion):
            stream.poll(first, after, activation())  # Raw live hour is still mandatory.
        for location, active in (((1000, 1000, 0), activation()),
                                 ((200, 0, 0), activation()),
                                 ((0, 0, 0), dict(active_streams=['inactive']))):
            early = stream.request(dict(before, position=location), active)
            late = stream.request(dict(after, position=location), active)
            self.assertIsNotNone(early)
            self.assertIsNotNone(late)
            self.assertNotEqual(early.key, late.key)
            # Complete queued synthetic jobs so each selection is independent.
            for index, (_, _, future) in enumerate(stream.executor.jobs):
                if not future.done():
                    stream.executor.finish(index)
            stream._collect()


if __name__ == "__main__":
    unittest.main()
