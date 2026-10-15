"""Opt-in resource profiling cannot alter original command ABI/ownership."""
import hashlib
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import probe_loader as host


class ResourceTimingTests(unittest.TestCase):
    def call(self, enabled, name='NodeArrayManLoad', clock_step=.001, usage=None):
        command = []
        def prepare(code):
            command.append(code)
            return 0x40000
        worker = SimpleNamespace(check_call_access=Mock(), tid=123,
                                 prepare_command=prepare, execute=Mock(return_value=(0, 0)))
        record = struct.pack('<18I', 6, *([0]*16), 1)
        def read(api, process, at, size):
            return {0x10000: b'\xc3', 0x30000: record, 0x40000: command[0] if command else b''}[at]
        log = Mock()
        clock = iter(index*clock_step for index in range(100))
        with patch.object(host, 'PROFILE_RESOURCE_CALLS', enabled), \
             patch('native_worker.retained_worker', return_value=worker), \
             patch.object(host, 'remote_read', side_effect=read), patch.object(host, 'remote_write'), \
             patch.object(host, 'thread_usage', side_effect=usage if usage is not None else lambda worker: None) as cpu, \
             patch.object(host.time, 'perf_counter', side_effect=lambda: next(clock)) as timer:
            result = host.native_call(None, 42, (name, 0x10000, 1, hashlib.sha256(b'\xc3').hexdigest()),
                                      [0x20000, 0], 0x30000, 123., log)
        self.assertEqual(result, 1)
        self.assertEqual(len(command), 1)
        worker.check_call_access.assert_called_once()
        self.assertEqual(cpu.call_count, 2 if enabled and name in (
            'NodeArrayManLoad', 'NodeArrayMemoryLoad', 'NodeArrayManUnload') else 0)
        return worker, log, timer

    def test_disabled_call_has_no_timing_or_changed_execute_signature(self):
        for name in ('NodeArrayManLoad', 'NodeArrayMemoryLoad', 'NativeAddString'):
            with self.subTest(name=name):
                worker, log, timer = self.call(False, name)
                worker.execute.assert_called_once_with(0x40000, 123.)
                timer.assert_not_called()
                self.assertNotIn('native_resource_timing', [call.args[0] for call in log.call_args_list])

    def test_nonresource_profile_only_logs_slow_calls(self):
        for step, expected in ((.001, False), (.003, True)):
            with self.subTest(step=step):
                worker, log, timer = self.call(True, 'NativeAddString', step)
                rows = [call.kwargs for call in log.call_args_list if call.args == ('native_slow_call',)]
                self.assertEqual(bool(rows), expected)
                self.assertIsInstance(worker.execute.call_args.kwargs['timings'], dict)
                if expected:
                    self.assertEqual(rows[0]['function'], 'NativeAddString')
                    self.assertGreater(rows[0]['total_ms'], 8)

    def test_resource_profile_records_phases_after_original_guards(self):
        for name in ('NodeArrayManLoad', 'NodeArrayMemoryLoad', 'NodeArrayManUnload'):
            with self.subTest(name=name):
                worker, log, timer = self.call(True, name)
                row = next(call.kwargs for call in log.call_args_list if call.args == ('native_resource_timing',))
                self.assertEqual(row['function'], name)
                self.assertGreater(row['total_ms'], row['execute_ms'])
                self.assertTrue({'pin_ms', 'prepare_ms', 'before_log_ms', 'post_guard_ms'} <= row.keys())
                self.assertIsInstance(worker.execute.call_args.kwargs['timings'], dict)

    def test_resource_profile_includes_only_monotonic_owned_thread_counters(self):
        for before, after, available in (((20,30,100), (10020,20030,12300), True),
                                        ((20,30,100), None, False),
                                        ((20,30,100), (19,30,101), False)):
            with self.subTest(after=after):
                worker, log, _ = self.call(True, usage=[before, after])
                row = next(call.kwargs for call in log.call_args_list if call.args == ('native_resource_timing',))
                self.assertEqual(row['thread_usage_available'], available)
                if available:
                    self.assertEqual((row['thread_kernel_ms'], row['thread_user_ms'], row['thread_cycles']), (1., 2., 12200))
                else:
                    self.assertNotIn('thread_cycles', row)

    def test_optional_thread_usage_reads_only_owned_handle_and_tolerates_absent_api(self):
        handles = []
        def times(thread, creation, exit_time, kernel, user):
            handles.append(thread)
            kernel._obj.value, user._obj.value = 10000, 20000
            return True
        def cycles(thread, out):
            handles.append(thread)
            out._obj.value = 456
            return True
        api = SimpleNamespace(GetThreadTimes=times, QueryThreadCycleTime=cycles)
        self.assertEqual(host.thread_usage(SimpleNamespace(api=api, thread=77)), (10000, 20000, 456))
        self.assertEqual(handles, [77,77])
        self.assertIsNone(host.thread_usage(SimpleNamespace()))
        api.GetThreadTimes = lambda *args: False
        self.assertIsNone(host.thread_usage(SimpleNamespace(api=api, thread=77)))


if __name__ == '__main__':
    unittest.main()
