"""Pure protocol tests: no child processes, game calls or executable memory."""
import struct
import hashlib
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import native_worker as n


class FakeAPI:
    def __init__(self, result=0, alive=True):
        self.result, self.alive = result, alive
        self.signalled = []
        self.closed = []
        self.allocations = 0
        self.protection = 4
        self.page_protections = {}
        self.protections = []

    def VirtualAllocEx(self, process, base, size, flags, protection):
        assert size == 4096 and protection == 4
        self.allocations += 1
        address = 0x24000000 + (self.allocations-1)*4096
        self.page_protections[address] = 4
        return address

    def VirtualProtectEx(self, process, address, size, protection, previous):
        previous._obj.value = self.page_protections[address]
        self.protection = protection
        self.page_protections[address] = protection
        self.protections.append(protection)
        return True

    def VirtualQueryEx(self, process, address, buffer, size):
        assert size == 48
        n.C.memmove(buffer, struct.pack('<QQI4xQIII4x', address, address, 4, 4096,
                    0x1000, self.page_protections[address], 0x20000), size)
        return size

    def FlushInstructionCache(self, process, address, size):
        assert self.protection == 0x20
        return True

    def WaitForSingleObject(self, thread, timeout):
        return 258 if self.alive else 0

    def WaitForMultipleObjects(self, count, handles, all_handles, milliseconds):
        assert count == 2 and tuple(handles) == (20, 30) and not all_handles
        assert 0 <= milliseconds <= 1000
        return self.result

    def GetExitCodeThread(self, thread, result):
        result._obj.value = 70
        return True

    def SetEvent(self, handle):
        self.signalled.append(handle)
        return True

    def CloseHandle(self, handle):
        self.closed.append(handle)
        return True


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.profile = patch.object(n.p, 'PROFILE_RESOURCE_CALLS', False)
        self.profile.start()
        self.addCleanup(self.profile.stop)

    def make_worker(self, result=0, alive=True):
        worker = n.NativeWorker.__new__(n.NativeWorker)
        worker.api = FakeAPI(result, alive)
        worker.process, worker.mailbox = 42, 0x20000000
        worker.request, worker.done, worker.thread = 10, 20, 30
        worker.tid, worker.count = 123, 0
        worker.command_page = None
        worker.command_preparations = 0
        worker.command_cache_hits = 0
        worker.command_cache = n.OrderedDict()
        worker.command_failed = False
        worker.log = Mock()
        worker._operation = None
        worker._operation_lock = threading.Lock()
        worker._waiting = None
        worker._waiting_lock = threading.Lock()
        worker.profile_frequency = 0
        worker._profile_returned = None
        worker.api._thaw_native_worker = worker
        return worker

    def test_opt_in_timing_separates_event_wait_from_callback_without_extra_commands(self):
        worker = self.make_worker()
        now, completed, results = [100.], [0], iter((258, 0))
        def wait(count, handles, all_handles, milliseconds):
            result = next(results)
            now[0] += .020 if result == 258 else .003
            if result == 0:
                completed[0] = 1
            return result
        def maintain():
            now[0] += .006
        worker.api.WaitForMultipleObjects = wait
        timing = {}
        with patch.object(n.time, 'monotonic', side_effect=lambda: now[0]), \
             patch.object(n.time, 'perf_counter', side_effect=lambda: now[0]), \
             patch.object(worker, 'snapshot', side_effect=lambda: self.state(completed[0])), \
             patch.object(n.p, 'remote_write') as write, worker.waiting_callback(maintain):
            self.assertEqual(worker.execute(0x24000000, 100.5, timings=timing), (0, 0))
        self.assertAlmostEqual(timing['wait_ms'], 23.)
        self.assertAlmostEqual(timing['callback_ms'], 6.)
        self.assertEqual((timing['wait_count'], timing['callback_count']), (2, 1))
        self.assertEqual(worker.api.signalled, [10])
        self.assertEqual(worker.count, 1)
        write.assert_called_once()

    def test_disabled_worker_timing_never_queries_profiling_clock(self):
        worker = self.make_worker()
        with patch.object(n.time, 'perf_counter', side_effect=AssertionError('unexpected profiling')), \
             patch.object(worker, 'snapshot', side_effect=[self.state(), self.state(1), self.state(1)]), \
             patch.object(n.p, 'remote_write'):
            self.assertEqual(worker.execute(0x24000000, time.monotonic()+.5), (0, 0))

    def test_custom_commands_log_only_slow_opt_in_calls(self):
        for elapsed, slow in ((.001, False), (.020, True)):
            with self.subTest(elapsed=elapsed):
                worker = self.make_worker()
                now = [100.]
                def wait(*args):
                    now[0] += elapsed
                    return 0
                worker.api.WaitForMultipleObjects = wait
                with patch.object(n.p, 'PROFILE_RESOURCE_CALLS', True), \
                     patch.object(n.time, 'monotonic', side_effect=lambda: now[0]), \
                     patch.object(n.time, 'perf_counter', side_effect=lambda: now[0]), \
                     patch.object(worker, 'snapshot', side_effect=[self.state(), self.state(1), self.state(1)]), \
                     patch.object(n.p, 'remote_write'):
                    self.assertEqual(worker.execute(0x24000000, 100.5), (0, 0))
                self.assertEqual(worker.log.call_count, int(slow))
                if slow:
                    self.assertEqual(worker.log.call_args.args, ('native_slow_worker_command',))
                    self.assertAlmostEqual(worker.log.call_args.kwargs['wait_ms'], elapsed*1000)
                    self.assertEqual(worker.log.call_args.kwargs['wait_count'], 1)

    def test_qpc_splits_queue_execution_and_host_resume_only_after_verified_completion(self):
        for queue_ms, execute_ms, resume_ms in ((2,25,4),(20,1,0),(0,1,20)):
            with self.subTest(queue_ms=queue_ms, execute_ms=execute_ms, resume_ms=resume_ms):
                worker=self.make_worker();worker.profile_frequency=1_000_000
                now=[100.];timing={}
                first=100_000_000+queue_ms*1000;last=first+execute_ms*1000
                def wait(*args):
                    now[0]+=(queue_ms+execute_ms+resume_ms)/1000
                    return 0
                worker.api.WaitForMultipleObjects=wait
                def read(api, process, address, size):
                    self.assertEqual((process,address,size),(42,worker.mailbox+n.QPC_BEGIN,16))
                    self.assertEqual(worker.count,1)
                    self.assertFalse(worker.command_failed)
                    return struct.pack('<QQ',first,last)
                with patch.object(n.time,'monotonic',side_effect=lambda:now[0]), \
                     patch.object(n.time,'perf_counter',side_effect=lambda:now[0]), \
                     patch.object(worker,'snapshot',side_effect=[self.state(),self.state(1),self.state(1)]), \
                     patch.object(n.p,'remote_write'),patch.object(n.p,'remote_read',side_effect=read) as reader:
                    self.assertEqual(worker.execute(0x24000000,100.5,timings=timing),(0,0))
                reader.assert_called_once()
                self.assertTrue(timing['worker_qpc_valid'])
                for name,value in (('native_queue_ms',queue_ms),('native_execution_ms',execute_ms),
                                   ('native_completion_to_host_ms',resume_ms)):
                    self.assertAlmostEqual(timing[name],value)
                self.assertEqual(timing['native_submitted_qpc_s'],100.)
                self.assertEqual(timing['native_begin_qpc_s'],first/worker.profile_frequency)
                self.assertEqual(timing['native_end_qpc_s'],last/worker.profile_frequency)
                self.assertEqual(timing['host_returned_qpc_s'],now[0])

    def test_qpc_fast_calls_and_wrong_completion_do_not_read_diagnostic_mailbox(self):
        for duration, count in ((.001,1),(.020,2)):
            worker=self.make_worker();worker.profile_frequency=1_000_000
            now=[100.]
            def wait(*args):now[0]+=duration;return 0
            worker.api.WaitForMultipleObjects=wait
            with patch.object(n.time,'monotonic',side_effect=lambda:now[0]), \
                 patch.object(n.time,'perf_counter',side_effect=lambda:now[0]), \
                 patch.object(worker,'snapshot',side_effect=[self.state(),self.state(count),self.state(count)]), \
                 patch.object(n.p,'remote_write'),patch.object(n.p,'remote_read') as reader:
                if count==1:worker.execute(0x24000000,100.5,timings={})
                else:
                    with self.assertRaisesRegex(RuntimeError,'completion'):
                        worker.execute(0x24000000,100.5,timings={})
            reader.assert_not_called()

    def test_qpc_invalid_clock_stamps_are_reported_without_inventing_durations(self):
        for stamps in ((0,0),(100_002,100_001),(99_990,100_010),(100_001,100_030),
                       (99_999,100_010),(100_001,100_021)):
            worker=self.make_worker();worker.profile_frequency=1000
            now=[100.];timing={}
            def wait(*args):now[0]+=.020;return 0
            worker.api.WaitForMultipleObjects=wait
            with patch.object(n.time,'monotonic',side_effect=lambda:now[0]), \
                 patch.object(n.time,'perf_counter',side_effect=lambda:now[0]), \
                 patch.object(worker,'snapshot',side_effect=[self.state(),self.state(1),self.state(1)]), \
                 patch.object(n.p,'remote_write'),patch.object(n.p,'remote_read',return_value=struct.pack('<QQ',*stamps)):
                worker.execute(0x24000000,100.5,timings=timing)
            # Clock ordering tolerates two QPC ticks for float conversion.
            expected=stamps in ((99_999,100_010),(100_001,100_021))
            self.assertEqual(timing['worker_qpc_valid'],expected)
            self.assertEqual('native_execution_ms' in timing,expected)

    def test_profile_clock_rejects_other_epoch_frequency_and_clock_implementation(self):
        def frequency(pointer):pointer._obj.value=10_000_000;return True
        def counter(pointer):pointer._obj.value=1_000_000_000;return True
        api=SimpleNamespace(QueryPerformanceFrequency=Mock(side_effect=frequency),
                            QueryPerformanceCounter=Mock(side_effect=counter))
        good=dict(implementation='QueryPerformanceCounter()',monotonic=True,adjustable=False,resolution=1e-7)
        for change, bracket in (({},(99.999,100.001)),({},(101.,102.)),
                                ({'resolution':1e-3},(99.,101.)),
                                ({'implementation':'OtherClock'},(99.,101.))):
            with patch.object(n.time,'get_clock_info',return_value=SimpleNamespace(**(good|change))), \
                 patch.object(n.time,'perf_counter',side_effect=bracket):
                if not change and bracket==(99.999,100.001):self.assertEqual(n.profile_clock(api),10_000_000)
                else:
                    with self.assertRaisesRegex(RuntimeError,'clock'):
                        n.profile_clock(api)

    def test_waiting_callback_keeps_one_busy_command_and_checks_completion(self):
        worker = self.make_worker()
        now, completed, waits, upkeep = [100.], [0], [], []
        results = iter((258, 258, 0))

        def wait(count, handles, all_handles, milliseconds):
            waits.append(milliseconds)
            result = next(results)
            if result == 258:
                now[0] += milliseconds / 1000
            else:
                completed[0] = 1
            return result

        def maintain():
            upkeep.append((now[0], threading.get_ident()))
            self.assertTrue(worker.command_failed)
            with self.assertRaisesRegex(RuntimeError, 'unusable'):
                worker.prepare_command(b'cannot overwrite in-flight code')

        worker.api.WaitForMultipleObjects = wait
        with patch.object(n.time, 'monotonic', side_effect=lambda: now[0]), \
             patch.object(worker, 'snapshot', side_effect=lambda: self.state(completed[0])), \
             patch.object(n.p, 'remote_write') as write:
            with worker.waiting_callback(maintain):
                self.assertEqual(worker.execute(0x24000000, 100.5), (0, 0))
        self.assertEqual(waits, [20, 20, 20])
        self.assertEqual(len(upkeep), 2)
        self.assertTrue(all(tid == threading.get_ident() for _, tid in upkeep))
        self.assertEqual(worker.api.signalled, [10])
        write.assert_called_once()
        self.assertEqual(worker.count, 1)
        self.assertFalse(worker.command_failed)
        self.assertIsNone(worker._waiting)
        self.assertFalse(worker._waiting_lock.locked())

    def test_wait_callback_time_cannot_extend_command_deadline(self):
        worker = self.make_worker()
        now, waits, upkeep = [100.], [], []

        def wait(count, handles, all_handles, milliseconds):
            waits.append(milliseconds)
            now[0] += milliseconds / 1000
            return 258

        def maintain():
            upkeep.append(now[0])
            now[0] += .04

        worker.api.WaitForMultipleObjects = wait
        with patch.object(n.time, 'monotonic', side_effect=lambda: now[0]), \
             patch.object(worker, 'snapshot', return_value=self.state()), \
             patch.object(n.p, 'remote_write'), \
             self.assertRaisesRegex(RuntimeError, 'exceeded'), \
             worker.operation_budget(None, .05), worker.waiting_callback(maintain):
            worker.execute(0x24000000, 999.)
        self.assertEqual(waits, [20])
        self.assertEqual(len(upkeep), 1)
        self.assertTrue(worker.command_failed)
        self.assertEqual(worker.count, 0)
        self.assertIsNone(worker._waiting)
        self.assertIsNone(worker._operation)
        self.assertFalse(worker._waiting_lock.locked())

    def test_fast_commands_share_throttled_upkeep_without_renewing_deadline(self):
        worker = self.make_worker()
        now, completed, deadlines, upkeep = [100.], [0], [], []

        def fast_wait(deadline):
            deadlines.append(deadline)
            now[0] += .009
            completed[0] += 1
            return 0, 0

        def maintain():
            upkeep.append((now[0], len(worker.api.signalled)))
            self.assertTrue(worker.command_failed)
            with self.assertRaisesRegex(RuntimeError, 'unusable'):
                worker.execute(0x24000000, 999.)

        with patch.object(n.time, 'monotonic', side_effect=lambda: now[0]), \
             patch.object(worker, 'snapshot', side_effect=lambda: self.state(completed[0])), \
             patch.object(worker, 'wait', side_effect=fast_wait), patch.object(n.p, 'remote_write') as write:
            with worker.operation_budget(100.1, .5), worker.waiting_callback(maintain):
                for _ in range(8):
                    self.assertEqual(worker.execute(0x24000000, 999.), (0, 0))
        self.assertEqual(deadlines, [100.1] * 8)
        self.assertEqual([command for _, command in upkeep], [4, 7])
        self.assertGreaterEqual(upkeep[1][0]-upkeep[0][0], .020)
        self.assertEqual(write.call_count, 8)
        self.assertEqual(len(worker.api.signalled), 8)
        self.assertEqual(worker.count, 8)
        self.assertFalse(worker.command_failed)
        self.assertIsNone(worker._waiting)

    def test_wait_callback_failure_preserves_failure_and_poisoned_worker(self):
        worker = self.make_worker(258)

        def lost_ownership():
            raise ValueError('GTA ownership ended')

        with patch.object(worker, 'snapshot', return_value=self.state()), \
             patch.object(n.p, 'remote_write'), \
             self.assertRaisesRegex(ValueError, 'GTA ownership ended'), \
             worker.waiting_callback(lost_ownership):
            worker.execute(0x24000000, time.monotonic() + .5)
        self.assertTrue(worker.command_failed)
        self.assertEqual(worker.api.signalled, [10])
        self.assertEqual(worker.count, 0)
        self.assertIsNone(worker._waiting)
        self.assertFalse(worker._waiting_lock.locked())

    def test_wait_callback_scope_rejects_nesting_foreign_thread_or_noncallable(self):
        worker = self.make_worker()
        with self.assertRaisesRegex(RuntimeError, 'callable'), worker.waiting_callback(None):
            self.fail('Invalid callback entered')
        with worker.waiting_callback(lambda: None):
            with self.assertRaisesRegex(RuntimeError, 'nest'), worker.waiting_callback(lambda: None):
                self.fail('Nested callback entered')
            owner = worker._waiting[1]
            with patch.object(n.threading, 'get_ident', return_value=owner + 1), \
                 patch.object(n.p, 'remote_write') as write, \
                 patch.object(worker, 'snapshot') as read:
                with self.assertRaisesRegex(RuntimeError, 'another host thread'):
                    worker.execute(0x24000000, time.monotonic() + .5)
                write.assert_not_called()
                read.assert_not_called()
        self.assertIsNone(worker._waiting)
        self.assertFalse(worker.command_failed)

    def test_wait_callback_never_runs_for_completion_fault_or_expired_deadline(self):
        for result, deadline in ((0, 101.), (1, 101.), (0xffffffff, 101.), (258, 100.)):
            worker = self.make_worker(result)
            upkeep = []
            with self.subTest(result=result, deadline=deadline), \
                 patch.object(n.time, 'monotonic', return_value=100.), \
                 patch.object(worker, 'snapshot', return_value=self.state()), \
                 worker.waiting_callback(lambda: upkeep.append(True)):
                self.assertEqual(worker.wait(deadline)[0], result)
            self.assertEqual(upkeep, [])

    def test_operation_clamps_each_command_without_renewing_whole_budget(self):
        worker = self.make_worker()
        now, completed, deadlines = [100.0], [0], []

        def wait(deadline):
            deadlines.append(deadline)
            completed[0] += 1
            now[0] += 0.01
            return 0, 0

        with patch.object(n.time, 'monotonic', side_effect=lambda: now[0]), \
             patch.object(worker, 'snapshot', side_effect=lambda: self.state(completed[0])), \
             patch.object(worker, 'wait', side_effect=wait), patch.object(n.p, 'remote_write'):
            with worker.operation_budget(100.5, 0.2):
                self.assertEqual(worker.execute(0x24000000, 99.0), (0, 0))
                now[0] = 100.45
                self.assertEqual(worker.execute(0x24000000, 999.0), (0, 0))
            self.assertEqual(deadlines, [100.2, 100.5])
            self.assertIsNone(worker._operation)
            with self.assertRaisesRegex(RuntimeError, 'deadline expired'):
                worker.execute(0x24000000, 99.0)
        self.assertEqual(worker.count, 2)
        self.assertFalse(worker.command_failed)

    def test_late_command_completion_poisoned_even_with_successful_event(self):
        worker = self.make_worker()
        now = [100.0]

        def late(deadline):
            self.assertEqual(deadline, 100.2)
            now[0] = 100.21
            return 0, 0

        with patch.object(n.time, 'monotonic', side_effect=lambda: now[0]), \
             patch.object(worker, 'snapshot', return_value=self.state()), \
             patch.object(worker, 'wait', side_effect=late), patch.object(n.p, 'remote_write'):
            with self.assertRaisesRegex(RuntimeError, 'exceeded'), worker.operation_budget(101., 0.2):
                worker.execute(0x24000000, 999.)
        self.assertTrue(worker.command_failed)
        self.assertIsNone(worker._operation)
        with self.assertRaisesRegex(RuntimeError, 'unusable'):
            worker.prepare_command(b'code')

    def test_continuous_operation_keeps_per_command_timeout_after_hours(self):
        worker = self.make_worker()
        now, completed, deadlines = [100.], [0], []
        def wait(deadline):
            deadlines.append(deadline)
            completed[0] += 1
            now[0] += .01
            return 0, 0
        with patch.object(n.time,'monotonic',side_effect=lambda:now[0]), \
             patch.object(worker,'snapshot',side_effect=lambda:self.state(completed[0])), \
             patch.object(worker,'wait',side_effect=wait), patch.object(n.p,'remote_write'):
            with worker.operation_budget(None,.2):
                worker.execute(0x24000000,99.)
                now[0] = 7300.
                worker.execute(0x24000000,99.)
            self.assertEqual(deadlines,[100.2,7300.2])
            with patch.object(worker,'wait',side_effect=lambda deadline:(now.__setitem__(0,deadline+.01) or (0,0))):
                with self.assertRaisesRegex(RuntimeError,'exceeded'), worker.operation_budget(None,.2):
                    worker.execute(0x24000000,99.)
            self.assertTrue(worker.command_failed)

    def test_whole_operation_expiry_checks_host_work_and_prevents_next_publish(self):
        for check_next_call in (False, True):
            worker = self.make_worker()
            now = [100.]
            with self.subTest(check_next_call=check_next_call), \
                 patch.object(n.time, 'monotonic', side_effect=lambda: now[0]), \
                 patch.object(worker, 'snapshot', return_value=self.state()), \
                 patch.object(n.p, 'remote_write') as write:
                with self.assertRaisesRegex(RuntimeError, 'operation deadline'), worker.operation_budget(101., 0.2):
                    now[0] = 101.
                    if check_next_call:
                        worker.execute(0x24000000, 999.)
                write.assert_not_called()
            self.assertTrue(worker.command_failed)
            self.assertIsNone(worker._operation)

    def test_nesting_and_foreign_thread_refused_before_shared_record_writes(self):
        from probe_resources import Platform
        worker = self.make_worker()
        platform = Platform.__new__(Platform)
        platform.worker = worker
        with patch.object(n.time, 'monotonic', return_value=100.), \
             patch.object(worker, 'snapshot', return_value=self.state()):
            with worker.operation_budget(101., 0.2):
                original = worker._operation
                with self.assertRaisesRegex(RuntimeError, 'nest'), worker.operation_budget(101., 0.2):
                    self.fail('Nested scope entered')
                self.assertEqual(worker._operation, original)
                with patch.object(n.threading, 'get_ident', return_value=original[2] + 1), \
                     patch.object(n.p, 'remote_write') as write, patch.object(n.p, 'remote_read') as read:
                    for invoke in (
                        lambda: worker.prepare_command(b'code'),
                        lambda: worker.execute(0x24000000, 999.),
                        lambda: n.p.native_call(worker.api, worker.process, None, [], 0, 999., None),
                        lambda: platform.invoke('unused', 0x24000000),
                    ):
                        with self.assertRaisesRegex(RuntimeError, 'another host thread'): invoke()
                    write.assert_not_called()
                    read.assert_not_called()
        self.assertFalse(worker.command_failed)

    def test_operation_invalid_limits_or_nonidle_entry_do_not_enter(self):
        for deadline, timeout in ((100., 1.), (float('nan'), 1.), (float('inf'), 1.),
                                  (101., 0), (101., -1), (101., float('inf')), (101., True)):
            worker = self.make_worker()
            with patch.object(n.time, 'monotonic', return_value=100.), \
                 patch.object(worker, 'snapshot') as read, self.assertRaises(RuntimeError), \
                 worker.operation_budget(deadline, timeout):
                self.fail('Invalid budget entered')
            read.assert_not_called()
        for state, alive in ((self.state(status=n.BUSY), True), (self.state(1), True),
                              (self.state(tid=999), True), (self.state(), False)):
            worker = self.make_worker(alive=alive)
            with patch.object(n.time, 'monotonic', return_value=100.), \
                 patch.object(worker, 'snapshot', return_value=state), self.assertRaisesRegex(RuntimeError, 'idle live'), \
                 worker.operation_budget(101., 0.2):
                self.fail('Busy/dead/foreign worker entered')
            self.assertIsNone(worker._operation)
            self.assertFalse(worker._operation_lock.locked())

    def test_bounded_pages_reuse_exact_glue_without_writable_executable_overlap(self):
        worker = self.make_worker()
        memory = {}
        with patch.object(worker, 'snapshot', return_value=self.state()), \
             patch.object(n, 'COMMAND_CACHE_LIMIT', 2), \
             patch.object(n.p, 'remote_write', side_effect=lambda api, process, at, data: memory.update({at:data})) as write, \
             patch.object(n.p, 'remote_read', side_effect=lambda api, process, at, size: memory[at]):
            self.assertEqual(worker.prepare_command(b'first'), 0x24000000)
            self.assertEqual(worker.prepare_command(b'next'), 0x24001000)
            self.assertEqual(worker.prepare_command(b'first'), 0x24000000)
            self.assertEqual(worker.prepare_command(b'last'), 0x24001000)
            self.assertEqual([call.args[-1] for call in write.call_args_list], [b'first', b'next', b'last'])
        self.assertEqual(worker.api.allocations, 2)
        self.assertEqual(worker.api.protections, [0x20, 0x20, 4, 0x20])
        self.assertEqual(worker.command_preparations, 3)
        self.assertEqual(worker.command_cache_hits, 1)
        self.assertEqual(list(worker.command_cache), [b'first', b'last'])

    def test_cache_hit_still_rejects_foreign_bytes_or_non_rx_page(self):
        for changed in ('bytes', 'protection'):
            worker = self.make_worker()
            with self.subTest(changed=changed), patch.object(worker, 'snapshot', return_value=self.state()), \
                 patch.object(n.p, 'remote_write') as write:
                address = worker.prepare_command(b'first')
                write.reset_mock()
                if changed == 'protection':
                    worker.api.page_protections[address] = 0x40
                with patch.object(n.p, 'remote_read', return_value=b'wrong'), self.assertRaises(RuntimeError):
                    worker.prepare_command(b'first')
                write.assert_not_called()
            self.assertTrue(worker.command_failed)
            self.assertEqual(worker.command_cache_hits, 0)

    def test_cache_never_exceeds_64_pages_or_aliases_retained_commands(self):
        worker = self.make_worker()
        memory = {}
        with patch.object(worker, 'snapshot', return_value=self.state()), \
             patch.object(n.p, 'remote_write', side_effect=lambda api, process, at, data: memory.update({at: data})), \
             patch.object(n.p, 'remote_read', side_effect=lambda api, process, at, size: memory[at]):
            for index in range(100):
                code = struct.pack('<I', index)
                worker.prepare_command(code)
                self.assertLessEqual(len(worker.command_cache), 64)
                self.assertEqual(len(set(worker.command_cache.values())), len(worker.command_cache))
                self.assertTrue(all(memory[at] == expected for expected, at in worker.command_cache.items()))
            for code, address in list(worker.command_cache.items()):
                self.assertEqual(worker.prepare_command(code), address)
        self.assertEqual(worker.api.allocations, 64)
        self.assertEqual(worker.command_preparations, 100)
        self.assertEqual(worker.command_cache_hits, 64)

    def test_traffic_sized_working_set_does_not_rewrite_pages_each_frame(self):
        worker, memory = self.make_worker(), {}
        with patch.object(worker, 'snapshot', return_value=self.state()), \
             patch.object(n.p, 'remote_write', side_effect=lambda api, process, at, data: memory.update({at: data})), \
             patch.object(n.p, 'remote_read', side_effect=lambda api, process, at, size: memory[at]):
            for _ in range(100):
                for command in range(40):
                    worker.prepare_command(struct.pack('<I', command))
        self.assertEqual(worker.command_preparations, 40)
        self.assertEqual(worker.command_cache_hits, 3960)
        self.assertEqual(worker.api.allocations, 40)
        self.assertEqual(worker.api.protections, [0x20] * 40)

    def test_corrupted_eviction_candidate_is_not_reprotected_or_overwritten(self):
        worker = self.make_worker()
        with patch.object(worker, 'snapshot', return_value=self.state()), \
             patch.object(n, 'COMMAND_CACHE_LIMIT', 1), patch.object(n.p, 'remote_write') as write:
            worker.prepare_command(b'original')
            write.reset_mock()
            protections = list(worker.api.protections)
            with patch.object(n.p, 'remote_read', return_value=b'foreign!'), \
                 self.assertRaisesRegex(RuntimeError, 'glue changed'):
                worker.prepare_command(b'replacement')
            write.assert_not_called()
            self.assertEqual(worker.api.protections, protections)
            self.assertEqual(list(worker.command_cache), [b'original'])
            self.assertTrue(worker.command_failed)

    def test_hit_cannot_bypass_idle_sequence_or_thread_ownership(self):
        worker = self.make_worker()
        with patch.object(worker, 'snapshot', return_value=self.state()), patch.object(n.p, 'remote_write'):
            worker.prepare_command(b'first')
        for state in (self.state(status=n.BUSY), self.state(1), self.state(tid=124)):
            with patch.object(worker, 'snapshot', return_value=state), patch.object(n.p, 'remote_read') as read, \
                 self.assertRaisesRegex(RuntimeError, 'busy or dead'):
                worker.prepare_command(b'first')
            read.assert_not_called()

    def test_busy_dead_or_failed_worker_cannot_rewrite_code(self):
        for state, alive, failed in ((self.state(status=n.BUSY), True, False),
                                     (self.state(), False, False), (self.state(), True, True)):
            worker = self.make_worker(alive=alive)
            worker.command_failed = failed
            with patch.object(worker, 'snapshot', return_value=state), \
                 patch.object(n.p, 'remote_write') as write, self.assertRaises(RuntimeError):
                worker.prepare_command(b'code')
            self.assertEqual(worker.api.allocations, 0)
            write.assert_not_called()

    def test_failed_page_write_poison_worker_before_another_command(self):
        worker = self.make_worker()
        with patch.object(worker, 'snapshot', return_value=self.state()), \
             patch.object(n.p, 'remote_write', side_effect=OSError('write failed')):
            with self.assertRaises(OSError):
                worker.prepare_command(b'code')
        self.assertTrue(worker.command_failed)
        with self.assertRaisesRegex(RuntimeError, 'unusable'):
            worker.execute(0x24000000, time.monotonic() + 1)

    def state(self, count=0, tid=123, status=n.IDLE):
        words = [0] * 16
        words[n.STATE // 4], words[n.TID // 4], words[n.COUNT // 4] = status, tid, count
        return tuple(words)

    def test_success_publishes_one_command_and_checks_completion(self):
        worker = self.make_worker()
        with patch.object(worker, "snapshot", side_effect=[self.state(), self.state(1), self.state(1)]), \
             patch.object(n.p, "remote_write") as write:
            self.assertEqual(worker.execute(0x22000000, time.monotonic() + 1), (0, 0))
        write.assert_called_once_with(worker.api, 42, 0x20000000, struct.pack("<I", 0x22000000))
        self.assertEqual(worker.count, 1)
        self.assertEqual(worker.api.signalled, [10])

    def test_rejects_stale_or_foreign_completion(self):
        for after in (self.state(0), self.state(2), self.state(1, tid=124), self.state(1, status=n.BUSY)):
            worker = self.make_worker()
            with self.subTest(after=after), \
                 patch.object(worker, "snapshot", side_effect=[self.state(), after, after]), \
                 patch.object(n.p, "remote_write"), self.assertRaises(RuntimeError):
                worker.execute(0x22000000, time.monotonic() + 1)
            self.assertEqual(worker.count, 0)

    def test_worker_fault_and_timeout_remain_available_to_fault_record_reader(self):
        for waited, expected in ((1, 70), (258, None), (0xffffffff, None)):
            worker = self.make_worker(waited)
            with self.subTest(waited=waited), patch.object(worker, "snapshot", return_value=self.state()), \
                 patch.object(n.p, "remote_write"):
                self.assertEqual(worker.execute(0x22000000, time.monotonic() + 1), (waited, expected))
                self.assertEqual(worker.count, 0)

    def test_rejects_dead_worker_before_publishing(self):
        worker = self.make_worker(alive=False)
        with patch.object(worker, "snapshot", return_value=self.state()), \
             patch.object(n.p, "remote_write") as write, self.assertRaises(RuntimeError):
            worker.execute(0x22000000, time.monotonic() + 1)
        write.assert_not_called()
        self.assertEqual(worker.api.signalled, [])

    def test_expired_deadline_never_publishes(self):
        worker = self.make_worker()
        with patch.object(worker, "snapshot") as read, self.assertRaises(RuntimeError):
            worker.execute(0x22000000, time.monotonic() - 1)
        read.assert_not_called()

    def test_owned_handles_close_once(self):
        worker = self.make_worker()
        worker.close()
        worker.close()
        self.assertEqual(worker.api.closed, [30, 10, 20])

    def test_only_matching_process_can_use_worker(self):
        worker = self.make_worker()
        worker.api._thaw_native_worker = worker
        self.assertIs(n.retained_worker(worker.api, 42), worker)
        with self.assertRaises(RuntimeError):
            n.retained_worker(worker.api, 43)

    def test_glue_has_owned_message_queue_and_bounded_drain(self):
        modules = {"kernel32.dll": {"base": 0x76000000}, "user32.dll": {"base": 0x75000000}}
        code, data = n.worker_stub(0x20000000, modules, 100, 104)
        self.assertEqual(len(data), 512)
        self.assertEqual(struct.unpack_from("<II", data, n.REQUEST), (100, 104))
        self.assertTrue(code.startswith(b"\x56\x57\xbe"))
        self.assertTrue(code.endswith(b"\x5f\x5e\xc2\x04\x00"))
        for export, _, _ in n.EXPORTS:
            self.assertEqual(data.count(export.encode("ascii") + b"\0"), 1)
        self.assertIn(b"\xbf\x20\0\0\0", code)  # bounded message drain
        self.assertNotIn(b"ResumeThread", data)
        self.assertLess(len(code), 4096)
        with self.assertRaises(RuntimeError):
            n.worker_stub(0x100000000, modules, 100, 104)

    def test_profile_off_glue_is_byte_identical_and_qpc_uses_disjoint_mailbox_fields(self):
        modules={'kernel32.dll':{'base':0x76000000},'user32.dll':{'base':0x75000000}}
        code,data=n.worker_stub(0x20000000,modules,100,104)
        self.assertEqual(hashlib.sha256(code).hexdigest(),'5c9e0bc107e1c0718e3131b75464821d135f55ffb21cc118db4d9a92e7813070')
        self.assertEqual(hashlib.sha256(data).hexdigest(),'5917ed7aafa95e40aecd059b61908efd6e3f5b969532f15cc71fc6b4495bdf63')
        measured,mailbox=n.worker_stub(0x20000000,modules,100,104,profile=True)
        self.assertEqual(mailbox.count(b'QueryPerformanceCounter\0'),1)
        self.assertEqual(measured.count(b'\xff\x56'+bytes([n.QPC])),2)
        self.assertEqual(measured.count(b'\xff\x16\x89\x46'+bytes([n.RETURN])),1)
        self.assertTrue(48+4<=n.LAST_WAIT and n.MSG+28<=n.QPC_BEGIN<n.QPC_END and n.QPC_END+8<=128)
        self.assertEqual(mailbox[n.QPC_BEGIN:n.QPC_END+8],bytes(16))
        self.assertEqual(len(mailbox),512)


if __name__ == "__main__":
    unittest.main()
