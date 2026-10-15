"""Optional bounded call-trace history for a long interactive engine lifetime."""
from collections import Counter, deque
import json
import os
import time

PROFILE_IO = os.environ.get('GTA_THAW_PROFILE_FRAMES') == '1'

REPETITIVE = frozenset(('before_qb_call', 'qb_call_result', 'qb_entry_after',
    'before_platform_call', 'platform_call_result', 'frame_phase', 'frame_input',
    'audio_frame_pass', 'gameplay_frame', 'camera_projection_begin',
    'camera_projection_result', 'camera_projection_pass', 'hud_layout_pass'))


class EventLog:
    def __init__(self, output, started, *, compact=False):
        self.output, self.started, self.compact = output, started, compact
        self.recent, self.counts = deque(maxlen=128), Counter()

    def write(self, row):
        self.output.write(json.dumps(row, ensure_ascii=False)+'\n')

    def __call__(self, phase, **values):
        row = dict(phase=phase, elapsed_ms=round((time.monotonic()-self.started)*1000), **values)
        if self.compact and phase in REPETITIVE:
            self.counts[phase] += 1
            self.recent.append(row)
            return
        if self.compact and phase == 'error':
            for context in self.recent:
                self.write(dict(context, retained_error_context=True))
            self.recent.clear()
        if self.compact and phase in ('error', 'pass', 'session_released', 'cleanup'):
            self.write(dict(phase='trace_counts', suppressed=dict(self.counts)))
        started = time.perf_counter() if PROFILE_IO else None
        self.write(row)
        written = time.perf_counter() if started is not None else None
        self.output.flush()
        if started is not None:
            finished = time.perf_counter()
            if finished-started > .008:
                # Append without a recursive flush; the next ordinary event or
                # close persists this diagnostic without adding another IO wait.
                self.write(dict(phase='log_slow_io', elapsed_ms=round((time.monotonic()-self.started)*1000),
                    source_phase=phase, write_ms=(written-started)*1000, flush_ms=(finished-written)*1000))
