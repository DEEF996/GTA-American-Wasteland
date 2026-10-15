"""Prepare bounded GTA collision tiles off the simulation thread.

This does not change either game's memory. A completed tile is only a validated
input for the original THAW scene loader; the caller still owns the native swap.
"""
from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import ProcessPoolExecutor
from contextlib import closing
from dataclasses import dataclass
import hashlib
import json
import math
import multiprocessing
from pathlib import Path
import sqlite3
import sys
import time

import geometry
import scene_export

SCENE_FIELDS = ("pid", "creation_100ns", "ped_address", "epoch", "area", "ped_area")
DEFAULT_RADIUS = 64.0
DEFAULT_GRID = 64.0
DEFAULT_MARGIN = 16.0


class StaleRegion(ValueError):
    """The request no longer belongs to the current scene or player location."""


class RegionFailed(RuntimeError):
    """A region could not be compiled without dropping required geometry."""


def _vector(value):
    if not isinstance(value, (list, tuple)) or len(value) != 3 or not all(
            isinstance(x, (int, float)) and math.isfinite(x) and abs(x) <= 100000 for x in value):
        raise ValueError("Expected a finite GTA world vector")
    return tuple(float(x) for x in value)


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _sha(value):
    return hashlib.sha256(_json(value)).hexdigest()


def _database_identity(path, *, digest=None):
    """Once at construction, outside the simulation loop; the DB is immutable."""
    before = _file_stamp(path)
    if digest is None:
        checksum = geometry.file_digest(path)
    else:
        checksum = digest(path)
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        values = {key: json.loads(raw) for key, raw in db.execute("SELECT key,value FROM metadata")}
        if values.get("schema") != geometry.SCHEMA or values.get("complete") is not True:
            raise ValueError("Incomplete or unsupported geometry database")
    finally:
        db.close()
    after = _file_stamp(path)
    if before != after:
        raise ValueError("Geometry database changed during initialization")
    return checksum, after


def _file_stamp(path):
    status = path.stat()
    return status.st_size, status.st_mtime_ns, status.st_ctime_ns


def _visibility_hours(path):
    """Share geometry only across hours with identical original model visibility.

    The ticket still uses the live raw hour. This content identity avoids a
    fresh world compilation at every GTA clock boundary when no timed model
    can possibly change. Using every definition is conservative across tiles.
    """
    db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    try:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'definitions' not in tables:
            return tuple(range(24))  # Minimal injected-executor fixtures lack definitions.
        windows = []
        for raw, in db.execute("SELECT DISTINCT json_extract(payload,'$.time_window') FROM definitions "
                               "WHERE json_extract(payload,'$.time_window') IS NOT NULL"):
            window = json.loads(raw)
            if (not isinstance(window, list) or len(window) != 2 or
                    any(type(value) is not int or not 0 <= value <= 24 for value in window)):
                raise ValueError('Invalid original model visibility window')
            windows.append(tuple(window))
        representatives, identities = {}, []
        for hour in range(24):
            visible = tuple(geometry.active_hour(window, hour) for window in sorted(windows))
            identities.append(representatives.setdefault(visible, hour))
        return tuple(identities)
    finally:
        db.close()


def _timed_collision_instances(path):
    """Load the small static timed subset once, outside the simulation loop."""
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'instances' not in tables:
            return None  # Conservative definition-wide identity for injected fixtures.
        rows = db.execute("""SELECT area,stream,conditional,minx,miny,minz,maxx,maxy,maxz,
            json_extract(payload,'$.definition.time_window') FROM instances
            WHERE dynamic=0 AND has_collision=1
            AND json_extract(payload,'$.definition.time_window') IS NOT NULL""").fetchall()
    result = []
    for area, stream, conditional, *values in rows:
        window = json.loads(values[-1])
        if (not isinstance(window, list) or len(window) != 2 or
                any(type(value) is not int or not 0 <= value <= 24 for value in window)):
            raise ValueError('Invalid original collision visibility window')
        low, high = _vector(values[:3]), _vector(values[3:6])
        if any(a > b for a, b in zip(low, high)):
            raise ValueError('Invalid timed collision instance bounds')
        result.append((area, stream, conditional, low, high, window))
    return result


