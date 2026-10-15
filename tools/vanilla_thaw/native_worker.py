"""One owned x86 command thread with an idle Win32 message pump.

Only our glue is generated here. The original game entrypoint is never called.
The parent owns the child lifetime through its kill-on-close Job Object; remote
allocations and duplicated event handles live only until that child terminates.
"""
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
import hashlib
from contextlib import contextmanager
from collections import OrderedDict
import math
import struct
import threading
import time

import probe_loader as p

# One producer, one consumer. Event synchronization publishes each command.
COMMAND, REQUEST, DONE, STATE, TID, COUNT = range(0, 24, 4)
SET_EVENT, WAIT, PEEK, TRANSLATE, DISPATCH, GET_TID = range(24, 48, 4)
LAST_WAIT, RETURN, MSG = 56, 60, 64
# Opt-in diagnostics only: MSG32 ends at +92, export names begin at +128.
QPC, QPC_BEGIN, QPC_END = 48, 96, 104
IDLE, BUSY, FAILED = 1, 2, 3
# Busy live frames use about 40 commands; 32 pages evicted their next frame's glue.
COMMAND_CACHE_LIMIT = 64
EXPORTS = (("SetEvent", SET_EVENT, "kernel32.dll"),
           ("MsgWaitForMultipleObjectsEx", WAIT, "user32.dll"),
           ("PeekMessageW", PEEK, "user32.dll"),
           ("TranslateMessage", TRANSLATE, "user32.dll"),
           ("DispatchMessageW", DISPATCH, "user32.dll"),
           ("GetCurrentThreadId", GET_TID, "kernel32.dll"))


