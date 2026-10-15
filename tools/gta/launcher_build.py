"""Verify the dependency-free desktop bootstrap before release packaging."""
import hashlib
import json
from pathlib import Path
import struct

from import_resources import plain_path

SOURCES = tuple('tools/gta/launcher_native/'+name for name in
                ('launcher.cpp', 'launcher_command.h', 'launcher_test.cpp', 'build.ps1'))


def validate_build(root):
    root = plain_path(root).resolve(strict=True)
    directory = plain_path(root/'build/gta-launcher')
    path = plain_path(directory/'launcher-build.json')
    if path.stat().st_size > 16384:
        raise ValueError('Oversized launcher build manifest')
    meta = json.loads(path.read_text(encoding='utf-8-sig'))
    if (meta.get('format') != 'gta-aw-launcher-build-v1' or meta.get('machine') != 'amd64' or
            meta.get('executable') != 'GTA American Wasteland.exe' or meta.get('tests_passed') is not True or
            [row.get('path') for row in meta.get('sources', [])] != list(SOURCES)):
        raise ValueError('Unsupported or untested desktop launcher build')
    for row in meta['sources']:
        source = plain_path(root/row['path'])
        if source.stat().st_size > 1024*1024 or hashlib.sha256(source.read_bytes()).hexdigest() != row.get('sha256'):
            raise ValueError('Desktop launcher source changed; rebuild the launcher')
    executable = plain_path(directory/meta['executable'])
    if not 256 <= executable.stat().st_size <= 2*1024*1024:
        raise ValueError('Invalid launcher executable size')
    data = executable.read_bytes()
    offset, = struct.unpack_from('<I', data, 0x3c)
    if (len(data) != meta.get('bytes') or hashlib.sha256(data).hexdigest() != meta.get('sha256') or
            data[:2] != b'MZ' or not 64 <= offset <= len(data)-6 or data[offset:offset+6] != b'PE\0\0\x64\x86'):
        raise ValueError('Desktop launcher executable changed or is not AMD64')
    return executable, meta
