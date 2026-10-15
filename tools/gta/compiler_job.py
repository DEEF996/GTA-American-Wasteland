"""Keep only the spawned collision compiler in its parent's Windows lifetime.

The parent retains the sole long-lived handle. The worker joins itself during
the ProcessPool initializer and closes its temporary handle before compiling.
https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects
"""
import ctypes as C
from ctypes import wintypes as W
import os
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "vanilla_thaw"))
from probe_loader import ExtendedJobLimits, success, winapi


def _api():
    api = winapi()
    api.OpenJobObjectW.argtypes = [W.DWORD, W.BOOL, W.LPCWSTR]
    api.OpenJobObjectW.restype = W.HANDLE
    return api


class OwnedCompilerJob:
    def __init__(self):
        self.api, self.handle = _api(), None
        self.owner_pid = os.getpid()
        self.name = f"Local\\THAW_GTA_COMPILER_{self.owner_pid}_{uuid.uuid4().hex}"
        C.set_last_error(0)
        self.handle = success(self.api.CreateJobObjectW(None, self.name), "Create collision compiler job")
        try:
            if C.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
                raise RuntimeError("Refusing a pre-existing collision compiler job")
            limits = ExtendedJobLimits()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            success(self.api.SetInformationJobObject(self.handle, 9, C.byref(limits), C.sizeof(limits)),
                    "Configure collision compiler lifetime")
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.handle is not None:
            success(self.api.CloseHandle(self.handle), "Close collision compiler job")
            self.handle = None


def join_compiler_job(name, owner_pid):
    """ProcessPool initializer: never assign the caller or a foreign process."""
    if type(owner_pid) is not int or owner_pid <= 0 or os.getpid() == owner_pid:
        raise RuntimeError("Only the owned compiler child may join its lifetime job")
    api = _api()
    handle = success(api.OpenJobObjectW(1, False, name), "Open parent collision compiler job")
    try:
        success(api.AssignProcessToJobObject(handle, api.GetCurrentProcess()),
                "Assign collision compiler child to parent lifetime")
    finally:
        success(api.CloseHandle(handle), "Close borrowed collision compiler job")
