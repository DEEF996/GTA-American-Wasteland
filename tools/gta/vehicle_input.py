"""Read transient GTA vehicle collision; retain local shapes independently of the map.

Only the original THAW moving-owner adapter executes contacts/grinds/skitching.
This module supplies observed GTA geometry and transforms, never gameplay code.
"""
from collections import OrderedDict
from concurrent.futures import ProcessPoolExecutor
import ctypes as C
import hashlib
import math
import multiprocessing
import struct

import geometry as g
import rail_export
import rails
import scene_export
from vehicle_col import repack_col
from native.inspect_state import read_mapping_body

HEADER = struct.Struct('<8s8I3Q6I4f2I')
VEHICLE = struct.Struct('<Q8I31f9IfI')
VERTEX, TRIANGLE = struct.Struct('<3f'), struct.Struct('<3H2B')
SPHERE, BOX = struct.Struct('<4f4B'), struct.Struct('<6f4B')
MAX_COUNTS = (8192, 8192, 512, 256)
OFFSETS = (3440, 101744, 167280, 177520)
ARRAYS = (VERTEX, TRIANGLE, SPHERE, BOX)
BODY_SIZE, MAPPING_SIZE = 184688, 184696
READY, TRUNCATED, FAULT, MAX_AGE_MS = 1, 2, 4, 250
MAX_COL_OBJECTS, MAX_COL_BYTES = 1024, 4*1024*1024
assert HEADER.size == 112 and VEHICLE.size == 208
assert OFFSETS[-1] + MAX_COUNTS[-1] * BOX.size == BODY_SIZE


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _finite(values, limit):
    return all(math.isfinite(value) and abs(value) <= limit for value in values)


def _bounds(low, high):
    return _finite((*low, *high), 256) and all(a <= b for a, b in zip(low, high))


def _rigid(m):
    if not _finite(m, 100000) or (m[3], m[7], m[11], m[15]) != (0, 0, 0, 1):
        return False
    for a in range(3):
        for b in range(a, 3):
            if abs(sum(m[a*4+i]*m[b*4+i] for i in range(3)) - (a == b)) >= .002:
                return False
    determinant = (m[0]*(m[5]*m[10]-m[6]*m[9]) - m[1]*(m[4]*m[10]-m[6]*m[8]) +
                   m[2]*(m[4]*m[9]-m[5]*m[8]))
    return abs(determinant-1) < .002


class GeometryCache:
    """Validated local primitives, keyed by their exact bytes, not a model ID."""
    def __init__(self, maximum=64):
        _require(type(maximum) is int and 1 <= maximum <= 256, 'Invalid vehicle shape cache limit')
        self.maximum, self.entries = maximum, OrderedDict()

    def get(self, bounds, chunks, counts):
        digest = hashlib.sha256(struct.pack('<4I6f', *counts, *bounds[0], *bounds[1]))
        for chunk in chunks:
            digest.update(chunk)
        key = digest.hexdigest()
        if key in self.entries:
            self.entries.move_to_end(key)
            return key, self.entries[key]
        vertices, faces, spheres, boxes = [list(layout.iter_unpack(chunk)) for layout, chunk in zip(ARRAYS, chunks)]
        _require(all(_finite(v, 256) for v in vertices), 'Non-finite/out-of-range vehicle vertex')
        _require(all(all(i < len(vertices) for i in face[:3]) for face in faces), 'Vehicle triangle outside vertices')
        _require(all(_finite(s[:3], 256) and math.isfinite(s[3]) and 0 < s[3] <= 256 for s in spheres),
                 'Invalid vehicle collision sphere')
        _require(all(_bounds(b[:3], b[3:6]) for b in boxes), 'Invalid vehicle collision box')
        model = dict(name='vehicle', flags=2, bounds=bounds, vertices=vertices, faces=faces, spheres=spheres, boxes=boxes)
        self.entries[key] = model
        if len(self.entries) > self.maximum:
            self.entries.popitem(last=False)
        return key, model