def worker_stub(mailbox, modules, request, done, *, profile=False):
    """Our stdcall thread entry; every command is also stdcall void*(void*)."""
    for value in (mailbox, request, done, *(modules[name]["base"] for name in
                                           ("kernel32.dll", "user32.dll"))):
        p.require(0 < value < 0x100000000, "Worker requires x86 addresses/handles")
    p.require(mailbox <= 0xfffff000, "Worker mailbox exceeds x86 space")
    data = bytearray(512)
    struct.pack_into("<II", data, REQUEST, request, done)
    code, labels, branches = bytearray(), {}, []
    word = lambda value: struct.pack("<I", value)

    def push(value):
        code.extend(b"\x68" + word(value))

    def label(name):
        p.require(name not in labels, "Duplicate worker label")
        labels[name] = len(code)

    def branch(opcode, target):
        code.extend(opcode)
        branches.append((len(code), target))
        code.extend(bytes(4))

    def call(offset):
        code.extend(b"\xff\x56" + bytes([offset]))  # call [esi+offset]

    def set_word(offset, value):
        code.extend(b"\xc7\x46" + bytes([offset]) + word(value))

    # Preserve nonvolatile registers; all original callees must honor x86 ABI.
    code.extend(b"\x56\x57\xbe" + word(mailbox))
    cursor = 128
    exports = EXPORTS + (("QueryPerformanceCounter", QPC, "kernel32.dll"),) if profile else EXPORTS
    for name, offset, module in exports:
        text = name.encode("ascii") + b"\0"
        data[cursor:cursor + len(text)] = text
        push(mailbox + cursor)
        push(modules[module]["base"])
        code.extend(b"\xff\x15" + word(0x68e0f4))  # stock GetProcAddress IAT
        code.extend(b"\x89\x46" + bytes([offset]) + b"\x85\xc0")
        branch(b"\x0f\x84", "failed")
        cursor += len(text)
    call(GET_TID)
    code.extend(b"\x89\x46" + bytes([TID]))
    # Ensure this thread has a message queue before advertising readiness.
    for value in (0, 0, 0, 0, mailbox + MSG):
        push(value)
    call(PEEK)
    set_word(STATE, IDLE)
    code.extend(b"\xff\x76" + bytes([DONE]))
    call(SET_EVENT)
    code.extend(b"\x85\xc0")
    branch(b"\x0f\x84", "failed")

    label("wait")
    for value in (4, 0x4ff, 0xffffffff, mailbox + REQUEST, 1):
        push(value)  # INPUTAVAILABLE, ALLINPUT, INFINITE, &event, one handle
    call(WAIT)
    code.extend(b"\x89\x46" + bytes([LAST_WAIT]) + b"\x85\xc0")
    branch(b"\x0f\x84", "command")
    code.extend(b"\x83\xf8\x01")
    branch(b"\x0f\x85", "failed")
    code.extend(b"\xbf" + word(32))
    label("pump")
    for value in (1, 0, 0, 0, mailbox + MSG):
        push(value)  # PM_REMOVE, max/min filters, all HWNDs, MSG32
    call(PEEK)
    code.extend(b"\x85\xc0")
    branch(b"\x0f\x84", "wait")
    code.extend(b"\x83\x7e" + bytes([MSG + 4, 0x12]))  # WM_QUIT
    branch(b"\x0f\x84", "failed")
    push(mailbox + MSG)
    call(TRANSLATE)
    push(mailbox + MSG)
    call(DISPATCH)
    code.extend(b"\x4f")  # Bound each drain; ready commands take priority.
    branch(b"\x0f\x85", "pump")
    branch(b"\xe9", "wait")

    label("command")
    code.extend(b"\x83\x3e\x00")
    branch(b"\x0f\x84", "failed")
    set_word(STATE, BUSY)
    if profile:
        push(mailbox + QPC_BEGIN)
        call(QPC)
        code.extend(b"\x85\xc0")
        branch(b"\x0f\x84", "failed")
    push(0)
    code.extend(b"\xff\x16\x89\x46" + bytes([RETURN]))
    if profile:
        push(mailbox + QPC_END)
        call(QPC)
        code.extend(b"\x85\xc0")
        branch(b"\x0f\x84", "failed")
    code.extend(b"\xff\x46" + bytes([COUNT]))
    set_word(STATE, IDLE)
    code.extend(b"\xff\x76" + bytes([DONE]))
    call(SET_EVENT)
    code.extend(b"\x85\xc0")
    branch(b"\x0f\x85", "wait")
    label("failed")
    set_word(STATE, FAILED)
    code.extend(b"\xb8" + word(0x71) + b"\x5f\x5e\xc2\x04\x00")
    for offset, target in branches:
        struct.pack_into("<i", code, offset, labels[target] - offset - 4)
    p.require(cursor <= len(data) and len(code) < 4096, "Worker exceeds owned pages")
    return bytes(code), bytes(data)


def profile_clock(api):
    """Verify once that Python and the owned Win32 worker share QPC's epoch."""
    for name in ('QueryPerformanceFrequency', 'QueryPerformanceCounter'):
        function = getattr(api, name)
        function.argtypes, function.restype = [C.POINTER(C.c_int64)], W.BOOL
    frequency, counter = C.c_int64(), C.c_int64()
    p.success(api.QueryPerformanceFrequency(C.byref(frequency)), 'Read profiling QPC frequency')
    info = time.get_clock_info('perf_counter')
    p.require(frequency.value > 0 and info.monotonic and not info.adjustable and
              'QueryPerformanceCounter' in info.implementation and
              math.isclose(info.resolution, 1/frequency.value, rel_tol=1e-9),
              'Profiling clock is not the shared Win32 QPC clock')
    before = time.perf_counter()
    p.success(api.QueryPerformanceCounter(C.byref(counter)), 'Read profiling QPC clock')
    after = time.perf_counter()
    tolerance = 2/frequency.value
    p.require(before-tolerance <= counter.value/frequency.value <= after+tolerance,
              'Profiling clocks have different epochs')
    return frequency.value


