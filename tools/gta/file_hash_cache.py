"""Disposable SHA-256 cache for local map data, never executable verification.

Windows hits require a local NTFS/ReFS file ID and true FILE_BASIC_INFO.ChangeTime.
The file stays open without write/delete sharing through lookup or hashing.
Other filesystems/platforms conservatively hash again. Cache I/O is optional;
errors reading or changing an actual input are not hidden.
"""
import ctypes as C
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile

FORMAT = 'gta-thaw-file-sha256-cache-v1'
MAX_BYTES = 4 * 1024 * 1024
MAX_ENTRIES = 4096
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_CACHE_ERRORS = (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError)


def _windows_api():
    if os.name != 'nt':
        return None
    from ctypes import wintypes as W
    api = C.WinDLL('kernel32', use_last_error=True)
    signatures = {
        'CreateFileW': ([W.LPCWSTR, W.DWORD, W.DWORD, C.c_void_p, W.DWORD, W.DWORD, W.HANDLE], W.HANDLE),
        'CloseHandle': ([W.HANDLE], W.BOOL),
        'GetFileInformationByHandleEx': ([W.HANDLE, C.c_int, C.c_void_p, W.DWORD], W.BOOL),
        'GetVolumeInformationByHandleW': ([W.HANDLE, W.LPWSTR, W.DWORD, C.c_void_p,
            C.c_void_p, C.c_void_p, W.LPWSTR, W.DWORD], W.BOOL),
    }
    for name, (args, result) in signatures.items():
        function = getattr(api, name)
        function.argtypes, function.restype = args, result
    return api


try:
    _API = _windows_api()
except (OSError, AttributeError):
    _API = None


def _open(path):
    if _API is None:
        return path.open('rb')
    import msvcrt
    # Share existing/new readers, but never accept an in-progress writable file
    # or allow replacement while returning a cached digest of its old handle.
    handle = _API.CreateFileW(str(path), 0x80000000, 1, None, 3, 0x80, None)
    if handle == C.c_void_p(-1).value:
        raise C.WinError(C.get_last_error())
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        _API.CloseHandle(handle)
        raise
    return os.fdopen(descriptor, 'rb')


def _strong_stamp(stream):
    if _API is None:
        return None
    import msvcrt
    import struct
    handle = msvcrt.get_osfhandle(stream.fileno())
    basic, identity = C.create_string_buffer(40), C.create_string_buffer(24)
    filesystem = C.create_unicode_buffer(32)
    if not (_API.GetFileInformationByHandleEx(handle, 0, basic, 40) and
            _API.GetFileInformationByHandleEx(handle, 18, identity, 24) and
            _API.GetVolumeInformationByHandleW(handle, None, 0, None, None, None, filesystem, 32)
            and filesystem.value.upper() in ('NTFS', 'REFS')):
        return None
    created, accessed, modified, changed, attributes = struct.unpack_from('<4qI', basic.raw)
    volume = struct.unpack_from('<Q', identity.raw)[0]
    file_id = int.from_bytes(identity.raw[8:24], 'little')
    current = os.fstat(stream.fileno())
    if (not stat.S_ISREG(current.st_mode) or attributes & 0x400 or
            not volume or not file_id or modified <= 0 or changed <= 0):
        return None
    return (volume, file_id, current.st_size, modified, changed)


def file_stamp(path):
    """Return a strong immutable tuple, or None when fast reuse is unsafe."""
    path = Path(path).resolve(strict=True)
    with _open(path) as stream:
        return _strong_stamp(stream)


def _weak_stamp(stream):
    value = os.fstat(stream.fileno())
    if not stat.S_ISREG(value.st_mode):
        raise ValueError('Hash input must be a regular file')
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def _valid_stamp(value):
    return (isinstance(value, (list, tuple)) and len(value) == 5 and
            all(type(number) is int and 0 <= number < 2**bits
                for number, bits in zip(value, (64, 128, 64, 64, 64))) and
            all(value[index] > 0 for index in (0, 1, 3, 4)))


