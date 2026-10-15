"""Synthetic wire, coordinate and local-shape tests; no running game needed."""
import copy
from concurrent.futures import Future
import hashlib
import math
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import geometry as g
import vehicle_input as v
from native.inspect_state import SnapshotBusy

IDENTITY = (1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1., 0., 10., 20., 30., 1.)


def state():
    return dict(pid=17, creation_100ns=123, sequence=8, epoch=3, area=0, age_ms=0)


def packet(*, boxes=False, spheres=False, empty=False):
    body = bytearray(v.BODY_SIZE)
    counts = (0, 0, 0, 0) if empty else (4, 2, int(spheres), int(boxes))
    header = [b'GTAVEH01', 1, v.BODY_SIZE, 17, 3, 0, 1, 40, 400, 123, 8, 1000,
              int(not empty), *counts, 0, 10., 20., 30., 80., 0, 0]
    v.HEADER.pack_into(body, 0, *header)
    if empty:
        return body
    vehicle = [1, 0x205, 400, 0, 401, 1, 0, 0, 1, *IDENTITY,
               3., 4., 5., 0., 0., 2., 1., 0., 0., -1., -2., -1., 1., 2., 1.,
               0, counts[0], 0, counts[1], 0, counts[2], 0, counts[3], 1, 1000., 0]
    v.VEHICLE.pack_into(body, v.HEADER.size, *vehicle)
    arrays = ([(-1., -2., 0.), (1., -2., 0.), (1., 2., 0.), (-1., 2., 0.)],
              [(0, 2, 1, 37, 93), (0, 3, 2, 37, 93)],
              [(0., 0., .3, .1, 51, 2, 4, 8)] if spheres else [],
              [(-1., -2., -1., 1., 2., 1., 47, 2, 17, 9)] if boxes else [])
    for offset, layout, rows in zip(v.OFFSETS, v.ARRAYS, arrays):
        for i, row in enumerate(rows):
            layout.pack_into(body, offset+i*layout.size, *row)
    return body


def changed(body, layout, offset, index, value):
    result = bytearray(body)
    values = list(layout.unpack_from(result, offset)); values[index] = value
    layout.pack_into(result, offset, *values)
    return result


def decode(body=None, *, now=1000, cache=None):
    return v.decode(packet() if body is None else body, state(), now, cache=cache)


