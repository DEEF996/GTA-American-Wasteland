"""Forward genuine stock board-smack edges under the active GTA movement lease."""
import struct

from preview_gameplay_move import require, scene
from preview_pose import Publisher

BODY = struct.Struct('<8s4I4Q4I3Q2I')
assert BODY.size == 104


def packet(state, identity, request, sequence, now_ms, source_tick, source_time_us,
           attack_id, whacking, walking):
    require(scene(state) == identity, 'Board attack scene changed')
    require(all(type(v) is int for v in (request, sequence, now_ms, source_tick, source_time_us, attack_id))
            and 0 < request < 2**64 and 0 < sequence < 0x3fffffff and 0 <= now_ms < 2**64
            and 0 < source_tick < 2**64 and 0 < source_time_us < 2**64
            and 0 <= attack_id < 2**64 and type(whacking) is bool and type(walking) is bool,
            'Invalid original attack state, clock or lease')
    return BODY.pack(b'GTAHIT01', 1, BODY.size, state['pid'], state['epoch'],
        state['creation_100ns'], request, sequence, now_ms, state['ped_address'], state['area'],
        state['ped_area'], 0, source_tick, source_time_us, attack_id, int(whacking), int(walking))


class AttackPublisher:
    def __init__(self, observer):
        self.observer = observer
        self.transport = Publisher(observer, transport='ATTACK_V1', packet_size=BODY.size)
        self.sequence = self.attack_id = 0
        self.request = None
        self.last_clock = None
        self.was_whacking = True

    def publish(self, state, identity, request, source_tick, source_time_us, whacking, *, walking=True):
        require(type(whacking) is bool and type(walking) is bool, 'Original attack flags must be booleans')
        if self.request != request:
            self.request, self.attack_id, self.last_clock, self.was_whacking = request, 0, None, True
        clock = (source_tick, source_time_us)
        if self.last_clock is not None:
            require(clock[0] > self.last_clock[0] and clock[1] > self.last_clock[1],
                    'Board attack must accompany a new original sample')
        if whacking and not self.was_whacking and walking:
            self.attack_id += 1
        sequence = self.sequence+1
        raw = packet(state, identity, request, sequence, self.observer.api.GetTickCount64(),
                     source_tick, source_time_us, self.attack_id, whacking, walking)
        self.transport.publish(raw, sequence)
        self.sequence, self.last_clock, self.was_whacking = sequence, clock, whacking

    def clear(self):
        self.transport.clear()
        # F8 pauses publication without changing the movement lease. Keep its
        # monotonic IDs/clocks, but require a released board before another swing.
        self.was_whacking = True

    def close(self):
        self.transport.close()