def _activation(activation):
    if not isinstance(activation, dict) or not isinstance(activation.get("active_streams"), (tuple, list)):
        raise ValueError("Explicit current conditional IPL activation is required")
    names = activation["active_streams"]
    if not all(isinstance(name, str) and name and
            len(name) <= 63 and all(char.isascii() and (char.isalnum() or char == "_") for char in name)
            for name in names) or len(set(names)) != len(names):
        raise ValueError("Invalid conditional IPL names")
    return tuple(sorted(names)), _sha(activation)


def context(state, activation):
    """Session identity is separate from reusable geometry content identity."""
    for field in SCENE_FIELDS[:4]:
        if type(state.get(field)) is not int or state[field] <= 0:
            raise ValueError("Missing current process/player identity")
    if (type(state.get("area")) is not int or not 0 <= state["area"] <= 255 or
            type(state.get("ped_area")) is not int or state["ped_area"] != state["area"]):
        raise StaleRegion("Area transition has not completed")
    if type(state.get("clock_hour")) is not int or not 0 <= state["clock_hour"] <= 23:
        raise ValueError("A live raw GTA clock hour is required")
    if not isinstance(state.get("age_ms"), (int, float)) or not 0 <= state["age_ms"] <= 1000:
        raise StaleRegion("Player snapshot is stale")
    _vector(state["position"])
    streams, activation_hash = _activation(activation)
    return tuple(state[field] for field in SCENE_FIELDS), state["clock_hour"], streams, activation_hash


@dataclass(frozen=True)
class Ticket:
    key: str
    scene: tuple
    hour: int
    active_streams: tuple
    activation_sha256: str
    center: tuple
    origin: tuple
    radius: float
    margin: float

    def covers(self, position, *, margin=None):
        """The source query is a cube, including its vertical extent."""
        clearance = self.margin if margin is None else margin
        if not math.isfinite(clearance) or not 0 <= clearance < self.radius:
            raise ValueError("Invalid collision clearance")
        return max(abs(a-b) for a, b in zip(_vector(position), self.center)) <= self.radius-clearance

    def validate(self, state, activation, *, require_coverage=True):
        scene, hour, streams, activation_hash = context(state, activation)
        if (scene, hour, streams, activation_hash) != (
                self.scene, self.hour, self.active_streams, self.activation_sha256):
            raise StaleRegion("Tile belongs to a different player, area, hour or IPL activation")
        if require_coverage and not self.covers(state["position"]):
            raise StaleRegion("Player left the prepared collision tile's safe coverage")


def _compile(request):
    """Runs exclusively in a spawned process; returns ready-to-load native input."""
    from compiler_payload import runtime_world_result
    path = Path(request["database"])
    if _file_stamp(path) != tuple(request["database_stamp"]):
        raise RegionFailed("Geometry database changed after the stream was initialized")
    recipe = request["recipe"]
    started = time.perf_counter()
    # Disk reads and full cached rail validation stay in this worker process.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "vanilla_thaw"))
    from world_tile_cache import TileCache
    cache = TileCache(request)
    cached = cache.load()
    loaded = time.perf_counter()
    if cached is not None:
        if (_file_stamp(path) != tuple(request['database_stamp']) or
                (request.get('rail_database') is not None and
                 _file_stamp(Path(request['rail_database'])) != tuple(request['rail_database_stamp']))):
            raise RegionFailed('World or rail database changed while a cached tile was being loaded')
        cached['timings_ms'] = dict(cache_hit=True, cache_load=(loaded-started)*1000,
            query=0., compile=0., prepare=0., total=(loaded-started)*1000)
        return runtime_world_result(cached)
    region = geometry.query_world(path, recipe["center"], recipe["radius"],
        interior=recipe["area"], active_streams=recipe["active_streams"], hour=recipe["hour"],
        include_dynamic=False, max_instances=10000, max_primitives=250000)
    queried = time.perf_counter()
    payload, report = scene_export.build_scene(region, primitive_policy=scene_export.TESSELLATE_SPHERES,
        hour=recipe["hour"], active_streams=recipe["active_streams"], origin=recipe["origin"])
    compiled = time.perf_counter()
    # This parser computes independent ray controls. It must not run at 60 Hz.
    import probe_gta_scene
    prepared = probe_gta_scene.prepare_inputs(payload, report)
    if request.get('rail_database') is not None:
        from global_rails import prepare_rails
        prepared['rails'] = prepare_rails(request['rail_database'], recipe['database_sha256'], report,
            cache_sha256=recipe['rail_cache_sha256'], stamp=request['rail_database_stamp'])
        from probe_world_rails import prepare_rail_validation
        prepared['rails']['validation'] = prepare_rail_validation(prepared)
    if _file_stamp(path) != tuple(request["database_stamp"]):
        raise RegionFailed("Geometry database changed while a tile was being compiled")
    finished = time.perf_counter()
    result = dict(key=request["key"], inputs=prepared,
                timings_ms=dict(cache_hit=False, cache_load=(loaded-started)*1000,
                                query=(queried-loaded)*1000, compile=(compiled-queried)*1000,
                                prepare=(finished-compiled)*1000, total=(finished-started)*1000),
                stats=report["stats"], omitted=report["omitted"],
                live_collision_complete=False, dynamic_entities_reconciled=False)
    result['timings_ms']['cache_saved'] = cache.save(result)
    result['timings_ms']['cache_save'] = (time.perf_counter()-finished)*1000
    result['timings_ms']['total'] = (time.perf_counter()-started)*1000
    return runtime_world_result(result)


