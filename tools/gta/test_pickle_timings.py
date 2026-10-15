import pickle
import threading
import unittest
from multiprocessing.reduction import ForkingPickler

from pickle_timings import PickleTimings


class PickleTimingTests(unittest.TestCase):
    def fixture(self):
        now, cpu = [1.], [2.]

        class Pickler:
            @staticmethod
            def loads(data, *args, **kwargs):
                now[0] += .010
                cpu[0] += .004
                return pickle.loads(data, *args, **kwargs)

            @classmethod
            def dumps(cls, obj, protocol=None):
                now[0] += .012
                cpu[0] += .003
                cls.seen = cls
                return memoryview(pickle.dumps(obj, protocol))

        original = {name: vars(Pickler)[name] for name in ('loads', 'dumps')}
        timing = PickleTimings(pickler=Pickler, clock=lambda: now[0], cpu_clock=lambda: cpu[0])
        self.addCleanup(timing.close)
        return Pickler, timing, original

    def test_original_results_bytes_and_subclass_semantics_are_preserved(self):
        cls, timing, _ = self.fixture()
        obj = {'key': [1, 'value', (2, 3)], 'binary': bytes(range(256))}
        encoded = cls.dumps(obj, protocol=5)
        self.assertEqual(encoded, pickle.dumps(obj, protocol=5))
        self.assertEqual(cls.loads(encoded), obj)
        class Child(cls):
            pass
        Child.dumps(obj, 4)
        self.assertIs(Child.seen, Child)
        reports = list(timing.pending)
        self.assertEqual([r['operation'] for r in reports], ['dumps', 'loads', 'dumps'])
        self.assertEqual(reports[0]['byte_count'], len(encoded))
        self.assertAlmostEqual(reports[0]['thread_cpu_ms'], 3)
        self.assertAlmostEqual(reports[1]['milliseconds'], 10)
        self.assertEqual(reports[1]['thread_id'], threading.get_native_id())
        self.assertTrue(all(not r['failed'] and r['end_qpc_s'] > r['start_qpc_s'] for r in reports))
        self.assertTrue(all(set(r).isdisjoint({'payload', 'object', 'result', 'data'}) for r in reports))

    def test_exception_is_unchanged_and_records_no_payload(self):
        cls, timing, _ = self.fixture()
        with self.assertRaises(pickle.UnpicklingError):
            cls.loads(b'not a pickle')
        row = timing.pending[-1]
        self.assertEqual((row['operation'], row['failed'], row['byte_count']), ('loads', True, 12))

    def test_bounded_background_observation_logs_only_on_flush(self):
        cls, timing, _ = self.fixture()
        logs = []
        thread = threading.Thread(target=lambda: [cls.dumps(i) for i in range(80)], name='QueueFeederThread')
        thread.start(); thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(timing.pending), 64)
        self.assertEqual(logs, [])
        self.assertEqual({r['thread_name'] for r in timing.pending}, {'QueueFeederThread'})
        timing.flush(lambda name, **row: logs.append((name, row)))
        self.assertEqual(len(logs), 64)
        self.assertEqual(len(timing.pending), 0)

    def test_close_restores_exact_descriptors_without_overwriting_foreign_replacement(self):
        cls, timing, original = self.fixture()
        foreign = staticmethod(lambda data: 'foreign')
        cls.loads = foreign
        timing.close(); timing.close()
        self.assertIs(vars(cls)['loads'], foreign)
        self.assertIs(vars(cls)['dumps'], original['dumps'])
        count = len(timing.pending)
        timing.replacements['loads'].__get__(None, cls)(pickle.dumps(1))
        self.assertEqual(len(timing.pending), count)

    def test_real_multiprocessing_descriptor_and_fast_path(self):
        original = {name: vars(ForkingPickler)[name] for name in ('loads', 'dumps')}
        timing = PickleTimings(clock=lambda: 1., cpu_clock=lambda: 2.)
        try:
            obj = (b'private bytes', {'a': [1, 2]})
            self.assertEqual(ForkingPickler.loads(ForkingPickler.dumps(obj)), obj)
            self.assertEqual(len(timing.pending), 0)
        finally:
            timing.close()
        for name, descriptor in original.items():
            self.assertIs(vars(ForkingPickler)[name], descriptor)


if __name__ == '__main__':
    unittest.main()
