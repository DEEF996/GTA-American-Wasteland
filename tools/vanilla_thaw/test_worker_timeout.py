import struct
import unittest
from unittest.mock import Mock

import probe_worker_timeout as diagnostic


class TimeoutTests(unittest.TestCase):
    def api(self):
        api=Mock()
        api.GetProcessId.return_value=42;api.GetProcessIdOfThread.return_value=42
        api.GetThreadId.return_value=12;api.GetThreadTimes.return_value=False
        api.SuspendThread.return_value=2;api.ResumeThread.return_value=3
        def context(thread,pointer):
            pointer._obj.Esp=0x20000;pointer._obj.Eip=0x40000;return True
        api.Wow64GetThreadContext.side_effect=context
        return api

    def test_foreign_thread_never_suspended(self):
        api=self.api();api.GetProcessIdOfThread.return_value=99
        with self.assertRaisesRegex(ValueError,'owned runtime'):
            diagnostic.capture_thread(api,5,6,12,Mock())
        api.SuspendThread.assert_not_called()

    def test_read_failure_still_restores_suspend_count(self):
        api=self.api()
        with self.assertRaises(OSError):
            diagnostic.capture_thread(api,5,6,12,Mock(side_effect=OSError('unreadable')))
        api.ResumeThread.assert_called_once_with(6)

    def test_context_and_stack_are_bounded(self):
        api=self.api();read=Mock(side_effect=lambda address,size:bytes(size))
        result=diagnostic.capture_thread(api,5,6,12,read)
        self.assertTrue(result['suspend_balanced'])
        self.assertEqual(len(result['stack_words']),64)
        self.assertEqual(read.call_count,2)
        api.ResumeThread.assert_called_once_with(6)


if __name__=='__main__':unittest.main()