class WorldStream:
    """Two background compilers, bounded work queue and bounded resident cache.

    Construct outside the simulation loop. Call request/prefetch and poll without
    waiting. Only close(wait=True), used after gameplay relinquishes ownership,
    waits for compilation. The caller must release control before leaving an
    active ticket's coverage if no replacement is ready.
    """

    def __init__(self, database, *, origin, radius=DEFAULT_RADIUS, grid=DEFAULT_GRID,
                 margin=DEFAULT_MARGIN, cache_entries=8, cache_bytes=64*1024*1024,
                 max_pending=2, executor=None):
        self.database = Path(database).resolve()
        self.origin = _vector(origin)
        if not all(math.isfinite(x) for x in (radius, grid, margin)) or not (
                16 <= radius <= 256 and 4 <= grid <= radius and 0 < margin < radius and grid/2 < radius-margin):
            raise ValueError("Tile dimensions leave no safe overlapping coverage")
        if not (type(cache_entries) is int and 1 <= cache_entries <= 64 and
                type(cache_bytes) is int and 16*1024*1024 <= cache_bytes <= 512*1024*1024 and
                type(max_pending) is int and 1 <= max_pending <= 2):
            raise ValueError("Invalid world stream resource limits")
        self.radius, self.grid, self.margin = float(radius), float(grid), float(margin)
        from file_hash_cache import FileHashCache
        digests = FileHashCache(self.database.parent / '.validated-map-hashes.json')
        self.database_sha256, self.database_stamp = _database_identity(self.database, digest=digests.digest)
        self.visibility_hours = _visibility_hours(self.database)
        self.timed_collision_instances = _timed_collision_instances(self.database)
        if _file_stamp(self.database) != self.database_stamp:
            raise ValueError('Geometry database changed during visibility initialization')
        self.rail_database = self.database.with_name('global_rails.db')
        self.rail_cache_sha256 = self.rail_database_stamp = None
        if self.rail_database.is_file():
            from global_rails import cache_identity
            self.rail_cache_sha256, self.rail_database_stamp = cache_identity(
                self.rail_database, world_sha256=self.database_sha256, digest=digests.digest)
        digests.save()
        # ProcessPoolExecutor marks queued work RUNNING before a worker starts
        # it. Submit only one job per worker so new current demand cannot sit
        # behind another non-cancellable batch of obsolete forecasts.
        self.cache_entries, self.cache_bytes, self.max_pending = cache_entries, cache_bytes, max_pending
        self.cache, self.pending, self.failures = OrderedDict(), {}, OrderedDict()
        self.pending_recipes = {}
        self._closed = False
        self._compiler_job = None
        if executor is not None:
            self.executor = executor
        else:
            from compiler_job import OwnedCompilerJob, join_compiler_job
            self._compiler_job = OwnedCompilerJob()
            try:
                self.executor = ProcessPoolExecutor(max_workers=2,
                    mp_context=multiprocessing.get_context("spawn"),
                    initializer=join_compiler_job,
                    initargs=(self._compiler_job.name, self._compiler_job.owner_pid))
            except BaseException:
                self._compiler_job.close()
                raise

    def tile_center(self, position):
        return tuple(math.floor(value/self.grid+.5)*self.grid for value in _vector(position))

    def visibility_identity(self, center, area, streams, hour):
        if self.timed_collision_instances is None:
            return self.visibility_hours[hour]
        # Match query_world's exact cube/area/activation/static selection. A
        # timed light across the map cannot invalidate this tile's collision.
        return tuple(geometry.active_hour(window, hour)
            for instance_area, stream, conditional, low, high, window in self.timed_collision_instances
            if instance_area in (area, 13) and (not conditional or stream in streams)
            and all(a <= p+self.radius and b >= p-self.radius for a, b, p in zip(low, high, center)))

    def _collect(self):
        for key, future in tuple(self.pending.items()):
            if not future.done():
                continue
            del self.pending[key]
            self.pending_recipes.pop(key, None)
            try:
                result = future.result()
                if result["key"] != key or not isinstance(result["inputs"]["col"], bytes):
                    raise RegionFailed("Background tile result has a different identity")
                self.cache[key] = result
                self.cache.move_to_end(key)
            except Exception as error:
                self.failures[key] = f"{type(error).__name__}: {error}"
        while len(self.cache) > self.cache_entries or sum(len(row["inputs"]["col"]) for row in self.cache.values()) > self.cache_bytes:
            self.cache.popitem(last=False)
        while len(self.failures) > self.cache_entries:
            self.failures.popitem(last=False)

    def request(self, state, activation, *, center=None, priority=False, cached_only=False):
        """Return a ticket, or None when the bounded queue is already full."""
        if self._closed:
            raise RuntimeError("World stream is closed")
        scene, hour, streams, activation_hash = context(state, activation)
        center = self.tile_center(state["position"] if center is None else center)
        recipe = dict(format="gta-thaw-world-tile-v1", database_sha256=self.database_sha256,
                      center=center, origin=self.origin, radius=self.radius, area=state["area"],
                      hour=hour, active_streams=streams, primitive_policy=scene_export.TESSELLATE_SPHERES,
                      rail_cache_sha256=self.rail_cache_sha256)
        content = dict(recipe, hour=self.visibility_identity(center, state['area'], streams, hour))
        key = _sha(content)
        self._collect()
        if cached_only and key not in self.cache:
            return None
        if key in self.cache:
            # Required current/next-hour requests run before speculative work;
            # collecting completed forecasts must not evict that fresh demand.
            self.cache.move_to_end(key)
        if key not in self.pending and key not in self.cache and key not in self.failures:
            if priority and len(self.pending) >= self.max_pending:
                # A changed direction must not queue behind an unstarted far
                # forecast. Running compilers and nearby covering work stay.
                distant = sorted(self.pending, key=lambda pending: max(abs(a-b) for a, b in
                    zip(self.pending_recipes[pending]['center'], state['position'])), reverse=True)
                for pending in distant:
                    target = self.pending_recipes[pending]
                    outside = max(abs(a-b) for a, b in zip(target['center'], state['position'])) > self.radius-self.margin
                    obsolete = (target['area'] != recipe['area'] or target['active_streams'] != streams or
                                self.visibility_identity(target['center'], target['area'], streams, target['hour']) !=
                                self.visibility_identity(target['center'], target['area'], streams, hour))
                    if (outside or obsolete) and self.pending[pending].cancel():
                        del self.pending[pending]
                        del self.pending_recipes[pending]
                        break
            if len(self.pending) >= self.max_pending:
                return None
            self.pending[key] = self.executor.submit(_compile,
                dict(key=key, recipe=recipe, database=str(self.database), database_stamp=self.database_stamp,
                     rail_database=str(self.rail_database) if self.rail_cache_sha256 else None,
                     rail_database_stamp=self.rail_database_stamp))
            self.pending_recipes[key] = recipe
        return Ticket(key, scene, hour, streams, activation_hash, center, self.origin, self.radius, self.margin)

    def poll(self, ticket, state, activation, *, require_coverage=True, optional=False):
        """Only an empty optional forecast may be skipped; shared guards stay strict."""
        if self._closed:
            raise RuntimeError("World stream is closed")
        ticket.validate(state, activation, require_coverage=require_coverage)
        if _file_stamp(self.database) != self.database_stamp:
            raise RegionFailed("Geometry database changed after the stream was initialized")
        if self.rail_cache_sha256 and _file_stamp(self.rail_database) != self.rail_database_stamp:
            raise RegionFailed('Global rail cache changed after the stream was initialized')
        self._collect()
        if ticket.key in self.failures:
            if optional and self.failures[ticket.key] == 'ValueError: Selected scene has no supported collision surfaces':
                return None
            raise RegionFailed(self.failures[ticket.key])
        result = self.cache.get(ticket.key)
        if result is None:
            return None
        if (tuple(result["inputs"]["coordinates"]["origin_gta"]) != self.origin or
                tuple(result["inputs"]["selection"]["center_gta"]) != ticket.center):
            raise RegionFailed("Compiled tile coordinate frame does not match its ticket")
        self.cache.move_to_end(ticket.key)
        return result

    def covering(self, state, activation):
        """Reticket only ready matching content; never compile speculative fallback tiles."""
        self._collect()
        centers = [row["inputs"]["selection"]["center_gta"] for row in self.cache.values()]
        tickets = []
        for center in sorted(centers, key=lambda c: max(abs(a-b) for a,b in zip(c,state["position"]))):
            ticket = self.request(state,activation,center=center,cached_only=True)
            if ticket is not None and ticket.covers(state["position"]):
                tickets.append(ticket)
        return tickets

    def prefetch(self, state, activation, *, velocity=None, lookahead_seconds=6.0):
        """Prepare the first travel boundary and nearby turns before far travel."""
        if not math.isfinite(lookahead_seconds) or not 0 < lookahead_seconds <= 10:
            raise ValueError("Invalid prefetch horizon")
        position = _vector(state["position"])
        if velocity is None:
            velocity = tuple(v*geometry.GTA_STEPS_PER_SECOND for v in _vector(state["raw_gta_velocity"]))
        velocity = _vector(velocity)  # Explicit GTA metres/second if supplied.
        center = self.tile_center(position)
        targets = [center]
        speed = max(abs(v) for v in velocity)
        if speed > .25:
            elapsed = 0.
            for _ in range(self.max_pending+self.cache_entries):
                at = tuple(p+v*elapsed for p, v in zip(position, velocity))
                center = self.tile_center(at)
                crossings = [max(0., (c + math.copysign(self.grid/2, v) - p)/v)
                             for p, c, v in zip(at, center, velocity) if abs(v) > .01]
                elapsed += min(crossings) + .001
                # Always prepare the first boundary, including slow movement.
                if elapsed > lookahead_seconds and len(targets) > 1:
                    break
                targets.append(self.tile_center(tuple(p+v*elapsed for p, v in zip(position, velocity))))
        # Two workers must not both spend their next batches on distant straight
        # travel when the player can turn before those batches finish. Horizontal
        # neighbors also prewarm during idle/backend startup. Vertical travel
        # retains its first real boundary; it does not invent a horizontal heading.
        center = targets[0]
        horizontal = max(abs(velocity[0]), abs(velocity[1]))
        dominant = 0 if abs(velocity[0]) >= abs(velocity[1]) else 1
        axes = (1-dominant,) if horizontal > .25 else (0, 1)
        if horizontal > .25 and len(targets) > 1 and targets[1][dominant] != center[dominant]:
            center = targets[1]
        turns = [tuple(value + (sign*self.grid if axis == i else 0.)
                       for i, value in enumerate(center))
                 for axis in axes for sign in (1, -1)]
        # A start near one side of its tile has less time before that turn's
        # coverage edge. Give that side the first free worker, not a fixed sign.
        turns.sort(key=lambda target: sum((a-b)**2 for a,b in zip(target,position)))
        # Leave room for the still-published center and its next-hour variant,
        # which may differ from the nearest-grid center used for prediction.
        budget = max(1, self.cache_entries-2)
        targets = list(dict.fromkeys([*targets[:2], *turns, *targets[2:]]))[:budget]
        tickets = []
        for target in targets:
            ticket = self.request(state, activation, center=target)
            if ticket is not None:
                tickets.append(ticket)
        return tickets

    def close(self, *, wait=True):
        if self._closed:
            return
        self._closed = True
        for future in self.pending.values():
            future.cancel()
        try:
            self.executor.shutdown(wait=wait, cancel_futures=True)
        finally:
            if self._compiler_job is not None:
                self._compiler_job.close()
            self.pending.clear()
            self.pending_recipes.clear()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