class VehicleWireTests(unittest.TestCase):
    def test_complete_snapshot_identity_and_observed_local_geometry(self):
        sample = decode(packet(boxes=True, spheres=True), now=1250)
        self.assertTrue(sample['ready'])
        self.assertEqual(sample['source_identity'], (17, 123, 3, 0))
        self.assertEqual(sample['age_ms'], 250)
        car, = sample['vehicles']
        self.assertEqual((car['id'], car['pool_ref'], car['model']), (1, 0x205, 400))
        self.assertEqual(car['matrix'], IDENTITY)
        self.assertEqual([len(car['geometry'][name]) for name in ('vertices', 'faces', 'spheres', 'boxes')], [4, 2, 1, 1])
        self.assertEqual(car['geometry']['faces'][0], (0, 2, 1, 37, 93))

    def test_header_protocol_lifetime_freshness_bounds_and_status(self):
        for index, value in ((0, b'GTAVEH00'), (1, 2), (2, 100), (3, 18), (4, 4), (5, 1),
                             (6, 8), (6, 0), (6, 5), (6, 3), (9, 124), (10, 0), (10, 9),
                             (12, 17), (13, 8193), (14, 8193), (15, 513), (16, 257),
                             (17, 1), (18, math.nan), (21, 81), (22, 1)):
            with self.subTest(index=index, value=value), self.assertRaises(ValueError):
                decode(changed(packet(), v.HEADER, 0, index, value))
        for now in (999, 1251):
            with self.subTest(now=now), self.assertRaises(ValueError): decode(now=now)
        with self.assertRaises(ValueError): v.decode(bytes(100), state(), 1000)
        with self.assertRaises(ValueError): v.decode(packet(), dict(state(), age_ms=251), 1000)
        for status in (0, 4):
            sample = decode(changed(packet(empty=True), v.HEADER, 0, 6, status))
            self.assertFalse(sample['ready']); self.assertEqual(sample['vehicles'], [])
        truncated = changed(changed(packet(), v.HEADER, 0, 6, 3), v.HEADER, 0, 17, 2)
        self.assertTrue(decode(truncated)['truncated'])

    def test_vehicle_lifetime_eligibility_and_all_array_ranges(self):
        for index, value in ((0, 0), (1, 0x100000), (1, 0x280), (2, 32768), (3, 1),
                             (5, 0), (5, 0x801), (6, 0x20000000), (7, 12), (8, 0), (8, 9),
                             (40, 1), (41, 5), (42, 1), (43, 3), (44, 1), (46, 1),
                             (48, 2), (49, math.nan), (50, 1)):
            with self.subTest(index=index), self.assertRaises(ValueError):
                decode(changed(packet(), v.VEHICLE, v.HEADER.size, index, value))
        with self.assertRaisesRegex(ValueError, 'unowned'):
            decode(changed(packet(), v.HEADER, 0, 13, 5))
        # Even disjoint valid ranges must not alias an original moving owner.
        body = packet()
        row = list(v.VEHICLE.unpack_from(body, v.HEADER.size))
        row[40], row[42] = 4, 2
        v.VEHICLE.pack_into(body, v.HEADER.size+v.VEHICLE.size, *row)
        body = changed(body, v.HEADER, 0, 12, 2)
        body = changed(changed(body, v.HEADER, 0, 13, 8), v.HEADER, 0, 14, 4)
        with self.assertRaisesRegex(ValueError, 'duplicate'): decode(body)

    def test_malformed_transform_motion_and_primitive_bytes_are_rejected(self):
        for index, value in ((9, -1), (9, 2), (9, 1.0015), (12, 1), (24, 0), (21, math.inf),
                             (25, 2001), (28, 201), (31, 257), (34, 2), (37, math.nan)):
            with self.subTest(index=index), self.assertRaises(ValueError):
                decode(changed(packet(), v.VEHICLE, v.HEADER.size, index, value))
        for layout, offset, index, value in ((v.VERTEX, v.OFFSETS[0], 0, math.nan),
            (v.TRIANGLE, v.OFFSETS[1], 0, 4), (v.SPHERE, v.OFFSETS[2], 3, 0),
            (v.SPHERE, v.OFFSETS[2], 0, math.inf), (v.BOX, v.OFFSETS[3], 0, 2)):
            with self.subTest(layout=layout, index=index), self.assertRaises(ValueError):
                decode(changed(packet(boxes=True, spheres=True), layout, offset, index, value))

    def test_exact_shape_cache_ignores_motion_but_detects_damage_and_material_changes(self):
        cache = v.GeometryCache(maximum=2)
        first = decode(cache=cache)['vehicles'][0]
        moved = changed(packet(), v.VEHICLE, v.HEADER.size, 21, 400.)
        second = decode(moved, cache=cache)['vehicles'][0]
        self.assertEqual(first['shape_key'], second['shape_key'])
        self.assertIs(first['geometry'], second['geometry'])
        for layout, offset, index, value in ((v.VERTEX, v.OFFSETS[0], 0, -.9),
                                          (v.TRIANGLE, v.OFFSETS[1], 3, 47)):
            car = decode(changed(packet(), layout, offset, index, value), cache=cache)['vehicles'][0]
            self.assertNotEqual(first['shape_key'], car['shape_key'])
        self.assertEqual(len(cache.entries), 2)
        self.assertNotIn(first['shape_key'], cache.entries)

    def test_mapping_is_read_only_busy_propagates_and_only_owned_handles_close(self):
        calls, closed, unmapped = [], [], []
        api = SimpleNamespace(OpenFileMappingW=lambda *a: calls.append(a) or 91,
            MapViewOfFile=lambda *a: calls.append(a) or 92, CloseHandle=closed.append,
            UnmapViewOfFile=unmapped.append, GetTickCount64=lambda: 1000)
        observed = []
        observer = SimpleNamespace(pid=17, created=123, process=77, api=api,
            read=lambda **kwargs: observed.append(kwargs) or state())
        reader = v.VehicleInput(observer)
        self.assertEqual(calls, [(4, False, 'Local\\THAW_GTA_VEHICLES_V1_17'), (91, 4, 0, 0, v.MAPPING_SIZE)])
        with patch.object(v, 'read_mapping_body', return_value=packet()) as read:
            self.assertTrue(reader.read(state(), deadline=5)['ready'])
            read.assert_called_once_with(92, v.BODY_SIZE, api, 77, 'vehicle snapshot', deadline=5)
        self.assertEqual(observed, [dict(deadline=5)])
        # GTA may advance between the caller's state sample and copying VEHICLES.
        with patch.object(v, 'read_mapping_body', return_value=packet()):
            self.assertEqual(reader.read(dict(state(), sequence=7))['sequence'], 8)
            for field in ('pid', 'creation_100ns', 'epoch', 'area'):
                stale = dict(state()); stale[field] += 1
                with self.subTest(field=field), self.assertRaises(ValueError): reader.read(stale)
        with patch.object(v, 'read_mapping_body', side_effect=SnapshotBusy('busy')):
            with self.assertRaises(SnapshotBusy): reader.read(state())
        reader.close(); reader.close()
        self.assertEqual(closed, [91]); self.assertEqual(unmapped, [92])
        api.MapViewOfFile = lambda *a: 0
        with self.assertRaises(OSError): v.VehicleInput(observer)
        self.assertEqual(closed, [91, 91])


