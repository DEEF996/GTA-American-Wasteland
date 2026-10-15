"""Coordinate private world tiles with the original THAW scene loader.

The caller must keep GTA's movement lease alive while ``ready`` is false and
must not step the source simulation beyond the current tile's safe coverage.
No movement packets, player writes, inputs or game launches happen here.
"""
from __future__ import annotations

import math
import time

from geometry import GTA_STEPS_PER_SECOND
from world_stream import WorldStream, StaleRegion, context, _vector
from probe_world import Rejected


class StreamedScene:
    def __init__(self, observer, database, *, origin=None, world=None, ipls=None,
                 clock=time.monotonic, sleeper=time.sleep):
        self.observer, self.clock, self.sleeper = observer, clock, sleeper
        self._owns_ipls = ipls is None
        if ipls is None:
            from inspect_ipls import IplObserver
            ipls = IplObserver(observer, database)
        self.ipls = ipls
        try:
            # A fixed frame lets validated map tiles survive process restarts.
            self.world = world or WorldStream(database,
                origin=(0., 0., 0.) if origin is None else origin)
        except BaseException:
            if self._owns_ipls:
                self.ipls.close()
            raise
        self.current_ticket = self.current_inputs = self.native = None
        self.pending_ticket = self.pending_result = None
        self.log = None
        self._reported_failures = {}
        self.source_frame = 0
        self.closed = False

    def _environment(self, state=None, position=None):
        before = self.observer.read() if state is None else state
        for _ in range(2):
            try:
                activation = self.ipls.read()
                break
            except Rejected as error:
                if str(error) not in ('ipl_changed_during_read', 'ipl_load_transition'):
                    raise
        else:
            raise StaleRegion('Conditional IPL load state is transitioning')
        after = self.observer.read()
        if context(before, activation) != context(after, activation):
            raise StaleRegion("GTA scene changed during IPL inspection")
        if (after.get("sequence", 0) < before.get("sequence", 0) or
                after.get("game_timer", 0) < before.get("game_timer", 0)):
            raise StaleRegion("GTA timeline changed during IPL inspection")
        selected = dict(after)
        if position is not None:
            selected["position"] = list(position)
        context(selected, activation)
        return selected, activation

    def initial(self, *, timeout=60.):
        """Compile and validate the initial tile before acquiring GTA ownership."""
        if self.closed or self.current_inputs is not None:
            raise RuntimeError("Initial geometry can only be prepared once")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Invalid initial world preparation timeout")
        until = self.clock() + timeout
        while self.clock() < until:
            try:
                state, activation = self._environment()
                ticket = self.world.request(state, activation)
                result = self.world.poll(ticket, state, activation) if ticket is not None else None
                if result is not None:
                    self.current_ticket, self.current_inputs = ticket, result["inputs"]
                    minute = state.get("clock_minute", 0)
                    if type(minute) is int and 55 <= minute <= 59:
                        self.world.request(self._next_hour(state), activation, center=ticket.center)
                    self.world.prefetch(state, activation)
                    return self.current_inputs
            except StaleRegion:
                pass
            self.sleeper(.01)
        raise TimeoutError("Current GTA collision region was not ready before the initial deadline")

    def bind(self, native):
        if self.current_inputs is None or self.native is not None:
            raise RuntimeError("Prepare initial geometry once before binding its native scene")
        if tuple(native.origin) != tuple(self.current_inputs["coordinates"]["origin_gta"]):
            raise ValueError("Native scene changed the stable GTA coordinate origin")
        self.native = native

    def bind_native(self, api, process, record, deadline, log, current, *, context, global_data):
        from probe_scene_stream import SceneStream
        self.log = log
        self.bind(SceneStream(api, process, record, deadline, log, current, self.current_inputs,
                              context=context, global_data=global_data))

    @staticmethod
    def _matches(ticket, state, activation, *, coverage=True):
        if ticket is None:
            return False
        try:
            ticket.validate(state, activation, require_coverage=coverage)
            return True
        except StaleRegion:
            return False

    @staticmethod
    def _next_hour(state):
        following = dict(state)
        following["clock_hour"] = (state["clock_hour"]+1) % 24
        return following

    def _discard_pending(self):
        if self.pending_ticket is not None:
            self.native.discard_pending()
            self.pending_ticket = self.pending_result = None

    def _reticket(self, ticket, inputs):
        self.current_ticket, self.current_inputs = ticket, inputs

    def _status(self, state, *, ready, swapped=False, reason=None, timings=()):
        return dict(ready=ready, swapped=swapped, current_key=self.current_ticket.key,
                    pending_key=self.pending_ticket.key if self.pending_ticket else None,
                    stage=self.native.stage, state=state, reason=reason,
                    origin=self.world.origin, inputs=self.current_inputs,
                    needs_source_frame=False,
                    step_timing_ms=list(timings))

    def update(self, state=None, *, position=None, active=False, source_frame=0):
        """Prewarm one native stage, or a bounded batch while already buffering.

        ``position`` is the proposed GTA-space root position after a source step;
        it uses the same session/clock/IPL identity as the fresh game snapshot.
        Call before stepping too. A False ready value requests buffering while
        geometry prepares, never an implicit F7 toggle or a stale-geometry grace.
        Pass the completed original frame count in ``source_frame``. Native
        retirement retains referenced predecessors within its bounded budget;
        it does not require an artificial simulation frame while GTA is idle.
        """
        if self.closed or self.native is None:
            raise RuntimeError("Streamed scene is closed or not bound to the native engine")
        if type(source_frame) is not int or source_frame < self.source_frame:
            raise ValueError("Original source frame counter must be monotonic")
        self.source_frame = source_frame
        step_timing = []

        def native_step(stage, call):
            started = self.clock()
            result = call()
            step_timing.append(dict(stage=stage, milliseconds=(self.clock()-started)*1000))
            return result

        # Area/clock changes can straddle these independent read-only snapshots.
        # Buffer that normal transition; malformed guards still raise an error.
        try:
            state, activation = self._environment(state, position)
        except StaleRegion:
            return self._status(self.observer.read(), ready=False, reason="scene_transition")
        current_ok = self._matches(self.current_ticket, state, activation)
        moved = max(abs(a-b) for a, b in zip(state["position"], self.current_ticket.center))
        # Reserve staging time before the strict coverage edge, even at a 64m grid.
        swap_distance = .75*min(self.world.grid, self.world.radius-self.world.margin)
        needs_swap = not current_ok or moved >= swap_distance

        def nearer(ticket):
            # A covering forecast can still be farther away than the current
            # tile after a turn. Publishing it would immediately request a swap back.
            return max(abs(a-b) for a, b in zip(state["position"], ticket.center)) < moved

        moving = max(abs(v) for v in _vector(state["raw_gta_velocity"])) * GTA_STEPS_PER_SECOND > .25
        # Keep the already prepared center when only time/session identity has
        # changed. Rounding the player's position again can select a different
        # tile and bypass the next-hour result prepared for this exact scene.
        center = self.current_ticket.center if moved < swap_distance else None
        desired = self.world.request(state, activation, center=center, priority=True)
        following = self._next_hour(state)
        next_hour = None
        minute = state.get("clock_minute", 0)
        # Reserve compiler capacity for the clock boundary before speculative
        # travel, while the current scene still safely covers the player.
        if type(minute) is int and 55 <= minute <= 59:
            center = self.current_ticket.center if current_ok else None
            next_hour = self.world.request(following, activation, center=center)
        warmed = self.world.prefetch(state, activation)
        # Idle prewarm fills only the bounded compiler cache. Native staging
        # remains for actual movement or the required next-hour scene.
        forecasts = (warmed if moving or moved >= swap_distance else
                     [desired] if desired is not None else [])

        # A published successor cannot be cancelled, even after a teleport or
        # IPL transition. Old native references must first pass retirement.
        retiring = self.native.stage == "retire"
        if retiring:
            if not native_step("retire", self.native.advance):
                raise RuntimeError("Native scene retirement did not complete")
            self.pending_ticket = self.pending_result = None

        # Session/hour/IPL changes may occur while a different tile is staged.
        # The next raw hour is the only permitted future identity.
        if self.pending_ticket is not None and not retiring:
            valid_now = self._matches(self.pending_ticket, state, activation, coverage=False)
            valid_future = next_hour is not None and self._matches(
                self.pending_ticket, following, activation, coverage=False)
            relevant = {ticket.key for ticket in forecasts}
            if desired is not None:
                relevant.add(desired.key)
            if next_hour is not None:
                relevant.add(next_hour.key)
            spatial_priority = (needs_swap and desired is not None and
                                not self._matches(self.pending_ticket, state, activation))
            covers_now = valid_now and self.pending_ticket.covers(state['position'])
            farther_forecast = current_ok and needs_swap and valid_now and not nearer(self.pending_ticket)
            if (not (valid_now or valid_future) or
                    (self.pending_ticket.key not in relevant and not covers_now) or
                    spatial_priority or farther_forecast):
                native_step("discard", self._discard_pending)

        if self.pending_ticket is None and not step_timing:
            choices = []
            if needs_swap and desired is not None:
                choices.append((desired, state))
            if next_hour is not None and not needs_swap:
                choices.append((next_hour, following))
            choices.extend((ticket, state) for ticket in forecasts)
            if needs_swap:
                # A turn may leave the latest forecast while a previously
                # compiled neighbor still safely covers the current player.
                choices.extend((ticket, state) for ticket in self.world.covering(state,activation))
            if next_hour is not None and needs_swap:
                choices.append((next_hour, following))
            seen = set()
            for ticket, expected in choices:
                if ticket.key in seen or (ticket.key == self.current_ticket.key and current_ok):
                    continue
                seen.add(ticket.key)
                if needs_swap and not ticket.covers(state["position"]):
                    continue
                if (current_ok and needs_swap and
                        self._matches(ticket, state, activation, coverage=False) and not nearer(ticket)):
                    continue
                optional = current_ok and (desired is None or ticket.key != desired.key)
                result = self.world.poll(ticket, expected, activation, require_coverage=False,
                                         optional=optional)
                if result is None:
                    failure = self.world.failures.get(ticket.key) if optional else None
                    if failure is not None and self._reported_failures.get(ticket.key) != failure:
                        if self.log is not None:
                            self.log('world_forecast_failed', key=ticket.key, center=ticket.center,
                                hour=ticket.hour, reason=failure, current_key=self.current_ticket.key,
                                current_center=self.current_ticket.center, position=state['position'],
                                current_ready=True)
                        self._reported_failures[ticket.key] = failure
                        while len(self._reported_failures) > self.world.cache_entries:
                            self._reported_failures.pop(next(iter(self._reported_failures)))
                    continue
                if result["inputs"]["col_sha256"] == self.current_inputs["col_sha256"]:
                    # This exact COL is already published. A new ticket proves
                    # the new time/activation selection has identical geometry.
                    if self._matches(ticket, state, activation):
                        self._reticket(ticket, result["inputs"])
                        current_ok = True
                        break
                    continue
                self.native.begin(result["inputs"])
                self.pending_ticket, self.pending_result = ticket, result
                break

        swapped = False
        if self.pending_ticket is not None and self.native.stage != "retire" and not step_timing:
            # Once buffered, several sub-millisecond loader/query stages can
            # finish in one host frame. Retirement remains a separate stage.
            batch_started = self.clock()
            for _ in range(4 if not current_ok else 1):
                committing = self.native.stage == "commit"
                permitted = (self._matches(self.pending_ticket, state, activation) and needs_swap and
                             (not current_ok or nearer(self.pending_ticket)))
                if committing and not permitted:
                    break
                stage = self.native.stage
                native_step(stage, self.native.advance)
                if stage == "commit":
                    if self.native.stage != "retire":
                        raise RuntimeError("Native publication did not enter retirement")
                    self._reticket(self.pending_ticket, self.pending_result["inputs"])
                    swapped = True
                    break
                if self.clock()-batch_started >= .004:
                    break

        # A native loader stage takes measurable time. Verify the scene again
        # before its result can be used by the next source frame.
        try:
            latest, activation_after = self._environment(position=position)
        except StaleRegion:
            return self._status(self.observer.read(), ready=False, swapped=swapped,
                                reason="scene_transition", timings=step_timing)
        ready = self._matches(self.current_ticket, latest, activation_after)
        return self._status(latest, ready=ready, swapped=swapped,
                            reason=None if ready else "current_region_preparing", timings=step_timing)

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.world.close()
        if self._owns_ipls:
            self.ipls.close()
