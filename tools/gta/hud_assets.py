"""Private stock HUD atlas bundle using the existing THAW IMG/FNT decoders.

Run explicitly on an already imported stock resource cache. Keeps original
scanlines/metrics, validates source hashes, and publishes to a new directory.
No reTHAWed files, native execution, game edits or redistributable assets.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import tempfile

TOOLS = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(TOOLS), str(TOOLS / 'waw'), str(TOOLS / 'vanilla_thaw')]
from assets_export import qbkey
from export_hud import decode_img, parse_font, pak_member
import import_qb as qb
from import_resources import plain_path

FORMAT = 'gta-thaw-private-hud-assets-v1'
MAX_TEXTURES, MAX_BYTES = 64, 32 * 1024 * 1024
BASE_SPRITES = ('balancemeter', 'balancemeter_2', 'balancemeter_bg',
                'balancearrow_glow', 'score_small', 'score_solo', 'special', 'specialbar',
                # Stance changes create these original icons only while playing.
                'nollie_icon', 'fakie_icon', 'switch_icon', 'pressure_icon', 'sw_pressure_icon')
SETUP_SPRITES = BASE_SPRITES + ('watch', 'hand', 'de_sponsor') + tuple(f'time_{i}' for i in range(1, 9))


def prepare(resources, layout_log, output):
    qb.require(os.name == 'nt', 'Exclusive directory publication requires Windows')
    resources = plain_path(resources).resolve(strict=True)
    layout_log = plain_path(layout_log).resolve(strict=True) if layout_log is not None else None
    raw_manifest, raw_log = resources.read_bytes(), layout_log.read_bytes() if layout_log else b''
    source = json.loads(raw_manifest)
    cache = resources.parent
    qb.require(source.get('format') == 'gta-thaw-private-stock-resources-v1' and
               source.get('sources_revalidated_before_publish') is True and
               Path(source['directory']).resolve() == cache, 'HUD requires a verified stock resource cache')
    original = Path(source['source']).resolve(strict=True)
    output = plain_path(output, missing=True).resolve()
    qb.require(not output.exists() and not output.is_relative_to(cache) and not output.is_relative_to(original),
               'HUD destination must be new and outside original/imported resources')
    rows = {row['path'].casefold(): row for row in source['files']}
    qb.require(len(rows) == len(source['files']), 'Duplicate resource path')
    reads = {}

    def read(name):
        row = rows.get(name.casefold())
        qb.require(row is not None, 'Missing stock HUD source: ' + name)
        path = plain_path(cache / row['path']).resolve(strict=True)
        qb.require(path.is_relative_to(cache), 'HUD source leaves its verified resource cache')
        payload = path.read_bytes()
        qb.require(len(payload) == row['bytes'] and qb.sha(payload) == row['sha256'], 'Stock HUD source changed')
        reads[row['path']] = row
        return payload, row

    layouts = [json.loads(line) for line in raw_log.splitlines() if line.strip()]
    layouts = [row for row in layouts if row.get('phase') == 'hud_layout_pass']
    qb.require(layout_log is None or (layouts and all(row.get('original_layout_update') is True and row['canvas'] == [640, 448]
                              for row in layouts)), 'HUD atlas selection requires genuine stock layout output')
    draws = [draw for row in layouts for draw in row['draws']]
    wanted = {draw['image_key'] for draw in draws if draw['kind'] == 'sprite'}
    wanted.update(qbkey(name) for name in (BASE_SPRITES if layout_log else SETUP_SPRITES))
    textures, fonts, pixels = [], {}, []

    def add(name, key, kind, payload, row, decoded):
        width, height, rgba = decoded
        qb.require(0 < width <= 1024 and 0 < height <= 1024 and len(rgba) == width * height * 4,
                   'HUD atlas has unsupported dimensions or pixel extent')
        index = len(textures)
        textures.append(dict(name=name, key=key, kind=kind, index=index, width=width, height=height,
                             source=row['path'], source_sha256=row['sha256'],
                             member_sha256=qb.sha(payload), rgba_sha256=qb.sha(rgba)))
        pixels.append((width, height, rgba))
        return index

    # The six original permanent fonts cover current score/tricks and the native
    # off-board timer; metrics are not adjusted to imitate another TH title.
    for name in ('testtitle', 'newtrickfont', 'newtimerfont', 'small', 'dialog', 'buttonspc'):
        if name == 'testtitle':
            _, row = read('data/worlds/Global/global.pak.wpc')
            read('data/worlds/Global/global.pab.wpc')
            payload = pak_member(cache / row['path'], qbkey(name))
        else:
            payload, row = read('data/fonts/' + name + '.fnt.wpc')
        font, image = parse_font(payload)
        font['texture_index'] = add(name, qbkey(name), 'font', payload, row, image)
        fonts[name] = font
    for key in sorted(wanted):
        matches = [row for name, row in rows.items() if name.startswith('data/images/panelsprites/') and
                   name.count('/') == 3 and name.endswith('.img.wpc') and
                   qbkey(Path(name).name[:-8]) == key]
        qb.require(len(matches) == 1, f'No unique ordinary stock panel sprite for {key:08x}')
        payload, row = read(matches[0]['path'])
        add(Path(row['path']).name[:-8], key, 'image', payload, row, decode_img(payload))
    qb.require(0 < len(textures) <= MAX_TEXTURES, 'Stock HUD atlas count exceeds limit')
    bundle = bytearray(b'GTAHUDTX' + struct.pack('<2I', 1, len(textures)))
    for width, height, rgba in pixels:
        bundle.extend(struct.pack('<3I', width, height, len(rgba)))
        bundle.extend(rgba)
    qb.require(len(bundle) <= MAX_BYTES, 'Stock HUD atlas bytes exceed limit')
    metadata = dict(format=FORMAT, canvas=[640, 448], textures=textures, fonts=fonts,
                    bundle='hud-atlases.bin', bundle_sha256=qb.sha(bundle),
                    resources=str(resources), resources_sha256=qb.sha(raw_manifest),
                    layout_log=str(layout_log) if layout_log else None,
                    layout_log_sha256=qb.sha(raw_log) if layout_log else None,
                    selection='observed-original-layout' if layout_log else 'stock-gameplay-sprites-and-timer',
                    source_inputs=list(reads.values()), original_files_modified=False,
                    game_material_private=True, scanlines='original IMG rows; original UVs',
                    native_layout_required=True, complete_HUD_coverage=False)
    # Validate every observed source font/image against the selected bundle before publishing.
    from hud_quads import Assets
    assets = Assets(metadata)
    for draw in draws:
        assets.validate_source(draw)
    for row in reads.values():
        read(row['path'])
    qb.require(resources.read_bytes() == raw_manifest and (layout_log is None or layout_log.read_bytes() == raw_log),
               'HUD import inputs changed')
    output.parent.mkdir(parents=True, exist_ok=True)
    parent = output.parent.resolve(strict=True)
    stage = Path(tempfile.mkdtemp(prefix='.stock-hud-', dir=parent))
    expected = stage.resolve(strict=True)
    try:
        (stage / 'hud-atlases.bin').write_bytes(bundle)
        (stage / 'manifest.json').write_bytes(qb.json_bytes(metadata))
        os.rename(stage, output)
    finally:
        if stage.exists():
            qb.remove_staging(stage, expected, parent)
    return dict(output=str(output), textures=len(textures), bytes=len(bundle), sha256=qb.sha(bundle))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resources', type=Path, required=True)
    parser.add_argument('--layout-log', type=Path, help='Optional genuine native layout; omitted for first-run static atlas import')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.resources, args.layout_log, args.output)))
