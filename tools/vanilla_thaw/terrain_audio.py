"""Private original terrain samples, passed unchanged to stock RAM registration.

The two pinned retail zone banks supply the 31 samples referenced by supported
terrain routes but absent from the global bank. Only metadata is public; first
startup extracts unchanged sample headers/PCM from the user's installation.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import tempfile

import import_resources
import probe_audio
import probe_loader as host

PINS_SHA = '9cbb1f613e078f08e16dcf073ac5009c3331024f66abea80aeafd338da6ba23e'
FORMAT = 'stock-thaw-private-terrain-audio-v1'
MAX_BUNDLE = 16*1024*1024


def pins():
    raw = Path(__file__).with_name('terrain_audio_sources.json').read_bytes()
    host.require(hashlib.sha256(raw).hexdigest() == PINS_SHA, 'Original terrain audio pins changed')
    value = json.loads(raw)
    host.require(value['format'] == 'stock-thaw-terrain-audio-pins-v1' and value['native_count'] == 144 and
                 len(value['sources']) == 2, 'Unexpected original terrain audio inventory')
    return value


def sample_rows(metadata):
    rows, cursor = [], 0
    for source in metadata['sources']:
        for sample in source['samples']:
            cursor = (cursor+127)&~127
            rows.append(dict(sample, offset=cursor))
            cursor += 128+sample['pcm_bytes']
    host.require(len(rows) == 31 and len({r['checksum'] for r in rows}) == 31 and cursor <= MAX_BUNDLE,
                 'Terrain audio sample count/identity/budget differs')
    return rows, cursor


def read_cache(output, original=None):
    output = import_resources.plain_path(output).resolve(strict=True)
    metadata = pins()
    manifest = json.loads(import_resources.plain_path(output/'manifest.json').read_text(encoding='utf-8'))
    rows, size = sample_rows(metadata)
    host.require(manifest.get('format') == FORMAT and manifest.get('pins_sha256') == PINS_SHA and
                 manifest.get('samples') == rows and manifest.get('bytes') == size and
                 manifest.get('source_files_modified') is False,
                 'Private terrain audio manifest differs')
    if original is not None:
        host.require(Path(manifest['source']).resolve() == Path(original).resolve(),
                     'Private terrain audio belongs to another source installation')
    path = import_resources.plain_path(output/'terrain-samples.bin')
    host.require(path.stat().st_size == size, 'Private terrain audio length differs')
    raw = path.read_bytes()
    host.require(hashlib.sha256(raw).hexdigest() == manifest['sha256'], 'Private terrain audio bytes changed')
    end = 0
    for row in rows:
        host.require(not any(raw[end:row['offset']]), 'Private terrain sample alignment changed')
        end = row['offset']+128+row['pcm_bytes']
        host.require(hashlib.sha256(raw[row['offset']:end]).hexdigest() == row['sha256'],
                     'Private terrain sample is not the pinned original')
    return dict(source=raw, samples=rows, native_count=metadata['native_count'], sha256=manifest['sha256'])


def prepare(original, output, *, stopped=None):
    """New-directory atomic publication; cancellation never deletes a prior cache."""
    import probe_audio_bank
    original = import_resources.plain_path(original).resolve(strict=True)
    output = import_resources.plain_path(output, missing=True).absolute()
    host.require(not output.resolve().is_relative_to(original), 'Terrain cache must stay outside the original game')
    host.require(not output.exists(), 'Refusing to replace an existing terrain audio cache')
    executable = import_resources.plain_path(original/'THAW.exe')
    host.require(hashlib.sha256(executable.read_bytes()).hexdigest() == host.FILES['THAW.exe'][1],
                 'Terrain samples require the supported original THAW image')
    metadata = pins()
    expected, size = sample_rows(metadata)
    output.parent.mkdir(parents=True, exist_ok=True)
    host.require(shutil.disk_usage(output.parent).free >= size*2+1024*1024,
                 'Insufficient space for private terrain audio cache')
    def check():
        if stopped is not None and stopped():
            raise RuntimeError('Terrain audio import cancelled')
    payload, index = bytearray(size), 0
    for row in metadata['sources']:
        check()
        with import_resources.plain_path(original/row['pak']).open('rb') as stream:
            header = stream.read(192)
            kind, offset, count, checksum, _, _, _, flags = struct.unpack_from('<8I', header)
            name = header[32:].split(b'\0', 1)[0].decode('ascii').replace('\\', '/').lower()
            host.require(kind == probe_audio_bank.probe_core.import_qb.qbkey('.qb') and flags & 0x20 and
                         name == row['member'] and count == row['qb_bytes'] and offset >= 192 and
                         checksum in (probe_audio_bank.probe_core.import_qb.qbkey(name),
                                      probe_audio_bank.probe_core.import_qb.qbkey(name[:-4])),
                         'Original zone sound metadata member differs')
            stream.seek(offset)
            source = stream.read(count)
        host.require(hashlib.sha256(source).hexdigest() == row['qb_sha256'], 'Original zone sound metadata changed')
        bank_path = import_resources.plain_path(original/row['bank'])
        host.require(bank_path.stat().st_size == row['bank_bytes'], 'Original zone sound bank length changed')
        bank = bank_path.read_bytes()
        host.require(hashlib.sha256(bank).hexdigest() == row['bank_sha256'], 'Original zone sound bank changed')
        actual = {sample['checksum']: sample for sample in probe_audio_bank.parse_samples(source, bank, **row['parser'])}
        for sample in row['samples']:
            host.require(actual[sample['checksum']] == {k:v for k,v in sample.items() if k != 'sha256'},
                         'Original sample metadata differs from selected authored fields')
            raw = bank[sample['offset']:sample['offset']+128+sample['pcm_bytes']]
            host.require(hashlib.sha256(raw).hexdigest() == sample['sha256'], 'Original sample span differs')
            offset = expected[index]['offset']
            payload[offset:offset+len(raw)] = raw
            index += 1
    check()
    stage = Path(tempfile.mkdtemp(prefix='.terrain-audio-', dir=output.parent))
    try:
        (stage/'terrain-samples.bin').write_bytes(payload)
        manifest = dict(format=FORMAT, source=str(original), pins_sha256=PINS_SHA, samples=expected,
                        bytes=size, sha256=hashlib.sha256(payload).hexdigest(), source_files_modified=False)
        (stage/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
        read_cache(stage, original)
        check()
        os.rename(stage, output)
    finally:
        if stage.exists():
            host.require(stage.resolve().parent == output.parent.resolve() and stage.name.startswith('.terrain-audio-'),
                         'Refusing cleanup outside the owned terrain audio stage')
            shutil.rmtree(stage)
    return manifest


def ensure(original, output):
    if not Path(output).exists():
        prepare(original, output)
    return read_cache(output, original)


def register(api, process, record, deadline, log, system, inputs):
    """Whole original method, source fields unchanged, no fabricated audio events."""
    import probe_audio_bank
    from probe_audio_frame import locked
    source, samples = inputs['source'], inputs['samples']
    expected, size = sample_rows(pins())
    host.require(samples == expected and len(source) == size and inputs['native_count'] == 144 and
                 hashlib.sha256(source).hexdigest() == inputs['sha256'], 'Prepared terrain audio input changed')
    pin = probe_audio_bank.PINS['SampleRegister']
    host.require(hashlib.sha256(system.read(pin[1], pin[2])).hexdigest() == pin[3], 'Original RAM audio loader changed')
    manager = system.word(probe_audio.MANAGER)
    start, end = system.word(0x84376c), system.word(0x843770)
    host.require(start <= manager < end and system.word(probe_audio.SAMPLES) == 113,
                 'Terrain registration requires the existing original global bank')
    address = system.allocate(source)
    host.require(address % 128 == 0 and 0x10000 <= address <= 0xffffffff-len(source), 'Terrain PCM alignment differs')
    floats = lambda value: struct.unpack('<I', struct.pack('<f', value))[0]
    with locked(system):
        before = system.read(0x6e0f90, 113*32)
        existing = {struct.unpack_from('<I', before, n*32)[0] for n in range(113)}
        host.require(not existing.intersection(row['checksum'] for row in samples), 'Terrain samples duplicate the global bank')
        for index, sample in enumerate(samples):
            args = [address+sample['offset'], sample['pcm_bytes'], sample['checksum'], sample['frequency'],
                    int(bool(sample['flags']&8)), 0, 0, sample['flags']&~8,
                    floats(sample['dropoff']), floats(sample['pitch']), floats(sample['volume']), 0]
            host.require(host.native_call(api, process, pin, args, record, deadline, log, manager)&255,
                         'Original RAM loader rejected a terrain sample')
            row = struct.unpack('<8I', system.read(0x6e0f90+(113+index)*32, 32))
            host.require(row[0] == sample['checksum'] and row[1] == sample['flags'] | (3<<16) and
                         row[2:5] == tuple(floats(sample[k]) for k in ('pitch', 'volume', 'dropoff')) and
                         row[7] == args[0] and start <= row[5] <= end-0x48,
                         'Original RAM sample did not preserve its source fields')
            desc = struct.unpack('<18I', system.read(row[5], 0x48))
            host.require(desc[7] == desc[14] == sample['pcm_bytes'] and desc[9] == args[0] and
                         desc[15] == 0 and desc[16] == args[0]+128, 'Original terrain sample descriptor differs')
        host.require(system.word(probe_audio.SAMPLES) == 144 and system.read(0x6e0f90, len(before)) == before and
                     system.read(address, len(source)) == source, 'Terrain registration changed prior bank/source bytes')
    system.terrain_audio_samples = 144
    log('terrain_audio_bank_pass', original_extra_samples=31, original_total_samples=144,
        source_sha256=inputs['sha256'], source_bytes=len(source), source_pointer=address,
        original_sample_registration=True, prior_global_bank_unchanged=True,
        all_sample_fields_and_pcm_verified=True, native_gameplay_trigger_verified=False)
    return dict(samples=144, source_pointer=address)
