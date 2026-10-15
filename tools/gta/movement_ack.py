"""Read GTA's applied MOVE acknowledgement; this never publishes a command."""
import ctypes as C
import struct
import time
from native.inspect_state import SnapshotBusy, read_mapping_body

BODY = struct.Struct('<8s4I8Q8I')
MAPPING_SIZE = BODY.size + 8
assert BODY.size == 120 and MAPPING_SIZE == 128
ACTIVE, TRAJECTORY, PREPARING = 1, 2, 4


def decode(body, pid, created, now_ms):
    if len(body) != BODY.size:
        raise ValueError('Invalid movement acknowledgement length')
    fields = BODY.unpack(body)
    magic, version, size, owner, flags, creation, sequence, tick = fields[:8]
    if (magic, version, size, owner, creation) != (b'GTAMACK1', 1, BODY.size, pid, created) or not pid or not created:
        raise ValueError('Movement acknowledgement belongs to another protocol/process lifetime')
    if not sequence or tick > now_ms or now_ms-tick > 100:
        raise ValueError('Movement acknowledgement is not fresh')
    request, applied, source_tick, source_us, generation = fields[8:13]
    ped, epoch, area, ped_area, game_timer, frame, entry_f7, f7 = fields[13:]
    if flags & ~7 or max(area, ped_area) > 255 or applied >= 0x3fffffff:
        raise ValueError('Invalid movement acknowledgement state')
    if flags & ACTIVE and (not ped or not epoch or not request or area != ped_area):
        raise ValueError('Active movement acknowledgement lacks its player identity')
    if flags & PREPARING and not flags & ACTIVE:
        raise ValueError('Inactive movement cannot be preparing')
    if flags & (ACTIVE | TRAJECTORY | PREPARING) == ACTIVE | TRAJECTORY:
        if not applied or not generation or (source_tick == 0) != (source_us == 0):
            raise ValueError('Applied trajectory acknowledgement lacks its source identity')
    return dict(protocol='GTAMACK1', pid=pid, creation_100ns=creation, sequence=sequence,
        tick_ms=tick, age_ms=now_ms-tick, request=request, applied_sequence=applied,
        source_tick=source_tick, source_time_us=source_us, collision_generation=generation,
        active=bool(flags & ACTIVE), trajectory=bool(flags & TRAJECTORY), preparing=bool(flags & PREPARING),
        ped_address=ped, epoch=epoch, area=area, ped_area=ped_area, game_timer=game_timer, frame_counter=frame,
        entry_f7_requests=entry_f7, f7_requests=f7)


def matches(ack, state, request, sequence, sample, generation):
    """Exact acceptance is necessary before replacing a normal packet by a buffer."""
    return (ack['active'] and ack['trajectory'] and not ack['preparing']
        and 0 <= ack['age_ms'] <= 100 and ack['protocol'] == 'GTAMACK1'
        and ack['entry_f7_requests'] == ack['f7_requests'] == state['f7_requests']
        and all(ack[name] == state[name] for name in
                ('pid', 'creation_100ns', 'ped_address', 'epoch', 'area', 'ped_area'))
        and (ack['request'], ack['applied_sequence'], ack['source_tick'], ack['source_time_us'],
             ack['collision_generation']) ==
            (request, sequence, sample['source_tick'], sample['source_time_us'], generation))


class MovementAck:
    """Borrow a verified Observer's process/API; own only a read-only mapping view."""
    def __init__(self, observer):
        self.observer, self.api = observer, observer.api
        self.mapping = self.view = None
        try:
            self.mapping = self.api.OpenFileMappingW(4, False, f'Local\\THAW_GTA_MOVE_ACK_V1_{observer.pid}')
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
                                 'movement acknowledgement', deadline=deadline)
        return decode(body, self.observer.pid, self.observer.created, self.api.GetTickCount64())

    def wait_for(self, request, sequence, sample, *, generation, timeout=.05):
        """Wait at most 50 ms for this packet; retain the normal packet meanwhile."""
        if not 0 < timeout <= .05:
            raise ValueError('Movement acknowledgement wait must be at most 50 ms')
        baseline = None
        fields = ('pid', 'creation_100ns', 'ped_address', 'epoch', 'area', 'ped_area', 'f7_requests')
        until = time.monotonic()+timeout
        while True:
            try:
                if baseline is None:
                    baseline = self.observer.read(deadline=until)
                state = self.observer.read(deadline=until)
                ack = self.read(deadline=until)
            except SnapshotBusy:
                if time.monotonic() >= until:
                    raise TimeoutError('GTA has not published a complete movement acceptance snapshot') from None
                continue
            if (any(state[name] != baseline[name] for name in fields)
                    or not state['control_eligible'] or state['flags'] & (8 | 64 | 256) != 8 | 64
                    or not 0 <= state['age_ms'] <= 100):
                raise ValueError('GTA ownership, scene or F7 changed while awaiting movement acceptance')
            if ack['entry_f7_requests'] != ack['f7_requests'] or ack['f7_requests'] != baseline['f7_requests']:
                raise ValueError('F7 changed before movement acknowledgement')
            if matches(ack, state, request, sequence, sample, generation):
                return ack
            if (not ack['active'] or ack['request'] != request or ack['applied_sequence'] > sequence):
                raise ValueError('Movement request was released or superseded before acknowledgement')
            if time.monotonic() >= until:
                raise TimeoutError('GTA has not acknowledged the exact movement sample')
            time.sleep(.001)

    def close(self):
        if self.view:
            self.api.UnmapViewOfFile(self.view)
            self.view = None
        if self.mapping:
            self.api.CloseHandle(self.mapping)
            self.mapping = None