class VehicleBridgeTests(unittest.TestCase):
    def test_conjugation_matches_direct_gta_transform_for_yaw_pitch_roll(self):
        local = (1.2, -2.3, .4)
        converted = v.local_to_thaw(local)
        for axis in range(3):
            for degrees in (-173, -90, -17, 0, 61, 180):
                angle = math.radians(degrees); c, s = math.cos(angle), math.sin(angle)
                m = list(IDENTITY)
                a, b = [(1, 2), (2, 0), (0, 1)][axis]
                m[4*a+a], m[4*a+b], m[4*b+a], m[4*b+b] = c, s, -s, c
                thaw = v.rotation_to_thaw(m)
                world = [sum(local[j]*m[4*j+i] for j in range(3))+m[12+i] for i in range(3)]
                expected = g.gta_to_thaw(world, origin=(5, 7, 11))
                position = g.gta_to_thaw(m[12:15], origin=(5, 7, 11))
                actual = [sum(converted[j]*thaw[4*j+i] for j in range(3))+position[i] for i in range(3)]
                with self.subTest(axis=axis, degrees=degrees):
                    for x, y in zip(expected, actual): self.assertAlmostEqual(x, y)

    def test_velocity_at_origin_preserves_native_center_of_mass_point_motion(self):
        sample = decode()
        car = sample['vehicles'][0]
        car['matrix'] = (0., 1., 0., 0., -1., 0., 0., 0., 0., 0., 1., 0., 10., 20., 30., 1.)
        with patch.object(v, 'compile_shape', return_value={'col': b'fixture'}):
            actual = v.VehicleBridge((10, 20, 30)).prepare(sample)['vehicles'][0]
        self.assertEqual(actual['position'], [0, 0, 0])
        self.assertEqual(actual['velocity'], g.gta_to_thaw((3, 4, 5)))
        self.assertEqual(actual['origin_velocity'], g.gta_to_thaw((5, 4, 5)))
        self.assertEqual(actual['angular_velocity'], [0, 2, 0])
        self.assertEqual(actual['center_of_mass'], v.local_to_thaw(car['center_of_mass']))
        # Velocity is already metres/sec; a second GTA *50 factor would fail this.
        self.assertAlmostEqual(actual['velocity'][0], 3/.0254)

    def test_forward_owner_basis_keeps_compound_collision_rails_and_skitch_behind_car(self):
        car = decode(packet(boxes=True, spheres=True))['vehicles'][0]
        shape = v.compile_shape(car['shape_key'], car['geometry'])
        old_region = v._local_region(car['geometry'])
        old_region['instances'][0]['quaternion'] = [0, 0, 0, 1]
        old_col, _ = v.scene_export.build_scene(old_region, origin=(0, 0, 0), hour=12,
            primitive_policy=v.scene_export.TESSELLATE_SPHERES)
        old = v.scene_export.parse_col(old_col)
        converted = v.scene_export.parse_col(shape['col'])
        def triangles(parsed, rotation):
            points = parsed['positions']; ix = parsed['indices']
            # Ignore object partition/order; retain each triangle's winding/material.
            result = []
            for i in range(0, len(ix), 3):
                tri = tuple(tuple(round(points[ix[i+j]][c]*rotation[c], 5) for c in range(3))
                            for j in range(3))
                result.append((min(tri, tri[1:]+tri[:1], tri[2:]+tri[:2]),
                               parsed['terrain_types'][i//3]))
            return sorted(result)
        self.assertEqual(triangles(old, (1, 1, 1)), triangles(converted, (-1, 1, -1)))
        old_graph = v.rails.generate(old_region, maximum_nodes=2048)
        old_points = {tuple(round(c, 5) for c in g.gta_to_thaw(p))
                      for rail in old_graph['rails'] for p in rail['points_gta']}
        new_points = {tuple(round(p[c]*(-1, 1, -1)[c], 5) for c in range(3))
                      for node in shape['rail_report']['nodes'] for p in [node['position_thaw']]}
        self.assertEqual(old_points, new_points)
        for angle in (0., .7, -1.9):
            c, s = math.cos(angle), math.sin(angle)
            matrix = [c,s,0.,0., -s,c,0.,0., 0.,0.,1.,0., 10.,20.,30.,1.]
            owner = v.rotation_to_thaw(matrix)
            position = g.gta_to_thaw(matrix[12:15])
            forward = g.gta_to_thaw(matrix[4:7], scale=1)
            self.assertEqual(owner[8:11], forward)
            for hook in shape['hooks']:
                world = [position[i]+sum(hook[j]*owner[4*j+i] for j in range(3)) for i in range(3)]
                root = [world[i]-27*owner[8+i] for i in range(3)]
                # Exact pinned Skitch expression must move out behind the rear bumper.
                self.assertAlmostEqual(sum((root[i]-world[i])*forward[i] for i in range(3)), -27)

    def test_compiled_cache_reuses_shapes_but_never_reuses_owner_identity(self):
        bridge = v.VehicleBridge((0, 0, 0), maximum_shapes=1)
        first = decode(); second = copy.deepcopy(first)
        second['vehicles'][0]['id'] = 2
        second['vehicles'][0]['matrix'] = tuple(list(IDENTITY[:12])+[40., 50., 60., 1.])
        with patch.object(v, 'compile_shape', return_value={'col': b'fixture'}) as compile:
            a, b = bridge.prepare(first), bridge.prepare(second)
            self.assertEqual(compile.call_count, 1)
            self.assertNotEqual(a['vehicles'][0]['id'], b['vehicles'][0]['id'])
            self.assertNotEqual(a['vehicles'][0]['position'], b['vehicles'][0]['position'])
            second['vehicles'][0]['shape_key'] = 'changed'
            bridge.prepare(second)
            self.assertEqual(compile.call_count, 2)
            self.assertEqual(len(bridge.shapes), 1)
        self.assertEqual(bridge.prepare(decode(packet(empty=True)))['vehicles'], [])

    def test_native_col_keeps_material_winding_and_real_box_rails(self):
        sample = decode(packet(boxes=True))
        model = sample['vehicles'][0]['geometry']
        # A sloping GTA triangle receives BANK in the static compiler; moving COL must not.
        model['vertices'][2] = (1., 2., 1.)
        shape = v.compile_shape('0'*64, model)
        parsed = v.scene_export.parse_col(shape['col'])
        self.assertEqual(parsed['stats']['triangles'], 14)
        self.assertTrue(all(flag in (0, 4) for flag in parsed['face_flags']))
        self.assertIn(17, parsed['terrain_types'])
        self.assertEqual(shape['report']['format'], 'gta-thaw-vehicle-local-collision-v1')
        self.assertEqual(shape['report']['bank_triangles'], 0)
        self.assertTrue(shape['rail_qb'])
        nodes = v.rail_export.read_nodes(shape['rail_qb'], shape['rail_report']['basename'])
        self.assertGreaterEqual(len(nodes), 2)
        self.assertEqual(shape['rail_report']['scene_col_sha256'], shape['report']['col_sha256'])
        self.assertTrue(all(abs(n['position_thaw'][1]-1/.0254) < 1e-4 for n in nodes))

    def test_mixed_spheres_use_existing_bounded_approximation_and_no_static_disk_state(self):
        car = decode(packet(boxes=True, spheres=True))['vehicles'][0]
        shape = v.compile_shape(car['shape_key'], car['geometry'])
        report = shape['report']
        self.assertTrue(report['selected_primitives_complete'])
        self.assertFalse(report['geometry_exact'])
        self.assertGreater(report['sphere_approximation']['triangles'], 0)
        self.assertLessEqual(report['sphere_approximation']['maximum_certified_error_metres'], .02)
        self.assertTrue(all(tri[3] in (0, 4) for tri in shape['triangles']))

    def test_hook_points_hit_actual_rear_box_surface_but_not_empty_bound_space(self):
        car = decode(packet(boxes=True))['vehicles'][0]
        shape = v.compile_shape(car['shape_key'], car['geometry'])
        self.assertEqual(len(shape['hooks']), 2)
        for point, x in zip(shape['hooks'], (.5, -.5)):
            gta = g.thaw_to_gta((-point[0], point[1], -point[2]))
            for actual, expected in zip(gta, (x, -2., -.2)):
                self.assertAlmostEqual(actual, expected, places=5)
        # A horizontal panel is not a rear bumper. Nor may a ray hit the wrong
        # side of an outward face just because the bounding box encloses it.
        positions = [[-1, 0, -2], [1, 0, -2], [1, 0, 2], [-1, 0, 2]]
        self.assertEqual(v.rear_hooks(positions, [(0, 1, 2), (0, 2, 3)]), [])
        positions = [[-2, -2, 2], [2, -2, 2], [-2, 2, 2], [0, 0, -2]]
        # The right ray passes through the triangle's absent upper-right corner.
        self.assertEqual(v.rear_hooks(positions, [(0, 1, 2)]), [])


class CompilerExecutor:
    def __init__(self):
        self.calls, self.shutdown_calls = [], []

    def submit(self, function, *args):
        future = Future()
        self.calls.append((function, args, future))
        return future

    def shutdown(self, **kwargs):
        self.shutdown_calls.append(kwargs)


class VehicleAsyncTests(unittest.TestCase):
    def test_runtime_payload_keeps_exact_native_inputs_and_full_offline_shape_unchanged(self):
        car = decode(packet(boxes=True))['vehicles'][0]
        full = v.compile_shape(car['shape_key'], car['geometry'])
        with patch.object(v, 'compile_shape', return_value=full):
            compact = v.compile_runtime_shape(car['shape_key'], car['geometry'])
        self.assertEqual(set(compact), {'col', 'report', 'rail_report', 'hooks'})
        self.assertEqual(compact['col'], full['col'])
        self.assertEqual(compact['rail_report'], {key: value for key, value in full['rail_report'].items()
                                                if key != 'candidate_provenance'})
        self.assertIn('candidate_provenance', full['rail_report'])
        self.assertEqual(compact['rail_report']['nodes'], full['rail_report']['nodes'])
        self.assertEqual(compact['hooks'], full['hooks'])
        self.assertEqual(compact['report']['stats'], full['report']['stats'])
        self.assertEqual(hashlib.sha256(compact['col']).hexdigest(), compact['report']['col_sha256'])
        from probe_vehicle_world import validate_shape_payload
        self.assertEqual(validate_shape_payload(compact['col'], compact['report']),
                         validate_shape_payload(full['col'], full['report']))
        self.assertIn('vertices', full)
        self.assertIn('face_provenance_in_col_order', full['report'])

    def test_twenty_cold_arrivals_use_compact_worker_results_without_retry_or_cache_overflow(self):
        executor = CompilerExecutor()
        bridge = v.VehicleBridge((0, 0, 0), maximum_shapes=4, executor=executor)
        sample = decode(packet(boxes=True))
        for index in range(20):
            key = f'{index:064x}'
            sample['vehicles'][0].update(id=index+1, shape_key=key)
            self.assertEqual(bridge.update(sample)['pending_shapes'], [key])
            function, args, future = executor.calls[-1]
            self.assertIs(function, v.compile_runtime_shape)
            future.set_result(function(*args))
            complete = bridge.update(sample)
            self.assertFalse(complete['pending_shapes'] or complete['shape_failures'])
            self.assertEqual(complete['vehicles'][0]['id'], index+1)
            self.assertEqual(set(complete['shapes'][key]), {'col', 'report', 'rail_report', 'hooks'})
            bridge.update(sample)
            self.assertEqual(len(executor.calls), index+1)
            self.assertLessEqual(len(bridge.shapes), 4)
        bridge.close(wait=False)

    def test_large_vehicle_preserves_all_spheres_with_grouped_native_bsp(self):
        car = decode()['vehicles'][0]
        car['geometry'].update(vertices=[], faces=[], boxes=[], bounds=[[-2., -2., -2.], [2., 2., 2.]],
            spheres=[(0., 0., 0., 1.5, 37, 0, 0, 0)]*21)
        shape = v.compile_shape(car['shape_key'], car['geometry'])
        self.assertLess(shape['report']['stats']['col_objects'], 1024)
        self.assertEqual(shape['report']['stats']['col_triangles'], 21*1280)
        self.assertEqual(len(shape['triangles']), 21*1280)
        self.assertEqual(len(shape['report']['face_provenance_in_col_order']), 21*1280)
        from probe_vehicle_world import MAX_SHAPE_OBJECTS, MAX_SHAPE_BYTES
        self.assertEqual((v.MAX_COL_OBJECTS, v.MAX_COL_BYTES), (MAX_SHAPE_OBJECTS, MAX_SHAPE_BYTES))

    def test_oversized_sphere_mesh_is_rejected_in_worker_without_losing_live_identity_or_retrying(self):
        sample = decode()
        car = sample['vehicles'][0]
        car['geometry'].update(vertices=[], faces=[], boxes=[], bounds=[[-2., -2., -2.], [2., 2., 2.]],
            spheres=[(0., 0., 0., 1.5, 37, 0, 0, 0)]*41)
        key = car['shape_key']
        executor = CompilerExecutor()
        bridge = v.VehicleBridge((0, 0, 0), executor=executor)
        bridge.update(sample)
        function, args, future = executor.calls[0]
        # More than 1,024 proven 50-face leaves cannot reach the native owner.
        try:
            function(*args)
        except ValueError as error:
            self.assertRegex(str(error), 'triangle budget')
            future.set_exception(error)
        else:
            self.fail('Oversized native collision was published')
        result = bridge.update(sample)
        self.assertEqual(result['shapes'], {})
        self.assertEqual(result['vehicles'][0]['id'], car['id'])
        self.assertIn(key, result['shape_failures'])
        bridge.update(sample)
        self.assertEqual(len(executor.calls), 1)

    def test_pending_shape_never_blocks_or_drops_observed_owner_and_finishes_with_latest_transform(self):
        executor = CompilerExecutor()
        bridge = v.VehicleBridge((0, 0, 0), executor=executor)
        sample = decode()
        key = sample['vehicles'][0]['shape_key']
        first = bridge.update(sample)
        self.assertEqual(first['pending_shapes'], [key])
        self.assertEqual(first['shapes'], {})
        self.assertEqual(first['vehicles'][0]['id'], 1)
        function, args, future = executor.calls[0]
        self.assertIs(function, v.compile_runtime_shape)
        self.assertEqual(args, (key, sample['vehicles'][0]['geometry']))
        sample['vehicles'][0]['matrix'] = tuple(list(IDENTITY[:12])+[40., 50., 60., 1.])
        # An unfinished Future.result would block forever, so the first two
        # updates also prove the gameplay path only polls completed work.
        self.assertEqual(len(bridge.update(sample)['pending_shapes']), 1)
        self.assertEqual(len(executor.calls), 1)
        future.set_result(dict(col=b'compiled', report=dict(shape_key=key)))
        complete = bridge.update(sample)
        self.assertEqual(complete['pending_shapes'], [])
        self.assertEqual(complete['shapes'][key]['col'], b'compiled')
        self.assertEqual(complete['vehicles'][0]['position'], g.gta_to_thaw((40, 50, 60)))

    def test_bounded_queue_cancels_absent_work_and_reports_failure_without_retry_storm(self):
        executor = CompilerExecutor()
        bridge = v.VehicleBridge((0, 0, 0), maximum_pending=1, executor=executor)
        a = decode(); b = copy.deepcopy(a)
        b['vehicles'][0].update(id=2, shape_key='second')
        first = bridge.update(a)
        bridge.update(b)
        self.assertTrue(executor.calls[0][2].cancelled())
        self.assertEqual(len(executor.calls), 2)
        executor.calls[1][2].set_exception(ValueError('unsupported actual shape'))
        failed = bridge.update(b)
        self.assertEqual(failed['shape_failures'], {'second': 'ValueError: unsupported actual shape'})
        self.assertEqual(failed['vehicles'][0]['id'], 2)
        self.assertEqual(failed['pending_shapes'], ['second'])
        bridge.update(b)
        self.assertEqual(len(executor.calls), 2)
        self.assertEqual(len(bridge.pending), 0)
        self.assertTrue(first['pending_shapes'])

    def test_wrong_worker_identity_is_rejected_and_close_cancels_owned_job(self):
        executor = CompilerExecutor()
        bridge = v.VehicleBridge((0, 0, 0), executor=executor)
        sample = decode(); key = sample['vehicles'][0]['shape_key']
        bridge.update(sample)
        executor.calls[0][2].set_result(dict(col=b'compiled', report=dict(shape_key='foreign')))
        self.assertIn(key, bridge.update(sample)['shape_failures'])
        sample['vehicles'][0]['shape_key'] = 'new'
        bridge.update(sample)
        bridge.close(wait=False); bridge.close()
        self.assertTrue(executor.calls[-1][2].cancelled())
        self.assertEqual(executor.shutdown_calls, [dict(wait=False, cancel_futures=True)])
        with self.assertRaisesRegex(ValueError, 'closed'): bridge.update(sample)


if __name__ == '__main__':
    unittest.main()
