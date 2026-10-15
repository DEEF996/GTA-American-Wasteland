"""Read the native aiming direction for walking input; never publish a command.

The adapter writes this seqlock every GTA tick. A stale, unavailable or foreign
sample must leave original input unchanged; it is not a reason to release J.
"""
import ctypes as C
import math
import struct

from native.inspect_state import read_mapping_body

BODY = struct.Struct('<8s4I4Q6I3fI')
MAPPING_SIZE = BODY.size + 8
assert BODY.size == 96 and MAPPING_SIZE == 104
ACTIVE, MAX_AGE_MS = 1, 100


def decode(body, pid, created, now_ms):
    if len(body) != BODY.size:
        raise ValueError('Invalid aim input length')
    fields = BODY.unpack(body)
    magic, version, size, owner, flags, creation, sequence, tick, request = fields[:9]
    ped, epoch, area, ped_area, entry_toggle, toggle = fields[9:15]
    forward, reserved = fields[15:18], fields[18]
    if (magic, version, size, owner, creation) != (b'GTAAIM01', 1, BODY.size, pid, created) or not pid or not created:
        raise ValueError('Aim input belongs to another protocol/process lifetime')
    if not sequence or tick > now_ms or now_ms - tick > MAX_AGE_MS:
        raise ValueError('Aim input is not fresh')
    if flags & ~ACTIVE or reserved or max(area, ped_area) > 255:
        raise ValueError('Invalid aim input state')
    active = bool(flags & ACTIVE)
    if active:
        if not request or not ped or not epoch or area != ped_area or entry_toggle != toggle:
            raise ValueError('Active aim input lacks matching movement identity')
        if (not all(math.isfinite(value) for value in forward) or
                abs(sum(value * value for value in forward) - 1) > .005 or
                forward[0] ** 2 + forward[1] ** 2 <= 1e-6):
            raise ValueError('Aim input has an invalid direction')
    elif request or any(value != 0 for value in forward):
        raise ValueError('Inactive aim input retained a direction or request')
    return dict(protocol='GTAAIM01', pid=pid, creation_100ns=creation, sequence=sequence,
                tick_ms=tick, age_ms=now_ms-tick, active=active, request=request,
                ped_address=ped, epoch=epoch, area=area, ped_area=ped_area,
                entry_f7_requests=entry_toggle, f7_requests=toggle, forward_gta=forward)


def matches(aim, state, request):
    """An aiming observation applies only to the currently owned walking input."""
    return (aim['protocol'] == 'GTAAIM01' and aim['active'] and aim['request'] == request and
            request > 0 and 0 <= aim['age_ms'] <= MAX_AGE_MS and
            state['control_eligible'] and 0 <= state['age_ms'] <= MAX_AGE_MS and
            state['flags'] & (8 | 64 | 256) == 8 | 64 and
            aim['entry_f7_requests'] == aim['f7_requests'] == state['f7_requests'] and
            all(aim[key] == state[key] for key in
                ('pid', 'creation_100ns', 'ped_address', 'epoch', 'area', 'ped_area')))


class AimInput:
    """Borrow Observer's process/API and own only this read-only mapping view."""
    def __init__(self, observer):
        self.observer, self.api = observer, observer.api
        self.mapping = self.view = None
        try:
            self.mapping = self.api.OpenFileMappingW(4, False, f'Local\\THAW_GTA_AIM_V1_{observer.pid}')
            if not self.mapping:
                raise C.WinError(C.get_last_error())
            self.view = self.api.MapViewOfFile(self.mapping, 4, 0, 0, MAPPING_SIZE)
            if not self.view:
                raise C.WinError(C.get_last_error())
        except BaseException:
            self.close()
            raise

    def read(self, *, deadline=None):
        body = read_mapping_body(self.view, BODY.size, self.api, self.observer.process,
                                 'aim input', deadline=deadline)
        return decode(body, self.observer.pid, self.observer.created, self.api.GetTickCount64())

    def close(self):
        if self.view:
            self.api.UnmapViewOfFile(self.view)
            self.view = None
        if self.mapping:
            self.api.CloseHandle(self.mapping)
            self.mapping = None
