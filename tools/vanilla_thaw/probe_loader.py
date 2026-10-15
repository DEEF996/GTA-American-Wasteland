"""Original stock functions executed in our private Rust runtime host.

The original executable is never started. By default the only remote entry is our
five-byte x86 return routine. --allocator additionally calls original MemMan
through owned glue with a fault record. THAW's entrypoint is never called.
Run: python -B probe_loader.py ABS_MANIFEST ABS_NEW_REPORT.jsonl
     [--allocator | --qb ABS_QB_MANIFEST | --script ABS_QB_MANIFEST
      | --animation ABS_ASSET_MANIFEST | --native ABS_QB_MANIFEST]
The parent should additionally enforce an external timeout.
"""
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import time

PROFILE_RESOURCE_CALLS = os.environ.get('GTA_THAW_PROFILE_FRAMES') == '1'

if __name__ == '__main__':
    # Keep ctypes structure identities shared with stock_host/native_worker.
    sys.modules['probe_loader'] = sys.modules[__name__]

FILES = {
    "THAW.exe": (21442560, "ff3351529586eefba74d85a807282e18101844e68ecaa5cee20a0a16bed0b118"),
    "binkw32.dll": (377856, "e3d19e66b6135925116c8e7585b6e6e3746e50d79f1ec863cda615b0189df1a9"),
    "d3dx9_27.dll": (2319568, "a70d571cd675c97c9eeb4a234dba1d667ffb54ec3bb14defb36b3e2f605ae257"),
}
STUB = bytes.fromhex("31c0c20400")
FUNCTIONS = [
    (0x543b20, 24, "b5e94a7db348918a5991dbab7f3c18bcfb7e668de36fedde59744269a6acf9a5"),
    (0x627620, 128, "071bc02f1acb0fe8b3382dfb5d5aace95b50384a489422bfbf85ef2233523751"),
    (0x626ed0, 400, "d4eb55184bf599a059c786a4c9bae290c635e55d26742e6674e56feb2161f0de"),
]
RECORD_FIELDS = ["status", "heap", "init_flag", "singleton", "arena_start", "arena_end",
                 "allocation_bytes", "exit_thread", "veh_handle", "exception_code",
                 "eip", "eax", "ecx", "edx", "esp", "exception_address", "phase_at_exception"]


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def validate_manifest(manifest_path: Path) -> Path:
    require(manifest_path.is_absolute(), "Manifest path must be absolute")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    staged = manifest_path.resolve().parent
    original = Path(manifest["source"])
    require(manifest.get("format") == "gta-thaw-stock-image-probe-v1", "Unsupported manifest format")
    require(manifest.get("entrypoint_allowed") is False, "Manifest must forbid original entrypoint")
    require(manifest.get("original_files_modified") is False, "Manifest must preserve source files")
    require(Path(manifest["directory"]).is_absolute() and Path(manifest["directory"]).resolve() == staged, "Staged directory must equal manifest parent")
    require(original.is_absolute() and original.resolve() != staged, "Refusing the installed original directory")
    entries = manifest.get("files", [])
    require(len(entries) == len(FILES) and {e["path"] for e in entries} == set(FILES), "Manifest must list exactly the three pinned files")
    for entry in entries:
        name = entry["path"]
        size, digest = FILES[name]
        path = staged / name
        require(path.resolve().parent == staged, "File escapes staged directory")
        require(not path.samefile(original / name), "Refusing an original file or hard link")
        require(entry["bytes"] == size and entry["sha256"] == digest, "Manifest pin differs")
        require(path.stat().st_size == size and hashlib.sha256(path.read_bytes()).hexdigest() == digest, f"Staged hash/size mismatch: {name}")
    return staged / "THAW.exe"


class StartupInfo(C.Structure):
    _fields_ = [("cb", W.DWORD), ("reserved", W.LPWSTR), ("desktop", W.LPWSTR),
                ("title", W.LPWSTR), ("x", W.DWORD), ("y", W.DWORD),
                ("x_size", W.DWORD), ("y_size", W.DWORD), ("x_count", W.DWORD),
                ("y_count", W.DWORD), ("fill", W.DWORD), ("flags", W.DWORD),
                ("show", W.WORD), ("reserved_size", W.WORD), ("reserved_ptr", C.c_void_p),
                ("stdin", W.HANDLE), ("stdout", W.HANDLE), ("stderr", W.HANDLE)]


class ProcessInfo(C.Structure):
    _fields_ = [("process", W.HANDLE), ("thread", W.HANDLE),
                ("pid", W.DWORD), ("tid", W.DWORD)]


class ModuleEntry(C.Structure):
    _fields_ = [("size", W.DWORD), ("module_id", W.DWORD), ("pid", W.DWORD),
                ("global_refs", W.DWORD), ("process_refs", W.DWORD),
                ("base", C.c_void_p), ("image_size", W.DWORD), ("module", W.HMODULE),
                ("name", W.WCHAR * 256), ("path", W.WCHAR * 260)]


class JobLimits(C.Structure):
    _fields_ = [("process_time", C.c_int64), ("job_time", C.c_int64),
                ("flags", W.DWORD), ("min_working_set", C.c_size_t),
                ("max_working_set", C.c_size_t), ("active_processes", W.DWORD),
                ("affinity", C.c_size_t), ("priority", W.DWORD), ("scheduling", W.DWORD)]


class ExtendedJobLimits(C.Structure):
    _fields_ = [("basic", JobLimits), ("io_counters", C.c_uint64 * 6),
                ("process_memory", C.c_size_t), ("job_memory", C.c_size_t),
                ("peak_process_memory", C.c_size_t), ("peak_job_memory", C.c_size_t)]


def winapi():
    require(sys.platform == "win32" and C.sizeof(C.c_void_p) == 8, "Run using 64-bit Windows Python")
    require((C.sizeof(StartupInfo), C.sizeof(ProcessInfo), C.sizeof(ModuleEntry)) == (104, 24, 1080), "Unexpected Win64 ABI layout")
    require(C.sizeof(ExtendedJobLimits) == 144 and ExtendedJobLimits.io_counters.offset == 64, "Unexpected Win64 Job Object layout")
    api = C.WinDLL("kernel32", use_last_error=True)
    signatures = {
        "CreateProcessW": ([W.LPCWSTR, W.LPWSTR, C.c_void_p, C.c_void_p, W.BOOL, W.DWORD, C.c_void_p, W.LPCWSTR, C.POINTER(StartupInfo), C.POINTER(ProcessInfo)], W.BOOL),
        "CreateJobObjectW": ([C.c_void_p, W.LPCWSTR], W.HANDLE),
        "SetInformationJobObject": ([W.HANDLE, C.c_int, C.c_void_p, W.DWORD], W.BOOL),
        "AssignProcessToJobObject": ([W.HANDLE, W.HANDLE], W.BOOL),
        "CloseHandle": ([W.HANDLE], W.BOOL),
        "SetErrorMode": ([W.UINT], W.UINT),
        "SuspendThread": ([W.HANDLE], W.DWORD),
        "ResumeThread": ([W.HANDLE], W.DWORD),
        "QueryFullProcessImageNameW": ([W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD)], W.BOOL),
        "IsWow64Process": ([W.HANDLE, C.POINTER(W.BOOL)], W.BOOL),
        "VirtualAllocEx": ([W.HANDLE, C.c_void_p, C.c_size_t, W.DWORD, W.DWORD], C.c_void_p),
        "VirtualProtectEx": ([W.HANDLE, C.c_void_p, C.c_size_t, W.DWORD, C.POINTER(W.DWORD)], W.BOOL),
        "VirtualQueryEx": ([W.HANDLE, C.c_void_p, C.c_void_p, C.c_size_t], C.c_size_t),
        "WriteProcessMemory": ([W.HANDLE, C.c_void_p, C.c_void_p, C.c_size_t, C.POINTER(C.c_size_t)], W.BOOL),
        "ReadProcessMemory": ([W.HANDLE, C.c_void_p, C.c_void_p, C.c_size_t, C.POINTER(C.c_size_t)], W.BOOL),
        "FlushInstructionCache": ([W.HANDLE, C.c_void_p, C.c_size_t], W.BOOL),
        "CreateRemoteThread": ([W.HANDLE, C.c_void_p, C.c_size_t, C.c_void_p, C.c_void_p, W.DWORD, C.POINTER(W.DWORD)], W.HANDLE),
        "CreateEventW": ([C.c_void_p, W.BOOL, W.BOOL, W.LPCWSTR], W.HANDLE),
        "SetEvent": ([W.HANDLE], W.BOOL),
        "GetCurrentProcess": ([], W.HANDLE),
        "DuplicateHandle": ([W.HANDLE, W.HANDLE, W.HANDLE, C.POINTER(W.HANDLE), W.DWORD, W.BOOL, W.DWORD], W.BOOL),
        "WaitForMultipleObjects": ([W.DWORD, C.POINTER(W.HANDLE), W.BOOL, W.DWORD], W.DWORD),
        "WaitForSingleObject": ([W.HANDLE, W.DWORD], W.DWORD),
        "GetExitCodeThread": ([W.HANDLE, C.POINTER(W.DWORD)], W.BOOL),
        "GetThreadTimes": ([W.HANDLE, *([C.POINTER(C.c_uint64)] * 4)], W.BOOL),
        "QueryThreadCycleTime": ([W.HANDLE, C.POINTER(C.c_uint64)], W.BOOL),
        "GetExitCodeProcess": ([W.HANDLE, C.POINTER(W.DWORD)], W.BOOL),
        "TerminateProcess": ([W.HANDLE, W.UINT], W.BOOL),
        "CreateToolhelp32Snapshot": ([W.DWORD, W.DWORD], W.HANDLE),
        "Module32FirstW": ([W.HANDLE, C.POINTER(ModuleEntry)], W.BOOL),
        "Module32NextW": ([W.HANDLE, C.POINTER(ModuleEntry)], W.BOOL),
    }
    for name, (arguments, result) in signatures.items():
        function = getattr(api, name)
        function.argtypes, function.restype = arguments, result
    return api