def decode(body, state, now_ms, *, cache=None):
    """Validate one seqlock body against the observed process/area lifetime."""
    _require(len(body) == BODY_SIZE, 'Invalid vehicle snapshot length')
    h = HEADER.unpack_from(body)
    magic, version, size, pid, epoch, area, status, frame, timer = h[:9]
    created, sequence, tick = h[9:12]
    count, *counts, dropped = h[12:18]
    center, radius = h[18:21], h[21]
    _require((magic, version, size, pid, created) ==
             (b'GTAVEH01', 1, BODY_SIZE, state['pid'], state['creation_100ns']) and pid and created,
             'Vehicle snapshot belongs to another protocol/process lifetime')
    _require(epoch == state['epoch'] and area == state['area'] and area <= 255,
             'Vehicle snapshot belongs to another world lifetime')
    _require(sequence and sequence <= state['sequence'] and tick <= now_ms and now_ms-tick <= MAX_AGE_MS and
             0 <= state['age_ms'] <= MAX_AGE_MS, 'Vehicle snapshot is stale or newer than the observed state')
    _require(not status & ~7 and not any(h[22:]) and count <= 16 and
             all(n <= maximum for n, maximum in zip(counts, MAX_COUNTS)) and
             radius == 80 and _finite(center, 100000), 'Invalid vehicle snapshot header')
    _require(status & READY or not any((count, *counts)), 'Unavailable vehicle snapshot retained geometry')
    _require(not status & FAULT or not status & READY, 'Faulted vehicle snapshot claims ready')
    _require(bool(status & TRUNCATED) == bool(dropped) or not status & READY,
             'Vehicle truncation status differs from dropped count')
    cache = cache if cache is not None else GeometryCache()
    vehicles, seen_ids, seen_refs, used = [], set(), set(), [0]*4
    for index in range(count):
        fields = VEHICLE.unpack_from(body, HEADER.size + index*VEHICLE.size)
        identity, pool_ref, model, vehicle_area, creation, entity_flags, physical_flags, kind, collision_flags = fields[:9]
        matrix, linear, angular, com = fields[9:25], fields[25:28], fields[28:31], fields[31:34]
        low, high, ranges = fields[34:37], fields[37:40], fields[40:48]
        driver, health, reserved = fields[48:51]
        starts, sizes = ranges[::2], ranges[1::2]
        _require(identity and identity not in seen_ids and pool_ref not in seen_refs and
                 pool_ref >> 8 < 4096 and not pool_ref & 0x80 and model <= 32767,
                 'Invalid/duplicate live vehicle identity')
        _require(vehicle_area == area and entity_flags & 1 and not entity_flags & 0x800 and
                 not physical_flags & 0x20000000 and kind <= 11 and
                 collision_flags <= 7 and collision_flags & 1 and driver <= 1 and not reserved and
                 math.isfinite(health) and abs(health) <= 100000, 'Vehicle is not eligible for collision')
        _require(_rigid(matrix) and _finite(linear, 2000) and _finite(angular, 200) and
                 _finite(com, 256) and _bounds(low, high), 'Invalid vehicle transform/velocity/bounds')
        _require(all(start == previous and size <= total-start for start, size, total, previous in
                     zip(starts, sizes, counts, used)) and
                 all(size <= maximum for size, maximum in zip(sizes, (4096, 4096, 256, 128))) and
                 any(sizes[1:]) and (not sizes[1] or sizes[0] >= 3), 'Invalid vehicle primitive ranges')
        chunks = [memoryview(body)[offset+start*layout.size:offset+(start+size)*layout.size]
                  for offset, layout, start, size in zip(OFFSETS, ARRAYS, starts, sizes)]
        key, geometry = cache.get((low, high), chunks, sizes)
        vehicles.append(dict(id=identity, pool_ref=pool_ref, model=model, area=vehicle_area,
            creation_time=creation, entity_flags=entity_flags, physical_flags=physical_flags,
            vehicle_class=kind, collision_flags=collision_flags, driver_present=bool(driver), health=health,
            matrix=matrix, velocity=linear, angular_velocity=angular, center_of_mass=com,
            shape_key=key, geometry=geometry))
        seen_ids.add(identity); seen_refs.add(pool_ref)
        used = [start+size for start, size in zip(starts, sizes)]
    _require(used == counts, 'Vehicle snapshot contains unowned geometry')
    return dict(protocol='GTAVEH01', source_identity=(pid, created, epoch, area), sequence=sequence,
                tick_ms=tick, age_ms=now_ms-tick, frame_counter=frame, game_timer=timer,
                ready=bool(status & READY), fault=bool(status & FAULT), truncated=bool(status & TRUNCATED),
                dropped=dropped, center_gta=center, radius_gta=radius, vehicles=vehicles)


