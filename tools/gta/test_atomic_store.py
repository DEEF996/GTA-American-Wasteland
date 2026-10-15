import ctypes as C
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch

import atomic_store as a


def fake_store32(address,value):
    """Sequential memory fixture; native fence semantics have separate C++ tests."""
    C.c_uint32.from_address(address).value=value
    return 1


def build_fixture(root):
    directory=root/'build/gta-transport';directory.mkdir(parents=True)
    sources=[]
    for relative in a.SOURCES:
        path=root/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'own source')
        sources.append(dict(path=relative,sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    raw=bytearray(256);raw[:2]=b'MZ';struct.pack_into('<I',raw,0x3c,128);raw[128:134]=b'PE\0\0\x64\x86'
    dll=directory/'thaw-gta-atomic.dll';dll.write_bytes(raw)
    meta=dict(format='gta-thaw-atomic-build-v1',machine='amd64',dll=dll.name,
              tests_passed=True,bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest(),sources=sources)
    manifest=directory/'transport-build.json';manifest.write_text(json.dumps(meta))
    return dll,manifest,meta


class AtomicBuildTests(unittest.TestCase):
    def test_exact_build_and_source_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);dll,manifest,meta=build_fixture(root)
            self.assertEqual(a.validate_build(root),(dll,meta))
            (root/a.SOURCES[0]).write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'source changed'):a.validate_build(root)

    def test_changed_binary_wrong_architecture_and_untested_build_rejected(self):
        for corruption in ('hash','architecture','untested','source_path'):
            with self.subTest(corruption=corruption),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);dll,manifest,meta=build_fixture(root)
                if corruption in ('hash','architecture'):
                    raw=bytearray(dll.read_bytes());raw[132:134]=b'\x4c\x01';dll.write_bytes(raw)
                    if corruption=='architecture':meta['sha256']=hashlib.sha256(raw).hexdigest()
                elif corruption=='untested':meta['tests_passed']=False
                else:meta['sources'][0]['path']='../foreign.cpp'
                manifest.write_text(json.dumps(meta))
                with self.assertRaises(ValueError):a.validate_build(root)

    def test_loaded_once_with_absolute_path_and_no_path_search(self):
        dll=Path.cwd()/'build/gta-transport/thaw-gta-atomic.dll'
        exchange=Mock();library=SimpleNamespace(thaw_atomic_store32=exchange)
        a.load.cache_clear()
        try:
            with patch.object(a,'validate_build',return_value=(dll,{})) as validate, \
                    patch.object(a.C,'WinDLL',return_value=library) as load:
                self.assertIs(a.load(),exchange);self.assertIs(a.load(),exchange)
                validate.assert_called_once_with();load.assert_called_once_with(str(dll),winmode=0x800)
                self.assertEqual(exchange.argtypes,[C.c_void_p,C.c_uint32])
                self.assertIs(exchange.restype,C.c_int)
        finally:a.load.cache_clear()


if __name__=='__main__':unittest.main()
