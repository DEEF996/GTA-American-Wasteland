"""Publish preparation/readiness independently of a gameplay lease."""
import struct

from preview_pose import Publisher

BODY = struct.Struct('<8s4I5Q')
assert BODY.size == 64
HIDDEN, PREPARING, READY, STARTING_GAMEPLAY = range(4)


class StartupStatus:
    def __init__(self, observer):
        self.observer = observer
        self.transport = Publisher(observer, transport='STATUS_V1', packet_size=BODY.size)
        self.request = max(1, observer.api.GetTickCount64())
        self.sequence = 0
        self.kind = HIDDEN
        self.since_ms = observer.api.GetTickCount64()

    def update(self, phase):
        kind = (READY if phase == 'ready' else PREPARING if phase == 'loading' else
            STARTING_GAMEPLAY if phase in ('starting_gameplay', 'restarting') else HIDDEN)
        now = self.observer.api.GetTickCount64()
        if kind != self.kind:
            self.kind, self.since_ms = kind, now
        self.sequence += 1
        body = BODY.pack(b'GTASTAT1', 1, BODY.size, self.observer.pid, kind,
            self.observer.created, self.request, self.sequence, now, self.since_ms)
        self.transport.publish(body, self.sequence)

    def close(self):
        self.transport.close()


def connect(observer):
    try:
        return StartupStatus(observer)
    except OSError as error:
        if getattr(error, 'winerror', None) != 2:
            raise
        return None  # Older ASI: optional status display must not disable gameplay.
