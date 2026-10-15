"""Coordinate transient vehicle observations between original source frames.

Call poll only while the native worker is idle, after static rail configuration.
Its readiness is diagnostic: pending vehicles must never gate the skating frame.
"""
import os
import time

from vehicle_input import MAX_AGE_MS, VehicleBridge, VehicleInput


class _StageTimings:
    """Opt-in call costs, excluding the time between source frames."""
    def __init__(self, log):
        self.log, self.clock = log, time.perf_counter
        self.polls, self.active, self.stages = 0, None, {}
        self.last_ms = 0.0

    def begin(self, active):
        if self.polls >= 60 or self.active != bool(active):
            self.flush()
        self.active = bool(active)
        self.polls += 1

    def call(self, name, function, *args, **kwargs):
        start = self.clock()
        try:
            return function(*args, **kwargs)
        finally:
            milliseconds = (self.clock()-start)*1000
            self.last_ms = milliseconds
            row = self.stages.setdefault(name, [0, 0.0, 0.0])
            row[0] += 1
            row[1] += milliseconds
            row[2] = max(row[2], milliseconds)

    def flush(self):
        if self.polls or self.stages:
            self.log('vehicle_stage_timings', polls=self.polls, active=self.active,
                     stages={name: dict(samples=row[0], total_ms=row[1],
                                        mean_ms=row[1]/row[0], max_ms=row[2])
                             for name, row in self.stages.items()})
            self.polls, self.stages = 0, {}


