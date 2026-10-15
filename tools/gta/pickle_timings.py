"""Scoped, opt-in observation of the backend's multiprocessing serialization."""
from collections import deque
import threading
import time


class PickleTimings:
    def __init__(self, *, pickler=None, clock=time.perf_counter, cpu_clock=time.thread_time):
        if pickler is None:
            from multiprocessing.reduction import ForkingPickler
            pickler = ForkingPickler
        self.pickler, self.clock, self.cpu_clock = pickler, clock, cpu_clock
        self.pending, self.active = deque(maxlen=64), True
        self.original = {name: vars(pickler)[name] for name in ('loads', 'dumps')}
        original_loads = pickler.loads
        original_dumps = self.original['dumps']

        def loads(data, *args, **kwargs):
            return self._call('loads', original_loads, data, args, kwargs)

        def dumps(cls, obj, *args, **kwargs):
            return self._call('dumps', original_dumps.__get__(None, cls), obj, args, kwargs)

        self.replacements = dict(loads=staticmethod(loads), dumps=classmethod(dumps))
        for name, descriptor in self.replacements.items():
            setattr(pickler, name, descriptor)

    def _call(self, operation, function, value, args, kwargs):
        started, cpu_started = self.clock(), self.cpu_clock()
        result, failed = None, True
        try:
            result = function(value, *args, **kwargs)
            failed = False
            return result
        finally:
            ended, cpu_ended = self.clock(), self.cpu_clock()
            if self.active and ended-started >= .002:
                payload = value if operation == 'loads' else result
                size = (payload.nbytes if isinstance(payload, memoryview) else len(payload)
                        if isinstance(payload, (bytes, bytearray)) else None)
                thread = threading.current_thread()
                self.pending.append(dict(operation=operation, start_qpc_s=started, end_qpc_s=ended,
                    milliseconds=(ended-started)*1000, thread_cpu_ms=(cpu_ended-cpu_started)*1000,
                    thread_id=threading.get_native_id(), thread_name=thread.name,
                    thread_class=type(thread).__name__, byte_count=size, failed=failed))

    def flush(self, log):
        # No payload inspection, disk I/O or log callbacks on manager/feeder threads.
        while self.pending:
            log('live_pickle_timing', **self.pending.popleft())

    def close(self):
        self.active = False
        for name, replacement in self.replacements.items():
            if vars(self.pickler).get(name) is replacement:
                setattr(self.pickler, name, self.original[name])