class VehicleInput:
    """Borrow Observer's process, own a read-only vehicle mapping and shape cache."""
    def __init__(self, observer):
        self.observer, self.api = observer, observer.api
        self.mapping = self.view = None
        self.cache = GeometryCache()
        try:
            self.mapping = self.api.OpenFileMappingW(4, False, f'Local\\THAW_GTA_VEHICLES_V1_{observer.pid}')
            if not self.mapping:
                raise C.WinError(C.get_last_error())
            self.view = self.api.MapViewOfFile(self.mapping, 4, 0, 0, MAPPING_SIZE)
            if not self.view:
                raise C.WinError(C.get_last_error())
        except BaseException:
            self.close()
            raise

    def read(self, state, *, deadline=None):
        _require((state['pid'], state['creation_100ns']) == (self.observer.pid, self.observer.created),
                 'Vehicle reader and observer process differ')
        _require(0 <= state['age_ms'] <= MAX_AGE_MS, 'Vehicle request uses an expired observer state')
        body = read_mapping_body(self.view, BODY_SIZE, self.api, self.observer.process,
                                 'vehicle snapshot', deadline=deadline)
        # Vehicle publication follows STATE. Reobserve after the copy so an ordinary
        # newer tick does not masquerade as incompatibility; a changed world still fails.
        current = self.observer.read(deadline=deadline)
        _require(all(current[key] == state[key] for key in ('pid', 'creation_100ns', 'epoch', 'area')),
                 'Vehicle observation crossed a process/world lifetime')
        return decode(body, current, self.api.GetTickCount64(), cache=self.cache)

    def close(self):
        if self.view:
            self.api.UnmapViewOfFile(self.view)
            self.view = None
        if self.mapping:
            self.api.CloseHandle(self.mapping)
            self.mapping = None


def rotation_to_thaw(matrix):
    """Map local (-x,z,y) into THAW world; owner +Z is GTA's forward +Y."""
    rows = (tuple(-v for v in matrix[0:3]), matrix[8:11], matrix[4:7])
    output = []
    for row in rows:
        output.extend((*g.gta_to_thaw(row, scale=1), 0.))
    return output + [0., 0., 0., 1.]


def local_to_thaw(point):
    return g.gta_to_thaw((-point[0], -point[1], point[2]))


def _cross(a, b):
    return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])


def _local_region(model):
    # One exact 180-degree turn applies to meshes, primitives and rails alike.
    # The owner compensates it, while original Skitch's -forward offset stays behind.
    instance = dict(g.placement(0, (0, 0, 0), (0, 0, 1, 0), 0, -1, 'vehicle'),
                    instance_id=1, source='live-vehicle', stream='vehicle-local', dynamic=False,
                    conditional=False, definition={'time_window': None})
    return dict(format='gta-sa-region-v1', coordinate_system='gta-z-up', center=[0, 0, 0], radius=512,
                interior=0, models={'vehicle': model}, instances=[instance], omitted={},
                live_dynamic_transforms_applied=False)


