"""Read GTA-owned audio/time controls; pace complete original 60 Hz frames."""
import ctypes as C
import math
import struct
import time

from native.inspect_state import read_mapping_body

BODY = struct.Struct('<8s4I4Q6IQ3fI')
MAPPING_SIZE = BODY.size + 8
assert BODY.size == 104 and MAPPING_SIZE == 112
LEASE, SLOW, EXITING = 1, 2, 4
MAX_AGE_MS = 100


def decode(body, pid, created, now_ms):
    if len(body) != BODY.size:
        raise ValueError('Invalid game effects length')
    fields = BODY.unpack(body)
    magic, version, size, owner, flags, creation, sequence, tick, request = fields[:9]
    ped, epoch, area, ped_area, entry_toggle, toggle = fields[9:15]
    event_sequence, scale, effect, volume, event_kind = fields[15:]
    if (magic, version, size, owner, creation) != (b'GTAFX001', 1, BODY.size, pid, created) or not pid or not created:
        raise ValueError('Game effects belong to another protocol/process lifetime')
    if not sequence or tick > now_ms or now_ms-tick > MAX_AGE_MS:
        raise ValueError('Game effects are not fresh')
    if (flags & ~(LEASE | SLOW | EXITING) or max(area, ped_area) > 255 or
            not all(math.isfinite(v) for v in (scale, effect, volume)) or
            not .2999 <= scale <= 1 or not 0 <= effect <= 1 or not 0 <= volume <= 1 or
            event_kind not in (0, 1, 2, 3) or bool(event_sequence) != bool(event_kind)):
        raise ValueError('Invalid game effects controls')
    if flags & LEASE:
        if not request or not ped or not epoch or area != ped_area or entry_toggle != toggle:
            raise ValueError('Game effects lack movement ownership')
    elif request or flags & (SLOW | EXITING):
        raise ValueError('Inactive game effects retained ownership')
    if (flags & EXITING and not flags & SLOW or
            not flags & SLOW and (scale != 1 or effect != 0) or
            abs(scale-(1-.7*effect)) > .0001):
        raise ValueError('Game effects scale disagrees with its transition')
    if flags & SLOW and (event_kind not in (1, 2) or bool(flags & EXITING) != (event_kind == 2)):
        raise ValueError('Game effects event disagrees with its transition')
    return dict(pid=pid, creation_100ns=creation, sequence=sequence, tick_ms=tick,
                age_ms=now_ms-tick, request=request, ped_address=ped, epoch=epoch,
                area=area, ped_area=ped_area, entry_f7_requests=entry_toggle, f7_requests=toggle,
                active=bool(flags & LEASE), slow=bool(flags & SLOW), exiting=bool(flags & EXITING),
                event_sequence=event_sequence, event_kind=event_kind,
                time_scale=scale, effect_amount=effect, sfx_volume=volume)


def matches(effect, state, request):
    return (effect['active'] and effect['request'] == request and request > 0 and
            0 <= effect['age_ms'] <= MAX_AGE_MS and state['control_eligible'] and
            0 <= state['age_ms'] <= MAX_AGE_MS and
            state['flags'] & (8 | 64 | 256) == 8 | 64 and
            effect['entry_f7_requests'] == effect['f7_requests'] == state['f7_requests'] and
            all(effect[key] == state[key] for key in
                ('pid', 'creation_100ns', 'ped_address', 'epoch', 'area', 'ped_area')))


class EffectsInput:
    def __init__(self, observer):
        self.observer, self.api = observer, observer.api
        self.mapping = self.view = None
        try:
            self.mapping = self.api.OpenFileMappingW(4, False, f'Local\\THAW_GTA_EFFECTS_V1_{observer.pid}')
            if not self.mapping:
                # An older installed adapter remains usable until its next restart.
                if C.get_last_error() == 2:
                    return
                raise C.WinError(C.get_last_error())
            self.view = self.api.MapViewOfFile(self.mapping, 4, 0, 0, MAPPING_SIZE)
            if not self.view:
                raise C.WinError(C.get_last_error())
        except BaseException:
            self.close()
            raise

    @property
    def available(self):
        return bool(self.view)

    def current(self, state, request):
        if not self.view:
            return None
        try:
            body = read_mapping_body(self.view, BODY.size, self.api, self.observer.process,
                                     'game effects', deadline=time.monotonic()+.001)
            effect = decode(body, self.observer.pid, self.observer.created, self.api.GetTickCount64())
            return effect if matches(effect, state, request) else None
        except (OSError, ValueError):
            return None  # A stale effect never tears down a healthy original engine.

    def close(self):
        if self.view:
            self.api.UnmapViewOfFile(self.view)
            self.view = None
        if self.mapping:
            self.api.CloseHandle(self.mapping)
            self.mapping = None


class SourcePacer:
    """Slow the original clock as a whole, without changing its physics delta.

    Host input/transport continues at 60 Hz. Fractional credit preserves an
    18 Hz source at scale .3 despite the host's coarser polling cadence.
    """
    PERIOD = 1/60

    def __init__(self):
        self.last = None
        self.credit = 0.

    def pause(self, now):
        self.last, self.credit = now, 0.

    def due(self, now, scale):
        if not math.isfinite(now) or not math.isfinite(scale) or not .2999 <= scale <= 1:
            raise ValueError('Invalid source pacing time/scale')
        elapsed = 0 if self.last is None else now-self.last
        first = self.last is None
        self.last = now
        if scale == 1 or first or elapsed < 0 or elapsed > .1:
            self.credit = 0.
            return True  # A stall permits one step, never a catch-up burst.
        self.credit += elapsed*scale
        if self.credit + 1e-9 < self.PERIOD:
            return False
        self.credit = max(0., self.credit-self.PERIOD) % self.PERIOD
        return True
