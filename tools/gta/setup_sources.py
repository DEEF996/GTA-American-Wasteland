"""Select and inspect local game sources. Never patches a source installation.

The GTA adapter currently supports one verified 1.0 US image. A stock THAW
backend is still required: finding THAW.exe is not proof of compatibility.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct

GTA_SHA256 = 'a559aa772fd136379155efa71f00c47aad34bbfeae6196b0fe1047d0645cbd26'
GTA_REQUIRED = ('gta_sa.exe', 'data/gta.dat', 'data/default.dat',
                'models/gta3.img', 'models/gta_int.img')
THAW_REQUIRED = ('THAW.exe', 'data/pak/qb.pak.wpc', 'data/pak/qb.pab.wpc')


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def locate(selected, executable, child):
    root = Path(selected).expanduser().resolve(strict=True)
    if root.is_file():
        if root.name.casefold() != executable.casefold():
            raise ValueError(f'Expected {executable}, or its containing directory')
        root = root.parent
    candidates = [p for p in (root, root / child) if (p / executable).is_file()]
    if len(candidates) != 1:
        raise ValueError(f'Expected exactly one {executable} installation in {root}')
    return candidates[0].resolve(strict=True)


def files(root, names):
    rows = []
    for name in names:
        path = root / name
        if not path.is_file() or not path.resolve().is_relative_to(root):
            raise ValueError(f'Missing or redirected game input: {name}')
        rows.append({'path': name, 'bytes': path.stat().st_size, 'sha256': digest(path)})
    return rows


def pe_info(path):
    with path.open('rb') as stream:
        header = stream.read(64)
        if len(header) != 64 or header[:2] != b'MZ':
            raise ValueError('Expected a Windows PE executable')
        offset, = struct.unpack_from('<I', header, 0x3c)
        if not 64 <= offset <= 1024 * 1024:
            raise ValueError('Invalid PE header offset')
        stream.seek(offset)
        header = stream.read(24)
        if len(header) != 24 or header[:4] != b'PE\0\0':
            raise ValueError('Invalid PE signature')
        machine, sections = struct.unpack_from('<HH', header, 4)
        optional_size, = struct.unpack_from('<H', header, 20)
        if machine != 0x14c or not 1 <= sections <= 96 or not 224 <= optional_size <= 4096:
            raise ValueError('Expected bounded x86 PE32 executable headers')
        optional = stream.read(optional_size)
        if len(optional) != optional_size or struct.unpack_from('<H', optional)[0] != 0x10b:
            raise ValueError('Expected PE32 optional header')
        return {'machine': 'x86', 'image_base': struct.unpack_from('<I', optional, 28)[0],
                'image_bytes': struct.unpack_from('<I', optional, 56)[0],
                'relocation_rva': struct.unpack_from('<I', optional, 136)[0],
                'relocation_bytes': struct.unpack_from('<I', optional, 140)[0]}


def inspect_sources(gta, thaw):
    gta = locate(gta, 'gta_sa.exe', 'GTA San Andreas')
    thaw = locate(thaw, 'THAW.exe', 'Game')
    gta_files = files(gta, GTA_REQUIRED)
    if gta_files[0]['sha256'] != GTA_SHA256:
        raise ValueError('Unsupported GTA executable: an adapter for this exact build is required')
    thaw_files = files(thaw, THAW_REQUIRED)
    return {'format': 'gta-thaw-local-sources-v1',
            'gta': {'directory': str(gta), 'files': gta_files, 'pe': pe_info(gta / 'gta_sa.exe')},
            'thaw': {'directory': str(thaw), 'files': thaw_files, 'pe': pe_info(thaw / 'THAW.exe'),
                     'rethawed_present': (thaw / 'reTHAWed.exe').exists() or (thaw / 'rthw.dll').exists(),
                     'stock_provenance_verified': False},
            'backend': {'ready': False, 'reason': 'local-import-and-isolated-installation-required'},
            'original_files_modified': False, 'assets_redistributed': False}


def save_configuration(report, destination):
    destination = Path(destination).resolve()
    for key in ('gta', 'thaw'):
        if destination.is_relative_to(Path(report[key]['directory'])):
            raise ValueError('Keep source configuration outside the selected original game directories')
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents silently replacing an earlier source choice.
    with destination.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def gui():
    from install import gui as installer_gui
    installer_gui()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gta', type=Path)
    parser.add_argument('--thaw', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--gui', action='store_true')
    args = parser.parse_args()
    if args.gui or not any((args.gta, args.thaw, args.output)):
        gui()
        return
    if not args.gta or not args.thaw:
        parser.error('--gta and --thaw must both be supplied')
    report = inspect_sources(args.gta, args.thaw)
    if args.output:
        save_configuration(report, args.output)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