def rear_hooks(positions, faces):
    """Two rear surface intersections, never points inferred only from a box.

    GTA rear -Y is local THAW -Z. Cast toward the body at two inboard X
    positions and bumper height; both must hit a rear-facing actual COL face.
    """
    faces = tuple(faces)
    low = [min(p[i] for p in positions) for i in range(3)]
    high = [max(p[i] for p in positions) for i in range(3)]
    if min(high[i]-low[i] for i in range(3)) <= .01:
        return []
    y = low[1] + .4*(high[1]-low[1])
    hooks = []
    for fraction in (.25, .75):
        x = low[0] + fraction*(high[0]-low[0])
        intersections = []
        for face in faces:
            a, b, c = (positions[i] for i in face)
            bx, by, cx, cy = b[0]-a[0], b[1]-a[1], c[0]-a[0], c[1]-a[1]
            determinant = bx*cy - by*cx  # Rear faces have a negative local Z normal.
            if determinant >= -1e-10:
                continue
            dx, dy = x-a[0], y-a[1]
            u, w = (dx*cy-dy*cx)/determinant, (bx*dy-by*dx)/determinant
            if u >= -1e-7 and w >= -1e-7 and u+w <= 1+1e-7:
                intersections.append(a[2]+u*(b[2]-a[2])+w*(c[2]-a[2]))
        if not intersections:
            return []
        hooks.append([x, y, min(intersections)])
    return hooks


def compile_shape(key, model):
    """Compile only local primitives; moving owners never become static map tiles."""
    region = _local_region(model)
    payload, original_report = scene_export.build_scene(region, origin=(0, 0, 0), hour=12,
        primitive_policy=scene_export.TESSELLATE_SPHERES, max_triangles=MAX_COL_OBJECTS*50,
        max_col_bytes=MAX_COL_BYTES)
    count = struct.unpack_from('<I', payload, 4)[0]
    _require(0 < count <= MAX_COL_OBJECTS and len(payload) <= MAX_COL_BYTES,
             f'Vehicle collision exceeds native budget: {count} objects (limit {MAX_COL_OBJECTS}), '
             f'{len(payload)} bytes (limit {MAX_COL_BYTES})')
    parsed = scene_export.parse_col(payload)
    indices = parsed['indices']
    faces = list(zip(indices[::3], indices[1::3], indices[2::3]))
    # Keep the proven primitive tessellation/winding, but not building transfer flags.
    compiled, order = scene_export.compile_col(dict(positions=parsed['positions'], indices=faces,
        format='thaw-col-v10', face_flags=[flag & ~(8 | 256) for flag in parsed['face_flags']],
        terrain_types=parsed['terrain_types']), coordinates='thaw', scale=1., return_face_order=True)
    # Preserve the existing exact tessellation and face order, while a genuine
    # stock BSP groups those faces into fewer native moving-mesh children.
    payload = bytearray(repack_col(compiled))
    count = struct.unpack_from('<I', payload, 4)[0]
    _require(0 < count <= MAX_COL_OBJECTS and len(payload) <= MAX_COL_BYTES,
             f'Converted vehicle collision exceeds native budget: {count} objects (limit {MAX_COL_OBJECTS}), '
             f'{len(payload)} bytes (limit {MAX_COL_BYTES})')
    keys = set()
    for index in range(count):
        checksum = scene_export.qbkey(f'gta_vehicle_{key}_{index}')
        _require(checksum and checksum not in keys, 'Vehicle collision checksum conflict')
        keys.add(checksum)
        struct.pack_into('<I', payload, 80+index*64, checksum)
    parsed = scene_export.parse_col(payload)
    _require(parsed['stats']['triangles'] == len(faces) and all(flag in (0, 4) for flag in parsed['face_flags']),
             'Vehicle collision face conversion differs')
    report = dict(original_report, format='gta-thaw-vehicle-local-collision-v1',
        col_sha256=hashlib.sha256(payload).hexdigest(), shape_key=key,
        face_provenance_in_col_order=[original_report['face_provenance_in_col_order'][i] for i in order],
        vert_triangles=0, bank_triangles=0,
        surface_policy={'kind': 'moving-owner', 'thaw_face_flags': sorted(set(parsed['face_flags'])),
                        'building_transfer_flags': False},
        stats=dict(original_report['stats'], col_bytes=len(payload), col_objects=len(keys)))
    graph = rails.generate(region, maximum_nodes=2048)
    rail_qb, rail_report = None, None
    if graph['rails']:
        # Matching source selection is unchanged; rail exporter needs its static local-frame schema.
        local_scene = dict(report, format='gta-thaw-static-scene-v1')
        rail_qb, rail_report = rail_export.build_rails(graph, local_scene,
            basename='gta_vehicle_'+key[:16], sort_native=True)
    indices = parsed['indices']
    return dict(col=bytes(payload), report=report, vertices=parsed['positions'],
                triangles=[(*tri, flag, terrain) for tri, flag, terrain in zip(
                    zip(indices[::3], indices[1::3], indices[2::3]), parsed['face_flags'], parsed['terrain_types'])],
                rail_qb=rail_qb, rail_report=rail_report, rails=graph,
                hooks=rear_hooks(parsed['positions'], zip(indices[::3], indices[1::3], indices[2::3])))


