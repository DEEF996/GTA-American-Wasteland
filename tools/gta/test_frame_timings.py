import unittest
from multiprocessing.reduction import ForkingPickler
from types import SimpleNamespace

from frame_timings import FrameTimings


class FrameTimingTests(unittest.TestCase):
    def test_disabled_does_not_read_clock_or_write_logs(self):
        def refuse(*args, **kwargs):
            self.fail('Disabled profiler performed work')
        callbacks = []
        original = {name: vars(ForkingPickler)[name] for name in ('loads', 'dumps')}
        class UnreadableWorker:
            def __getattr__(self, name):
                refuse()
        timing = FrameTimings(refuse, enabled=False, clock=refuse, gc_callbacks=callbacks,
                              native_worker=UnreadableWorker())
        timing.begin(); timing.mark('source'); timing.flush(); timing.close()
        self.assertEqual(callbacks, [])
        for name, descriptor in original.items():
            self.assertIs(vars(ForkingPickler)[name], descriptor)

    def test_exact_stage_work_and_interrupted_iterations_are_aggregated_without_per_frame_logs(self):
        now, logs = [0.], []
        timing = FrameTimings(lambda name, **row: logs.append((name, row)), enabled=True,
                              clock=lambda: now[0], gc_callbacks=[])
        for _ in range(60):
            timing.begin()
            now[0] += .006; timing.mark('sleep')
            now[0] += .010; timing.mark('source')
            now[0] += .002  # Includes a continue/buffer path ending before another mark.
        self.assertEqual(logs, [])
        timing.begin()  # Finishes exactly the previous 60 iterations.
        self.assertEqual(len(logs), 1)
        name, report = logs[0]
        self.assertEqual((name, report['iterations']), ('live_frame_timings', 60))
        for stage, value in [('sleep', 6), ('source', 10), ('other', 2), ('total', 18), ('work', 12)]:
            self.assertAlmostEqual(report['stages'][stage]['mean_ms'], value)
            self.assertAlmostEqual(report['stages'][stage]['max_ms'], value)
        now[0] += .005; timing.mark('rails')
        timing.flush(); timing.flush()
        self.assertEqual(len(logs), 2)
        self.assertEqual(logs[-1][1]['iterations'], 1)
        self.assertNotIn('source', logs[-1][1]['stages'])
        timing.close()

    def test_native_cache_counters_are_sampled_only_with_an_aggregate(self):
        logs = []
        worker = SimpleNamespace(command_preparations=40, command_cache_hits=100,
                                 command_cache={1: 4096, 2: 8192})
        timing = FrameTimings(lambda name, **row: logs.append((name, row)), enabled=True,
                              clock=lambda: 1., gc_callbacks=[], native_worker=worker)
        for _ in range(60):
            timing.begin()
        self.assertEqual(logs, [])
        worker.command_preparations += 3
        worker.command_cache_hits += 17
        timing.flush()
        self.assertEqual(logs[0][1]['native_commands'],
                         dict(preparations=43, cache_hits=117, cached_pages=2))
        self.assertEqual(logs[0][1]['iterations'], 60)
        timing.close()
        self.assertEqual(len(logs), 1)

    def test_collections_log_outside_callback_and_close_removes_only_own_listener(self):
        now, logs = [0.], []
        other = lambda *args: None
        callbacks = [other]
        timing = FrameTimings(lambda name, **row: logs.append((name, row)), enabled=True,
                              clock=lambda: now[0], gc_callbacks=callbacks)
        callback = callbacks[-1]
        callback('start', {'generation': 2})
        now[0] = .050
        callback('stop', {'generation': 2, 'collected': 12})
        self.assertEqual(logs, [])
        timing.flush()
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0][0], 'live_gc_timing')
        report = logs[0][1]
        self.assertEqual((report['generation'], report['milliseconds'], report['collected']), (2, 50., 12))
        self.assertEqual((report['start_qpc_s'], report['end_qpc_s']), (0., .050))
        self.assertGreater(report['thread_id'], 0)
        self.assertGreaterEqual(report['thread_cpu_ms'], 0)
        callback('start', {'generation': 0})
        now[0] += .0005
        callback('stop', {'generation': 0, 'collected': 0})
        timing.close(); timing.close()
        self.assertEqual(len(logs), 1)
        self.assertEqual(callbacks, [other])

    def test_close_flushes_pickle_diagnostics_after_restoring_original_descriptors(self):
        original = {name: vars(ForkingPickler)[name] for name in ('loads', 'dumps')}
        logs = []
        def log(name, **fields):
            for key, descriptor in original.items():
                self.assertIs(vars(ForkingPickler)[key], descriptor)
            logs.append((name, fields))
        timing = FrameTimings(log, enabled=True, gc_callbacks=[])
        self.addCleanup(timing.close)
        timing.pickle_timings.pending.append(dict(operation='loads', milliseconds=3))
        timing.close()
        self.assertEqual(logs, [('live_pickle_timing', dict(operation='loads', milliseconds=3))])

    def test_slow_frame_records_one_measured_iteration_excluding_sleep_from_threshold(self):
        now, logs, reads = [1.], [], []
        def clock():
            reads.append(now[0])
            return now[0]
        timing = FrameTimings(lambda name, **row: logs.append((name, row)), enabled=True,
                              clock=clock, gc_callbacks=[])
        self.addCleanup(timing.close)
        timing.begin()
        now[0] += .0625; timing.mark('sleep')
        now[0] += .03125; timing.mark('source')
        before = len(reads)
        timing.begin()
        # Only the existing end-of-frame mark and next-frame start read time.
        self.assertEqual(len(reads)-before, 2)
        self.assertEqual(logs, [('live_frame_slow', dict(start_qpc_s=1., end_qpc_s=1.09375,
            stages_ms=dict(sleep=62.5, source=31.25, other=0., total=93.75, work=31.25)))])
        now[0] += .0625; timing.mark('sleep')
        now[0] += .015625; timing.mark('source')
        timing.flush()
        self.assertEqual([name for name, _ in logs], ['live_frame_slow', 'live_frame_timings'])
        self.assertEqual(logs[-1][1]['iterations'], 2)


if __name__ == '__main__':
    unittest.main()
