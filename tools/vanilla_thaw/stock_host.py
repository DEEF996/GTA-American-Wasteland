"""Start only our pinned Rust host; the stock executable is a read-only input.

The caller owns the ProcessInfo and kill-on-close Job Object from creation,
including failed startup. No fallback launches the original THAW application.
"""
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import time
import uuid

import probe_loader as p

ROOT = Path(__file__).resolve().parents[2]
HOST_NAME = 'gta-thaw-runtime.exe'
IMAGE_BASE = 0x400000


def validate_host(root=ROOT):
    directory = (Path(root) / 'build/gta-runtime').resolve(strict=True)
    manifest = directory / 'host-build.json'
    p.require(manifest.stat().st_size <= 65536, 'Oversized Rust host build manifest')
    meta = json.loads(manifest.read_text(encoding='utf-8-sig'))
    p.require(meta.get('format') == 'gta-thaw-rust-host-build-v1'
              and meta.get('executable') == HOST_NAME and meta.get('machine') == 'i686'
              and meta.get('tests_passed') is True and meta.get('file_events_isolated') is True,
              'Unsupported or untested Rust host build')
    tls = meta.get('tls', {})
    p.require(tls.get('file') == 'gta-thaw-tls.dll', 'Rust host TLS companion manifest differs')
    for name, pin in ((HOST_NAME, meta), ('gta-thaw-tls.dll', tls)):
        executable = directory / name
        p.require(executable.resolve(strict=True).parent == directory,
                  'Rust host component leaves its build directory')
        p.require(executable.stat().st_size <= 64*1024*1024, 'Oversized Rust host component')
        data = executable.read_bytes()
        p.require(len(data) == pin.get('bytes') and hashlib.sha256(data).hexdigest() == pin.get('sha256'),
                  'Rust host component hash/size differs; restore the complete integration package')
        p.require(len(data) >= 64 and data[:2] == b'MZ', 'Rust host component is not a Windows executable')
        offset, = struct.unpack_from('<I', data, 0x3c)
        p.require(64 <= offset <= len(data)-6 and data[offset:offset+6] == b'PE\0\0\x4c\x01',
                  'Rust host component must be the verified x86 build')
    return directory / HOST_NAME, meta


def host_command(executable, source, event, report):
    p.require(Path(executable).name == HOST_NAME and Path(source).name.casefold() == 'thaw.exe'
              and Path(executable).is_absolute() and Path(source).is_absolute()
              and Path(report).is_absolute(), 'Rust host requires separate absolute executable and source paths')
    return subprocess.list2cmdline([str(executable), '--source', str(source),
                                   '--ready-event', event, '--report', str(report)])


def validate_report(value, source, pid, created):
    p.require(value.get('format') == 'gta-thaw-rust-host-v1' and value.get('ready') is True
              and value.get('pid') == pid
              and type(created) is int and 0 < created < 2**64
              and type(value.get('created_100ns')) is int and value['created_100ns'] == created
              and value.get('file_events_isolated') is True
              and value.get('file_event_namespace') == f'Local\\GTA_THAW_FILE_{pid}_{created:016x}'
              and os.path.normcase(str(Path(value.get('source_executable', '')).resolve()))
                  == os.path.normcase(str(Path(source).resolve()))
              and value.get('source_sha256') == p.FILES['THAW.exe'][1]
              and value.get('image_base') == IMAGE_BASE
              and value.get('sections_mapped') == 9 and value.get('imports_bound') == 211
              and value.get('mapped_original_headers') is False
              and value.get('tls_callbacks_called') is False and value.get('static_tls_bytes') == 8
              and type(value.get('static_tls_index')) is int and 0 <= value['static_tls_index'] < 4096
              and value.get('stock_text_sha256') == '8f8feb6bc8c7a217919e362da752db6ef631be1e03ed956c1748fb2175066da8'
              and value.get('original_entrypoint_called') is False
              and value.get('original_process_created') is False,
              'Rust host readiness does not match the owned stock image')
    # Read SizeOfImage from the same verified source, not from untrusted output.
    data = Path(source).read_bytes()
    header, = struct.unpack_from('<I', data, 0x3c)
    size, = struct.unpack_from('<I', data, header+24+56)
    p.require(value.get('image_size') == size, 'Rust host mapped image size differs')
    return value


def launch(api, process, job, source, report_path, log):
    executable, build = validate_host()
    report = report_path.with_suffix('.host.json')
    p.require(not report.exists(), 'Rust host startup report must be a new file')
    event_name = 'Local\\GTA_THAW_HOST_' + uuid.uuid4().hex
    C.set_last_error(0)
    event = p.success(api.CreateEventW(None, True, False, event_name), 'Create Rust host ready event')
    try:
        p.require(C.get_last_error() != 183, 'Rust host ready event already exists')
        startup = p.StartupInfo()
        startup.cb, startup.flags = C.sizeof(startup), 1
        command = C.create_unicode_buffer(host_command(executable, source, event_name, report))
        old_error_mode = api.SetErrorMode(0x8003)
        try:
            p.success(api.CreateProcessW(str(executable), command, None, None, False, 0x08000004,
                                        None, str(source.parent), C.byref(startup), C.byref(process)),
                      'CreateProcessW owned Rust host (suspended)')
        finally:
            api.SetErrorMode(old_error_mode)
        p.success(api.AssignProcessToJobObject(job, process.process), 'Assign Rust host to lifetime job')
        p.require(api.ResumeThread(process.thread) == 1, 'Rust host primary thread was not suspended once')
        log('rust_host_started', pid=process.pid, tid=process.tid, executable=str(executable),
            executable_sha256=build['sha256'], stock_source=str(source),
            original_process_created=False, original_entrypoint_called=False)
        handles = (W.HANDLE * 2)(event, process.process)
        waited = api.WaitForMultipleObjects(2, handles, False, 10000)
        if waited != 0:
            detail = ''
            if report.is_file() and report.stat().st_size <= 65536:
                detail = report.read_text(encoding='utf-8')
            raise RuntimeError(f'Rust host did not become ready (wait={waited}): {detail}')
        p.require(api.WaitForSingleObject(process.process, 0) == 258, 'Rust host exited during readiness')
        p.require(report.is_file() and report.stat().st_size <= 65536, 'Missing/oversized Rust host report')
        api.GetProcessTimes.argtypes = [W.HANDLE, *([C.POINTER(C.c_uint64)] * 4)]
        api.GetProcessTimes.restype = W.BOOL
        times = [C.c_uint64() for _ in range(4)]
        p.success(api.GetProcessTimes(process.process, *(C.byref(value) for value in times)),
                  'Verify Rust host process lifetime')
        verified = validate_report(json.loads(report.read_text(encoding='utf-8')), source, process.pid, times[0].value)
        path, count = C.create_unicode_buffer(32768), W.DWORD(32768)
        p.success(api.QueryFullProcessImageNameW(process.process, 0, path, C.byref(count)),
                  'Verify owned Rust host executable')
        p.require(os.path.normcase(str(Path(path.value).resolve())) == os.path.normcase(str(executable.resolve())),
                  'Owned process is not the Rust host')
        for address, count, expected in p.FUNCTIONS:
            p.require(hashlib.sha256(p.remote_read(api, process.process, address, count)).hexdigest() == expected,
                      f'Rust host stock function differs at {address:#x}')
        log('rust_host_ready', **{key: value for key, value in verified.items() if key != 'phase'})
        return time.monotonic() + 10
    finally:
        api.CloseHandle(event)
