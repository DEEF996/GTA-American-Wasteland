"""Load our bounded x64 interlocked store, without a global-flush fallback."""
import ctypes as C
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import struct

from import_resources import plain_path

ROOT = Path(__file__).resolve().parents[2]
SOURCES = tuple('tools/gta/transport_native/'+name for name in
                ('atomic_store.cpp','atomic_store_test.cpp','build.ps1'))


def require(ok, message):
    if not ok:
        raise ValueError(message)


def validate_build(root=ROOT):
    root = plain_path(root).resolve(strict=True)
    directory = plain_path(root/'build/gta-transport').resolve(strict=True)
    manifest = plain_path(directory/'transport-build.json')
    require(directory.is_relative_to(root) and manifest.stat().st_size <= 16384,
            'Atomic transport build path or manifest differs')
    meta = json.loads(manifest.read_text(encoding='utf-8-sig'))
    require(meta.get('format') == 'gta-thaw-atomic-build-v1' and
            meta.get('machine') == 'amd64' and meta.get('dll') == 'thaw-gta-atomic.dll' and
            meta.get('tests_passed') is True, 'Unsupported or untested atomic transport build')
    sources = meta.get('sources')
    require(isinstance(sources,list) and len(sources) == len(SOURCES) and
            [row.get('path') for row in sources] == list(SOURCES), 'Atomic transport source manifest differs')
    for row in sources:
        source = plain_path(root/row['path']).resolve(strict=True)
        require(source.is_relative_to(root) and source.stat().st_size <= 1024*1024 and
                hashlib.sha256(source.read_bytes()).hexdigest() == row.get('sha256'),
                'Atomic transport source changed; rebuild the x64 transport')
    dll = plain_path(directory/'thaw-gta-atomic.dll').resolve(strict=True)
    require(dll.parent == directory and 64 <= dll.stat().st_size <= 2*1024*1024,
            'Atomic transport DLL path or size differs')
    raw = dll.read_bytes()
    require(len(raw) == meta.get('bytes') and hashlib.sha256(raw).hexdigest() == meta.get('sha256'),
            'Atomic transport DLL hash differs; restore the complete integration package')
    offset, = struct.unpack_from('<I',raw,0x3c)
    require(raw[:2] == b'MZ' and 64 <= offset <= len(raw)-6 and
            raw[offset:offset+6] == b'PE\0\0\x64\x86', 'Atomic transport DLL must be AMD64')
    return dll,meta


@lru_cache(maxsize=1)
def load():
    require(C.sizeof(C.c_void_p) == 8, 'Atomic transport requires the bundled x64 Python runtime')
    dll,_ = validate_build()
    # Absolute private DLL; dependencies may come only from System32, not PATH/CWD.
    library = C.WinDLL(str(dll),winmode=0x800)
    exchange = library.thaw_atomic_store32
    exchange.argtypes = [C.c_void_p,C.c_uint32]
    exchange.restype = C.c_int
    # ctypes retains the owning DLL in the returned function; this cache retains
    # that function for the lifetime of all publisher mappings.
    return exchange