def success(value, operation):
    if not value:
        raise C.WinError(C.get_last_error(), operation)
    return value


def module_snapshot(api, pid, deadline):
    invalid = C.c_void_p(-1).value
    # Windows documents retrying ERROR_BAD_LENGTH when the loader list changes.
    # Keep this experiment bounded even if the child never finishes loading.
    for attempt in range(1, 9):
        snapshot = api.CreateToolhelp32Snapshot(0x18, pid)
        error = C.get_last_error() if snapshot == invalid else 0
        if snapshot != invalid or error != 24 or attempt == 8:
            return snapshot, attempt, error
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return snapshot, attempt, error
        time.sleep(min(0.001, remaining))


def modules(api, pid, deadline):
    snapshot, attempts, error = module_snapshot(api, pid, deadline)
    if error:
        return {"error": error, "snapshot_attempts": attempts, "modules": []}
    result = []
    try:
        entry = ModuleEntry()
        entry.size = C.sizeof(entry)
        if not api.Module32FirstW(snapshot, C.byref(entry)):
            return {"error": C.get_last_error(), "snapshot_attempts": attempts, "modules": []}
        while True:
            result.append({"name": entry.name, "path": entry.path, "base": entry.base, "bytes": entry.image_size})
            if not api.Module32NextW(snapshot, C.byref(entry)):
                error = C.get_last_error()
                return {"error": 0 if error == 18 else error, "snapshot_attempts": attempts, "modules": result}
            require(len(result) < 512, "Unexpected module list length")
    finally:
        api.CloseHandle(snapshot)


def remote_read(api, process, address, count):
    buffer = C.create_string_buffer(count)
    transferred = C.c_size_t()
    success(api.ReadProcessMemory(process, address, buffer, count, C.byref(transferred)), "ReadProcessMemory owned diagnostic")
    require(transferred.value == count, "Partial diagnostic read")
    return buffer.raw


def remote_write(api, process, address, data):
    buffer = C.create_string_buffer(data)
    transferred = C.c_size_t()
    success(api.WriteProcessMemory(process, address, buffer, len(data), C.byref(transferred)), "WriteProcessMemory owned allocation")
    require(transferred.value == len(data), "Partial owned-buffer write")


def allocator_stub(code_base, data_base, kernel32_base):
    """Assemble only our ABI glue; no original instruction bytes are embedded.

    Record status: 1 setup, 2 native call, 3 native returned, 4 exception,
    5 setup failed, 6 completed. Original IAT addresses are exact pinned metadata.
    """
    for address in (code_base, data_base, kernel32_base):
        require(0x10000 <= address <= 0xFFFFEFFF, "Invalid owned x86 base")
    word = lambda value: struct.pack("<I", value)
    code = bytearray()
    failure_fixups = []

    def push(value):
        code.extend(b"\x68" + word(value))

    def set_record(offset, value):
        code.extend(b"\xc7\x05" + word(data_base + offset) + word(value))

    def store_eax(offset):
        code.extend(b"\xa3" + word(data_base + offset))

    def load_eax(address):
        code.extend(b"\xa1" + word(address))

    def call_iat(address):
        code.extend(b"\xff\x15" + word(address))

    def fail_if_zero():
        code.extend(b"\x85\xc0\x0f\x84")
        failure_fixups.append(len(code))
        code.extend(bytes(4))

    set_record(0, 1)
    push(data_base + 128)  # ExitThread is resolved before installing the handler.
    push(kernel32_base)
    call_iat(0x68e0f4)  # Original OS-resolved GetProcAddress slot.
    store_eax(28)
    fail_if_zero()
    push(data_base + 208)
    push(kernel32_base)
    call_iat(0x68e0f4)
    store_eax(448)  # The lower record also contains shared native-call outputs.
    fail_if_zero()
    push(data_base + 160)
    push(kernel32_base)
    call_iat(0x68e0f4)
    fail_if_zero()
    code.extend(b"\x68")
    handler_fixup = len(code)
    code.extend(bytes(4))
    push(1)
    code.extend(b"\xff\xd0")  # AddVectoredExceptionHandler(1, our_handler)
    store_eax(32)
    fail_if_zero()
    push(0)
    push(0x10010000)
    push(0)
    call_iat(0x68e094)  # HeapCreate
    store_eax(4)
    fail_if_zero()
    code.extend(b"\xa3" + word(0x84f5c4))
    code.extend(b"\xc7\x05" + word(0x84f5c8) + word(2))
    code.extend(b"\xc7\x05" + word(0x84d94c) + word(0))
    set_record(0, 2)
    code.extend(b"\xb8" + word(0x543b20) + b"\xff\xd0")
    set_record(0, 3)
    for address, offset in [(0x83e294, 8), (0x84388c, 12), (0x84376c, 16), (0x843770, 20)]:
        load_eax(address)
        if offset == 8:
            code.extend(b"\x25" + word(0xff))
        store_eax(offset)
    load_eax(data_base + 16)
    code.extend(b"\x50")  # arena pointer
    push(0)
    load_eax(data_base + 4)
    code.extend(b"\x50")
    call_iat(0x68e09c)  # HeapSize: proves the arena belongs to our heap.
    store_eax(24)
    set_record(0, 6)
    code.extend(STUB)
    failure = len(code)
    set_record(0, 5)
    code.extend(b"\xb8\x01\x00\x00\x00\xc2\x04\x00")
    handler = len(code)
    for fixup in failure_fixups:
        code[fixup:fixup + 4] = struct.pack("<i", failure - fixup - 4)
    code[handler_fixup:handler_fixup + 4] = word(code_base + handler)
    load_eax(data_base)
    store_eax(64)
    set_record(0, 4)
    code.extend(b"\x8b\x44\x24\x04\x8b\x10\x8b\x48\x04")
    # edx=EXCEPTION_RECORD, ecx=x86 CONTEXT. Only caller-saved registers used.
    for displacement, destination in [(0, 36), (12, 60)]:
        code.extend(b"\x8b\x82" + word(displacement))
        store_eax(destination)
    for displacement, destination in [(184, 40), (176, 44), (172, 48), (168, 52), (196, 56)]:
        code.extend(b"\x8b\x81" + word(displacement))
        store_eax(destination)
    for displacement, destination in [(164, 460), (160, 464), (156, 468), (180, 472)]:
        code.extend(b"\x8b\x81" + word(displacement))
        store_eax(destination)
    # ExitThread releases the stack. Copy it while live through the OS API so
    # an unreadable fault stack returns failure instead of faulting the handler.
    push(data_base + 452)
    push(64)
    push(data_base + 256)
    load_eax(data_base + 56)
    code.extend(b"\x50")
    push(0xffffffff)
    call_iat(data_base + 448)
    store_eax(456)
    push(70)
    call_iat(data_base + 28)  # ExitThread; our host remains alive for readback.
    code.extend(b"\xcc")  # ExitThread must never return.
    data = bytearray(512)
    data[128:139] = b"ExitThread\0"
    data[160:188] = b"AddVectoredExceptionHandler\0"
    data[208:226] = b"ReadProcessMemory\0"
    require(len(code) < 4096 and len(data) == 512, "Owned glue exceeds its pages")
    return bytes(code), bytes(data)


