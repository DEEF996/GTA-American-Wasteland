"""Keep observed button edges while original 60 Hz frames are paced more slowly.

Snapshots remain simultaneous: separate taps are never ORed into a chord.
Queued gestures retain their observed direction, including diagonal aerials;
ordinary steering uses the latest axes as soon as that short queue is empty.
"""
from collections import deque
import math
import struct

from input_mapping import MappedInput
from probe_frame_input import validate_packet

MAX_PENDING = 8
MAX_AGE_SECONDS = .25


def _time(now):
    if isinstance(now, bool) or not isinstance(now, (int, float)) or not math.isfinite(now):
        raise ValueError('Input sample needs a finite host time')


class SlowMotionInput:
    def __init__(self):
        self.queue = deque()
        self.clear()

    def clear(self):
        self.queue.clear()
        self.latest = self.previous = self.context = self.last_time = None

    def _expire(self, now):
        # Do not retain the tail of an expired gesture: its press may already
        # have been dropped. Resume the actual held snapshot without replay.
        if self.queue and (now < self.queue[0][0] or now-self.queue[0][0] > MAX_AGE_SECONDS):
            self.queue.clear()

    def observe(self, mapped, now, *, scale, context):
        _time(now)
        if (isinstance(scale, bool) or not isinstance(scale, (int, float)) or
                not math.isfinite(scale) or not .2999 <= scale <= 1):
            raise ValueError('Input pacing scale is invalid')
        if (not isinstance(mapped, MappedInput) or type(mapped.recenter_camera) is not bool or
                type(mapped.skitch_requested) is not bool):
            raise ValueError('Input queue requires a complete mapped snapshot')
        validate_packet(mapped.packet)
        if (context != self.context or self.last_time is not None and
                (now < self.last_time or now-self.last_time > MAX_AGE_SECONDS)):
            self.clear()
        self.context, self.latest, self.last_time = context, mapped, now
        self._expire(now)
        signature = (struct.unpack_from('<I', mapped.packet, 0x24)[0], mapped.skitch_requested)
        if signature != self.previous and (scale < 1 or self.queue):
            if self.previous is not None or any(signature):
                if len(self.queue) == MAX_PENDING:
                    # Overload never releases J or invents a combined gesture.
                    # Drop the old backlog and retain only this real snapshot.
                    self.queue.clear()
                self.queue.append((now, mapped))
        self.previous = signature

    def consume(self, now):
        """Exactly once per NEW source frame, before camera-relative mapping."""
        _time(now)
        if self.latest is None:
            raise ValueError('Input must be observed after a pause/context change')
        self._expire(now)
        return self.queue.popleft()[1] if self.queue else self.latest
