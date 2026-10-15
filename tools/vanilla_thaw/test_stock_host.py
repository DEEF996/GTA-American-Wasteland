import ctypes as C
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest

import stock_host as host


class StockHostTests(unittest.TestCase):
    def fixture(self, root):
        directory = root / 'build/gta-runtime'
        directory.mkdir(parents=True)
        executable = directory / host.HOST_NAME
        data = bytearray(256)
        data[:2] = b'MZ'
        struct.pack_into('<I', data, 0x3c, 64)
        data[64:70] = b'PE\0\0\x4c\x01'
        executable.write_bytes(data)
        (directory/'gta-thaw-tls.dll').write_bytes(data)
        meta = dict(format='gta-thaw-rust-host-build-v1', executable=host.HOST_NAME,
                    machine='i686', tests_passed=True, file_events_isolated=True, bytes=len(data),
                    sha256=hashlib.sha256(data).hexdigest(),
                    tls=dict(file='gta-thaw-tls.dll', bytes=len(data),
                             sha256=hashlib.sha256(data).hexdigest()))
        path = directory / 'host-build.json'
        path.write_text(json.dumps(meta))
        return executable, path, meta

    def test_build_hash_and_architecture_checked_before_process_creation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            executable, manifest, meta = self.fixture(root)
            self.assertEqual(host.validate_host(root), (executable, meta))
            executable.write_bytes(executable.read_bytes() + b'changed')
            with self.assertRaisesRegex(RuntimeError, 'hash/size'):
                host.validate_host(root)
            data = bytearray(executable.read_bytes())
            data[68:70] = b'\x64\x86'
            executable.write_bytes(data)
            meta.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
            manifest.write_text(json.dumps(meta))
            with self.assertRaisesRegex(RuntimeError, 'x86'):
                host.validate_host(root)

    def test_untested_or_original_executable_is_never_selected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            executable, manifest, meta = self.fixture(root)
            for change in ({'tests_passed': False}, {'executable': 'THAW.exe'}, {'machine': 'amd64'},
                           {'file_events_isolated':False}, {'file_events_isolated':None}):
                manifest.write_text(json.dumps(dict(meta, **change)))
                with self.assertRaisesRegex(RuntimeError, 'Unsupported or untested'):
                    host.validate_host(root)
            with self.assertRaises(RuntimeError):
                host.host_command(root/'THAW.exe', root/'THAW.exe', 'event', root/'result.json')

    def test_tls_companion_must_be_complete_and_match_build(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            executable, manifest, meta = self.fixture(root)
            tls = executable.with_name('gta-thaw-tls.dll')
            tls.write_bytes(tls.read_bytes()+b'changed')
            with self.assertRaisesRegex(RuntimeError, 'hash/size'):
                host.validate_host(root)
            meta['tls']['file'] = '../other.dll'
            manifest.write_text(json.dumps(meta))
            with self.assertRaisesRegex(RuntimeError, 'TLS companion'):
                host.validate_host(root)

    @unittest.skipUnless(hasattr(C, 'WinDLL'), 'Windows argument quoting')
    def test_source_is_only_an_argument_even_with_spaces(self):
        args = [r'C:\Mod Folder\gta-thaw-runtime.exe', '--source', r'C:\Game Files\THAW.exe',
                '--ready-event', 'Local\\own-event', '--report', r'C:\Mod Folder\new report.json']
        command = host.host_command(Path(args[0]), Path(args[2]), args[4], Path(args[6]))
        shell = C.WinDLL('shell32')
        shell.CommandLineToArgvW.argtypes = [C.c_wchar_p, C.POINTER(C.c_int)]
        shell.CommandLineToArgvW.restype = C.POINTER(C.c_wchar_p)
        kernel = C.WinDLL('kernel32')
        kernel.LocalFree.argtypes = [C.c_void_p]
        count = C.c_int()
        values = shell.CommandLineToArgvW(command, C.byref(count))
        try:
            self.assertEqual([values[i] for i in range(count.value)], args)
        finally:
            kernel.LocalFree(values)

    def test_readiness_rejects_wrong_lifetime_source_or_original_execution(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder)/'THAW.exe'
            data = bytearray(256)
            struct.pack_into('<I', data, 0x3c, 64)
            struct.pack_into('<I', data, 64+24+56, 0x184d000)
            source.write_bytes(data)
            value = dict(format='gta-thaw-rust-host-v1', ready=True, pid=123,
                         created_100ns=789,file_events_isolated=True,
                         file_event_namespace='Local\\GTA_THAW_FILE_123_0000000000000315',
                         source_executable=str(source), source_sha256=host.p.FILES['THAW.exe'][1],
                         image_base=0x400000, image_size=0x184d000,
                         sections_mapped=9, imports_bound=211, mapped_original_headers=False,
                         tls_callbacks_called=False, static_tls_bytes=8, static_tls_index=0,
                         stock_text_sha256='8f8feb6bc8c7a217919e362da752db6ef631be1e03ed956c1748fb2175066da8',
                         original_entrypoint_called=False, original_process_created=False)
            self.assertEqual(host.validate_report(value, source, 123, 789), value)
            for change in ({'pid': 124}, {'ready': False}, {'source_executable': str(source.parent/'other.exe')},
                           {'source_sha256': '0'*64}, {'image_base': 0x20000000},
                           {'original_entrypoint_called': True}, {'original_process_created': True},
                           {'mapped_original_headers': True}, {'sections_mapped': 8}, {'imports_bound': 0},
                           {'static_tls_bytes': 0}, {'static_tls_index': -1}, {'tls_callbacks_called': True},
                           {'created_100ns':788}, {'created_100ns':True}, {'file_events_isolated':False},
                           {'file_event_namespace':'io_event'}, {'file_event_namespace':'Global\\GTA_THAW_FILE_123_0000000000000315'},
                           {'file_event_namespace':'Local\\GTA_THAW_FILE_124_0000000000000315'}):
                with self.assertRaisesRegex(RuntimeError, 'readiness'):
                    host.validate_report(dict(value, **change), source, 123, 789)
            for created in (0, True, 790, 2**64):
                with self.assertRaisesRegex(RuntimeError, 'readiness'):
                    host.validate_report(value, source, 123, created)
            with self.assertRaisesRegex(RuntimeError, 'image size'):
                host.validate_report(dict(value, image_size=4096), source, 123, 789)


if __name__ == '__main__':
    unittest.main()
