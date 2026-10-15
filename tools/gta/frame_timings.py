"""Opt-in aggregate stage timings; no gameplay clock or per-frame disk writes."""
from collections import deque
import gc
import os
import threading
import time


class FrameTimings:
    def __init__(self, log, *, enabled=None, clock=time.perf_counter, gc_callbacks=None, native_worker=None):
        self.enabled = os.environ.get('GTA_THAW_PROFILE_FRAMES') == '1' if enabled is None else enabled
        self.log, self.clock = log, clock
        self.native_worker = native_worker
        self.started = self.last = None
        self.current, self.stages = {}, {}
        self.frames = 0
        self.gc_callbacks = gc.callbacks if gc_callbacks is None else gc_callbacks
        self.gc_started, self.gc_pending = None, deque(maxlen=64)
        self.gc_callback = self._collection
        self.pickle_timings = None
        if self.enabled:
            from pickle_timings import PickleTimings
            self.pickle_timings = PickleTimings()
            self.gc_callbacks.append(self.gc_callback)

    def _collection(self, phase, info):
        # Never format/write logs inside a collection or keep collected objects alive.
        if phase == 'start':
            self.gc_started = self.clock(), time.thread_time(), threading.get_native_id()
        elif phase == 'stop' and self.gc_started is not None:
            ended, cpu_ended = self.clock(), time.thread_time()
            started, cpu_started, thread_id = self.gc_started
            elapsed = (ended-started)*1000
            self.gc_started = None
            if elapsed >= 2:
                self.gc_pending.append((info['generation'], elapsed, info['collected'], started,
                                        ended, (cpu_ended-cpu_started)*1000, thread_id))

    def _collections(self):
        while self.gc_pending:
            generation, elapsed, collected, started, ended, cpu_ms, thread_id = self.gc_pending.popleft()
            self.log('live_gc_timing', generation=generation, milliseconds=elapsed,
                     collected=collected, start_qpc_s=started, end_qpc_s=ended,
                     thread_cpu_ms=cpu_ms, thread_id=thread_id)
        if self.pickle_timings is not None:
            self.pickle_timings.flush(self.log)

    def begin(self):
        if self.enabled:
            self._finish()
            self.started = self.last = self.clock()

    def mark(self, stage):
        if self.enabled and self.started is not None:
            now = self.clock()
            self.current[stage] = self.current.get(stage, 0.) + (now-self.last)*1000
            self.last = now

    def _finish(self):
        self._collections()
        if self.started is None:
            return
        self.mark('other')
        self.current['total'] = (self.last-self.started)*1000
        self.current['work'] = self.current['total']-self.current.get('sleep', 0.)
        if self.current['work'] > 25:
            self.log('live_frame_slow', start_qpc_s=self.started, end_qpc_s=self.last,
                     stages_ms=dict(self.current))
        for name, elapsed in self.current.items():
            total, maximum, samples = self.stages.get(name, (0., 0., 0))
            self.stages[name] = total+elapsed, max(maximum, elapsed), samples+1
        self.frames += 1
        self.current, self.started = {}, None
        if self.frames >= 60:
            self._write()

    def _write(self):
        if self.frames:
            worker = self.native_worker
            counters = {} if worker is None else dict(native_commands=dict(
                preparations=worker.command_preparations, cache_hits=worker.command_cache_hits,
                cached_pages=len(worker.command_cache)))
            self.log('live_frame_timings', iterations=self.frames,
                     stages={name: dict(mean_ms=total/self.frames, max_ms=maximum, samples=samples)
                             for name, (total, maximum, samples) in self.stages.items()}, **counters)
            self.frames, self.stages = 0, {}

    def flush(self):
        if self.enabled:
            self._finish()
            self._write()

    def close(self):
        if self.gc_callback in self.gc_callbacks:
            self.gc_callbacks.remove(self.gc_callback)
        if self.pickle_timings is not None:
            self.pickle_timings.close()
        self.flush()