class VehicleRuntime:
    def __init__(self, observer, world, origin, *, reader=None, bridge=None, clock=None, log=None):
        self.world = world
        self.reader = reader if reader is not None else VehicleInput(observer)
        try:
            self.bridge = bridge if bridge is not None else VehicleBridge(origin, maximum_shapes=24)
        except BaseException:
            self.reader.close()
            raise
        self.clock = clock if clock is not None else observer.api.GetTickCount64
        self.log = log if log is not None else lambda *args, **kwargs: None
        self._timings = _StageTimings(self.log) if os.environ.get('GTA_THAW_PROFILE_FRAMES') == '1' else None
        self.identity = self.last_tick = None
        self._sequence = None
        self._observed = self._pending = 0
        self._shape_failures = {}
        self._parked, self._retired, self._closed = True, False, False
        self._native = {}
        self._log_tick = self._log_signature = None
        self._contact = None

    def _check(self):
        if self._closed:
            raise RuntimeError('Vehicle runtime is closed')

    def _world_counts(self):
        # Local ownership dictionaries only: never read the original process.
        owners = getattr(self.world, 'vehicles', {})
        return dict(owners=len(owners), shapes=len(getattr(self.world, 'shapes', {})),
                    retired=sum(bool(v.get('retired')) for v in owners.values()),
                    parked=sum(bool(v.get('parked')) for v in owners.values()))

    def _profile_sync(self, prepared, state, active):
        before = self._world_counts()
        result = self._timings.call('world_sync', self.world.sync, prepared,
                                    allow_create=True, active=active)
        if self._timings.last_ms > 8:
            self.log('vehicle_world_sync_slow', milliseconds=self._timings.last_ms,
                     pid=state['pid'], state_sequence=state['sequence'], position=state.get('position'),
                     observation_sequence=prepared['sequence'], runtime_phase='active' if active else 'prewarm',
                     before=before, after=self._world_counts(), native=result)
        return result

    def _status(self, reason, *, ready=False, active=False):
        now = self.clock()
        result = dict(ready=ready, reason=reason, active=active, observed=self._observed,
                      pending_shapes=self._pending, shape_failures=self._shape_failures,
                      sequence=self._sequence, observation_age_ms=None if self.last_tick is None else now-self.last_tick,
                      native=self._native)
        signature = (reason, ready, active, self._observed, self._pending, len(self._shape_failures),
                     *(self._native.get(key) for key in ('active', 'retired', 'pending', 'created', 'ready')))
        # Never log positions/tick changes per frame. Changes are coalesced to
        # 100 ms; a stable active/prewarming service gets a one-second heartbeat.
        elapsed = None if self._log_tick is None else now-self._log_tick
        if (elapsed is None or elapsed < 0 or elapsed >= 1000 or
                elapsed >= 100 and signature != self._log_signature):
            self.log('vehicle_runtime_state', **result)
            self._log_tick, self._log_signature = now, signature
        return result

    def park(self):
        """Stop participation once; original owners decide when references can retire."""
        self._check()
        if not self._parked:
            self._native = self.world.park()
            self._parked = True
            self.log('vehicles_parked', native=self._native)
        self._contact_changed(None)
        return self._native

    def _contact_changed(self, contact, sample=None):
        signature = None if contact is None else (contact['contact'], contact['vehicle_id'])
        if signature != self._contact:
            self.log('vehicle_contact_changed',
                     source_frame=None if sample is None else sample['frame'], contact=contact)
            self._contact = signature

    def after_step(self, sample):
        """Observe a completed original frame; publish only owned vehicle IDs."""
        self._check()
        contact = None
        if not self._parked:
            native = self.world.observe_skitch(sample) or self.world.observe_rail(sample)
            if native is not None:
                # Manager/owner pointers remain inside the native ownership
                # checks. Moving transforms and rail-node progress are not log events.
                contact = {key: native[key] for key in
                           ('contact', 'vehicle_id', 'rail_name', 'native_node_index') if key in native}
        self._contact_changed(contact, sample)
        return contact

    def prepare_input(self, skitch_requested):
        """Gate new grabs after poll and before one NEW original gameplay step."""
        self._check()
        if type(skitch_requested) is not bool:
            raise ValueError('Skitch input must be boolean')
        if self._timings is None:
            self.world.set_skitch_input(skitch_requested and not self._parked)
        else:
            self._timings.call('prepare_input', self.world.set_skitch_input,
                               skitch_requested and not self._parked)

    def diagnostic_snapshot(self):
        """Read only for an already detected, bounded source discontinuity."""
        self._check()
        return dict(sequence=self._sequence,tick_ms=self.last_tick,
                    source_identity=self.identity,**self.world.diagnostic_snapshot())

    def _retire(self, state, now, reason):
        if not self._retired:
            empty = dict(source_identity=self.identity, sequence=state['sequence'], tick_ms=now,
                         ready=False, truncated=False, fault=False, dropped=0,
                         vehicles=[], shapes={}, pending_shapes=[], shape_failures={})
            # This is an explicit lease/lifetime retirement, not an invented
            # empty packet in response to a contended seqlock.
            self._native = self.world.sync(empty, allow_create=False, active=False)
            self._retired = self._parked = True
            self.last_tick = None
            self._sequence = None
            self._observed = self._pending = 0
            self._shape_failures = {}
            self._contact_changed(None)
            self.log('vehicles_retired', reason=reason, native=self._native)

    def poll(self, state, *, active=False):
        """Prewarm while J is off; update live owners before each NEW driver.step.

        Busy/stale read errors keep the last accepted owners until their real
        timestamp expires. Native sync/pin/worker failures deliberately escape.
        """
        self._check()
        if self._timings is not None:
            self._timings.begin(active)
        now = self.clock()
        identity = tuple(state[key] for key in ('pid', 'creation_100ns', 'epoch', 'area'))
        if identity != self.identity:
            previous, self.identity = self.identity, identity
            self._retired = False
            if previous is not None:
                self._retire(state, now, 'world_lifetime_changed')
        if not active:
            self.park()
        flags = state['flags']
        if (not state['control_eligible'] or not flags & 8 or flags & (2 | 4 | 32) or
                not 0 <= state['age_ms'] < MAX_AGE_MS):
            self.park()
            return self._status('gta_inactive')
        active = bool(active and not flags & 256)
        if not active:
            self.park()
        try:
            deadline = time.monotonic()+.002
            snapshot = (self.reader.read(state, deadline=deadline) if self._timings is None else
                        self._timings.call('snapshot_read', self.reader.read, state, deadline=deadline))
        except (OSError, ValueError) as error:
            now = self.clock()
            expired = self.last_tick is not None and (now < self.last_tick or now-self.last_tick >= MAX_AGE_MS)
            if expired:
                self._retire(state, now, 'observation_expired')
            result = self._status('observation_expired' if expired else 'observation_pending',
                                  ready=self.last_tick is not None and not self._retired,
                                  active=active and not self._parked)
            result['observation_error'] = str(error)
            return result
        now = self.clock()
        if tuple(snapshot['source_identity']) != self.identity:
            # VehicleInput normally prevents this; injected/replayed callers
            # must not be able to revive owners from a different epoch either.
            raise ValueError('Vehicle observation belongs to another world lifetime')
        if now < snapshot['tick_ms'] or now-snapshot['tick_ms'] >= MAX_AGE_MS:
            self._retire(state, now, 'observation_expired')
            return self._status('observation_expired')
        if not snapshot['ready']:
            self._retire(state, now, 'observation_unavailable')
            return self._status('observation_unavailable')
        self.last_tick = snapshot['tick_ms']
        self._sequence = snapshot['sequence']
        self._retired = False
        prepared = (self.bridge.update(snapshot) if self._timings is None else
                    self._timings.call('bridge_update', self.bridge.update, snapshot))
        # No catch here: a poisoned native worker or an ABI mismatch is not
        # ordinary asynchronous shape backpressure.
        self._native = (self.world.sync(prepared, allow_create=True, active=active) if self._timings is None else
                        self._profile_sync(prepared, state, active))
        self._parked = not active
        failures = prepared['shape_failures']
        pending = len(prepared['pending_shapes'])
        self._observed, self._pending, self._shape_failures = len(prepared['vehicles']), pending, failures
        ready = not failures and not pending and bool(self._native['ready'])
        return self._status('ready' if ready else 'vehicles_preparing', ready=ready, active=active)

    def close(self):
        """Park native participation and always stop this private compiler/reader."""
        if self._closed:
            return
        failure = None
        cleanups = [self.park, lambda: self.bridge.close(wait=False), self.reader.close]
        if self._timings is not None:
            cleanups.append(self._timings.flush)
        for cleanup in cleanups:
            try:
                cleanup()
            except BaseException as error:
                if failure is None:
                    failure = error
                else:
                    self.log('vehicles_cleanup_error', message=str(error), original_error=str(failure))
        self._closed = True
        if failure is not None:
            raise failure