class FileHashCache:
    def __init__(self, cache_path):
        self.path = Path(os.path.abspath(cache_path))
        self.entries = {}
        self.stats = dict(hits=0, hashed_bytes=0)
        try:
            if self._safe_location() and self.path.is_file() and self.path.stat().st_size <= MAX_BYTES:
                with self.path.open('rb') as stream:
                    raw = stream.read(MAX_BYTES + 1)
                if len(raw) > MAX_BYTES:
                    return
                value = json.loads(raw)
                if not isinstance(value, dict):
                    return
                rows = value.get('entries')
                if value.get('format') != FORMAT or not isinstance(rows, dict) or len(rows) > MAX_ENTRIES:
                    return
                for key, row in rows.items():
                    if (not isinstance(key, str) or len(key) > 32768 or not Path(key).is_absolute() or
                            not isinstance(row, dict) or not _valid_stamp(row.get('stamp')) or
                            not isinstance(row.get('sha256'), str) or not _SHA.fullmatch(row['sha256'])):
                        return
                self.entries = rows
        except _CACHE_ERRORS:
            pass

    def _safe_location(self):
        # Never create parents, traverse reparse points, or publish outside the
        # literal cache directory supplied by the validated map installation.
        for parent in self.path.parents:
            value = parent.lstat()
            if not stat.S_ISDIR(value.st_mode) or getattr(value, 'st_file_attributes', 0) & 0x400:
                return False
        if self.path.parent.resolve(strict=True) != self.path.parent:
            return False
        if self.path.exists() or self.path.is_symlink():
            value = self.path.lstat()
            if not stat.S_ISREG(value.st_mode) or getattr(value, 'st_file_attributes', 0) & 0x400:
                return False
        return True

    def digest(self, path):
        path = Path(path).resolve(strict=True)
        key = os.path.normcase(str(path))
        reusable = path.suffix.lower() not in ('.exe', '.dll', '.asi')
        with _open(path) as stream:
            weak_before = _weak_stamp(stream)
            before = _strong_stamp(stream)
            cached = self.entries.get(key)
            if reusable and before is not None and cached is not None and tuple(cached['stamp']) == before:
                if _strong_stamp(stream) != before or _weak_stamp(stream) != weak_before:
                    raise ValueError('Map input changed during cached hash lookup: ' + str(path))
                self.stats['hits'] += 1
                return cached['sha256']
            result = hashlib.file_digest(stream, 'sha256').hexdigest()
            if _weak_stamp(stream) != weak_before or _strong_stamp(stream) != before:
                raise ValueError('Map input changed during hashing: ' + str(path))
        self.stats['hashed_bytes'] += weak_before[2]
        if reusable and before is not None:
            self.entries.pop(key, None)
            if len(self.entries) >= MAX_ENTRIES:
                self.entries.pop(next(iter(self.entries)))
            self.entries[key] = dict(stamp=before, sha256=result)
        else:
            self.entries.pop(key, None)
        return result

    def save(self):
        """Best-effort atomic publication; failure leaves the old cache intact."""
        temporary = None
        identity = None
        try:
            if not self._safe_location():
                return False
            raw = json.dumps(dict(format=FORMAT, entries=self.entries), separators=(',', ':')).encode('utf-8')
            if len(raw) > MAX_BYTES:
                return False
            descriptor, name = tempfile.mkstemp(prefix='.file-hashes-', suffix='.tmp', dir=self.path.parent)
            temporary = Path(name)
            with os.fdopen(descriptor, 'wb') as stream:
                value = os.fstat(stream.fileno())
                identity = (value.st_dev, value.st_ino)
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            value = temporary.lstat()
            if (not self._safe_location() or temporary.resolve().parent != self.path.parent or
                    (value.st_dev, value.st_ino) != identity or not stat.S_ISREG(value.st_mode) or
                    getattr(value, 'st_file_attributes', 0) & 0x400):
                return False
            os.replace(temporary, self.path)
            temporary = None
            return True
        except _CACHE_ERRORS:
            return False
        finally:
            if temporary is not None:
                try:
                    value = temporary.lstat()
                    if (self._safe_location() and temporary.resolve().parent == self.path.parent and
                            (value.st_dev, value.st_ino) == identity and stat.S_ISREG(value.st_mode) and
                            not getattr(value, 'st_file_attributes', 0) & 0x400):
                        temporary.unlink(missing_ok=True)
                except _CACHE_ERRORS:
                    pass