def compile_runtime_shape(key, model):
    """Return only the checked bytes/native node inputs across the process boundary."""
    from compiler_payload import runtime_rail_report
    shape = compile_shape(key, model)
    report = shape['report']
    # Full face/vertex/provenance lists belong to offline proofs. Unpickling
    # those in the parent manager thread held the gameplay thread's GIL for ms.
    compact = {name: report[name] for name in ('format', 'shape_key', 'col_sha256', 'stats')}
    return dict(col=shape['col'], report=compact,
                rail_report=runtime_rail_report(shape['rail_report']), hooks=shape['hooks'])


class VehicleBridge:
    """Prepare local shapes in a child process and publish the latest vehicle transforms.

    Call update while waiting for J to prewarm. Missing shapes are explicit;
    owners retain identity while preparing and decide safe native replacement.
    prepare is a blocking offline/test path, never the gameplay update path.
    """
    def __init__(self, origin, *, maximum_shapes=64, maximum_pending=4, executor=None):
        g.gta_to_thaw(origin)
        _require(type(maximum_shapes) is int and 1 <= maximum_shapes <= 256, 'Invalid compiled vehicle cache limit')
        _require(type(maximum_pending) is int and 1 <= maximum_pending <= 16, 'Invalid vehicle compiler queue limit')
        self.origin, self.maximum = tuple(origin), maximum_shapes
        self.shapes = OrderedDict()
        self.pending, self.failures = {}, OrderedDict()
        self.maximum_pending, self.executor = maximum_pending, executor
        self._compiler_job, self._closed = None, False

    def _check(self, snapshot):
        _require(not self._closed, 'Vehicle bridge is closed')
        _require(snapshot['protocol'] == 'GTAVEH01' and 0 <= snapshot['age_ms'] <= MAX_AGE_MS,
                 'Vehicle bridge needs a fresh decoded observation')

    def _trim(self):
        for cache in (self.shapes, self.failures):
            while len(cache) > self.maximum:
                cache.popitem(last=False)

    def _start(self):
        if self.executor is None:
            from compiler_job import OwnedCompilerJob, join_compiler_job
            self._compiler_job = OwnedCompilerJob()
            try:
                self.executor = ProcessPoolExecutor(max_workers=1,
                    mp_context=multiprocessing.get_context('spawn'), initializer=join_compiler_job,
                    initargs=(self._compiler_job.name, self._compiler_job.owner_pid))
            except BaseException:
                self._compiler_job.close()
                self._compiler_job = None
                raise

    def update(self, snapshot):
        """Never await compilation; all vehicle identities survive pending shapes."""
        self._check(snapshot)
        for key, future in tuple(self.pending.items()):
            if not future.done():
                continue
            del self.pending[key]
            if future.cancelled():
                continue
            try:
                shape = future.result()
                _require(shape['report']['shape_key'] == key and isinstance(shape['col'], bytes),
                         'Background vehicle shape identity differs')
                self.shapes[key] = shape
            except Exception as error:
                self.failures[key] = f'{type(error).__name__}: {error}'
        wanted = {v['shape_key'] for v in snapshot['vehicles']}
        for key, future in tuple(self.pending.items()):
            if key not in wanted and future.cancel():
                del self.pending[key]
        for vehicle in snapshot['vehicles']:
            key = vehicle['shape_key']
            if key in self.shapes:
                self.shapes.move_to_end(key)
            elif key not in self.pending and key not in self.failures and len(self.pending) < self.maximum_pending:
                self._start()
                try:
                    self.pending[key] = self.executor.submit(compile_runtime_shape, key, vehicle['geometry'])
                except Exception as error:
                    self.failures[key] = f'{type(error).__name__}: {error}'
        self._trim()
        return self._convert(snapshot, {key: self.shapes[key] for key in wanted if key in self.shapes})

    def prepare(self, snapshot):
        """Blocking local proof/benchmark path; use update in the game loop."""
        self._check(snapshot)
        shapes = {}
        for vehicle in snapshot['vehicles']:
            key = vehicle['shape_key']
            if key not in self.shapes:
                self.shapes[key] = compile_shape(key, vehicle['geometry'])
            self.shapes.move_to_end(key)
            shapes[key] = self.shapes[key]
        self._trim()
        return self._convert(snapshot, shapes)

    def _convert(self, snapshot, shapes):
        vehicles = []
        for vehicle in snapshot['vehicles']:
            key = vehicle['shape_key']
            matrix = vehicle['matrix']
            com_world = [sum(vehicle['center_of_mass'][a]*matrix[4*a+i] for a in range(3)) for i in range(3)]
            angular_delta = _cross(vehicle['angular_velocity'], com_world)
            origin_velocity = [v-delta for v, delta in zip(vehicle['velocity'], angular_delta)]
            vehicles.append(dict(id=vehicle['id'], shape_key=key, pool_ref=vehicle['pool_ref'],
                model=vehicle['model'], vehicle_class=vehicle['vehicle_class'], health=vehicle['health'],
                driver_present=vehicle['driver_present'], position=g.gta_to_thaw(matrix[12:15], origin=self.origin),
                matrix=rotation_to_thaw(matrix), velocity=g.gta_to_thaw(vehicle['velocity']),
                origin_velocity=g.gta_to_thaw(origin_velocity),
                angular_velocity=g.gta_to_thaw(vehicle['angular_velocity'], scale=1),
                center_of_mass=local_to_thaw(vehicle['center_of_mass'])))
        return dict(sequence=snapshot['sequence'], tick_ms=snapshot['tick_ms'],
                    source_identity=snapshot['source_identity'], ready=snapshot['ready'],
                    truncated=snapshot['truncated'], fault=snapshot['fault'], dropped=snapshot['dropped'],
                    vehicles=vehicles, shapes=shapes,
                    pending_shapes=sorted({v['shape_key'] for v in vehicles} - shapes.keys()),
                    shape_failures={v['shape_key']: self.failures[v['shape_key']] for v in vehicles
                                    if v['shape_key'] in self.failures})

    def close(self, *, wait=True):
        if self._closed:
            return
        self._closed = True
        for future in self.pending.values():
            future.cancel()
        try:
            if self.executor is not None:
                self.executor.shutdown(wait=wait, cancel_futures=True)
        finally:
            if self._compiler_job is not None:
                self._compiler_job.close()
            self.pending.clear()
            self.shapes.clear()