def probe_allocator(api, process, kernel32_base, deadline, log):
    before = {}
    for address, size, expected in FUNCTIONS:
        contents = remote_read(api, process, address, size)
        digest = hashlib.sha256(contents).hexdigest()
        require(digest == expected, f"Loaded stock function hash differs at {address:#x}")
        before[address] = contents
        log("native_function_hash", before_call=True, address=address, bytes=size, original_sha256=expected, mapped_sha256=digest)
    require(struct.unpack("<I", remote_read(api, process, 0x84388c, 4))[0] == 0 and remote_read(api, process, 0x83e294, 1) == b"\0", "Original manager must be uninitialized")
    code_base = success(api.VirtualAllocEx(process, None, 4096, 0x3000, 4), "Allocate owned allocator glue")
    data_base = success(api.VirtualAllocEx(process, None, 4096, 0x3000, 4), "Allocate owned result record")
    code, data = allocator_stub(code_base, data_base, kernel32_base)
    remote_write(api, process, code_base, code)
    remote_write(api, process, data_base, data)
    old = W.DWORD()
    success(api.VirtualProtectEx(process, code_base, 4096, 0x20, C.byref(old)), "Seal allocator glue RX")
    success(api.FlushInstructionCache(process, code_base, len(code)), "Flush allocator glue")
    from native_worker import retained_worker
    worker = retained_worker(api, process)
    log("before_memman_thread", function=0x543b20, owned_glue=code_base, owned_record=data_base, glue_bytes=len(code), glue_sha256=hashlib.sha256(code).hexdigest(), original_code_patched=False, original_entrypoint_called=False, retained_worker=True, worker_tid=worker.tid)
    waited, returned = worker.execute(code_base, deadline)
    # Read even on timeout: the fault handler may have reported before DLL
    # thread-detach blocked. The parent still terminates the whole child.
    values = struct.unpack("<" + "I" * len(RECORD_FIELDS), remote_read(api, process, data_base, 4 * len(RECORD_FIELDS)))
    result = dict(zip(RECORD_FIELDS, values))
    log("memman_record", thread_wait=waited, worker_return=returned, worker_tid=worker.tid, **result)
    require(waited == 0, "Original MemMan command failed or exceeded ten seconds")
    require(remote_read(api, process, code_base, len(code)) == code, "Our allocator glue changed during execution")
    for address, size, expected in FUNCTIONS:
        contents = remote_read(api, process, address, size)
        log("native_function_hash", before_call=False, address=address, bytes=size, original_sha256=expected, mapped_sha256=hashlib.sha256(contents).hexdigest(), changed_bytes=sum(a != b for a, b in zip(contents, before[address])))
    require(result["status"] == 6 and returned == 0, f"Original MemMan failed; status={result['status']}, worker return={returned}, EIP={result['eip']:#x}")
    require(result["init_flag"] == 1 and result["singleton"] == 0x842b18, "Original manager singleton is invalid")
    require(result["arena_start"] != 0 and result["arena_end"] - result["arena_start"] == 0x10000000, "Original arena must have 256 MiB extent")
    require(0x10000000 <= result["allocation_bytes"] < 0xffffffff, "Arena does not belong to owned Windows heap")
    log("memman_pass", scope="original stock MemMan initialization and owned arena only; no QB or gameplay")
    return data_base


def native_call_stub(function, arguments, record, this=None):
    """Small cdecl/thiscall glue for the explicit pinned QB bootstrap calls."""
    pack = lambda value: struct.pack("<I", value)
    require(len(arguments) <= 12, "Unexpected bootstrap argument count")
    code = bytearray(b"\xc7\x05" + pack(record) + pack(2))
    for value in reversed(arguments):
        code.extend(b"\x68" + pack(value))
    if this is not None:
        code.extend(b"\xb9" + pack(this))
    code.extend(b"\xb8" + pack(function) + b"\xff\xd0")
    if this is None and arguments:
        code.extend(b"\x83\xc4" + bytes([4 * len(arguments)]))
    code.extend(b"\xa3" + pack(record + 68))
    code.extend(b"\xc7\x05" + pack(record) + pack(6))
    code.extend(STUB)
    return bytes(code)


def thread_usage(worker):
    """Optional profiler counters for the already owned command thread only."""
    api = getattr(worker, 'api', None)
    thread = getattr(worker, 'thread', None)
    if thread is None or not all(hasattr(api, name) for name in ('GetThreadTimes', 'QueryThreadCycleTime')):
        return None
    fields, cycles = [C.c_uint64() for _ in range(4)], C.c_uint64()
    if not api.GetThreadTimes(thread, *map(C.byref, fields)) or not api.QueryThreadCycleTime(thread, C.byref(cycles)):
        return None
    return fields[2].value, fields[3].value, cycles.value