class NativeWorker:
    def __init__(self, api, process, modules, deadline, log):
        self.api, self.process, self.log = api, process, log
        self.request = self.done = self.thread = None
        self.count = 0
        self.command_page = None
        self.command_preparations = 0
        self.command_cache_hits = 0
        self.command_cache = OrderedDict()
        self.command_failed = False
        self._operation = None
        self._operation_lock = threading.Lock()
        self._waiting = None
        self._waiting_lock = threading.Lock()
        self.profile_frequency = 0
        self._profile_returned = None
        try:
            if p.PROFILE_RESOURCE_CALLS:
                self.profile_frequency = profile_clock(api)
            self.request = p.success(api.CreateEventW(None, False, False, None), "Create owned worker request event")
            self.done = p.success(api.CreateEventW(None, False, False, None), "Create owned worker completion event")
            remote_events = []
            for handle in (self.request, self.done):
                duplicate = W.HANDLE()
                p.success(api.DuplicateHandle(api.GetCurrentProcess(), handle, process, C.byref(duplicate), 0, False, 2), "Duplicate owned event into owned child")
                remote_events.append(duplicate.value)
            self.mailbox = p.success(api.VirtualAllocEx(process, None, 4096, 0x3000, 4), "Allocate owned worker mailbox")
            self.code_address = p.success(api.VirtualAllocEx(process, None, 4096, 0x3000, 4), "Allocate owned worker glue")
            p.require(self.code_address < 0x100000000, "Worker code exceeds x86 space")
            self.code, data = worker_stub(self.mailbox, modules, *remote_events,
                                         profile=bool(self.profile_frequency))
            p.remote_write(api, process, self.mailbox, data)
            p.remote_write(api, process, self.code_address, self.code)
            old = W.DWORD()
            p.success(api.VirtualProtectEx(process, self.code_address, 4096, 0x20, C.byref(old)), "Seal owned worker RX")
            p.success(api.FlushInstructionCache(process, self.code_address, len(self.code)), "Flush owned worker")
            tid = W.DWORD()
            self.thread = p.success(api.CreateRemoteThread(process, None, 0, self.code_address, None, 0, C.byref(tid)), "Create retained owned command thread")
            self.tid = tid.value
            waited, returned = self.wait(deadline)
            state = self.snapshot()
            log("native_worker_started", wait_result=waited, return_value=returned,
                worker_tid=self.tid, mailbox=state, glue_bytes=len(self.code),
                glue_sha256=hashlib.sha256(self.code).hexdigest(),
                message_pump=True, original_primary_resumed=False,
                qpc_frequency=self.profile_frequency)
            p.require(waited == 0 and state[STATE // 4] == IDLE and
                      state[TID // 4] == self.tid and state[COUNT // 4] == 0,
                      "Owned command worker did not initialize")
        except BaseException:
            self.close()
            raise

    def snapshot(self):
        return struct.unpack("<16I", p.remote_read(self.api, self.process, self.mailbox, 64))

    def _pulse_waiting(self, deadline, timings=None):
        waiting = self._waiting
        if waiting is not None:
            now = time.monotonic()
            if waiting[2] <= now < deadline:
                waiting[2] = now + .020
                if timings is None:
                    waiting[0]()
                else:
                    started = time.perf_counter()
                    try:
                        waiting[0]()
                    finally:
                        timings['callback_ms'] = timings.get('callback_ms', 0.) + (time.perf_counter()-started)*1000
                        timings['callback_count'] = timings.get('callback_count', 0) + 1

    def wait(self, deadline, *, timings=None):
        handles = (W.HANDLE * 2)(self.done, self.thread)
        waiting = self._waiting
        if waiting is not None:
            p.require(waiting[1] == threading.get_ident(), "Owned wait callback belongs to another host thread")
        while True:
            now = time.monotonic()
            if waiting is not None and now >= deadline:
                return 258, None
            milliseconds = max(0, int((deadline - now) * 1000))
            if waiting is not None:
                milliseconds = min(20, milliseconds)
            started = time.perf_counter() if timings is not None else None
            result = self.api.WaitForMultipleObjects(2, handles, False, milliseconds)
            if timings is not None:
                returned_at = time.perf_counter()
                if result == 0 and self.profile_frequency:
                    self._profile_returned = returned_at
                timings['wait_ms'] = timings.get('wait_ms', 0.) + (returned_at-started)*1000
                timings['wait_count'] = timings.get('wait_count', 0) + 1
            if result != 258 or waiting is None or not milliseconds or time.monotonic() >= deadline:
                break
            # Host transport upkeep only: this command still owns the worker,
            # and neither its source state nor its fixed deadline advances.
            self._pulse_waiting(deadline, timings)
        if result == 1:
            code = W.DWORD()
            p.success(self.api.GetExitCodeThread(self.thread, C.byref(code)), "Read owned worker exit")
            return result, code.value
        if result == 0:
            return result, self.snapshot()[RETURN // 4]
        return result, None

    def execute(self, address, deadline, *, timings=None):
        profile_here = timings is None and p.PROFILE_RESOURCE_CALLS
        if profile_here:
            timings = {}
        started = time.perf_counter() if timings is not None else None
        self.check_call_access()
        p.require(0 < address < 0x100000000, "Invalid owned command address")
        if self._operation is not None:
            # The operation deadline is fixed at scope entry; only the shorter
            # per-command timeout starts again for this particular command.
            deadline = time.monotonic() + self._operation[1]
            if self._operation[0] is not None:
                deadline = min(self._operation[0], deadline)
        p.require(time.monotonic() < deadline, "Owned worker deadline expired")
        before = self.snapshot()
        p.require(before[STATE // 4] == IDLE and before[TID // 4] == self.tid and
                  before[COUNT // 4] == self.count and
                  self.api.WaitForSingleObject(self.thread, 0) == 258,
                  "Owned worker is not idle/alive or its command sequence changed")
        p.remote_write(self.api, self.process, self.mailbox + COMMAND, struct.pack("<I", address))
        self.command_failed = True
        self._profile_returned = None
        submitted = time.perf_counter() if self.profile_frequency and timings is not None else None
        p.success(self.api.SetEvent(self.request), "Publish owned worker command")
        if timings is not None:
            timings['dispatch_ms'] = (time.perf_counter()-started)*1000
        # Several fast calls can exhaust the lease without one wait timing out.
        self._pulse_waiting(deadline, timings)
        waited, returned = self.wait(deadline) if timings is None else self.wait(deadline, timings=timings)
        if self._operation is not None:
            p.require(time.monotonic() < deadline, "Owned command exceeded its operation or command deadline")
        if waited == 0:
            after = self.snapshot()
            p.require(after[STATE // 4] == IDLE and after[TID // 4] == self.tid and
                      after[COUNT // 4] == self.count + 1,
                      "Owned worker completion has invalid thread/state/sequence")
            self.count += 1
            self.command_failed = False
            # Read only after the existing idle/thread/command-count guards and
            # only for slow calls. This never waits for or executes more work.
            if (submitted is not None and self._profile_returned is not None and
                    time.perf_counter()-started > .008):
                first, last = struct.unpack('<QQ', p.remote_read(
                    self.api, self.process, self.mailbox + QPC_BEGIN, 16))
                begin, end = first/self.profile_frequency, last/self.profile_frequency
                tolerance = 2/self.profile_frequency
                valid = (first > 0 and first <= last and submitted-tolerance <= begin and
                         end <= self._profile_returned+tolerance)
                timings['worker_qpc_valid'] = valid
                if valid:
                    timings.update(native_queue_ms=max(0., begin-submitted)*1000,
                        native_execution_ms=(end-begin)*1000,
                        native_completion_to_host_ms=max(0., self._profile_returned-end)*1000,
                        native_submitted_qpc_s=submitted, native_begin_qpc_s=begin,
                        native_end_qpc_s=end, host_returned_qpc_s=self._profile_returned)
        if profile_here:
            total_ms = (time.perf_counter()-started)*1000
            if total_ms > 8:
                self.log('native_slow_worker_command', address=address, total_ms=total_ms,
                         worker_tid=self.tid, wait_result=waited, **timings)
        return waited, returned

    def check_call_access(self):
        """Check before touching a shared call record or command page.

        Outside an explicit operation the original single-producer bootstrap
        contract is unchanged. A scoped operation belongs to its entering host
        thread; a foreign caller cannot prepare, publish or clear its record.
        """
        operation = self._operation
        if self._waiting is not None:
            p.require(self._waiting[1] == threading.get_ident(), "Owned wait callback belongs to another host thread")
        if operation is not None:
            p.require(operation[2] == threading.get_ident(), "Owned operation belongs to another host thread")
            if operation[0] is not None and time.monotonic() >= operation[0]:
                self.command_failed = True
                raise RuntimeError("Owned operation deadline expired")
        p.require(not self.command_failed, "Owned worker is unusable after a failed command")

    @contextmanager
    def waiting_callback(self, callback):
        """Maintain already accepted host transport while a command is busy.

        The callback runs on the calling host thread at least 20 ms apart, after
        publishing commands or a wait timeout, never beyond the command deadline.
        It must not read or invoke original engine state, invent source progress
        or extend the deadline. A failed callback leaves the in-flight command
        poisoned; the existing owner must terminate the whole child.
        """
        p.require(callable(callback), "Owned wait callback must be callable")
        self.check_call_access()
        p.require(self._waiting_lock.acquire(blocking=False), "Owned wait callbacks cannot nest or overlap")
        try:
            p.require(self._waiting is None, "Owned wait callback already exists")
            self._waiting = [callback, threading.get_ident(), time.monotonic() + .020]
            yield self
        finally:
            self._waiting = None
            self._waiting_lock.release()

    @contextmanager
    def operation_budget(self, absolute_deadline, command_timeout):
        """Run one nonnested operation on the same retained worker.

        Entry and successful exit require IDLE/alive and matching TID/count.
        Inside, commands override captured bootstrap deadlines with the lesser
        of the optional immutable absolute deadline and their own timeout. None
        explicitly selects continuous gameplay with only per-command timeouts. Outside,
        caller deadlines keep their previous meaning. This does not renew the
        child lifetime or provide cancellation/teardown; timeout poisons the
        worker and its owner must terminate the whole child. No native command
        is started by entering/leaving this context manager.
        """
        # None explicitly selects an interactive lifetime; every native call
        # still has its own finite timeout and the same single-owner checks.
        p.require((absolute_deadline is None or (type(absolute_deadline) in (int, float) and
                  math.isfinite(absolute_deadline) and absolute_deadline > time.monotonic())) and
                  type(command_timeout) in (int, float) and math.isfinite(command_timeout) and
                  command_timeout > 0,
                  "Operation requires a future finite deadline (or None) and positive finite command timeout")
        p.require(self._operation_lock.acquire(blocking=False), "Owned operations cannot nest or overlap")
        try:
            p.require(self._operation is None, "Owned operation already exists")
            self._operation = (absolute_deadline, command_timeout, threading.get_ident())
            self.check_call_access()
            state = self.snapshot()
            p.require(state[STATE // 4] == IDLE and state[TID // 4] == self.tid and
                      state[COUNT // 4] == self.count and self.api.WaitForSingleObject(self.thread, 0) == 258,
                      "Owned operation must start on the idle live worker")
            try:
                yield self
                self.check_call_access()
                state = self.snapshot()
                p.require(state[STATE // 4] == IDLE and state[TID // 4] == self.tid and
                          state[COUNT // 4] == self.count and self.api.WaitForSingleObject(self.thread, 0) == 258,
                          "Owned operation did not leave its worker idle/alive")
            except BaseException:
                # Do not hide the original refusal with a second timeout error.
                if absolute_deadline is not None and time.monotonic() >= absolute_deadline:
                    self.command_failed = True
                raise
        finally:
            self._operation = None
            self._operation_lock.release()

    def prepare_command(self, code):
        """Reuse bounded, byte-identical sealed glue after the prior call returns.

        The mailbox's IDLE completion is published by the separate permanent
        worker stub after it returns from this page. No game code is rewritten.
        The page is writable or executable, never both at once.
        """
        self.check_call_access()
        p.require(isinstance(code, bytes) and 0 < len(code) <= 4096,
                  "Owned command must fit its single page")
        state = self.snapshot()
        p.require(state[STATE // 4] == IDLE and state[TID // 4] == self.tid and
                  state[COUNT // 4] == self.count and self.api.WaitForSingleObject(self.thread, 0) == 258,
                  "Cannot rewrite command page while worker is busy or dead")
        try:
            if code in self.command_cache:
                address = self.command_cache[code]
                self._verify_command_page(address, code)
                self.command_cache.move_to_end(code)
                self.command_cache_hits += 1
                self.command_page = address
                return address
            previous = W.DWORD()
            if len(self.command_cache) < COMMAND_CACHE_LIMIT:
                self.command_page = p.success(self.api.VirtualAllocEx(self.process, None, 4096, 0x3000, 4),
                                              "Allocate reusable owned command page")
                p.require(0 < self.command_page <= 0xfffff000, "Owned command page exceeds x86 space")
            else:
                old_code, address = next(iter(self.command_cache.items()))
                self._verify_command_page(address, old_code)
                self.command_cache.popitem(last=False)
                self.command_page = address
                p.success(self.api.VirtualProtectEx(self.process, self.command_page, 4096, 4, C.byref(previous)),
                          "Make idle owned command page writable")
                p.require(previous.value == 0x20, "Owned command page was not sealed RX")
            p.remote_write(self.api, self.process, self.command_page, code)
            p.success(self.api.VirtualProtectEx(self.process, self.command_page, 4096, 0x20, C.byref(previous)),
                      "Seal reusable owned command page RX")
            p.require(previous.value == 4, "Owned command page was not writable")
            p.success(self.api.FlushInstructionCache(self.process, self.command_page, len(code)),
                      "Flush reusable owned command page")
            self.command_preparations += 1
            self.command_cache[code] = self.command_page
            return self.command_page
        except BaseException:
            self.command_failed = True
            raise

    def _verify_command_page(self, address, code):
        # The owner API requires Win64 Python even for its x86 child. Query the
        # real page protection without rewriting it or flushing executable code.
        info = C.create_string_buffer(48)
        p.require(self.api.VirtualQueryEx(self.process, address, info, 48) == 48,
                  "Cannot inspect owned command page")
        base, allocation, _, size, state, protection, kind = struct.unpack('<QQI4xQIII4x', info.raw)
        p.require(base == allocation == address and size >= 4096 and
                  state == 0x1000 and protection == 0x20 and kind == 0x20000,
                  "Cached owned command page lost its private RX allocation")
        p.require(p.remote_read(self.api, self.process, address, len(code)) == code,
                  "Cached owned command glue changed")

    def verify(self):
        p.require(self.api.WaitForSingleObject(self.thread, 0) == 258, "Retained worker exited")
        state = self.snapshot()
        p.require(state[STATE // 4] == IDLE and state[TID // 4] == self.tid and
                  state[COUNT // 4] == self.count, "Retained worker lost its final idle state")
        p.require(p.remote_read(self.api, self.process, self.code_address, len(self.code)) == self.code,
                  "Owned worker code changed")
        for code, address in self.command_cache.items():
            self._verify_command_page(address, code)
        self.log("native_worker_pass", worker_tid=self.tid, commands=self.count,
                 command_glue_pages=len(self.command_cache),
                 command_glue_preparations=self.command_preparations,
                 command_glue_cache_hits=self.command_cache_hits,
                 same_thread_for_all_native_calls=True, idle_message_pump=True,
                 original_primary_resumed=False)

    def close(self):
        # Called after child termination on the normal path. No forced individual
        # thread termination: a failed command causes the whole owned child to end.
        for name in ("thread", "request", "done"):
            handle = getattr(self, name, None)
            if handle:
                self.api.CloseHandle(handle)
                setattr(self, name, None)


def retained_worker(api, process):
    worker = getattr(api, "_thaw_native_worker", None)
    p.require(worker is not None and worker.process == process, "Native call requires its owned retained worker")
    return worker
