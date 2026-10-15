import io
import json
import unittest
from unittest.mock import patch
import probe_log
from probe_log import EventLog


class LogTests(unittest.TestCase):
    def test_slow_flush_is_measured_without_recursive_flush(self):
        now = [0.]
        class Output(io.StringIO):
            flush_count = 0
            def write(self, value):
                now[0] += .001
                return super().write(value)
            def flush(self):
                self.flush_count += 1
                now[0] += .020
        output = Output()
        with patch.object(probe_log, 'PROFILE_IO', True), \
             patch.object(probe_log.time, 'perf_counter', side_effect=lambda: now[0]):
            EventLog(output, 0)('native_wait_transport_upkeep', tick=10)
        rows = [json.loads(s) for s in output.getvalue().splitlines()]
        self.assertEqual(output.flush_count, 1)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]['phase'], 'log_slow_io')
        self.assertEqual(rows[1]['source_phase'], 'native_wait_transport_upkeep')
        self.assertAlmostEqual(rows[1]['write_ms'], 1)
        self.assertAlmostEqual(rows[1]['flush_ms'], 20)

    def test_profiled_compact_calls_do_not_time_or_write(self):
        output = io.StringIO()
        with patch.object(probe_log, 'PROFILE_IO', True), \
             patch.object(probe_log.time, 'perf_counter', side_effect=AssertionError('unexpected clock')):
            log = EventLog(output, 0, compact=True)
            for i in range(1000):
                log('qb_call_result', number=i)
        self.assertEqual(output.getvalue(), '')

    def test_disabled_io_profile_never_queries_clock(self):
        output = io.StringIO()
        with patch.object(probe_log, 'PROFILE_IO', False), \
             patch.object(probe_log.time, 'perf_counter', side_effect=AssertionError('unexpected clock')):
            EventLog(output, 0)('native_wait_transport_upkeep')
        self.assertEqual(len(output.getvalue().splitlines()), 1)

    def test_compact_trace_is_bounded_and_error_retains_last_calls(self):
        output = io.StringIO()
        log = EventLog(output, 0, compact=True)
        for i in range(1000):
            log('qb_call_result', number=i)
        self.assertEqual(output.getvalue(), '')
        self.assertEqual(len(log.recent), 128)
        log('error', message='native call failed')
        rows = [json.loads(s) for s in output.getvalue().splitlines()]
        self.assertEqual(rows[0]['number'], 872)
        self.assertEqual(rows[-2]['suppressed'], {'qb_call_result':1000})
        self.assertEqual(rows[-1]['message'], 'native call failed')

    def test_probe_default_preserves_every_event(self):
        output = io.StringIO()
        log = EventLog(output, 0)
        log('qb_call_result', number=1)
        self.assertEqual(json.loads(output.getvalue())['number'], 1)