def native_call(api, process, pin, arguments, record, deadline, log, this=None):
    from native_worker import retained_worker
    worker = retained_worker(api, process)
    worker.check_call_access()
    name, address, size, digest = pin
    timing = {} if PROFILE_RESOURCE_CALLS else None
    started = time.perf_counter() if timing is not None else None
    original = remote_read(api, process, address, size)
    require(hashlib.sha256(original).hexdigest() == digest, f"Pinned {name} entry differs")
    if timing is not None:
        pinned = time.perf_counter()
        timing['pin_ms'] = (pinned-started)*1000
    # Preserve the live VEH/ExitThread pointers in the first record words.
    remote_write(api, process, record + 36, bytes(36))
    remote_write(api, process, record + 452, bytes(24))
    code = native_call_stub(address, arguments, record, this)
    owned = worker.prepare_command(code)
    if timing is not None:
        prepared = time.perf_counter()
        timing['prepare_ms'] = (prepared-pinned)*1000
    log("before_qb_call", function=name, address=address, pinned_bytes=size, sha256=digest, this=this, arguments=arguments, worker_tid=worker.tid)
    if timing is not None:
        usage_before = thread_usage(worker) if name in ('NodeArrayManLoad', 'NodeArrayMemoryLoad', 'NodeArrayManUnload') else None
        executing = time.perf_counter()
        timing['before_log_ms'] = (executing-prepared)*1000
        waited, returned = worker.execute(owned, deadline, timings=timing)
        completed = time.perf_counter()
        timing['execute_ms'] = (completed-executing)*1000
        if name in ('NodeArrayManLoad', 'NodeArrayMemoryLoad', 'NodeArrayManUnload'):
            usage_after = thread_usage(worker)
            available = (usage_before is not None and usage_after is not None and
                         all(after >= before for before, after in zip(usage_before, usage_after)))
            timing['thread_usage_available'] = available
            if available:
                timing['thread_kernel_ms'] = (usage_after[0]-usage_before[0])/10000
                timing['thread_user_ms'] = (usage_after[1]-usage_before[1])/10000
                timing['thread_cycles'] = usage_after[2]-usage_before[2]
    else:
        waited, returned = worker.execute(owned, deadline)
    values = struct.unpack("<18I", remote_read(api, process, record, 72))
    result = dict(zip(RECORD_FIELDS, values))
    log("qb_call_result", function=name, thread_wait=waited, worker_return=returned, worker_tid=worker.tid, return_value=values[17], **result)
    if waited != 0 or result["status"] != 6:
        # Read only the owned child's bounded native state. Its worker may have
        # already exited; missing diagnostics must never hide the original fault.
        diagnostic = {}
        try:
            globals_ = struct.unpack("<4I", remote_read(api, process, 0x6dcf84, 16))
            diagnostic["vm_globals_6dcf84"] = list(globals_)
            diagnostic["updating_owner_checksum"] = struct.unpack("<I", remote_read(api, process, 0x6dd4a8, 4))[0]
            script = globals_[0]
            if script % 4 == 0 and result["arena_start"] <= script and script + 0xfc <= result["arena_end"]:
                state = struct.unpack("<63I", remote_read(api, process, script, 0xfc))
                diagnostic["current_script"] = {"address": script, "program_counter": state[4],
                                                "owner": state[0xc4 // 4], "source_checksum": state[0xd0 // 4]}
        except (OSError, RuntimeError) as error:
            diagnostic["vm_read_error"] = str(error)
        try:
            diagnostic.update(read_fault_stack(api, process, record))
            diagnostic["callee_saved_registers"] = dict(zip(
                ("ebx", "esi", "edi", "ebp"), struct.unpack("<4I", remote_read(api, process, record + 460, 16))))
        except (OSError, RuntimeError) as error:
            diagnostic["stack_read_error"] = str(error)
        if waited == 258:
            try:
                from probe_worker_timeout import collect
                diagnostic['owned_thread_timeout'] = collect(api, process, worker)
            except (OSError, ValueError, RuntimeError) as error:
                diagnostic['owned_thread_timeout_error'] = str(error)
        log("native_fault_diagnostics", function=name, **diagnostic)
    require(waited == 0 and result["status"] == 6, f"Native {name} failed: status={result['status']} EIP={result['eip']:#x} wait={waited}")
    require(returned == 0, f"Native-call glue returned {returned}")
    require(remote_read(api, process, owned, len(code)) == code, "Our native-call glue changed")
    current = remote_read(api, process, address, size)
    log("qb_entry_after", function=name, sha256=hashlib.sha256(current).hexdigest(), changed_bytes=sum(a != b for a, b in zip(current, original)))
    if timing is not None:
        finished = time.perf_counter()
        timing['post_guard_ms'] = (finished-completed)*1000
        timing['total_ms'] = (finished-started)*1000
        if name in ('NodeArrayManLoad', 'NodeArrayMemoryLoad', 'NodeArrayManUnload') or timing['total_ms'] > 8:
            log('native_resource_timing' if name in ('NodeArrayManLoad', 'NodeArrayMemoryLoad', 'NodeArrayManUnload') else
                'native_slow_call', function=name, worker_tid=worker.tid, **timing)
    return values[17]


def read_fault_stack(api, process, record):
    count, copied = struct.unpack("<II", remote_read(api, process, record + 452, 8))
    diagnostic = {"fault_stack_captured": copied != 0 and count == 64,
                  "fault_stack_bytes": count}
    if diagnostic["fault_stack_captured"]:
        diagnostic["fault_stack_words"] = list(struct.unpack("<16I", remote_read(api, process, record + 256, 64)))
    return diagnostic


QB_PINS = {
    "debug_heap": ("DebugHeapInit", 0x626850, 162, "c079af2e8de057640f84599d1f18006d5119e1acf5353c05c44f23222750516e"),
    "names": ("NameRegistryInit", 0x40a850, 149, "7d76a89722544672a41e2f16256f8c7ff4af6cc446db0fea3e9ec737d97a662f"),
    "buckets": ("InitQBBuckets_dispatch_entry", 0x4170b0, 6, "aa11230263fb6f72e49f658be8cae22ce8f5f1b85acd503714910fb216dc65ce"),
    "load": ("PipedSourceLoad_dispatch_entry", 0x40dec0, 6, "e9932de48cfd010eda995caf260cc8958e83717ab237bf45d2ff55655b0c320a"),
    "unload": ("SourceUnload_dispatch_entry", 0x40ce90, 6, "b0c587b5006d0b70acfc528a89ef339d1dd16ebdc7049695ff41f564683400e3"),
    "lookup": ("GlobalAliasLookup", 0x417110, 111, "2d9e7da5aefc983ca15f6db69a49b5ae09e29884248060a4c8587ac68955fabc"),
    "crc": ("FilenameChecksum", 0x401b00, 73, "e43775eda7e07d31ce80172ec770640f21533e28c51e56354cb88f7127927c44"),
}


# Exact stock metadata; six-byte pins cover dispatch entries, not full bodies.
VM_PINS = {
    "ArenaAllocate": ("ArenaAllocate", 0x401bc0, 6, "005a9a620caae09f61f548ba2cc43c442d689e588fcfdd0a2b4af04600418bc3"),
    "OwnedArenaCtor": ("OwnedArenaCtor", 0x627d90, 142, "34cbe1449adb0a1fdb6528c67752f06eb56bded5e3c9aa2cd359c9f23fd99966"),
    "CreateHeap": ("CreateHeap", 0x626510, 113, "d58155f1986dd87c0daffca7d95f3ba5dd8feae80a7a028930add965cb3ae9b7"),
    "HeapContextPush": ("HeapContextPush", 0x626260, 36, "0618d94e5fb6c4434feb1accaba72d1c90904bbc74e780a1683ce2f8944eebc3"),
    "HeapContextPop": ("HeapContextPop", 0x626290, 57, "4210c6cf2e7ebda9ca432215a40869161c4a36437e4536ad47444c8025bd0546"),
    "ScriptContextFactory": ("ScriptContextFactory", 0x41b950, 74, "ed897d7372ef34ef5d80257ecea85659b18a4a17e98dea9691347a30b813b8ae"),
    "ScriptCacheFactory": ("ScriptCacheFactory", 0x413e30, 74, "eedfa2c9b1ad100d98350e6bf89496104c42c606796d32ff388adca11ce19865"),
    "ScriptAllocate": ("ScriptAllocate", 0x411770, 62, "594f27c3f214be6dd39993d955b362d4f03981859b5a25d7adb0535e997f65ef"),
    "ScriptCtor": ("ScriptCtor", 0x411d20, 6, "e321bf10e8a1d86c7219e29fc17f388c40da68175ce8ed746d45aab5731b5da9"),
    "ScriptSetSource": ("ScriptSetSource", 0x412710, 6, "e7c44260e3f24c23a999cf3f4abf79858b2a22f8314365bfd53070004f45780d"),
    "ScriptUpdate": ("ScriptUpdate", 0x413000, 645, "78dcf9d690635ba7ad860e9e740caa41f0545e40b9eb03bfbcd619c52f718181"),
    "StructGetChecksum": ("StructGetChecksum", 0x414a50, 250, "af0bbe74f18b7a15395e6064b88888d41028cf699d1095f294c1f74672be55a3"),
    "ScriptDtor": ("ScriptDtor", 0x4125d0, 314, "0824905b5a47c27d07038feebbd0858a6c0439604870a74e33e85b046877e816"),
    "ScriptFree": ("ScriptFree", 0x4117b0, 6, "f97cc202bdbe07140b9461174e3eb9401b949f915685957de050f1e2c7ff572a"),
}


def qb_fixture(script=False, native=False):
    require(type(script) is bool and type(native) is bool and not (script and native), "QB fixture selection must use one explicit boolean mode")
    if native:
        return (175, 15124, "730aceaaebdca5f711ac7930214b0e7279ff0e5affbc4d841e061c8b4f4776ca", "scripts/game/igc/igc_player.qb.wpc")
    if script:
        return (89, 25908, "9505da426f4d603df37bd08a0a1efd39891b11f675e7283c2b47aa165c7f61dd", "scripts/game/net/xboxlivemenu.qb.wpc")
    return (214, 48, "255551bd4c5f4d69046d9e594c76abdff86747e8091e20fd6e961cca60ab0a3b", "scripts/game/cas_team.qb.wpc")


def read_qb_source(manifest_path, image_manifest, script=False, native=False):
    index, size, expected, name = qb_fixture(script=script, native=native)
    require(manifest_path.is_absolute(), "QB manifest path must be absolute")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    native_image = json.loads(image_manifest.read_text(encoding="utf-8-sig"))
    require(manifest.get("format") == "stock-thaw-core-qb-cache-v1" and manifest.get("member_count") == 268, "Unsupported core QB cache")
    require(Path(manifest["source_directory"]).resolve() == Path(native_image["source"]).resolve(), "QB and native-image sources differ")
    require(manifest.get("sources_revalidated_before_publish") is True, "QB source verification is absent")
    row = manifest["members"][index]
    require(row["order"] == index and row["cache_file"] == f"members/{index:04d}.qb" and row["bytes"] == size and row["sha256"] == expected, "Expected original source is absent")
    path = manifest_path.parent / row["cache_file"]
    require(path.resolve().is_relative_to(manifest_path.parent.resolve()), "QB input escapes cache")
    data = path.read_bytes()
    require(len(data) == size and hashlib.sha256(data).hexdigest() == expected, "Original source hash differs")
    require(row["canonical_name"] == name, "Unexpected original source filename")
    return data, name


def probe_qb(api, process, record, deadline, log, source, filename):
    require(len(source) == 48 and filename == "scripts/game/cas_team.qb.wpc", "Scalar QB probe requires its exact 48-byte fixture")
    def word(address):
        return struct.unpack("<I", remote_read(api, process, address, 4))[0]

    def call(key, arguments=(), this=None):
        return native_call(api, process, QB_PINS[key], list(arguments), record, deadline, log, this)

    require(remote_read(api, process, 0x6d8678, 1) == b"\1", "Original debug checksum flag differs")
    for address, size in [(0x842b18 + 0x184, 8), (0x6dbd74, 20), (0x6dd0fc, 4), (0x6dcf8c, 8), (0x844468, 0xe10)]:
        require(not any(remote_read(api, process, address, size)), f"QB context already initialized at {address:#x}")
    call("debug_heap", this=0x842b18)
    require(word(0x842b18 + 0x184) != 0 and word(0x842b18 + 0x188) != 0, "Original debug heap missing")
    call("names")
    names = [word(0x6dbd74 + index * 4) for index in range(5)]
    require(names[0] != 0 and names[1] == 0 and names[2] != 0 and names[3] != 0 and names[4] == names[3], "Original name registry is invalid")
    call("buckets")
    buckets = word(0x6dd0fc)
    require(buckets >= 0x10000 and buckets % 4 == 0, "Native QB table is absent")
    initial_buckets = remote_read(api, process, buckets, 0x4000)
    require(not any(initial_buckets), "Native QB table is not empty")
    owned = success(api.VirtualAllocEx(process, None, 4096, 0x3000, 4), "Allocate owned original QB input")
    encoded_name = filename.encode("ascii") + b"\0"
    remote_write(api, process, owned, source)
    remote_write(api, process, owned + 256, encoded_name)
    source_crc = call("crc", (owned + 256,))
    require(source_crc == 0xb790d035, "Native filename checksum differs from the original archive member")
    require(call("lookup", (0x5a2dfb63,)) == 0, "Scalar symbol was already present")
    call("load", (owned + 256, owned, 0))
    require(remote_read(api, process, owned, 1) == b"\1", "Original QB source was not marked processed")
    symbol = call("lookup", (0x5a2dfb63,))
    require(symbol == owned + 28, "Native lookup did not return the original piped source header")
    header = remote_read(api, process, symbol, 20)
    words = struct.unpack("<5I", header)
    require(words == (0x10400, 0x5a2dfb63, source_crc, 1, 0), "Native integer type/ownership/value differs")
    log("qb_loaded", source_bytes=48, source_sha256=hashlib.sha256(source).hexdigest(), filename=filename, source_crc=source_crc, symbol_crc=words[1], symbol_pointer=symbol, integer_value=words[3], original_loader_executed=True)
    require(word(0x842b18 + 0x114) != 0, "Original main heap required by source unload is absent")
    require(len(remote_read(api, process, buckets + 0x4000, 4)) == 4, "Original iterator's lookahead word is inaccessible")
    call("unload", (source_crc,))
    require(call("lookup", (0x5a2dfb63,)) == 0, "Native unload left scalar symbol present")
    require(word(0x6dd0fc) == buckets and remote_read(api, process, buckets, 0x4000) == initial_buckets, "Native unload did not restore all 4096 buckets")
    log("qb_pass", original_load_lookup_unload=True, buckets_restored=4096, scope="one real stock scalar QB member; no script execution or gameplay")


def probe_script(api, process, record, deadline, log, source, filename):
    """One real constant-return script, with the original cache and interpreter."""
    manager = 0x842b18

    def word(address):
        return struct.unpack("<I", remote_read(api, process, address, 4))[0]

    def call(name, arguments=(), this=None):
        pin = VM_PINS.get(name) or QB_PINS[name]
        return native_call(api, process, pin, list(arguments), record, deadline, log, this)

    def idle():
        return all(word(address) == 0 for address in (0x6dcf84, 0x6dcf8c, 0x6dcf90))

    require(idle(), "Original script registry is not idle")
    require(not any(remote_read(api, process, 0x6dd3f4, 8)) and not any(remote_read(api, process, 0x6dcfac, 8)), "Original script context/cache already initialized")
    profiles = [(0x23000, 0x692598, 0x124, "Script"),
                (0xc800, 0x6a2308, 0x12c, "Script cache small"),
                (0x7800, 0x6a22f4, 0x134, "Script cache big")]
    initial_count = word(manager + 0xc0)
    require(initial_count <= 0x30 - len(profiles), "Original heap registry has no room")
    require(all(word(manager + slot + offset) == 0 for _, _, slot, _ in profiles for offset in (0, 4)), "Original script heaps already exist")
    for index, (capacity, name_ptr, slot, name) in enumerate(profiles):
        require(remote_read(api, process, name_ptr, len(name) + 1) == name.encode("ascii") + b"\0", "Original heap name differs")
        arena = call("ArenaAllocate", (0x20,))
        require(arena >= 0x10000 and arena % 4 == 0, "Original arena allocation failed")
        require(call("OwnedArenaCtor", (capacity,), this=arena) == arena, "Original arena constructor returned a different pointer")
        heap = call("CreateHeap", (arena, 1, name_ptr), this=manager)
        require(heap >= 0x10000 and heap % 4 == 0, "Original script heap creation failed")
        require(word(manager + 0xc0) == initial_count + index + 1 and word(manager + 4 * (initial_count + index)) == heap, "Original heap factory registry differs")
        # The original startup publishes these slots after the same constructors.
        remote_write(api, process, manager + slot, struct.pack("<2I", arena, heap))
        log("vm_heap", name=name, capacity=capacity, arena=arena, heap=heap, manager_slot=slot)
    context = call("ScriptContextFactory", (1,))
    require(context >= 0x10000 and word(0x6dd3f4) == context and word(0x6dd3f8) == 1, "Original ScriptContext factory failed")
    cache = call("ScriptCacheFactory", (1,))
    require(cache >= 0x10000 and word(0x6dcfac) == cache and word(0x6dcfb0) == 1, "Original ScriptCache factory failed")
    buckets = word(0x6dd0fc)
    initial_buckets = remote_read(api, process, buckets, 0x4000)
    require(not any(initial_buckets), "Native QB table must be empty before script source load")
    name_offset = (len(source) + 15) & ~15
    encoded_name = filename.encode("ascii") + b"\0"
    owned = success(api.VirtualAllocEx(process, None, name_offset + len(encoded_name), 0x3000, 4), "Allocate owned whole original script source")
    remote_write(api, process, owned, source)
    remote_write(api, process, owned + name_offset, encoded_name)
    source_crc = call("crc", (owned + name_offset,))
    require(call("lookup", (0x71b073ff,)) == 0, "Selected script was already registered")
    call("load", (owned + name_offset, owned, 0))
    symbol = call("lookup", (0x71b073ff,))
    require(symbol == owned + 14204, "Original script lookup did not return its source section")
    header = struct.unpack("<5I", remote_read(api, process, symbol, 20))
    require(header[:4] == (0x70400, 0x71b073ff, source_crc, owned + 14224), "Original script type, owner or stored body pointer differs")
    require(struct.unpack("<3I", remote_read(api, process, owned + 14224, 12)) == (0x348b6e5d, 34, 25), "Original compressed script header differs")
    log("vm_source_loaded", source_bytes=len(source), source_sha256=hashlib.sha256(source).hexdigest(), filename=filename, source_crc=source_crc, script_crc=0x71b073ff, compressed_bytes=25, decoded_bytes=34, host_decompressed=False)
    call("HeapContextPush", (word(manager + 0x128),), this=manager)
    script = call("ScriptAllocate", (0xfc, 1))
    require(script >= 0x10000 and script % 4 == 0, "Original CScript allocation failed")
    require(call("ScriptCtor", this=script) == script, "Original CScript constructor returned a different object")
    require(word(0x6dcf8c) == script and word(0x6dcf90) == 1, "Original script registry did not publish the instance")
    call("ScriptSetSource", (0x71b073ff, 0, 0, 0), this=script)
    call("HeapContextPop", this=manager)
    update_result = call("ScriptUpdate", this=script)  # Exactly once, never a retry loop.
    require(update_result == 0 and word(script + 0x10) == 0 and word(0x6dcf84) == 0, "Bounded original script did not finish in one update")
    returned_struct = word(script + 0x18)
    require(returned_struct >= 0x10000, "Original script returned no CStruct")
    remote_write(api, process, record + 72, bytes(4))
    found = call("StructGetChecksum", (0, record + 72, 0), this=returned_struct)
    returned_checksum = word(record + 72)
    require(found & 0xff == 1 and returned_checksum == 0xd43297cf, "Original script did not return the expected unnamed FALSE checksum")
    log("vm_executed", script_name="xboxlive_menu_players_is_player_muted", script=script, updates=1, update_result=update_result, returned_struct=returned_struct, returned_checksum=returned_checksum, expected_name="FALSE", native_callbacks_registered=0, original_vm_executed=True)
    call("ScriptDtor", this=script)
    call("ScriptFree", (script,))
    require(idle(), "Original destructor/free did not restore the idle script registry")
    call("unload", (source_crc,))
    require(call("lookup", (0x71b073ff,)) == 0, "Original source unload retained the selected script")
    require(word(0x6dd0fc) == buckets and remote_read(api, process, buckets, 0x4000) == initial_buckets, "Original source unload did not restore all 4096 buckets")
    log("vm_pass", original_load_execute_destroy_unload=True, native_updates=1, buckets_restored=4096, registry_idle=True, cache_context_heaps_retained_until_process_exit=True, scope="one original constant-return script through original VM/cache; no skater or gameplay")


def probe(manifest_path: Path, report_path: Path, allocator=False, qb_manifest=None, script=False, animation_manifest=None, native=False, profile_manifest=None, components=False, resources_manifest=None, game_profile_manifest=None, scene=False, animation_motion=False, core=False, skater_assets_manifest=None, frames=False, compact_log=False):
    executable = validate_manifest(manifest_path)
    require(game_profile_manifest is None or (resources_manifest is not None and qb_manifest is not None and profile_manifest is None), "Combined Game proof requires resource/QB caches and its separate final-profile input")
    require(not scene or game_profile_manifest is not None, "Scene proof requires the combined genuine Game/resource context")
    require(not animation_motion or animation_manifest is not None, "Dense original animation requires the pinned animation inputs")
    require(not core or (scene and game_profile_manifest is not None), "Complete core proof requires the combined resource/Game/scene context")
    require(skater_assets_manifest is None or core, "Whole CSkater requires the complete core and native service bootstrap")
    require(not frames or skater_assets_manifest is not None, "Original frames require the whole original actor bootstrap")
    components = components or resources_manifest is not None
    require(not (components and profile_manifest), "Component proof requires no loaded profile sources")
    native = native or profile_manifest is not None or components
    qb_input = read_qb_source(qb_manifest, manifest_path) if qb_manifest else None
    script_input = read_qb_source(qb_manifest, manifest_path, script=True) if script or native else None
    native_input = None
    if native:
        import probe_native
        native_input = read_qb_source(qb_manifest, manifest_path, native=True)
    animation_input = None
    if animation_manifest:
        import probe_animation
        animation_input = probe_animation.read_assets(animation_manifest, manifest_path)
    profile_input = None
    if profile_manifest:
        import probe_profile
        profile_input = probe_profile.read_inputs(profile_manifest, manifest_path)
    component_input = None
    if components:
        import probe_components
        component_input = probe_components.read_metadata()
    resource_input = None
    if resources_manifest:
        import probe_resources
        resource_input = probe_resources.read_inputs(resources_manifest, qb_manifest, manifest_path)
    game_input = None
    if game_profile_manifest:
        import probe_game
        game_input = probe_game.read_inputs(qb_manifest, game_profile_manifest, manifest_path)
    scene_input = None
    if scene:
        import probe_scene
        scene_input = probe_scene.read_inputs(resources_manifest, manifest_path)
    core_input = None
    if core:
        import probe_core
        core_input = probe_core.read_inputs(qb_manifest, manifest_path)
    skater_input = None
    if skater_assets_manifest:
        import probe_skater
        import probe_skater_animation
        import probe_frontend
        import probe_frontend_base
        import probe_lighting
        import probe_terrain
        skater_input = {"animation": probe_skater_animation.read_inputs(skater_assets_manifest, manifest_path),
                        "base": probe_frontend_base.read_inputs(qb_manifest, manifest_path),
                        "lighting": probe_lighting.read_inputs(qb_manifest, manifest_path),
                        "terrain": probe_terrain.read_inputs(qb_manifest, manifest_path),
                        "common": probe_skater.verify_common_resources(resources_manifest, manifest_path)}
        probe_skater.read_metadata()
    frame_input = None
    if frames:
        import probe_frames
        import probe_frame_input
        import probe_fonts
        import probe_global_assets
        import probe_hud
        import probe_animation_banks
        import probe_audio
        import probe_audio_bank
        import probe_career
        probe_frames.read_metadata()
        frame_input = {"fonts": probe_fonts.read_inputs(qb_manifest, manifest_path),
                       "global_assets": probe_global_assets.read_inputs(qb_manifest, resources_manifest, manifest_path),
                       "hud": probe_hud.read_inputs(qb_manifest, manifest_path),
                       "career": probe_career.read_inputs(qb_manifest, manifest_path),
                       "audio_bank": probe_audio_bank.read_inputs(qb_manifest, resources_manifest, manifest_path),
                       "animation_banks": probe_animation_banks.read_inputs(qb_manifest, resources_manifest, manifest_path)}
    allocator = allocator or qb_input is not None or animation_input is not None
    require(report_path.is_absolute(), "Report path must be absolute")
    api = winapi()
    process = ProcessInfo()
    remote_thread = None
    job = None
    worker = None
    started = time.monotonic()
    with report_path.open("x", encoding="utf-8", newline="\n") as report:
        from probe_log import EventLog
        log = EventLog(report, started, compact=compact_log)

        try:
            from stock_host import launch, HOST_NAME
            log("validated", schema=2, scope="Rust stock-image host plus original MemMan" if allocator else "Rust stock-image host plus owned return stub only", source_executable=str(executable), original_process_created=False, original_entrypoint_called=False, allocator_requested=allocator)
            job = success(api.CreateJobObjectW(None, None), "Create owned child lifetime job")
            limits = ExtendedJobLimits()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            success(api.SetInformationJobObject(job, 9, C.byref(limits), C.sizeof(limits)), "Configure owned child job lifetime")
            deadline = launch(api, process, job, executable, report_path, log)
            log("child_job_assigned", kill_on_job_close=True, original_process_created=False)
            wow64 = W.BOOL()
            success(api.IsWow64Process(process.process, C.byref(wow64)), "IsWow64Process")
            require(wow64.value, "Rust host must be x86 under WOW64")
            before = modules(api, process.pid, deadline)
            log("modules_before", **before)
            # The host has mapped and bound the source image. Our command glue
            # lives in separate allocations; the original entrypoint is unused.
            address = success(api.VirtualAllocEx(process.process, None, 4096, 0x3000, 4), "VirtualAllocEx owned stub")
            require(address < 0x100000000, "Owned stub is outside x86 address space")
            buffer = C.create_string_buffer(STUB)
            written = C.c_size_t()
            success(api.WriteProcessMemory(process.process, address, buffer, len(STUB), C.byref(written)), "WriteProcessMemory owned stub")
            require(written.value == len(STUB), "Partial stub write")
            old = W.DWORD()
            success(api.VirtualProtectEx(process.process, address, 4096, 0x20, C.byref(old)), "Seal owned stub RX")
            success(api.FlushInstructionCache(process.process, address, len(STUB)), "FlushInstructionCache")
            log("before_owned_thread", owned_entry=address, owned_code_sha256=hashlib.sha256(STUB).hexdigest(), original_entrypoint_called=False)
            remote_id = W.DWORD()
            remote_thread = success(api.CreateRemoteThread(process.process, None, 0, address, None, 0, C.byref(remote_id)), "CreateRemoteThread owned return stub")
            remaining = max(0, int((deadline - time.monotonic()) * 1000))
            waited = api.WaitForSingleObject(remote_thread, remaining)
            log("owned_thread_wait", result=waited, remote_tid=remote_id.value)
            require(waited == 0, "Owned remote thread did not finish within ten seconds" if waited == 258 else f"Thread wait failed: {waited}")
            code = W.DWORD()
            success(api.GetExitCodeThread(remote_thread, C.byref(code)), "GetExitCodeThread")
            require(code.value == 0, f"Owned return stub exited with {code.value:#x}")
            readback = C.create_string_buffer(len(STUB))
            transferred = C.c_size_t()
            success(api.ReadProcessMemory(process.process, address, readback, len(STUB), C.byref(transferred)), "ReadProcessMemory owned stub")
            require(transferred.value == len(STUB) and readback.raw == STUB, "Owned stub changed during execution")
            after = modules(api, process.pid, deadline)
            log("modules_after", **after)
            names = {m["name"].lower(): m for m in after["modules"]}
            require(after["error"] == 0 and "kernel32.dll" in names and HOST_NAME in names,
                    "Cannot verify the owned Rust host and its kernel32 module")
            require("thaw.exe" not in names and "rethawed.exe" not in names,
                    "The Rust host must not load an original game as a Windows module")
            if allocator:
                from native_worker import NativeWorker
                require("user32.dll" in names, "Cannot verify the child's Win32 message-pump module")
                worker = NativeWorker(api, process.process, names, deadline, log)
                api._thaw_native_worker = worker
                record = probe_allocator(api, process.process, names["kernel32.dll"]["base"], deadline, log)
                if qb_input:
                    probe_qb(api, process.process, record, deadline, log, *qb_input)
                if script_input:
                    probe_script(api, process.process, record, deadline, log, *script_input)
                if native_input:
                    probe_native.run(api, process.process, record, deadline, log, *native_input)
                if animation_input:
                    probe_animation.run(api, process.process, record, deadline, log, animation_input, sample_hz=120 if animation_motion else None)
                if profile_input:
                    probe_profile.run(api, process.process, record, deadline, log, profile_input)
                if component_input:
                    probe_components.run(api, process.process, record, deadline, log, component_input)
                preloaded = probe_core.run(api, process.process, record, deadline, log, core_input) if core_input else None
                if resource_input:
                    resource_system = probe_resources.run(api, process.process, record, deadline, log, resource_input, names, preloaded=preloaded)
                    if frames:
                        probe_fonts.run_platform(api, process.process, record, deadline, log)
                        probe_audio.run(api, process.process, record, deadline, log, resource_system)
                if game_input:
                    game_context = probe_game.run(api, process.process, record, deadline, log, game_input, preloaded=preloaded)
                    if frames:
                        probe_career.run(api, process.process, record, deadline, log, frame_input["career"],
                                         preloaded, resource_system, game_context)
                if scene_input:
                    probe_scene.run(api, process.process, record, deadline, log, scene_input)
                    if frames:
                        probe_audio_bank.run(api, process.process, record, deadline, log, frame_input["audio_bank"],
                                             preloaded, resource_system)
                if skater_input:
                    log("skater_common_resources_verified", files=skater_input["common"]["files"], bytes=skater_input["common"]["bytes"], original_and_private_bytes_equal=True)
                    probe_terrain.run(api, process.process, record, deadline, log, skater_input["terrain"],
                                      preloaded, resource_system, game_context)
                    probe_frontend.run_screen(api, process.process, record, deadline, log)
                    probe_lighting.run(api, process.process, record, deadline, log, skater_input["lighting"],
                                       preloaded, resource_system)
                    probe_frontend_base.run(api, process.process, record, deadline, log, skater_input["base"])
                    probe_skater.viewports(api, process.process, record, deadline, log, resource_system)
                    probe_skater_animation.run(api, process.process, record, deadline, log, skater_input["animation"], preloaded)
                    skater_context = probe_skater.run(api, process.process, record, deadline, log, resource_system, game_context)
                    if frames:
                        probe_frames.run(api, process.process, record, deadline, log, skater_context,
                                         frame_input, preloaded, resource_system)
                worker.verify()
                require(api.WaitForSingleObject(process.process, 0) == 258, "Rust host stopped during original calls")
            scope = "stock Game/Career/Events/FreeSkate/Replay context with native 10/10 profile, task/component and real D3D9 resource prerequisites; no skater or gameplay" if game_input else "stock FileService, textures/metadata, decals, caches and AssetsManager with actual hidden Win32/D3D9 resources; no skater or gameplay" if resource_input else "stock Task/Component managers, 60 descriptor initializers and all 93 type registrations; no skater or task updates" if component_input else "stock original ProfileManager and custom_classic profile with ten native 10/10 statistics; no skater" if profile_input else "stock registry publication and one native IsTrue dispatch through original VM; no skater" if native_input else "stock MemMan, one native Ollie animation and 52-bone palette; no skater or GTA retargeting" if animation_input else "stock MemMan, QB and one real script executed by the original VM; no gameplay" if script_input else "stock MemMan and one scalar QB source load/lookup/unload; no gameplay" if qb_input else "stock MemMan initialized; no QB or gameplay" if allocator else "loader/stub only; no allocator or gameplay test"
            if scene_input:
                scope = "original stock COL loader and feeler hit/miss on an authored THAW-unit floor, with Game/resource prerequisites; no skater or gameplay"
            if core_input:
                scope = "all 268 original core QB sources published in archive order, retained original resource/Game/profile/scene context; no skater or gameplay"
            if skater_input:
                scope = "whole original local CSkater/ProfileInit with native model, components, skeleton, camera and ten maximum statistics; no gameplay update or GTA publication"
            if frames:
                scope = "original stock cleanup/owner/audio/script gameplay frames with genuine timing and decoded input; see frame-driver events for count and inputs; adapter publication, if any, is recorded separately"
            log("pass", original_process_created=False, original_entrypoint_called=False, runtime_host=HOST_NAME, owned_return_code=code.value, stock_image_base=0x400000, kernel32_base=names["kernel32.dll"]["base"], scope=scope)
        except BaseException as error:
            log("error", message=str(error), error_type=type(error).__name__)
            raise
        finally:
            # Closing the last noninherited job handle also handles parent death.
            # On an ordinary exit we still explicitly terminate and verify below.
            if job:
                api.CloseHandle(job)
            if process.process:
                terminated = bool(api.TerminateProcess(process.process, 0))
                termination_error = 0 if terminated else C.get_last_error()
                waited = api.WaitForSingleObject(process.process, 2000)
                exit_code = W.DWORD()
                got_exit = bool(api.GetExitCodeProcess(process.process, C.byref(exit_code)))
                log("cleanup", termination_requested=True, terminate_succeeded=terminated, termination_error=termination_error, wait_result=waited, exit_code=exit_code.value if got_exit else None, child_stopped=waited == 0)
                if worker:
                    worker.close()
                    api._thaw_native_worker = None
                if remote_thread:
                    api.CloseHandle(remote_thread)
                api.CloseHandle(process.thread)
                api.CloseHandle(process.process)
                require(waited == 0, "Owned child did not terminate; inspect recorded pid")


def snapshot_self_test():
    class FakeSnapshotAPI:
        def __init__(self, errors):
            self.errors = iter(errors)
            self.calls = 0

        def CreateToolhelp32Snapshot(self, flags, pid):
            require(flags == 0x18 and pid == 42, "Snapshot test scope differs")
            self.calls += 1
            error = next(self.errors)
            C.set_last_error(error)
            return C.c_void_p(-1).value if error else 0x1234

    for errors, expired, expected_attempts, expected_error in [([24, 24, 0], False, 3, 0), ([24] * 8, False, 8, 24), ([5], False, 1, 5), ([24], True, 1, 24)]:
        fake = FakeSnapshotAPI(errors)
        snapshot, attempts, error = module_snapshot(fake, 42, time.monotonic() + (-1 if expired else 1))
        require(attempts == expected_attempts and fake.calls == attempts and error == expected_error, "Snapshot retry limit/error policy differs")
        require(snapshot == (C.c_void_p(-1).value if error else 0x1234), "Snapshot test handle differs")


def main():
    if len(sys.argv) == 2 and sys.argv[1] == "--self-test":
        winapi()
        snapshot_self_test()
        require(len(STUB) == 5 and STUB == b"\x31\xc0\xc2\x04\x00", "Invalid stdcall x86 return stub")
        require(sum(size for size, _ in FILES.values()) == 24139984, "Pinned input sizes changed")
        code, data = allocator_stub(0x20000000, 0x21000000, 0x76000000)
        require(len(code) < 4096 and data[128:139] == b"ExitThread\0" and data[160:188] == b"AddVectoredExceptionHandler\0", "Allocator stub data layout mismatch")
        cdecl = native_call_stub(0x40dec0, [0x20000000, 0x21000000, 0], 0x22000000)
        thiscall = native_call_stub(0x626850, [], 0x22000000, this=0x842b18)
        require(b"\x83\xc4\x0c" in cdecl and b"\xb9\x18\x2b\x84\x00" in thiscall and cdecl.endswith(STUB) and thiscall.endswith(STUB), "cdecl/thiscall glue differs")
        set_source = native_call_stub(0x412710, [0x71b073ff, 0, 0, 0], 0x22000000, this=0x23000000)
        require(set_source.count(b"\x68\0\0\0\0") == 3 and b"\x83\xc4" not in set_source, "thiscall must pass four arguments without caller stack cleanup")
        texture_load = native_call_stub(0x4ec930, [0x20000000, 16412, 0xf4fd2d63, 1, 1, 0, 1], 0x22000000, this=0x23000000)
        require(texture_load.count(b"\x68") == 7 and b"\x83\xc4" not in texture_load, "TextureRAM thiscall requires seven callee-popped stack arguments")
        resource_load = native_call_stub(0x41aee0, [0x39937d56, 7, 0, 0x20000000, 5376, 0, 0, 0, 0], 0x22000000, this=0x23000000)
        require(resource_load.count(b"\x68") == 9 and b"\x83\xc4" not in resource_load, "ResourceRAM thiscall requires nine callee-popped arguments")
        retained_spawn = native_call_stub(0x413380, [0xd10c7f04] + [0] * 9, 0x22000000)
        require(retained_spawn.count(b"\x68") == 10 and b"\x83\xc4\x28" in retained_spawn,
                "Retained SpawnScript requires ten caller-popped arguments")
        try:
            native_call_stub(0x413380, [0] * 11, 0x22000000)
        except RuntimeError:
            pass
        else:
            raise RuntimeError("Native argument guard accepted an unaudited eleven-argument call")
        limits = ExtendedJobLimits()
        limits.basic.flags = 0x2000
        require(bytes(limits)[16:20] == struct.pack("<I", 0x2000), "Job kill-on-close flag layout differs")
        print("PASS: Win64 API layouts and owned x86 stubs; no child created")
        return
    require(len(sys.argv) == 3 or (len(sys.argv) == 4 and sys.argv[3] == "--allocator") or (len(sys.argv) == 5 and sys.argv[3] in ("--qb", "--script", "--animation", "--animation-motion", "--native", "--components")) or (len(sys.argv) == 6 and sys.argv[3] in ("--profile", "--resources")) or (len(sys.argv) == 7 and sys.argv[3] in ("--game", "--scene", "--core")) or (len(sys.argv) == 8 and sys.argv[3] in ("--skater", "--frames")), "Usage: probe_loader.py ABS_STAGED_MANIFEST ABS_NEW_REPORT.jsonl [--allocator | --qb ABS_QB_MANIFEST | --script ABS_QB_MANIFEST | --animation ABS_ASSET_MANIFEST | --animation-motion ABS_ASSET_MANIFEST | --native ABS_QB_MANIFEST | --profile ABS_QB_MANIFEST ABS_PROFILE_MANIFEST | --components ABS_QB_MANIFEST | --resources ABS_QB_MANIFEST ABS_RESOURCE_MANIFEST | --game ABS_QB_MANIFEST ABS_RESOURCE_MANIFEST ABS_PROFILE_MANIFEST | --scene ABS_QB_MANIFEST ABS_COMMON_RESOURCE_MANIFEST ABS_PROFILE_MANIFEST | --core ABS_QB_MANIFEST ABS_COMMON_RESOURCE_MANIFEST ABS_PROFILE_MANIFEST | --skater/--frames ABS_QB_MANIFEST ABS_COMMON_RESOURCE_MANIFEST ABS_PROFILE_MANIFEST ABS_ASSET_MANIFEST]")
    animation = len(sys.argv) == 5 and sys.argv[3] in ("--animation", "--animation-motion")
    profile = len(sys.argv) == 6 and sys.argv[3] == "--profile"
    resources = len(sys.argv) == 6 and sys.argv[3] == "--resources"
    skater = len(sys.argv) == 8 and sys.argv[3] in ("--skater", "--frames")
    game = skater or (len(sys.argv) == 7 and sys.argv[3] in ("--game", "--scene", "--core"))
    probe(Path(sys.argv[1]), Path(sys.argv[2]), allocator=len(sys.argv) == 4, qb_manifest=Path(sys.argv[4]) if len(sys.argv) >= 5 and not animation else None, script=len(sys.argv) == 5 and sys.argv[3] == "--script", animation_manifest=Path(sys.argv[4]) if animation else None, native=len(sys.argv) == 5 and sys.argv[3] == "--native", profile_manifest=Path(sys.argv[5]) if profile else None, components=len(sys.argv) == 5 and sys.argv[3] == "--components", resources_manifest=Path(sys.argv[5]) if resources or game else None, game_profile_manifest=Path(sys.argv[6]) if game else None, scene=game and sys.argv[3] in ("--scene", "--core", "--skater", "--frames"), animation_motion=animation and sys.argv[3] == "--animation-motion", core=game and sys.argv[3] in ("--core", "--skater", "--frames"), skater_assets_manifest=Path(sys.argv[7]) if skater else None, frames=skater and sys.argv[3] == "--frames")
    print("PASS: original functions executed in our Rust host; THAW.exe was not started. See report for scope.")


if __name__ == "__main__":
    main()
