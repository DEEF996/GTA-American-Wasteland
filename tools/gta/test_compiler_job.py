"""Mocked compiler job ownership; creates no processes or real Windows jobs."""
import ctypes as C
import unittest
from unittest.mock import Mock, patch

import compiler_job as jobs


class CompilerJobTests(unittest.TestCase):
    def setUp(self):
        self.api = Mock()
        self.api.CreateJobObjectW.return_value = 42
        self.api.SetInformationJobObject.return_value = True
        self.api.CloseHandle.return_value = True
        self.api.OpenJobObjectW.return_value = 77
        self.api.GetCurrentProcess.return_value = 99
        self.api.AssignProcessToJobObject.return_value = True
        self.patch = patch.object(jobs, "_api", return_value=self.api)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        C.set_last_error(0)

    def test_parent_sets_kill_on_close_without_assigning_itself(self):
        job = jobs.OwnedCompilerJob()
        handle, kind, pointer, size = self.api.SetInformationJobObject.call_args.args
        self.assertEqual((handle, kind, size), (42, 9, 144))
        self.assertEqual(pointer._obj.basic.flags, 0x2000)
        self.assertTrue(job.name.startswith(f"Local\\THAW_GTA_COMPILER_{job.owner_pid}_"))
        self.api.AssignProcessToJobObject.assert_not_called()
        job.close()
        job.close()
        self.api.CloseHandle.assert_called_once_with(42)

    def test_worker_joins_only_its_current_process_and_closes_borrowed_handle(self):
        with patch.object(jobs.os, "getpid", return_value=222):
            jobs.join_compiler_job("parent-job", 111)
        self.api.OpenJobObjectW.assert_called_once_with(1, False, "parent-job")
        self.api.AssignProcessToJobObject.assert_called_once_with(77, 99)
        self.api.CloseHandle.assert_called_once_with(77)

    def test_initializer_refuses_parent_before_opening_any_job(self):
        with patch.object(jobs.os, "getpid", return_value=111):
            with self.assertRaisesRegex(RuntimeError, "Only the owned"):
                jobs.join_compiler_job("parent-job", 111)
        self.api.OpenJobObjectW.assert_not_called()
        self.api.AssignProcessToJobObject.assert_not_called()

    def test_missing_parent_job_fails_before_any_compilation_can_begin(self):
        self.api.OpenJobObjectW.return_value = 0
        with patch.object(jobs.os, "getpid", return_value=222), self.assertRaises(OSError):
            jobs.join_compiler_job("gone-parent-job", 111)
        self.api.AssignProcessToJobObject.assert_not_called()
        self.api.CloseHandle.assert_not_called()

    def test_assignment_failure_releases_temporary_handle(self):
        self.api.AssignProcessToJobObject.return_value = False
        with patch.object(jobs.os, "getpid", return_value=222), self.assertRaises(OSError):
            jobs.join_compiler_job("parent-job", 111)
        self.api.CloseHandle.assert_called_once_with(77)

    def test_configuration_failure_releases_owned_handle(self):
        self.api.SetInformationJobObject.return_value = False
        with self.assertRaises(OSError):
            jobs.OwnedCompilerJob()
        self.api.CloseHandle.assert_called_once_with(42)

    def test_preexisting_job_is_not_modified(self):
        def existing(*_):
            C.set_last_error(183)
            return 42
        self.api.CreateJobObjectW.side_effect = existing
        with self.assertRaisesRegex(RuntimeError, "pre-existing"):
            jobs.OwnedCompilerJob()
        self.api.SetInformationJobObject.assert_not_called()
        self.api.CloseHandle.assert_called_once_with(42)


if __name__ == "__main__":
    unittest.main()
